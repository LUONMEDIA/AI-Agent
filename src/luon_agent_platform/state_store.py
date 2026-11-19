"""Durable local workflow state, leases, and tamper-evident event history.

This SQLite implementation is single-host coordination only. It is not a
distributed lock, shared multi-host queue, authorization service, or executor.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
from typing import Any, Iterator


SCHEMA_VERSION = 1
TASK_STATUSES = {
    "pending", "ready", "claimed", "running", "blocked",
    "succeeded", "failed", "needs_reconciliation", "cancelled",
}


class StateStoreError(RuntimeError):
    """Base class for state-store errors."""


class WorkflowConflict(StateStoreError):
    """A workflow ID or idempotency key was reused with different content."""


class InvalidTransition(StateStoreError):
    """A requested workflow/task state transition is not allowed."""


class VersionConflict(StateStoreError):
    """The caller's expected version is stale."""


class LeaseExpired(StateStoreError):
    """The execution lease is missing, expired, or does not match."""


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("DATETIME_MUST_BE_TIMEZONE_AWARE")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


class StateStore:
    """SQLite-backed local state store.

    Keep the database on a private local disk outside the public repository.
    Do not put this file on a network share and do not use it for multi-host
    workers. A future server database/queue must replace this adapter for that.
    """

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path).expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.database_path), timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise StateStoreError(f"UNSUPPORTED_STATE_SCHEMA:{version}")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS workflows (
                    workflow_id TEXT PRIMARY KEY,
                    spec_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    task_id TEXT PRIMARY KEY,
                    workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id),
                    task_type TEXT NOT NULL,
                    required_capability TEXT NOT NULL,
                    target_agent TEXT,
                    payload_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    idempotency_key TEXT,
                    attempt INTEGER NOT NULL DEFAULT 0,
                    version INTEGER NOT NULL DEFAULT 1,
                    assigned_agent_id TEXT,
                    lease_owner TEXT,
                    lease_token TEXT,
                    lease_expires_at TEXT,
                    last_error_code TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE UNIQUE INDEX IF NOT EXISTS uq_task_idempotency
                ON tasks(workflow_id, idempotency_key)
                WHERE idempotency_key IS NOT NULL;

                CREATE INDEX IF NOT EXISTS ix_tasks_workflow_status
                ON tasks(workflow_id, status);

                CREATE TABLE IF NOT EXISTS dependencies (
                    task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    depends_on_task_id TEXT NOT NULL REFERENCES tasks(task_id),
                    PRIMARY KEY (task_id, depends_on_task_id),
                    CHECK (task_id <> depends_on_task_id)
                );

                CREATE TABLE IF NOT EXISTS events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    workflow_id TEXT NOT NULL REFERENCES workflows(workflow_id),
                    task_id TEXT REFERENCES tasks(task_id),
                    event_type TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    occurred_at TEXT NOT NULL,
                    reason_code TEXT,
                    details_json TEXT NOT NULL,
                    previous_event_sha256 TEXT,
                    event_sha256 TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS ix_events_workflow_sequence
                ON events(workflow_id, sequence);
                """
            )
            connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        finally:
            connection.close()

    @staticmethod
    def _validate_definition(definition: dict[str, Any]) -> tuple[str, list[dict[str, Any]], str]:
        if not isinstance(definition, dict):
            raise ValueError("WORKFLOW_DEFINITION_NOT_OBJECT")
        if set(definition) - {"schema_version", "workflow_id", "tasks"}:
            raise ValueError("UNKNOWN_WORKFLOW_DEFINITION_FIELDS")
        if definition.get("schema_version") != "1.0":
            raise ValueError("UNSUPPORTED_WORKFLOW_SCHEMA")
        workflow_id = definition.get("workflow_id")
        if not isinstance(workflow_id, str) or not workflow_id.strip():
            raise ValueError("WORKFLOW_ID_REQUIRED")
        tasks = definition.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("WORKFLOW_TASKS_REQUIRED")

        normalized: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        seen_idempotency: set[str] = set()
        allowed = {
            "task_id", "task_type", "required_capability", "target_agent",
            "payload", "depends_on", "idempotency_key",
        }

        for item in tasks:
            if not isinstance(item, dict) or set(item) - allowed:
                raise ValueError("INVALID_TASK_DEFINITION_FIELDS")
            task_id = item.get("task_id")
            task_type = item.get("task_type")
            capability = item.get("required_capability")
            for field_name, value in (
                ("TASK_ID", task_id), ("TASK_TYPE", task_type),
                ("REQUIRED_CAPABILITY", capability),
            ):
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"{field_name}_REQUIRED")
            if task_id in seen_ids:
                raise ValueError(f"DUPLICATE_TASK_ID:{task_id}")
            seen_ids.add(task_id)

            payload = item.get("payload", {})
            if not isinstance(payload, dict):
                raise ValueError(f"TASK_PAYLOAD_NOT_OBJECT:{task_id}")
            _canonical(payload)

            dependencies = item.get("depends_on", [])
            if (not isinstance(dependencies, list)
                    or any(not isinstance(value, str) or not value.strip() for value in dependencies)
                    or len(dependencies) != len(set(dependencies))):
                raise ValueError(f"INVALID_DEPENDENCIES:{task_id}")

            target_agent = item.get("target_agent")
            if target_agent is not None and (not isinstance(target_agent, str) or not target_agent.strip()):
                raise ValueError(f"INVALID_TARGET_AGENT:{task_id}")

            idempotency_key = item.get("idempotency_key")
            if idempotency_key is not None:
                if not isinstance(idempotency_key, str) or not idempotency_key.strip():
                    raise ValueError(f"INVALID_IDEMPOTENCY_KEY:{task_id}")
                if idempotency_key in seen_idempotency:
                    raise ValueError(f"DUPLICATE_IDEMPOTENCY_KEY:{idempotency_key}")
                seen_idempotency.add(idempotency_key)

            normalized.append({
                "task_id": task_id,
                "task_type": task_type,
                "required_capability": capability,
                "target_agent": target_agent,
                "payload": payload,
                "depends_on": sorted(dependencies),
                "idempotency_key": idempotency_key,
            })

        for item in normalized:
            for dependency in item["depends_on"]:
                if dependency not in seen_ids:
                    raise ValueError(f"UNKNOWN_DEPENDENCY:{item['task_id']}:{dependency}")
                if dependency == item["task_id"]:
                    raise ValueError(f"SELF_DEPENDENCY:{item['task_id']}")

        graph = {item["task_id"]: item["depends_on"] for item in normalized}
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise ValueError(f"WORKFLOW_CYCLE_DETECTED:{node}")
            if node in visited:
                return
            visiting.add(node)
            for parent in graph[node]:
                visit(parent)
            visiting.remove(node)
            visited.add(node)

        for node in graph:
            visit(node)

        normalized.sort(key=lambda item: item["task_id"])
        spec = {"workflow_id": workflow_id, "tasks": normalized}
        spec_hash = hashlib.sha256(_canonical(spec).encode("utf-8")).hexdigest()
        return workflow_id, normalized, spec_hash

    def _append_event(
        self,
        connection: sqlite3.Connection,
        workflow_id: str,
        task_id: str | None,
        event_type: str,
        actor: str,
        reason_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        previous = connection.execute(
            "SELECT sequence, event_sha256 FROM events ORDER BY sequence DESC LIMIT 1"
        ).fetchone()
        sequence = (previous["sequence"] + 1) if previous else 1
        previous_hash = previous["event_sha256"] if previous else None
        occurred_at = _iso(_now())
        event_id = secrets.token_hex(16)
        details_json = _canonical(details or {})
        record = {
            "sequence": sequence,
            "event_id": event_id,
            "workflow_id": workflow_id,
            "task_id": task_id,
            "event_type": event_type,
            "actor": actor,
            "occurred_at": occurred_at,
            "reason_code": reason_code,
            "details": json.loads(details_json),
            "previous_event_sha256": previous_hash,
        }
        event_hash = hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest()
        connection.execute(
            """
            INSERT INTO events (
                sequence, event_id, workflow_id, task_id, event_type, actor,
                occurred_at, reason_code, details_json,
                previous_event_sha256, event_sha256
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                sequence, event_id, workflow_id, task_id, event_type, actor,
                occurred_at, reason_code, details_json, previous_hash, event_hash,
            ),
        )

    def create_workflow(self, definition: dict[str, Any], actor: str = "system") -> dict[str, Any]:
        workflow_id, tasks, spec_hash = self._validate_definition(definition)
        now = _iso(_now())
        existing = False

        with self._transaction() as connection:
            row = connection.execute(
                "SELECT spec_sha256 FROM workflows WHERE workflow_id = ?", (workflow_id,)
            ).fetchone()
            if row:
                if row["spec_sha256"] != spec_hash:
                    raise WorkflowConflict(f"WORKFLOW_ID_REUSED_WITH_DIFFERENT_SPEC:{workflow_id}")
                existing = True
            else:
                connection.execute(
                    """
                    INSERT INTO workflows(workflow_id, spec_sha256, status, version, created_at, updated_at)
                    VALUES (?, ?, 'queued', 1, ?, ?)
                    """,
                    (workflow_id, spec_hash, now, now),
                )
                self._append_event(connection, workflow_id, None, "workflow.created", actor)

                for item in tasks:
                    initial_status = "ready" if not item["depends_on"] else "pending"
                    connection.execute(
                        """
                        INSERT INTO tasks (
                            task_id, workflow_id, task_type, required_capability,
                            target_agent, payload_json, status, idempotency_key,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item["task_id"], workflow_id, item["task_type"],
                            item["required_capability"], item["target_agent"],
                            _canonical(item["payload"]), initial_status,
                            item["idempotency_key"], now, now,
                        ),
                    )

                for item in tasks:
                    for dependency in item["depends_on"]:
                        connection.execute(
                            "INSERT INTO dependencies(task_id, depends_on_task_id) VALUES (?, ?)",
                            (item["task_id"], dependency),
                        )

                for item in tasks:
                    self._append_event(
                        connection, workflow_id, item["task_id"], "task.created", actor,
                        details={"task_type": item["task_type"], "required_capability": item["required_capability"]},
                    )
                    if not item["depends_on"]:
                        self._append_event(connection, workflow_id, item["task_id"], "task.ready", actor)

                self._refresh_workflow_status(connection, workflow_id, actor)

        return self.get_workflow(workflow_id)

    @staticmethod
    def _row_to_task(connection: sqlite3.Connection, row: sqlite3.Row, include_lease_token: bool = False) -> dict[str, Any]:
        task = dict(row)
        task["payload"] = json.loads(task.pop("payload_json"))
        task["depends_on"] = [
            item["depends_on_task_id"]
            for item in connection.execute(
                "SELECT depends_on_task_id FROM dependencies WHERE task_id = ? ORDER BY depends_on_task_id",
                (task["task_id"],),
            ).fetchall()
        ]
        if not include_lease_token:
            task.pop("lease_token", None)
        return task

    def get_task(self, task_id: str) -> dict[str, Any]:
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if not row:
                raise StateStoreError(f"TASK_NOT_FOUND:{task_id}")
            return self._row_to_task(connection, row)
        finally:
            connection.close()

    def get_workflow(self, workflow_id: str) -> dict[str, Any]:
        connection = self._connect()
        try:
            row = connection.execute("SELECT * FROM workflows WHERE workflow_id = ?", (workflow_id,)).fetchone()
            if not row:
                raise StateStoreError(f"WORKFLOW_NOT_FOUND:{workflow_id}")
            tasks = connection.execute(
                "SELECT * FROM tasks WHERE workflow_id = ? ORDER BY task_id", (workflow_id,)
            ).fetchall()
            return {
                **dict(row),
                "tasks": [self._row_to_task(connection, item) for item in tasks],
            }
        finally:
            connection.close()

    def list_ready_tasks(self, workflow_id: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT t.* FROM tasks t
                WHERE t.workflow_id = ? AND t.status = 'ready'
                  AND NOT EXISTS (
                    SELECT 1 FROM dependencies d
                    JOIN tasks parent ON parent.task_id = d.depends_on_task_id
                    WHERE d.task_id = t.task_id AND parent.status <> 'succeeded'
                  )
                ORDER BY t.created_at, t.task_id
                """,
                (workflow_id,),
            ).fetchall()
            return [self._row_to_task(connection, row) for row in rows]
        finally:
            connection.close()

    @staticmethod
    def _check_version(row: sqlite3.Row, expected_version: int | None) -> None:
        if expected_version is not None and row["version"] != expected_version:
            raise VersionConflict(
                f"STALE_TASK_VERSION:{row['task_id']}:expected={expected_version}:actual={row['version']}"
            )

    @staticmethod
    def _check_dependencies(connection: sqlite3.Connection, task_id: str) -> None:
        incomplete = connection.execute(
            """
            SELECT parent.task_id FROM dependencies d
            JOIN tasks parent ON parent.task_id = d.depends_on_task_id
            WHERE d.task_id = ? AND parent.status <> 'succeeded'
            """,
            (task_id,),
        ).fetchall()
        if incomplete:
            raise InvalidTransition(f"DEPENDENCIES_NOT_COMPLETE:{task_id}")

    def claim_task(
        self,
        task_id: str,
        agent_id: str,
        worker_id: str,
        lease_seconds: int = 300,
        expected_version: int | None = None,
        actor: str = "orchestrator",
    ) -> dict[str, Any]:
        if not agent_id.strip() or not worker_id.strip():
            raise ValueError("AGENT_AND_WORKER_IDS_REQUIRED")
        if not isinstance(lease_seconds, int) or not 1 <= lease_seconds <= 3600:
            raise ValueError("LEASE_SECONDS_OUT_OF_RANGE")
        now = _now()
        expires = _iso(now + timedelta(seconds=lease_seconds))
        token = secrets.token_urlsafe(32)

        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if not row:
                raise StateStoreError(f"TASK_NOT_FOUND:{task_id}")
            self._check_version(row, expected_version)
            if row["status"] != "ready":
                raise InvalidTransition(f"TASK_NOT_READY:{task_id}:{row['status']}")
            self._check_dependencies(connection, task_id)
            connection.execute(
                """
                UPDATE tasks SET status='claimed', assigned_agent_id=?, lease_owner=?,
                    lease_token=?, lease_expires_at=?, attempt=attempt+1,
                    version=version+1, updated_at=?
                WHERE task_id=?
                """,
                (agent_id, worker_id, token, expires, _iso(now), task_id),
            )
            self._append_event(
                connection, row["workflow_id"], task_id, "task.claimed", actor,
                details={"agent_id": agent_id, "worker_id": worker_id, "lease_expires_at": expires},
            )
            self._refresh_workflow_status(connection, row["workflow_id"], actor)

        claimed = self.get_task(task_id)
        claimed["lease_token"] = token
        return claimed

    @staticmethod
    def _verify_lease(row: sqlite3.Row, lease_token: str, now: datetime | None = None) -> None:
        stored = row["lease_token"] or ""
        if not lease_token or not hmac.compare_digest(stored, lease_token):
            raise LeaseExpired(f"LEASE_TOKEN_INVALID:{row['task_id']}")
        expires = datetime.fromisoformat(row["lease_expires_at"])
        if expires <= (now or _now()):
            raise LeaseExpired(f"LEASE_EXPIRED:{row['task_id']}")

    def start_task(self, task_id: str, lease_token: str, actor: str = "worker") -> dict[str, Any]:
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if not row:
                raise StateStoreError(f"TASK_NOT_FOUND:{task_id}")
            if row["status"] != "claimed":
                raise InvalidTransition(f"TASK_NOT_CLAIMED:{task_id}:{row['status']}")
            self._verify_lease(row, lease_token)
            now = _iso(_now())
            connection.execute(
                "UPDATE tasks SET status='running', version=version+1, updated_at=? WHERE task_id=?",
                (now, task_id),
            )
            self._append_event(connection, row["workflow_id"], task_id, "task.started", actor)
            self._refresh_workflow_status(connection, row["workflow_id"], actor)
        return self.get_task(task_id)

    def finish_task(
        self,
        task_id: str,
        lease_token: str,
        outcome: str,
        actor: str = "worker",
        reason_code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        allowed = {"succeeded", "failed", "blocked", "needs_reconciliation"}
        if outcome not in allowed:
            raise ValueError(f"INVALID_TASK_OUTCOME:{outcome}")
        if outcome in {"failed", "blocked", "needs_reconciliation"} and not reason_code:
            raise ValueError("REASON_CODE_REQUIRED")

        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if not row:
                raise StateStoreError(f"TASK_NOT_FOUND:{task_id}")
            if row["status"] != "running":
                raise InvalidTransition(f"TASK_NOT_RUNNING:{task_id}:{row['status']}")
            self._verify_lease(row, lease_token)
            now = _iso(_now())
            connection.execute(
                """
                UPDATE tasks SET status=?, lease_owner=NULL, lease_token=NULL,
                    lease_expires_at=NULL, last_error_code=?, version=version+1, updated_at=?
                WHERE task_id=?
                """,
                (outcome, reason_code, now, task_id),
            )
            event_type = {
                "succeeded": "task.succeeded",
                "failed": "task.failed",
                "blocked": "task.blocked",
                "needs_reconciliation": "task.needs_reconciliation",
            }[outcome]
            self._append_event(
                connection, row["workflow_id"], task_id, event_type, actor,
                reason_code=reason_code, details=details or {},
            )
            if outcome == "succeeded":
                self._release_ready_dependents(connection, row["workflow_id"], actor)
            self._refresh_workflow_status(connection, row["workflow_id"], actor)
        return self.get_task(task_id)

    def release_blocked_task(
        self,
        task_id: str,
        actor: str,
        evidence_ref: str,
        reason_code: str = "OPERATOR_UNBLOCKED",
    ) -> dict[str, Any]:
        if not evidence_ref.strip():
            raise ValueError("EVIDENCE_REFERENCE_REQUIRED")
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if not row:
                raise StateStoreError(f"TASK_NOT_FOUND:{task_id}")
            if row["status"] != "blocked":
                raise InvalidTransition(f"TASK_NOT_BLOCKED:{task_id}:{row['status']}")
            self._check_dependencies(connection, task_id)
            connection.execute(
                """
                UPDATE tasks SET status='ready', last_error_code=NULL,
                    version=version+1, updated_at=? WHERE task_id=?
                """,
                (_iso(_now()), task_id),
            )
            self._append_event(
                connection, row["workflow_id"], task_id, "task.unblocked", actor,
                reason_code=reason_code, details={"evidence_ref": evidence_ref},
            )
            self._refresh_workflow_status(connection, row["workflow_id"], actor)
        return self.get_task(task_id)

    def resolve_reconciliation(
        self,
        task_id: str,
        resolution: str,
        actor: str,
        evidence_ref: str,
    ) -> dict[str, Any]:
        if resolution not in {"succeeded", "failed", "ready"}:
            raise ValueError(f"INVALID_RECONCILIATION_RESOLUTION:{resolution}")
        if not evidence_ref.strip():
            raise ValueError("EVIDENCE_REFERENCE_REQUIRED")
        with self._transaction() as connection:
            row = connection.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()
            if not row:
                raise StateStoreError(f"TASK_NOT_FOUND:{task_id}")
            if row["status"] != "needs_reconciliation":
                raise InvalidTransition(f"TASK_NOT_NEEDS_RECONCILIATION:{task_id}:{row['status']}")
            if resolution == "ready":
                self._check_dependencies(connection, task_id)
            now = _iso(_now())
            connection.execute(
                """
                UPDATE tasks SET status=?, lease_owner=NULL, lease_token=NULL,
                    lease_expires_at=NULL, last_error_code=NULL,
                    version=version+1, updated_at=? WHERE task_id=?
                """,
                (resolution, now, task_id),
            )
            self._append_event(
                connection, row["workflow_id"], task_id, "task.reconciliation_resolved", actor,
                reason_code=f"RESOLVED_AS_{resolution.upper()}",
                details={"resolution": resolution, "evidence_ref": evidence_ref},
            )
            if resolution == "succeeded":
                self._release_ready_dependents(connection, row["workflow_id"], actor)
            self._refresh_workflow_status(connection, row["workflow_id"], actor)
        return self.get_task(task_id)

    def _release_ready_dependents(
        self, connection: sqlite3.Connection, workflow_id: str, actor: str
    ) -> None:
        rows = connection.execute(
            """
            SELECT t.* FROM tasks t
            WHERE t.workflow_id=? AND t.status='pending'
              AND NOT EXISTS (
                SELECT 1 FROM dependencies d
                JOIN tasks parent ON parent.task_id=d.depends_on_task_id
                WHERE d.task_id=t.task_id AND parent.status <> 'succeeded'
              )
            ORDER BY t.task_id
            """,
            (workflow_id,),
        ).fetchall()
        for row in rows:
            connection.execute(
                "UPDATE tasks SET status='ready', version=version+1, updated_at=? WHERE task_id=?",
                (_iso(_now()), row["task_id"]),
            )
            self._append_event(connection, workflow_id, row["task_id"], "task.ready", actor)

    @staticmethod
    def _aggregate_workflow_status(statuses: list[str]) -> str:
        if any(status == "needs_reconciliation" for status in statuses):
            return "needs_reconciliation"
        if any(status in {"ready", "claimed", "running"} for status in statuses):
            return "running"
        if any(status == "blocked" for status in statuses):
            return "blocked"
        if any(status == "failed" for status in statuses):
            return "failed"
        if statuses and all(status == "succeeded" for status in statuses):
            return "completed"
        if statuses and all(status == "cancelled" for status in statuses):
            return "cancelled"
        return "queued"

    def _refresh_workflow_status(
        self, connection: sqlite3.Connection, workflow_id: str, actor: str
    ) -> None:
        statuses = [
            row["status"] for row in connection.execute(
                "SELECT status FROM tasks WHERE workflow_id=?", (workflow_id,)
            ).fetchall()
        ]
        new_status = self._aggregate_workflow_status(statuses)
        current = connection.execute(
            "SELECT status FROM workflows WHERE workflow_id=?", (workflow_id,)
        ).fetchone()
        if current and current["status"] != new_status:
            old_status = current["status"]
            connection.execute(
                "UPDATE workflows SET status=?, version=version+1, updated_at=? WHERE workflow_id=?",
                (new_status, _iso(_now()), workflow_id),
            )
            self._append_event(
                connection, workflow_id, None, "workflow.status_changed", actor,
                details={"from": old_status, "to": new_status},
            )

    def reconcile_expired_leases(
        self, now: datetime | None = None, actor: str = "reconciler"
    ) -> list[str]:
        cutoff = _iso(now or _now())
        affected: list[str] = []
        with self._transaction() as connection:
            rows = connection.execute(
                """
                SELECT * FROM tasks
                WHERE status IN ('claimed', 'running')
                  AND lease_expires_at IS NOT NULL AND lease_expires_at <= ?
                ORDER BY workflow_id, task_id
                """,
                (cutoff,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    """
                    UPDATE tasks SET status='needs_reconciliation',
                        lease_owner=NULL, lease_token=NULL, lease_expires_at=NULL,
                        last_error_code='LEASE_EXPIRED', version=version+1, updated_at=?
                    WHERE task_id=?
                    """,
                    (cutoff, row["task_id"]),
                )
                self._append_event(
                    connection, row["workflow_id"], row["task_id"],
                    "task.needs_reconciliation", actor, reason_code="LEASE_EXPIRED",
                    details={"previous_status": row["status"], "worker_id": row["lease_owner"]},
                )
                affected.append(row["task_id"])
            for workflow_id in sorted({row["workflow_id"] for row in rows}):
                self._refresh_workflow_status(connection, workflow_id, actor)
        return affected

    def get_events(self, workflow_id: str | None = None) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            if workflow_id is None:
                rows = connection.execute("SELECT * FROM events ORDER BY sequence").fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM events WHERE workflow_id=? ORDER BY sequence", (workflow_id,)
                ).fetchall()
            result = []
            for row in rows:
                item = dict(row)
                item["details"] = json.loads(item.pop("details_json"))
                result.append(item)
            return result
        finally:
            connection.close()

    def verify_event_chain(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            rows = connection.execute("SELECT * FROM events ORDER BY sequence").fetchall()
            previous_hash = None
            for row in rows:
                record = {
                    "sequence": row["sequence"],
                    "event_id": row["event_id"],
                    "workflow_id": row["workflow_id"],
                    "task_id": row["task_id"],
                    "event_type": row["event_type"],
                    "actor": row["actor"],
                    "occurred_at": row["occurred_at"],
                    "reason_code": row["reason_code"],
                    "details": json.loads(row["details_json"]),
                    "previous_event_sha256": previous_hash,
                }
                expected = hashlib.sha256(_canonical(record).encode("utf-8")).hexdigest()
                if row["previous_event_sha256"] != previous_hash or row["event_sha256"] != expected:
                    return {
                        "valid": False,
                        "events_checked": row["sequence"],
                        "failed_sequence": row["sequence"],
                    }
                previous_hash = row["event_sha256"]
            return {"valid": True, "events_checked": len(rows), "failed_sequence": None}
        finally:
            connection.close()

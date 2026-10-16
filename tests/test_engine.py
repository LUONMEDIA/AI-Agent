import base64
import dataclasses
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timedelta, timezone
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from cms_agent.approval import canonical_bytes
from unittest.mock import patch

from cms_agent.engine import Hold, load_batch, dry_run, execute_item, RemoteRecord
from cms_agent.mock import MockCMS


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "source").mkdir()
        self.pdf = self.root / "source" / "fictional.pdf"
        self.pdf.write_bytes(b"%PDF-1.4\n%synthetic test fixture\n%%EOF")
        self.digest = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        self.manifest = self.root / "batch.json"
        self.template = {
            "schema_version": 1, "case_id": "TEST-ONLY",
            "source_root": "source",
            "items": [{"item_id": "synthetic-1", "product_class": "CSDL",
                       "document_number": "TEST-001", "title": "Fictional test",
                       "target_module": "TEST_MODULE",
                       "source_file": "fictional.pdf", "source_sha256": self.digest,
                       "fields": {"category": "mock"}, "action": "PUBLISH_STEP1"}]
        }
        self.write()
        self.signing_key = Ed25519PrivateKey.generate()  # TEST ONLY, ephemeral
        pub = self.signing_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        self.trusted_pub = base64.b64encode(pub).decode()
        now = datetime.now(timezone.utc)
        self.permit = {
            "schema_version": 2, "purpose": "CMS_STEP1",
            "case_id": "TEST-ONLY", "approval_id": "ACK-TEST-ONLY",
            "operator_id": "SYNTHETIC_OPERATOR",
            "issued_at": (now - timedelta(minutes=1)).isoformat(),
            "expires_at": (now + timedelta(hours=1)).isoformat(),
            "item_actions": {"synthetic-1": "PUBLISH_STEP1"},
            "item_fingerprints": {},
            "allowed_modules": ["TEST_MODULE"], "explicit_news_publish_item_ids": [],
            "target_origin": "https://synthetic.invalid"
        }
        self.live = patch.dict(os.environ, {
            "CMS_AGENT_ENABLE_LIVE": "YES",
            "CMS_AGENT_APPROVAL_ID": "ACK-TEST-ONLY",
            "CMS_AGENT_APPROVAL_PUBLIC_KEY_B64": self.trusted_pub,
        })

    def seal(self, permit, batch=None):
        item = (batch or self.load()).items[0]
        to_sign = dict(permit)
        to_sign["item_fingerprints"] = dict(to_sign.get("item_fingerprints", {}))
        if not to_sign["item_fingerprints"]:
            to_sign["item_fingerprints"][item.item_id] = item.fingerprint
        to_sign.pop("signature", None)
        to_sign["signature"] = base64.b64encode(
            self.signing_key.sign(canonical_bytes(to_sign))
        ).decode()
        return to_sign

    def write(self):
        self.manifest.write_text(json.dumps(self.template), encoding="utf-8")

    def load(self):
        return load_batch(self.manifest)

    def run_item(self, cms=None, permit=None):
        batch = self.load()
        with self.live:
            return execute_item(batch, batch.items[0], self.seal(permit or self.permit, batch), cms or MockCMS())

    def test_dry_run_only_plans(self):
        plan = dry_run(self.load())
        self.assertEqual(plan["mode"], "DRY_RUN_NO_NETWORK_NO_MUTATION")
        self.assertEqual(len(plan["items"]), 1)

    def test_digest_mismatch_blocks(self):
        self.template["items"][0]["source_sha256"] = "a" * 64
        self.write()
        with self.assertRaisesRegex(Hold, "SHA256_MISMATCH"):
            self.load()

    def test_source_traversal_blocks(self):
        self.template["items"][0]["source_file"] = "../fictional.pdf"
        self.write()
        with self.assertRaisesRegex(Hold, "SOURCE_PATH_NOT_RELATIVE"):
            self.load()

    def test_duplicate_batch_id_blocks(self):
        self.template["items"].append(dict(self.template["items"][0]))
        self.write()
        with self.assertRaisesRegex(Hold, "DUPLICATE_ITEM_ID"):
            self.load()

    def test_document_step1_and_replay_are_idempotent(self):
        cms = MockCMS()
        first = self.run_item(cms)
        second = self.run_item(cms)
        self.assertEqual(first["status"], "PUBLISHED_STEP1_VERIFIED")
        self.assertEqual(second["status"], "ALREADY_PUBLISHED")
        self.assertEqual(cms.created, 1)
        self.assertEqual(cms.published, 1)

    def test_news_brief_defaults_to_draft(self):
        row = self.template["items"][0]
        row.update(product_class="TIN_VAN", action="SAVE_DRAFT")
        self.write()
        permit = dict(self.permit, item_actions={"synthetic-1": "SAVE_DRAFT"})
        cms = MockCMS()
        result = self.run_item(cms, permit)
        self.assertEqual(result["status"], "DRAFT_VERIFIED")
        self.assertEqual(cms.published, 0)

    def test_news_publish_without_extra_approval_blocks(self):
        self.template["items"][0]["product_class"] = "TIN_VAN"
        self.write()
        cms = MockCMS()
        result = self.run_item(cms)
        self.assertEqual(result["status"], "HOLD")
        self.assertEqual(result["reason"], "NEWS_PUBLISH_NEEDS_SEPARATE_EXPLICIT_APPROVAL")
        self.assertEqual(cms.created, 0)

    def test_news_exact_approval_can_publish(self):
        self.template["items"][0]["product_class"] = "TIN_VAN"
        self.write()
        permit = dict(self.permit, explicit_news_publish_item_ids=["synthetic-1"])
        self.assertEqual(self.run_item(MockCMS(), permit)["status"], "PUBLISHED_STEP1_VERIFIED")

    def test_wrong_case_blocks_before_cms(self):
        cms = MockCMS()
        self.assertEqual(self.run_item(cms, dict(self.permit, case_id="OTHER"))["status"], "HOLD")
        self.assertEqual(cms.created, 0)

    def test_live_requires_exact_ack(self):
        batch = self.load()
        cms = MockCMS()
        with patch.dict(os.environ, {"CMS_AGENT_ENABLE_LIVE": "NO"}):
            result = execute_item(batch, batch.items[0], self.permit, cms)
        self.assertEqual(result["status"], "HOLD")
        self.assertEqual(cms.created, 0)

    def test_existing_record_mismatch_blocks_without_changes(self):
        cms = MockCMS()
        cms.records["fake-1"] = RemoteRecord(
            "fake-1", "TEST-001", "TEST_MODULE", "WRONG", self.digest, "DRAFT")
        result = self.run_item(cms)
        self.assertEqual(result["status"], "HOLD")
        self.assertEqual(cms.published, 0)

    def test_remote_ambiguous_duplicate_blocks(self):
        cms = MockCMS()
        batch = self.load()
        for record_id in ("r1", "r2"):
            cms.records[record_id] = RemoteRecord(
                record_id, "TEST-001", "TEST_MODULE", batch.items[0].fingerprint,
                self.digest, "DRAFT")
        self.assertEqual(self.run_item(cms)["reason"], "MULTIPLE_REMOTE_MATCHES")

    def test_create_unknown_requires_reconciliation(self):
        class UncertainCMS(MockCMS):
            def create_draft(self, item):
                super().create_draft(item)
                raise RuntimeError("server timeout after save")
        cms = UncertainCMS()
        result = self.run_item(cms)
        self.assertEqual(result["status"], "NEEDS_RECONCILE")
        self.assertEqual(cms.created, 1)

    def test_readback_file_mismatch_holds(self):
        class WrongFileCMS(MockCMS):
            def readback(self, record_id):
                r = super().readback(record_id)
                return RemoteRecord(r.record_id, r.number, r.module, r.fingerprint, "x" * 64, r.status, r.title, r.fields)
        self.assertEqual(self.run_item(WrongFileCMS())["reason"],
                         "REMOTE_CONTENT_OR_ATTACHMENT_MISMATCH")

    def test_expired_approval_holds(self):
        permit = dict(self.permit, expires_at="2020-01-01T00:00:00Z")
        self.assertEqual(self.run_item(MockCMS(), permit)["reason"], "APPROVAL_EXPIRED_NOT_YET_VALID_OR_TOO_LONG")

    def test_signature_tamper_blocks(self):
        batch = self.load()
        signed = self.seal(self.permit, batch)
        signed["item_actions"]["synthetic-1"] = "SAVE_DRAFT"
        cms = MockCMS()
        with self.live:
            result = execute_item(batch, batch.items[0], signed, cms)
        self.assertEqual(result["reason"], "APPROVAL_SIGNATURE_INVALID")
        self.assertEqual(cms.created, 0)

    def test_signed_content_fingerprint_drift_blocks(self):
        batch = self.load()
        old_permit = self.seal(self.permit, batch)
        self.template["items"][0]["fields"]["category"] = "updated"
        self.write()
        new_batch = self.load()
        cms = MockCMS()
        with self.live:
            result = execute_item(new_batch, new_batch.items[0], old_permit, cms)
        self.assertEqual(result["reason"], "ITEM_FINGERPRINT_NOT_APPROVED")
        self.assertEqual(cms.created, 0)

    def test_unsigned_approval_holds(self):
        batch = self.load()
        cms = MockCMS()
        permit = self.seal(self.permit)
        permit["signature"] = ""
        with self.live:
            result = execute_item(batch, batch.items[0], permit, cms)
        self.assertEqual(result["reason"], "APPROVAL_SIGNATURE_INVALID")
        self.assertEqual(cms.created, 0)

    def test_public_key_missing_holds(self):
        batch = self.load()
        permit = self.seal(self.permit)
        cms = MockCMS()
        with patch.dict(os.environ, {
            "CMS_AGENT_ENABLE_LIVE": "YES",
            "CMS_AGENT_APPROVAL_ID": "ACK-TEST-ONLY",
            "CMS_AGENT_APPROVAL_PUBLIC_KEY_B64": ""
        }):
            result = execute_item(batch, batch.items[0], permit, cms)
        self.assertEqual(result["reason"], "TRUSTED_APPROVAL_PUBLIC_KEY_MISSING")
        self.assertEqual(cms.created, 0)

    def test_future_or_overlong_approval_is_rejected(self):
        now = datetime.now(timezone.utc)
        permit = dict(self.permit, issued_at=(now + timedelta(minutes=5)).isoformat())
        self.assertEqual(self.run_item(MockCMS(), permit)["status"], "HOLD")
        permit = dict(self.permit, expires_at=(now + timedelta(days=2)).isoformat())
        self.assertEqual(self.run_item(MockCMS(), permit)["status"], "HOLD")

    def test_missing_action_or_module_is_fail_closed(self):
        cms = MockCMS()
        permit = dict(self.permit, item_actions={})
        self.assertEqual(self.run_item(cms, permit)["reason"], "APPROVAL_INVALID_ITEM_ACTIONS")
        permit = dict(self.permit, allowed_modules=["OTHER_MODULE"])
        self.assertEqual(self.run_item(cms, permit)["reason"], "MODULE_NOT_APPROVED")
        self.assertEqual(cms.created, 0)

    def test_title_drift_blocks(self):
        before = self.seal(self.permit)
        self.template["items"][0]["title"] = "Modified after operator approval"
        self.write()
        batch = self.load()
        cms = MockCMS()
        with self.live:
            result = execute_item(batch, batch.items[0], before, cms)
        self.assertEqual(result["reason"], "ITEM_FINGERPRINT_NOT_APPROVED")
        self.assertEqual(cms.created, 0)

    def test_unapproved_portal_origin_blocks(self):
        class OtherPortal(MockCMS):
            origin = "https://other.invalid"
        cms = OtherPortal()
        self.assertEqual(self.run_item(cms)["reason"], "CMS_ORIGIN_NOT_APPROVED")
        self.assertEqual(cms.created, 0)

    def test_signed_origin_tamper_blocks(self):
        permit = self.seal(self.permit)
        permit["target_origin"] = "https://other.invalid"
        batch = self.load()
        cms = MockCMS()
        with self.live:
            result = execute_item(batch, batch.items[0], permit, cms)
        self.assertEqual(result["reason"], "APPROVAL_SIGNATURE_INVALID")
        self.assertEqual(cms.created, 0)

    def test_batch_stops_on_uncertain_mutation(self):
        from cms_agent.runner import run_batch
        from cms_agent.engine import Batch
        batch = self.load()
        duplicate = dataclasses.replace(batch.items[0], item_id="synthetic-2")
        many = Batch(batch.case_id, (batch.items[0], duplicate))
        class TimeoutCMS(MockCMS):
            def create_draft(self, item):
                super().create_draft(item)
                raise RuntimeError("ambiguous save timeout")
        cms = TimeoutCMS()
        audit = []
        with self.live:
            signed = self.seal(self.permit, batch)
            results = run_batch(many, signed, cms, lambda *args: audit.append(args))
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "NEEDS_RECONCILE")
        self.assertEqual([r[2] for r in audit], ["BEFORE_REMOTE", "AFTER_REMOTE"])
        self.assertEqual(cms.created, 1)

    def test_private_audit_writes_two_events_with_no_raw_case(self):
        from cms_agent.runner import PrivateAudit
        secure = self.root / "secure"
        secure.mkdir(mode=0o700)
        sink = PrivateAudit(secure / "events.jsonl")
        sink("TEST-ONLY", "synthetic-1", "BEFORE_REMOTE", "START", "")
        sink("TEST-ONLY", "synthetic-1", "AFTER_REMOTE", "DRAFT_VERIFIED", "")
        raw = (secure / "events.jsonl").read_text()
        self.assertNotIn("TEST-ONLY", raw)
        self.assertEqual(len(raw.splitlines()), 2)

    def test_private_audit_blocks_world_readable_dir(self):
        from cms_agent.runner import PrivateAudit
        from cms_agent.engine import Hold
        weak = self.root / "weak"
        weak.mkdir()
        weak.chmod(0o755)
        with self.assertRaisesRegex(Hold, "PERMISSIONS_TOO_OPEN"):
            PrivateAudit(weak / "audit.jsonl")

    def test_cli_rejects_mock_live_adapter(self):
        from cms_agent.cli import main
        from contextlib import redirect_stdout
        from io import StringIO
        output = StringIO()
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "", "CI": "false"}):
            with redirect_stdout(output):
                status = main([str(self.manifest), "--live", "--adapter",
                               "cms_agent.mock.MockCMS", "--approval-file",
                               str(self.manifest)])
        self.assertEqual(status, 2)
        self.assertIn("UNREVIEWED_LIVE_ADAPTER_BLOCKED", output.getvalue())

    def test_cli_always_blocks_live_inside_ci(self):
        from cms_agent.cli import main
        from contextlib import redirect_stdout
        from io import StringIO
        out = StringIO()
        with patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}):
            with redirect_stdout(out):
                code = main([str(self.manifest), "--live", "--adapter",
                             "cms_agent.browser.BrowserCMS", "--approval-file",
                             str(self.manifest)])
        self.assertEqual(code, 2)
        self.assertIn("LIVE_FORBIDDEN_IN_CI", out.getvalue())

    def test_audit_symlink_destination_blocks(self):
        from cms_agent.runner import PrivateAudit
        from cms_agent.engine import Hold
        secure = self.root / "private-audit"
        secure.mkdir(mode=0o700)
        target = secure / "actual.jsonl"
        target.write_text("")
        target.chmod(0o600)
        link = secure / "link.jsonl"
        link.symlink_to(target)
        with self.assertRaisesRegex(Hold, "AUDIT_SYMLINK_FORBIDDEN"):
            PrivateAudit(link)

    def test_duplicate_number_same_module_blocks(self):
        row = dict(self.template["items"][0])
        row["item_id"] = "synthetic-2"
        self.template["items"].append(row)
        self.write()
        with self.assertRaisesRegex(Hold, "DUPLICATE_DOCUMENT_NUMBER_IN_MODULE"):
            self.load()

    def test_source_changes_after_approval_block(self):
        batch = self.load()
        permit = self.seal(self.permit, batch)
        self.pdf.write_bytes(self.pdf.read_bytes() + b"\nchanged after load")
        cms = MockCMS()
        with self.live:
            result = execute_item(batch, batch.items[0], permit, cms)
        self.assertEqual(result["reason"], "SOURCE_CHANGED_AFTER_APPROVAL")
        self.assertEqual(cms.created, 0)

    def test_readback_title_diff_blocks_after_create(self):
        class WrongTitle(MockCMS):
            def readback(self, record_id):
                r = super().readback(record_id)
                return dataclasses.replace(r, title="Wrong displayed title")
        result = self.run_item(WrongTitle())
        self.assertEqual(result["status"], "NEEDS_RECONCILE")
        self.assertEqual(result["reason"], "POST_CREATE_READBACK_UNVERIFIED")

    def test_readback_metadata_diff_blocks_after_create(self):
        class WrongMeta(MockCMS):
            def readback(self, record_id):
                r = super().readback(record_id)
                return dataclasses.replace(r, fields={"category": "WRONG"})
        result = self.run_item(WrongMeta())
        self.assertEqual(result["status"], "NEEDS_RECONCILE")
        self.assertEqual(result["reason"], "POST_CREATE_READBACK_UNVERIFIED")

    def test_readback_after_publish_fails_reconcile(self):
        class LostResult(MockCMS):
            def readback(self, record_id):
                r = super().readback(record_id)
                if r.status == "PUBLISHED":
                    raise OSError("unknown after approval")
                return r
        cms = LostResult()
        result = self.run_item(cms)
        self.assertEqual(result["status"], "NEEDS_RECONCILE")
        self.assertEqual(result["reason"], "POST_PUBLISH_READBACK_UNVERIFIED")
        self.assertEqual(cms.published, 1)

    def test_batch_stops_on_any_hold_including_item_specific(self):
        from cms_agent.runner import run_batch
        from cms_agent.engine import Batch
        batch = self.load()
        other = dataclasses.replace(
            batch.items[0], item_id="synthetic-2", document_number="TEST-002")
        many = Batch(batch.case_id, (batch.items[0], other))
        class WrongRecord(MockCMS):
            def find(self, item):
                return [RemoteRecord("bad", item.document_number, item.target_module,
                                     "incorrect", item.source_sha256, "DRAFT", item.title, item.fields)]
        cms = WrongRecord()
        events = []
        with self.live:
            result = run_batch(many, self.seal(self.permit, batch), cms,
                               lambda *args: events.append(args))
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "HOLD")
        self.assertEqual([x[2] for x in events], ["BEFORE_REMOTE", "AFTER_REMOTE"])

    def test_corrupted_draft_before_publish_reconciles(self):
        class MutatedDraft(MockCMS):
            def __init__(self):
                super().__init__()
                self.read_count = 0
            def readback(self, record_id):
                self.read_count += 1
                r = super().readback(record_id)
                if self.read_count >= 2:
                    return dataclasses.replace(r, title="Changed by another actor")
                return r
        cms = MutatedDraft()
        result = self.run_item(cms)
        self.assertEqual(result["status"], "NEEDS_RECONCILE")
        self.assertEqual(result["reason"], "PRE_PUBLISH_READBACK_UNVERIFIED")
        self.assertEqual(cms.published, 0)


if __name__ == "__main__":
    unittest.main()

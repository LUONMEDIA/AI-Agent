"""Block accidental confidential material in this PUBLIC repository.

Pure-stdlib CI guard, not a substitute for GitHub secret scanning, history review,
private production repos, or operator access controls.
"""
from __future__ import annotations

from pathlib import Path
import re
import subprocess
import sys


BANNED_SUFFIXES = frozenset({".pdf", ".docx", ".p12", ".pem", ".key", ".sqlite3", ".db"})
BANNED_PATH_PARTS = frozenset({"private", ".auth", "runtime", "secrets"})
CHECKOUT_ROOT = Path(__file__).resolve().parents[1]

# Construct phrases in fragments so the scanner doesn't trip on its own source.
SENSITIVE_RE = [
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE" + r" KEY-----"),
    re.compile(r"\bghp_" + r"[A-Za-z0-9]{25,}\b"),
    re.compile(r"\bgithub_pat_" + r"[A-Za-z0-9_]{30,}\b"),
    re.compile(r"(?i)\bauthorization\s*:\s*bearer\s+" + r"[A-Za-z0-9._-]{24,}"),
    re.compile(r"(?i)\b(?:sessionid|session_cookie|session_token)\s*[:=]\s*" + r"['\"][^'\"]{20,}['\"]"),
]


def problem(path: str, content: bytes) -> str | None:
    file = Path(path)
    if file.suffix.lower() in BANNED_SUFFIXES:
        return "BANNED_FILE_TYPE"
    if any(part.lower() in BANNED_PATH_PARTS for part in file.parts):
        return "PRIVATE_RUNTIME_PATH"
    if file.name.startswith(".env") and file.name != ".env.example":
        return "UNTRACKED_ENV_FILE"
    if b"\0" in content:
        return "UNEXPECTED_BINARY_FILE"
    try:
        data = content.decode("utf-8")
    except UnicodeDecodeError:
        return "NON_UTF8_CONTENT"
    for signature in SENSITIVE_RE:
        if signature.search(data):
            return "POSSIBLE_SECRET_PATTERN"
    return None


def scan_tracked(root: Path = CHECKOUT_ROOT) -> list[tuple[str, str]]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        check=True, capture_output=True,
    )
    paths = (entry.decode("utf-8") for entry in result.stdout.split(b"\0") if entry)
    failures = []
    for path in paths:
        full = root / path
        if not full.is_file() or full.is_symlink():
            failures.append((path, "MISSING_OR_SYMLINK"))
            continue
        issue = problem(path, full.read_bytes())
        if issue:
            failures.append((path, issue))
    return failures


def main() -> int:
    issues = scan_tracked()
    if issues:
        for path, why in issues:
            print(f"BLOCKED: {path}: {why}")
        return 2
    print("PUBLIC_REPOSITORY_CONTENT_SCAN_PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())

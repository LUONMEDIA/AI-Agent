import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
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
        self.permit = {
            "case_id": "TEST-ONLY", "approval_id": "ACK-TEST-ONLY",
            "expires_at": "2099-01-01T00:00:00+00:00",
            "item_actions": {"synthetic-1": "PUBLISH_STEP1"},
            "allowed_modules": ["TEST_MODULE"], "explicit_news_publish_item_ids": []
        }
        self.live = patch.dict(os.environ, {
            "CMS_AGENT_ENABLE_LIVE": "YES",
            "CMS_AGENT_APPROVAL_ID": "ACK-TEST-ONLY"
        })

    def write(self):
        self.manifest.write_text(json.dumps(self.template), encoding="utf-8")

    def load(self):
        return load_batch(self.manifest)

    def run_item(self, cms=None, permit=None):
        batch = self.load()
        with self.live:
            return execute_item(batch, batch.items[0], permit or self.permit, cms or MockCMS())

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
                return RemoteRecord(r.record_id, r.number, r.module, r.fingerprint, "x" * 64, r.status)
        self.assertEqual(self.run_item(WrongFileCMS())["reason"],
                         "REMOTE_CONTENT_OR_ATTACHMENT_MISMATCH")

    def test_expired_approval_holds(self):
        permit = dict(self.permit, expires_at="2020-01-01T00:00:00Z")
        self.assertEqual(self.run_item(MockCMS(), permit)["reason"], "APPROVAL_EXPIRED")


if __name__ == "__main__":
    unittest.main()

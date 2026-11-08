"""Versioned metadata must survive reopen and reject silent rewrites."""

import json
import tempfile
import unittest
from pathlib import Path

from rag_evaluation.models import ExposureEvent
from rag_evaluation.registry import Registry, RegistryError


def exposure(event_id="seen-one", **changes):
    return ExposureEvent(**({
        "id": event_id, "revision": 1, "kind": "exposure",
        "actor_id": "developer-one", "actor_role": "developer",
        "occurred_at": "2026-11-05T14:00:00+08:00", "access": "question",
        "asset_sha256": ["a" * 64],
    } | changes))


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "registry"
        self.registry = Registry.initialize(self.root)

    def test_reopen_preserves_records_order_and_external_anchor(self):
        first = self.registry.append(exposure())
        second = self.registry.append(exposure("seen-two"))
        reopened = Registry(self.root)
        entries = reopened.entries(expected_head=second["sha256"])
        self.assertEqual([row["document"]["id"] for row in entries], ["seen-one", "seen-two"])
        self.assertEqual(entries[1]["previous_sha256"], first["sha256"])
        with self.assertRaisesRegex(RegistryError, "anchor_mismatch"):
            reopened.entries(expected_head=first["sha256"])

    def test_duplicate_event_and_exposure_revision_cannot_erase_history(self):
        self.registry.append(exposure())
        with self.assertRaisesRegex(RegistryError, "revision_conflict"):
            self.registry.append(exposure())
        with self.assertRaisesRegex(RegistryError, "exposure_is_append_only"):
            self.registry.append(exposure(revision=2, asset_sha256=["b" * 64]))
        self.assertEqual(len(self.registry.entries()), 1)

    def test_payload_tampering_is_detected_before_any_new_append(self):
        self.registry.append(exposure())
        path = self.root / "records" / "00000001.json"
        row = json.loads(path.read_bytes())
        row["document"]["actor_role"] = "custodian"
        path.write_text(json.dumps(row), encoding="utf-8")
        with self.assertRaisesRegex(RegistryError, "chain_invalid"):
            self.registry.append(exposure("seen-two"))
        self.assertFalse((self.root / "records" / "00000002.json").exists())

    def test_partial_record_and_missing_sequence_are_not_silently_ignored(self):
        self.registry.append(exposure())
        (self.root / "records" / "00000003.json").write_bytes(b'{"incomplete":')
        with self.assertRaisesRegex(RegistryError, "chain_invalid"):
            self.registry.entries()

    def test_existing_registry_and_write_lock_fail_without_overwriting(self):
        with self.assertRaisesRegex(RegistryError, "registry_exists"):
            Registry.initialize(self.root)
        lock = self.root / ".writer.lock"
        lock.write_text("synthetic other writer", encoding="utf-8")
        with self.assertRaisesRegex(RegistryError, "registry_busy"):
            self.registry.append(exposure())
        self.assertEqual(lock.read_text(encoding="utf-8"), "synthetic other writer")

    def test_uninitialized_directory_and_git_or_production_storage_are_rejected(self):
        with self.assertRaisesRegex(RegistryError, "registry_uninitialized"):
            Registry(self.root / "other")
        for path in (Path.cwd() / "data" / "evaluation", Path.cwd() / ".git" / "evaluation"):
            with self.subTest(path=path), self.assertRaisesRegex(RegistryError, "unsafe_registry_root"):
                Registry.initialize(path)


if __name__ == "__main__":
    unittest.main()

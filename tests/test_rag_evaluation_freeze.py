"""Freeze tests never seal real question bodies or authorize a run."""

import tempfile
import unittest

from rag_evaluation.freeze import FreezeError, freeze_protocol, verify_freeze
from rag_evaluation.gate import record_ref
from rag_evaluation.models import ExposureEvent
from rag_evaluation.registry import RegistryError
from test_rag_evaluation_gate import AdmissionFixture


class FreezeGovernanceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rag-freeze-")
        self.addCleanup(temporary.cleanup)
        self.fixture = AdmissionFixture(temporary.name)
        self.fixture.register()

    def seal(self, **changes):
        return freeze_protocol(
            self.fixture.registry, record_ref(self.fixture.plan), self.fixture.roots,
            freeze_id="seal-one", actor_id=self.fixture.plan.people.custodian, **changes,
        )

    def verify(self, row):
        from rag_evaluation.models import parse_record
        return verify_freeze(
            self.fixture.registry, record_ref(parse_record(row["document"])),
            self.fixture.roots, expected_freeze_head=row["sha256"],
        )

    def test_seal_and_independent_anchor_reverification(self):
        row = self.seal()
        report = self.verify(row)
        self.assertEqual(report["status"], "valid_metadata_seal")
        self.assertFalse(report["run_authorized"])

    def test_blocked_or_wrong_actor_cannot_seal(self):
        with self.assertRaisesRegex(FreezeError, "custodian_required"):
            freeze_protocol(self.fixture.registry, record_ref(self.fixture.plan),
                            self.fixture.roots, freeze_id="wrong", actor_id="developer")
        self.fixture.approve(record_ref(self.fixture.plan), "budget", "owner", decision="revoked")
        with self.assertRaisesRegex(FreezeError, "admission_blocked"):
            self.seal()

    def test_seal_is_not_overwritten_or_repeated_under_a_different_id(self):
        row = self.seal()
        with self.assertRaisesRegex(FreezeError, "protocol_already_frozen"):
            self.seal()
        with self.assertRaises(RegistryError):
            from rag_evaluation.models import parse_record
            document = parse_record(row["document"])
            document.revision = 2
            self.fixture.registry.append(document)

    def test_wrong_external_anchor_is_invalid(self):
        row = self.seal()
        from rag_evaluation.models import parse_record
        report = verify_freeze(
            self.fixture.registry, record_ref(parse_record(row["document"])),
            self.fixture.roots, expected_freeze_head="0" * 64,
        )
        self.assertEqual(report["status"], "invalid")

    def test_later_approval_revocation_invalidates_admission(self):
        row = self.seal()
        self.fixture.approve(record_ref(self.fixture.plan), "quality", "owner", decision="revoked")
        self.assertEqual(self.verify(row)["status"], "invalid")

    def test_later_exposure_invalidates_admission(self):
        row = self.seal()
        self.fixture.registry.append(ExposureEvent(
            id="later-exposure", revision=1, actor_id="developer", actor_role="developer",
            occurred_at="2026-11-05T16:00:00+08:00", access="summary", case_ids=["c-one"],
        ))
        self.assertEqual(self.verify(row)["status"], "invalid")

    def test_changed_artifact_invalidates_seal(self):
        row = self.seal()
        (self.fixture.root / "receipt.txt").write_bytes(b"changed after seal")
        self.assertEqual(self.verify(row)["status"], "invalid")

    def test_registry_writer_lock_prevents_check_then_append_race(self):
        with self.fixture.registry.transaction():
            with self.assertRaisesRegex(RegistryError, "registry_busy"):
                self.seal()


if __name__ == "__main__":
    unittest.main()

"""Portable comparison seals bind readiness evidence without authorizing execution."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from rag_evaluation.artifacts import fingerprint
from rag_evaluation.comparison_freeze import (
    ComparisonFreezeError,
    freeze_comparison,
    verify_comparison_freeze,
)
from rag_evaluation.cli import main
from rag_evaluation.gate import record_ref
from rag_evaluation.models import (
    ComparisonPlan,
    ReadinessSubmission,
    SystemSnapshot,
    parse_record,
)
from rag_evaluation.registry import Registry, RegistryError


EVIDENCE_CATEGORIES = (
    "scope_and_sampling", "dataset_permission", "split_and_exposure_audit",
    "gold_rubric_and_calibration", "metrics_statistics_and_sample_size",
    "blind_review_plan", "access_and_data_flow", "system_runtime_identity",
    "run_safety_and_recovery", "serious_error_audit", "approvals_and_signoff",
)


class PortableComparisonFreezeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="comparison-freeze-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.registry = Registry.initialize(self.root / "registry")
        self.evidence_root = self.root / "evidence"
        self.evidence_root.mkdir()
        evidence_file = self.evidence_root / "readiness-receipt.txt"
        evidence_file.write_text("Synthetic readiness evidence.", encoding="utf-8")
        evidence_ref = fingerprint(self.evidence_root, evidence_file.name, root_id="evidence")

        baseline = SystemSnapshot(
            id="baseline", revision=1, system_id="team-baseline",
            identity="team/baseline", root_id="evidence",
        )
        candidate = SystemSnapshot(
            id="candidate", revision=1, system_id="team-candidate",
            identity="team/candidate", root_id="evidence",
        )
        candidate_two = SystemSnapshot(
            id="candidate-two", revision=1, system_id="team-candidate-two",
            identity="team/candidate-two", root_id="evidence",
        )
        self.registry.append(baseline)
        self.registry.append(candidate)
        self.registry.append(candidate_two)
        self.plan = ComparisonPlan(
            id="portable-plan", revision=1, mode="exploratory_screening", arms=[
                {"arm_id": "baseline-prod", "role": "baseline",
                 "snapshot": record_ref(baseline)},
                {"arm_id": "candidate-r7", "role": "candidate",
                 "snapshot": record_ref(candidate)},
                {"arm_id": "candidate-r8-reranker", "role": "candidate",
                 "snapshot": record_ref(candidate_two)},
            ],
        )
        self.registry.append(self.plan)
        self.readiness = ReadinessSubmission(
            id="portable-readiness", revision=1, comparison_plan=record_ref(self.plan),
            evidence=[{"category": category, "artifact": evidence_ref}
                      for category in EVIDENCE_CATEGORIES],
            roles=[
                {"role_id": "evaluation_owner", "person_id": "acme-eval-owner-17"},
                {"role_id": "data_custodian", "person_id": "acme-data-steward-04"},
                {"role_id": "independent_auditor", "person_id": "acme-audit-09"},
                {"role_id": "annotator", "person_id": "acme-annotator-21"},
                {"role_id": "gold_reviewer", "person_id": "acme-gold-reviewer-12"},
                {"role_id": "adjudicator", "person_id": "acme-adjudicator-06"},
            ],
            review_assignments=[
                {"unit_id": "unit-1", "arm_id": "baseline-prod",
                 "reviewer_id": "acme-reviewer-31", "actor_type": "human"},
                {"unit_id": "unit-1", "arm_id": "baseline-prod",
                 "reviewer_id": "acme-reviewer-32", "actor_type": "human"},
                {"unit_id": "unit-1", "arm_id": "candidate-r7",
                 "reviewer_id": "acme-reviewer-33", "actor_type": "human"},
                {"unit_id": "unit-1", "arm_id": "candidate-r7",
                 "reviewer_id": "acme-reviewer-34", "actor_type": "human"},
                {"unit_id": "unit-1", "arm_id": "candidate-r8-reranker",
                 "reviewer_id": "acme-reviewer-35", "actor_type": "human"},
                {"unit_id": "unit-1", "arm_id": "candidate-r8-reranker",
                 "reviewer_id": "acme-reviewer-36", "actor_type": "human"},
            ],
        )
        self.registry.append(self.readiness)
        self.reference = record_ref(self.readiness)
        self.roots = {"evidence": self.evidence_root}

    def freeze(self, **changes):
        options = {
            "freeze_id": "portable-seal", "actor_id": "acme-data-steward-04",
        }
        options.update(changes)
        return freeze_comparison(
            self.registry, self.reference, self.roots, **options,
        )

    def verify(self, row, *, expected_head=None):
        seal = parse_record(row["document"])
        return verify_comparison_freeze(
            self.registry, record_ref(seal), self.roots,
            expected_freeze_head=expected_head or row["sha256"],
        )

    def test_freezes_complete_readiness_and_verifies_external_anchor(self):
        row = self.freeze()

        report = self.verify(row)

        self.assertEqual(len(self.plan.arms), 3)
        self.assertEqual(len(self.plan.comparisons), 2)
        self.assertTrue(all(
            comparison["result_type"] == "exploratory"
            for comparison in self.plan.comparisons
        ))
        self.assertEqual(
            report["historical_readiness"]["status"],
            "complete_for_independent_review",
        )
        self.assertEqual(row["document"]["kind"], "comparison_freeze")
        self.assertEqual(report["status"], "valid_metadata_seal")
        self.assertFalse(report["run_authorized"])

    def test_only_declared_data_custodian_can_freeze(self):
        with self.assertRaisesRegex(ComparisonFreezeError, "custodian_required"):
            self.freeze(actor_id="acme-reviewer-31")

    def test_incomplete_or_changed_readiness_cannot_freeze(self):
        self.evidence_root.joinpath("readiness-receipt.txt").write_text(
            "Changed after registration.", encoding="utf-8",
        )

        with self.assertRaisesRegex(ComparisonFreezeError, "readiness_blocked"):
            self.freeze()

        self.assertFalse(any(
            row["document"]["kind"] == "comparison_freeze"
            for row in self.registry.entries()
        ))

    def test_plan_cannot_be_frozen_twice(self):
        self.freeze()

        with self.assertRaisesRegex(ComparisonFreezeError, "comparison_already_frozen"):
            self.freeze(freeze_id="second-seal")

    def test_wrong_external_anchor_is_invalid(self):
        row = self.freeze()

        report = self.verify(row, expected_head="0" * 64)

        self.assertEqual(report["status"], "invalid")
        self.assertIn("freeze_anchor_mismatch", {item["code"] for item in report["issues"]})

    def test_tampered_freeze_reference_digest_is_rejected(self):
        row = self.freeze()
        seal = parse_record(row["document"])
        tampered_reference = record_ref(seal).model_copy(update={"sha256": "0" * 64})

        report = verify_comparison_freeze(
            self.registry, tampered_reference, self.roots,
            expected_freeze_head=row["sha256"],
        )

        self.assertEqual(report["status"], "invalid")
        self.assertIn(
            "comparison_freeze_reference_digest_mismatch",
            {item["code"] for item in report["issues"]},
        )

    def test_cli_freeze_and_verify_portable_comparison(self):
        readiness_path = self.root / "readiness-ref.json"
        readiness_path.write_text(self.reference.model_dump_json(), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            freeze_code = main([
                "freeze-comparison", "--registry", str(self.registry.root),
                "--reference", str(readiness_path), "--freeze-id", "cli-seal",
                "--actor-id", "acme-data-steward-04", "--root",
                f"evidence={self.evidence_root}",
            ])
        freeze_report = json.loads(output.getvalue())
        self.assertEqual(freeze_code, 0)
        self.assertFalse(freeze_report["run_authorized"])

        seal_ref_path = self.root / "freeze-ref.json"
        seal_ref_path.write_text(
            json.dumps(freeze_report["reference"]), encoding="utf-8",
        )
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            verify_code = main([
                "verify-comparison-freeze", "--registry", str(self.registry.root),
                "--reference", str(seal_ref_path), "--expected-freeze-head",
                freeze_report["freeze_head"], "--root", f"evidence={self.evidence_root}",
            ])

        self.assertEqual(verify_code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "valid_metadata_seal")

    def test_superseded_readiness_invalidates_existing_seal(self):
        row = self.freeze()
        revised = self.readiness.model_copy(update={"revision": 2})
        self.registry.append(revised)

        self.assertEqual(self.verify(row)["status"], "invalid")

    def test_registry_lock_prevents_freeze_check_then_append_race(self):
        with self.registry.transaction():
            with self.assertRaisesRegex(RegistryError, "registry_busy"):
                self.freeze()


if __name__ == "__main__":
    unittest.main()

"""Readiness checks verify evidence structure, never evidence truth or run permission."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from rag_evaluation.artifacts import fingerprint
from rag_evaluation.cli import main
from rag_evaluation.gate import record_ref
from rag_evaluation.models import ComparisonPlan, ReadinessSubmission, SystemSnapshot
from rag_evaluation.registry import Registry


EVIDENCE_CATEGORIES = [
    "scope_and_sampling", "dataset_permission", "split_and_exposure_audit",
    "gold_rubric_and_calibration", "metrics_statistics_and_sample_size",
    "blind_review_plan", "access_and_data_flow", "system_runtime_identity",
    "run_safety_and_recovery", "serious_error_audit", "approvals_and_signoff",
]


class ReadinessReviewTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="readiness-review-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.registry = Registry.initialize(self.root / "registry")

        baseline = SystemSnapshot(
            id="baseline", revision=1, system_id="team-baseline",
            identity="team/baseline", root_id="evidence",
        )
        candidate = SystemSnapshot(
            id="candidate", revision=1, system_id="team-candidate",
            identity="team/candidate", root_id="evidence",
        )
        self.registry.append(baseline)
        self.registry.append(candidate)
        self.plan = ComparisonPlan(
            id="team-plan", revision=1, mode="confirmatory", arms=[
                {"arm_id": "reference-system", "role": "baseline",
                 "snapshot": record_ref(baseline)},
                {"arm_id": "release-42", "role": "candidate",
                 "snapshot": record_ref(candidate)},
            ],
        )
        self.registry.append(self.plan)

        self.evidence_root = self.root / "evidence"
        self.evidence_root.mkdir()
        self.evidence = []
        for category in EVIDENCE_CATEGORIES:
            path = self.evidence_root / f"{category}.txt"
            path.write_text(f"Synthetic evidence placeholder for {category}.", encoding="utf-8")
            self.evidence.append({
                "category": category,
                "artifact": fingerprint(
                    self.evidence_root, path.name, root_id="evidence",
                ).model_dump(mode="json"),
            })

    def submission(self, **updates):
        payload = {
            "schema_version": "2",
            "kind": "readiness_submission",
            "id": "team-readiness",
            "revision": 1,
            "comparison_plan": record_ref(self.plan).model_dump(mode="json"),
            "mode": "team_formal",
            "evidence": self.evidence,
            "roles": [
                {"role_id": "evaluation_owner", "person_id": "owner-1"},
                {"role_id": "data_custodian", "person_id": "custodian-1"},
                {"role_id": "independent_auditor", "person_id": "auditor-1"},
                {"role_id": "annotator", "person_id": "annotator-1"},
                {"role_id": "gold_reviewer", "person_id": "gold-reviewer-1"},
                {"role_id": "adjudicator", "person_id": "adjudicator-1"},
            ],
            "review_assignments": [
                {"unit_id": "unit-001", "arm_id": "reference-system",
                 "reviewer_id": "reviewer-1", "actor_type": "human"},
                {"unit_id": "unit-001", "arm_id": "reference-system",
                 "reviewer_id": "reviewer-2", "actor_type": "human"},
                {"unit_id": "unit-001", "arm_id": "release-42",
                 "reviewer_id": "reviewer-3", "actor_type": "human"},
                {"unit_id": "unit-001", "arm_id": "release-42",
                 "reviewer_id": "reviewer-4", "actor_type": "human"},
            ],
        }
        payload.update(updates)
        return payload

    def invoke(self, payload):
        document = self.root / "readiness.json"
        document.write_text(json.dumps(payload), encoding="utf-8")
        registered = main([
            "register", "--registry", str(self.registry.root), "--document", str(document),
        ])
        self.assertEqual(registered, 0)
        reference_path = self.root / "readiness-ref.json"
        rows = self.registry.entries()
        from rag_evaluation.models import parse_record
        readiness = parse_record(rows[-1]["document"])
        reference_path.write_text(record_ref(readiness).model_dump_json(), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main([
                "review-readiness", "--registry", str(self.registry.root),
                "--reference", str(reference_path), "--root",
                f"evidence={self.evidence_root}",
            ])
        return code, json.loads(output.getvalue())

    def test_complete_bundle_is_only_ready_for_independent_review(self):
        code, report = self.invoke(self.submission())

        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "complete_for_independent_review")
        self.assertEqual(report["formal_readiness"], "not_assessed")
        self.assertEqual(report["evidence_validity"], "not_assessed")
        self.assertFalse(report["run_authorized"])
        self.assertEqual(report["verified_evidence_count"], len(EVIDENCE_CATEGORIES))

    def test_missing_evidence_and_required_human_role_are_reported(self):
        payload = self.submission(
            evidence=self.evidence[:-1],
            roles=[role for role in self.submission()["roles"]
                   if role["role_id"] != "data_custodian"],
        )

        code, report = self.invoke(payload)

        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "blocked")
        self.assertIn("approvals_and_signoff", report["missing_evidence"])
        self.assertIn("data_custodian", report["missing_roles"])

    def test_placeholder_people_and_reviewers_do_not_satisfy_readiness_slots(self):
        payload = self.submission()
        for role in payload["roles"]:
            role["person_id"] = f"TODO-{role['role_id']}"
        for index, assignment in enumerate(payload["review_assignments"], 1):
            assignment["reviewer_id"] = f"TODO-reviewer-{index}"

        code, report = self.invoke(payload)

        self.assertEqual(code, 2)
        self.assertIn("readiness_placeholders_unreplaced", report["issues"])
        self.assertNotEqual(report["status"], "complete_for_independent_review")

    def test_same_human_cannot_review_two_arms_for_one_unit(self):
        assignments = self.submission()["review_assignments"]
        assignments[2]["reviewer_id"] = assignments[0]["reviewer_id"]

        code, report = self.invoke(self.submission(review_assignments=assignments))

        self.assertEqual(code, 2)
        self.assertIn("reviewer_cross_arm_assignment_conflict", report["issues"])
        self.assertFalse(report["run_authorized"])

    def test_agent_assignments_do_not_count_toward_two_human_reviewers(self):
        assignments = [
            {"unit_id": "unit-001", "arm_id": "reference-system",
             "reviewer_id": "reviewer-1", "actor_type": "human"},
            {"unit_id": "unit-001", "arm_id": "reference-system",
             "reviewer_id": "reviewer-2", "actor_type": "human"},
            {"unit_id": "unit-001", "arm_id": "release-42",
             "reviewer_id": "reviewer-3", "actor_type": "human"},
            {"unit_id": "unit-001", "arm_id": "release-42",
             "reviewer_id": "agent-1", "actor_type": "model"},
        ]

        code, report = self.invoke(self.submission(review_assignments=assignments))

        self.assertEqual(code, 2)
        self.assertIn("two_independent_human_reviews_required_per_arm", report["issues"])

    def test_changed_evidence_file_blocks_readiness(self):
        self.evidence_root.joinpath("scope_and_sampling.txt").write_text(
            "Changed after the evidence digest was recorded.", encoding="utf-8",
        )

        code, report = self.invoke(self.submission())

        self.assertEqual(code, 2)
        self.assertIn("evidence_integrity_unverified:scope_and_sampling", report["issues"])
        self.assertNotEqual(report["status"], "complete_for_independent_review")

    def test_restricted_evidence_is_not_opened_or_claimed_verified(self):
        payload = self.submission()
        payload["evidence"][0]["artifact"]["visibility"] = "restricted"
        payload["evidence"][0]["artifact"]["path"] = "does-not-exist.txt"

        code, report = self.invoke(payload)

        self.assertEqual(code, 2)
        self.assertIn("evidence_integrity_unverified:scope_and_sampling", report["issues"])
        self.assertEqual(report["verified_evidence_count"], len(EVIDENCE_CATEGORIES) - 1)

    def test_example_submission_is_schema_valid_complete_shape_template(self):
        example = Path(__file__).resolve().parents[1] / "examples" / "readiness-submission.json"

        submission = ReadinessSubmission.model_validate(
            json.loads(example.read_text(encoding="utf-8"))
        )

        self.assertEqual(submission.kind, "readiness_submission")
        self.assertEqual(len(submission.evidence), len(EVIDENCE_CATEGORIES))
        self.assertEqual(len(submission.roles), 6)
        self.assertEqual(len(submission.review_assignments), 6)


if __name__ == "__main__":
    unittest.main()

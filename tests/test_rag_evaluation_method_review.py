"""Method review is distinct from dataset and execution admission."""

import contextlib
import io
import json
import tempfile
import unittest

from rag_evaluation.cli import main
from rag_evaluation.gate import check_protocol, record_ref
from rag_evaluation.method_review import review_method_spec
from test_rag_evaluation_gate import AdmissionFixture


class MethodReviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rag-method-review-")
        self.addCleanup(temporary.cleanup)
        self.fixture = AdmissionFixture(temporary.name)

    def test_method_review_can_pass_while_run_admission_is_blocked(self):
        self.fixture.plan.dataset = None
        self.fixture.plan.snapshots = {}
        self.fixture.plan.budget = self.fixture.plan.budget.model_copy(update={
            "absolute_p95_seconds": None,
            "currency": None,
            "memory_mb": None,
            "concurrency": None,
            "task_timeout_seconds": None,
            "retries": None,
            "max_model_calls": None,
            "stop_conditions": [],
        })
        self.fixture.register(approvals=False)

        method = review_method_spec(
            self.fixture.registry, record_ref(self.fixture.plan), self.fixture.roots,
        )
        run = check_protocol(
            self.fixture.registry, record_ref(self.fixture.plan), self.fixture.roots,
            mode="formal",
        )

        self.assertEqual(method["status"], "ready_for_independent_method_review")
        self.assertTrue(method["method_artifacts_complete"])
        self.assertTrue(method["independent_signoff_required"])
        self.assertFalse(method["protocol_frozen"])
        self.assertFalse(method["run_authorized"])
        self.assertEqual(run["status"], "blocked")
        self.assertFalse(run["run_authorized"])

    def test_method_review_reports_missing_rule_artifact(self):
        self.fixture.plan.rules.pop("serious_errors")
        self.fixture.register(approvals=False)

        report = review_method_spec(
            self.fixture.registry, record_ref(self.fixture.plan), self.fixture.roots,
        )

        self.assertEqual(report["status"], "blocked")
        self.assertIn(
            {"code": "method_rule_missing", "location": "rules.serious_errors"},
            report["issues"],
        )
        self.assertFalse(report["run_authorized"])

    def test_method_review_rejects_changed_important_claim_contract_fields(self):
        for field, value in (("decision", "minimum_floor"),
                             ("na_policy", "not_allowed")):
            with self.subTest(field=field):
                root = self.fixture.root / field
                root.mkdir()
                fixture = AdmissionFixture(root)
                gate = fixture.plan.analysis.metric_gates["important_claim_coverage"]
                fixture.plan.analysis.metric_gates["important_claim_coverage"] = (
                    gate.model_copy(update={field: value})
                )
                fixture.register(approvals=False)
                report = review_method_spec(
                    fixture.registry, record_ref(fixture.plan), fixture.roots,
                )
                self.assertIn(
                    {"code": "method_metric_gate_mismatch",
                     "location": "analysis.metric_gates.important_claim_coverage"},
                    report["issues"],
                )

    def test_method_review_rejects_incomplete_threshold_contract(self):
        self.fixture.plan.thresholds = self.fixture.plan.thresholds.model_copy(update={
            "primary_gain": None,
        })
        self.fixture.register(approvals=False)

        report = review_method_spec(
            self.fixture.registry, record_ref(self.fixture.plan), self.fixture.roots,
        )

        self.assertIn(
            {"code": "method_threshold_missing", "location": "thresholds.primary_gain"},
            report["issues"],
        )

    def test_cli_review_method_is_non_authorizing(self):
        self.fixture.register(approvals=False)
        reference_path = self.fixture.root / "protocol-ref.json"
        reference_path.write_text(
            record_ref(self.fixture.plan).model_dump_json(), encoding="utf-8",
        )
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            code = main([
                "review-method", "--registry", str(self.fixture.registry.root),
                "--reference", str(reference_path), "--root", f"open={self.fixture.root}",
            ])

        report = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "ready_for_independent_method_review")
        self.assertFalse(report["protocol_frozen"])
        self.assertFalse(report["run_authorized"])


if __name__ == "__main__":
    unittest.main()

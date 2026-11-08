"""CLI contract and subprocess smoke tests use isolated synthetic records."""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from rag_evaluation.cli import _parser, main
from rag_evaluation.gate import record_ref
from rag_evaluation.models import ComparisonPlan, SystemSnapshot
from rag_evaluation.registry import Registry
from test_rag_evaluation_gate import AdmissionFixture
from test_rag_evaluation_joint_power import gate_thresholds, pilot_records


class GovernanceCliTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="rag-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def invoke(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(list(arguments))
        return code, json.loads(output.getvalue())

    def test_init_register_list_reject_duplicate(self):
        registry = str(self.root / "journal")
        self.assertEqual(self.invoke("init", "--registry", registry)[0], 0)
        document = self.root / "snapshot.json"
        document.write_text(json.dumps({
            "kind": "snapshot", "id": "local-A", "revision": 1, "arm": "A",
            "identity": "local_reference", "root_id": "project",
        }), encoding="utf-8")
        self.assertEqual(self.invoke("register", "--registry", registry,
                                     "--document", str(document))[0], 0)
        self.assertEqual(self.invoke("register", "--registry", registry,
                                     "--document", str(document))[0], 3)
        code, report = self.invoke("list", "--registry", registry)
        self.assertEqual(code, 0)
        self.assertEqual(len(report["records"]), 1)
        self.assertNotIn("document", report["records"][0])

    def test_register_system_snapshot_returns_portable_reference(self):
        registry = str(self.root / "journal")
        self.assertEqual(self.invoke("init", "--registry", registry)[0], 0)
        document = self.root / "system-snapshot.json"
        document.write_text(json.dumps({
            "kind": "system_snapshot", "schema_version": "2", "id": "model-r42",
            "revision": 1, "system_id": "team-model", "identity": "org/model-r42",
            "root_id": "evidence",
        }), encoding="utf-8")

        code, report = self.invoke("register", "--registry", registry,
                                   "--document", str(document))

        self.assertEqual(code, 0)
        self.assertEqual(report["reference"]["kind"], "system_snapshot")
        self.assertEqual(report["reference"]["id"], "model-r42")
        self.assertEqual(len(report["reference"]["sha256"]), 64)
        self.assertFalse(report["run_authorized"])

    def test_capture_system_emits_portable_snapshot_for_configured_paths(self):
        source = self.root / "candidate"
        source.mkdir()
        (source / "service.py").write_text("answer = 42\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.email",
                        "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(source), "add", "service.py"], check=True)
        subprocess.run(["git", "-C", str(source), "commit", "-qm", "fixture"], check=True)

        code, report = self.invoke(
            "capture-system", "--source-root", str(source), "--root-id", "team-source",
            "--snapshot-id", "team-rag-r42", "--system-id", "team-rag",
            "--identity", "acme/rag@r42", "--path", "service.py",
            "--allowed-path", "service.py",
        )

        self.assertEqual(code, 0)
        self.assertEqual(report["kind"], "system_snapshot")
        self.assertEqual(report["system_id"], "team-rag")
        self.assertEqual(report["identity"], "acme/rag@r42")
        self.assertEqual([item["path"] for item in report["materials"]["code"]],
                         ["service.py"])
        self.assertFalse(report["runtime_verified"])

    def test_invalid_document_does_not_echo_untrusted_payload(self):
        registry = str(self.root / "journal")
        self.invoke("init", "--registry", registry)
        document = self.root / "bad.json"
        document.write_text('{"kind":"protocol","secret":"DO-NOT-ECHO-THIS"}', encoding="utf-8")
        code, report = self.invoke("register", "--registry", registry,
                                   "--document", str(document))
        self.assertEqual(code, 3)
        self.assertNotIn("DO-NOT-ECHO-THIS", json.dumps(report))

    def test_validate_accepts_multi_candidate_screening_as_exploratory(self):
        document = self.root / "comparison-plan.json"
        document.write_text(json.dumps({
            "kind": "comparison_plan", "schema_version": "2", "id": "screen-1",
            "revision": 1, "mode": "exploratory_screening", "arms": [
                {"arm_id": "control", "role": "baseline", "snapshot": {
                    "kind": "system_snapshot", "id": "base", "revision": 1, "sha256": "a" * 64,
                }},
                {"arm_id": "candidate-one", "role": "candidate", "snapshot": {
                    "kind": "system_snapshot", "id": "one", "revision": 1, "sha256": "b" * 64,
                }},
                {"arm_id": "candidate-two", "role": "candidate", "snapshot": {
                    "kind": "system_snapshot", "id": "two", "revision": 1, "sha256": "c" * 64,
                }},
            ],
        }), encoding="utf-8")

        code, report = self.invoke("validate", "--document", str(document))

        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "valid")
        self.assertEqual(report["schema_version"], "2")
        self.assertEqual(len(report["comparisons"]), 2)
        self.assertTrue(all(item["result_type"] == "exploratory"
                            for item in report["comparisons"]))
        self.assertFalse(report["run_authorized"])

    def test_validate_rejects_multi_candidate_confirmation_without_echoing_input(self):
        document = self.root / "invalid-confirmation.json"
        payload = {
            "kind": "comparison_plan", "schema_version": "2", "id": "confirm-1",
            "revision": 1, "mode": "confirmatory", "arms": [
                {"arm_id": "control", "role": "baseline", "snapshot": {
                    "kind": "system_snapshot", "id": "base", "revision": 1, "sha256": "a" * 64,
                }},
                {"arm_id": "candidate-one", "role": "candidate", "snapshot": {
                    "kind": "system_snapshot", "id": "one", "revision": 1, "sha256": "b" * 64,
                }},
                {"arm_id": "candidate-two", "role": "candidate", "snapshot": {
                    "kind": "system_snapshot", "id": "two", "revision": 1, "sha256": "c" * 64,
                }},
            ],
        }
        document.write_text(json.dumps(payload), encoding="utf-8")

        code, report = self.invoke("validate", "--document", str(document))

        self.assertEqual(code, 3)
        self.assertEqual(report["status"], "invalid")
        self.assertNotIn("candidate-two", json.dumps(report))

    def test_review_comparison_resolves_registered_snapshot_references(self):
        registry_path = self.root / "review-registry"
        registry = Registry.initialize(registry_path)
        refs = {}
        for record_id in ("base", "candidate"):
            snapshot = SystemSnapshot(
                id=record_id, revision=1, system_id=record_id,
                identity=f"org/{record_id}", root_id="evidence",
            )
            registry.append(snapshot)
            refs[record_id] = record_ref(snapshot)
        plan = ComparisonPlan(
            id="review-plan", revision=1, mode="confirmatory", arms=[
                {"arm_id": "baseline-main", "role": "baseline", "snapshot": refs["base"]},
                {"arm_id": "candidate-next", "role": "candidate",
                 "snapshot": refs["candidate"]},
            ],
        )
        registry.append(plan)
        plan_ref = record_ref(plan)
        reference = self.root / "comparison-ref.json"
        reference.write_text(plan_ref.model_dump_json(), encoding="utf-8")

        code, report = self.invoke(
            "review-comparison", "--registry", str(registry_path),
            "--reference", str(reference),
        )

        self.assertEqual(code, 2)
        self.assertEqual(report["integrity_status"], "valid")
        self.assertEqual(report["formal_readiness"], "not_assessed")
        self.assertEqual(report["run_authorized"], False)
        self.assertIn("independent_holdout_or_sampling_justification",
                      report["required_evidence"])
        self.assertEqual(report["resolved_system_count"], 2)

    def test_review_comparison_marks_superseded_snapshot_stale(self):
        registry_path = self.root / "stale-review-registry"
        registry = Registry.initialize(registry_path)
        base = SystemSnapshot(
            id="base", revision=1, system_id="base", identity="org/base",
            root_id="evidence",
        )
        candidate = SystemSnapshot(
            id="candidate", revision=1, system_id="candidate",
            identity="org/candidate", root_id="evidence",
        )
        registry.append(base)
        registry.append(candidate)
        plan = ComparisonPlan(
            id="stale-plan", revision=1, mode="confirmatory", arms=[
                {"arm_id": "main", "role": "baseline", "snapshot": record_ref(base)},
                {"arm_id": "next", "role": "candidate", "snapshot": record_ref(candidate)},
            ],
        )
        registry.append(plan)
        registry.append(base.model_copy(update={"revision": 2, "identity": "org/base-v2"}))
        reference = self.root / "stale-comparison-ref.json"
        reference.write_text(record_ref(plan).model_dump_json(), encoding="utf-8")

        code, report = self.invoke(
            "review-comparison", "--registry", str(registry_path),
            "--reference", str(reference),
        )

        self.assertEqual(code, 2)
        self.assertEqual(report["integrity_status"], "stale")
        self.assertIn("system_snapshot_superseded", report["issues"])
        self.assertFalse(report["run_authorized"])

    def test_check_exit_codes_and_freeze_verify(self):
        fixture = AdmissionFixture(self.root)
        fixture.register(approvals=False)
        ref = self.root / "ref.json"
        ref.write_text(record_ref(fixture.plan).model_dump_json(), encoding="utf-8")
        arguments = ["--registry", str(fixture.registry.root), "--reference", str(ref),
                     "--root", f"open={self.root}"]
        self.assertEqual(self.invoke("check", *arguments)[0], 2)

    def test_subprocess_help_is_available_without_production_imports(self):
        result = subprocess.run(
            [sys.executable, "-B", "-m", "rag_evaluation", "--help"],
            capture_output=True, text=True, timeout=15, check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("freeze", result.stdout)
        self.assertIn("register-comparison-bundle", result.stdout)
        self.assertIn("draft-readiness", result.stdout)

    def test_duplicate_root_binding_is_rejected(self):
        registry = str(self.root / "journal")
        self.invoke("init", "--registry", registry)
        document = self.root / "ref.json"
        document.write_text('{"kind":"protocol","id":"plan","revision":1,"sha256":"' +
                            "0" * 64 + '"}', encoding="utf-8")
        code, _ = self.invoke("check", "--registry", registry, "--reference", str(document),
                              "--root", f"open={self.root}", "--root", f"open={self.root}")
        self.assertEqual(code, 3)

    def test_configured_inventory_cli_records_team_defined_systems(self):
        source = self.root / "team-system"
        source.mkdir()
        (source / "service.py").write_text("answer = 42\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(source)], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(source), "config", "user.email",
                        "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(source), "add", "service.py"], check=True)
        subprocess.run(["git", "-C", str(source), "commit", "-qm", "fixture"], check=True)
        candidate = self.root / "team-candidate"
        candidate.mkdir()
        (candidate / "service.py").write_text("answer = 43\n", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(candidate)], check=True)
        subprocess.run(["git", "-C", str(candidate), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(candidate), "config", "user.email",
                        "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(candidate), "add", "service.py"], check=True)
        subprocess.run(["git", "-C", str(candidate), "commit", "-qm", "fixture"], check=True)
        config = self.root / "inventory-config.json"
        config.write_text(json.dumps({
            "schema_version": "1", "inventory_id": "team-eval-01",
            "systems": [
                {
                    "system_id": "support-rag-r7", "identity": "acme/support-rag@r7",
                    "root_id": "team-source", "source_root": str(source),
                    "snapshot_id": "support-rag-r7-source", "paths": ["service.py"],
                    "allowed_paths": ["service.py"],
                },
                {
                    "system_id": "support-rag-r8", "identity": "acme/support-rag@r8",
                    "root_id": "candidate-source", "source_root": str(candidate),
                    "snapshot_id": "support-rag-r8-source", "paths": ["service.py"],
                    "allowed_paths": ["service.py"],
                },
            ],
            "materials": [],
            "comparison": {
                "plan_id": "team-screening-01", "mode": "exploratory_screening",
                "arms": [
                    {"system_id": "support-rag-r7", "arm_id": "stable",
                     "role": "baseline"},
                    {"system_id": "support-rag-r8", "arm_id": "release-r8",
                     "role": "candidate"},
                ],
            },
        }), encoding="utf-8")
        output = self.root / "configured-inventory"

        code, report = self.invoke(
            "inventory-configured", "--config", str(config), "--output", str(output)
        )

        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "inventory_recorded")
        self.assertFalse(report["run_authorized"])
        self.assertEqual(report["comparison_bundle"]["status"], "draft")
        self.assertEqual(report["comparison_bundle"]["comparison_count"], 1)
        self.assertFalse(report["comparison_bundle"]["registered"])
        self.assertTrue(Path(report["comparison_bundle"]["comparison_plan_file"]).is_file())
        saved = json.loads((output / "inventory.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["systems"][0]["system_id"], "support-rag-r7")
        self.assertFalse(saved["readiness_assessed"])
        self.assertEqual(
            set(saved["comparison_bundle"]["system_snapshot_files"]),
            {"support-rag-r7", "support-rag-r8"},
        )

    def test_fingerprint_never_prints_file_body(self):
        path = self.root / "open.txt"
        path.write_bytes(b"synthetic-not-for-stdout")
        code, result = self.invoke("fingerprint", "--root-id", "open", "--path", "open.txt",
                                   "--source-root", str(self.root), "--visibility", "restricted")
        self.assertEqual(code, 0)
        self.assertEqual(result["size_bytes"], 24)
        self.assertEqual(result["visibility"], "restricted")
        self.assertNotIn("synthetic-not-for-stdout", json.dumps(result))

    def test_power_simulation_writes_reproducible_primary_only_report(self):
        spec = self.root / "power-spec.json"
        spec.write_text(json.dumps({
            "baseline_rate": 0.60,
            "candidate_rate": 0.70,
            "discordance_rate": 0.20,
            "source_group_icc": 0.05,
            "source_group_count": 8,
            "case_counts": [80],
            "minimum_observed_gain": 0.05,
            "confidence_level": 0.95,
            "target_power": 0.80,
            "simulation_trials": 20,
            "bootstrap_resamples": 99,
            "seed": 12345,
        }), encoding="utf-8")
        report_path = self.root / "power-report.json"
        code, report = self.invoke("power-simulate", "--spec", str(spec),
                                   "--report", str(report_path))

        self.assertEqual(code, 0)
        self.assertEqual(report["result_type"], "scenario_only_primary_power")
        self.assertEqual(report["scope"], "primary_gain_gate_only")
        self.assertFalse(report["formal_power_claim_permitted"])
        self.assertEqual(json.loads(report_path.read_text(encoding="utf-8")), report)

    def test_power_simulation_rejects_unknown_spec_fields_without_echoing_input(self):
        spec = self.root / "invalid-power-spec.json"
        spec.write_text(json.dumps({"secret": "DO-NOT-ECHO-THIS"}), encoding="utf-8")
        code, report = self.invoke("power-simulate", "--spec", str(spec))

        self.assertEqual(code, 3)
        self.assertEqual(report["status"], "invalid")
        self.assertNotIn("DO-NOT-ECHO-THIS", json.dumps(report))

    def test_stratified_power_cli_outputs_limited_reproducible_report(self):
        spec = self.root / "stratified-power-spec.json"
        spec.write_text(json.dumps({
            "strata": [
                {"stratum_id": "notes", "weight": 0.7,
                 "baseline_rate": 0.60, "candidate_rate": 0.72,
                 "discordance_rate": 0.22, "source_group_icc": 0.05,
                 "source_group_count": 4, "case_counts": [40]},
                {"stratum_id": "papers", "weight": 0.3,
                 "baseline_rate": 0.45, "candidate_rate": 0.55,
                 "discordance_rate": 0.25, "source_group_icc": 0.1,
                 "source_group_count": 4, "case_counts": [40]},
            ],
            "minimum_observed_gain": 0.05,
            "confidence_level": 0.95,
            "target_power": 0.80,
            "simulation_trials": 20,
            "bootstrap_resamples": 99,
            "seed": 12345,
        }), encoding="utf-8")
        report_path = self.root / "stratified-power-report.json"

        code, report = self.invoke(
            "power-simulate-stratified", "--spec", str(spec), "--report", str(report_path)
        )

        self.assertEqual(code, 0)
        self.assertEqual(report["result_type"], "scenario_only_stratified_primary_power")
        self.assertFalse(report["formal_power_claim_permitted"])
        self.assertEqual(report["scenarios"][0]["case_count"], 80)
        self.assertEqual(json.loads(report_path.read_text(encoding="utf-8")), report)

    def test_joint_power_cli_keeps_formal_claim_and_run_disabled(self):
        spec = self.root / "joint-power-spec.json"
        spec.write_text(json.dumps({
            "pilot_records": pilot_records(),
            "stratum_weights": {"notes": 0.5, "papers": 0.5},
            "source_group_counts_by_stratum": {"notes": [4], "papers": [4]},
            "gate_thresholds": gate_thresholds(),
            "simulation_trials": 20,
            "bootstrap_resamples": 99,
            "confidence_level": 0.95,
            "target_power": 0.80,
            "seed": 7421,
            "audit_complete": True,
        }), encoding="utf-8")
        report_path = self.root / "joint-power-report.json"

        code, report = self.invoke(
            "power-simulate-joint", "--spec", str(spec), "--report", str(report_path)
        )

        self.assertEqual(code, 0)
        self.assertEqual(report["scope"], "all_confirmatory_gates")
        self.assertFalse(report["formal_power_claim_permitted"])
        self.assertFalse(report["run_authorized"])
        self.assertEqual(json.loads(report_path.read_text(encoding="utf-8")), report)

    def test_report_output_is_exclusive_and_contains_same_blockers(self):
        fixture = AdmissionFixture(self.root)
        fixture.register(approvals=False)
        reference = self.root / "ref.json"
        reference.write_text(record_ref(fixture.plan).model_dump_json(), encoding="utf-8")
        report_path = self.root / "report.json"
        arguments = ["check", "--registry", str(fixture.registry.root),
                     "--reference", str(reference), "--root", f"open={self.root}",
                     "--report", str(report_path)]
        code, report = self.invoke(*arguments)
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(report_path.read_text(encoding="utf-8")), report)
        self.assertEqual(self.invoke(*arguments)[0], 3)


if __name__ == "__main__":
    unittest.main()

"""Configured comparison bundles are checked before append-only registration."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rag_evaluation.bundle_registration import _bundle_file
from rag_evaluation.cli import main
from rag_evaluation.comparison_review import review_comparison
from rag_evaluation.gate import record_ref
from rag_evaluation.models import ComparisonPlan, RecordRef, SystemSnapshot
from rag_evaluation.readiness_review import REQUIRED_EVIDENCE_CATEGORIES, REQUIRED_HUMAN_ROLES
from rag_evaluation.registry import Registry


class ComparisonBundleRegistrationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="comparison-bundle-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.bundle_dir = self.root / "bundle"
        snapshot_dir = self.bundle_dir / "system-snapshots"
        snapshot_dir.mkdir(parents=True)
        self.snapshots = [
            SystemSnapshot(
                id="snapshot-base", revision=1, system_id="team-base",
                identity="org/service@current", root_id="source-base",
            ),
            SystemSnapshot(
                id="snapshot-r2", revision=1, system_id="team-r2",
                identity="org/service@r2", root_id="source-r2",
            ),
        ]
        for snapshot in self.snapshots:
            (snapshot_dir / f"{snapshot.id}.json").write_text(
                snapshot.model_dump_json(), encoding="utf-8"
            )
        self.plan = ComparisonPlan(
            id="team-screening", revision=1, mode="exploratory_screening", arms=[
                {"arm_id": "current", "role": "baseline",
                 "snapshot": record_ref(self.snapshots[0])},
                {"arm_id": "release-r2", "role": "candidate",
                 "snapshot": record_ref(self.snapshots[1])},
            ],
        )
        self.inventory_path = self.bundle_dir / "inventory.json"
        self.write_bundle()
        self.registry_path = self.root / "registry"

    def write_bundle(self, snapshot_files=None, plan_file="comparison-plan.draft.json"):
        if snapshot_files is None:
            snapshot_files = {
                snapshot.system_id: f"system-snapshots/{snapshot.id}.json"
                for snapshot in self.snapshots
            }
        bundle = {
            "status": "draft",
            "system_snapshot_files": snapshot_files,
            "comparison_plan_file": plan_file,
            "comparison_plan": self.plan.model_dump(mode="json"),
            "registered": False,
            "readiness_assessed": False,
            "run_authorized": False,
        }
        payload = {
            "schema_version": "1", "inventory_id": "team-eval",
            "systems": [item.model_dump(mode="json") for item in self.snapshots],
            "materials": [], "missing_materials": [],
            "restricted_materials_not_read": [], "comparison_bundle": bundle,
            "readiness_assessed": False, "run_authorized": False,
        }
        (self.bundle_dir / plan_file).write_text(
            self.plan.model_dump_json(), encoding="utf-8"
        )
        self.inventory_path.write_text(json.dumps(payload), encoding="utf-8")

    def invoke(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(list(arguments))
        return code, json.loads(output.getvalue())

    def test_cli_imports_snapshots_and_plan_and_returns_reviewable_reference(self):
        self.assertEqual(self.invoke("init", "--registry", str(self.registry_path))[0], 0)

        code, report = self.invoke(
            "register-comparison-bundle", "--registry", str(self.registry_path),
            "--bundle", str(self.inventory_path),
        )

        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "registered")
        self.assertEqual(report["new_record_count"], 3)
        self.assertEqual(report["plan_reference"]["id"], "team-screening")
        self.assertFalse(report["readiness_assessed"])
        self.assertFalse(report["run_authorized"])
        registry = Registry(self.registry_path)
        self.assertEqual(len(registry.entries()), 3)
        review = review_comparison(
            registry, RecordRef.model_validate(report["plan_reference"])
        )
        self.assertEqual(review["integrity_status"], "valid")
        self.assertEqual(review["formal_readiness"], "not_assessed")

    def test_reimport_is_idempotent_and_does_not_append_duplicates(self):
        Registry.initialize(self.registry_path)

        first_code, first = self.invoke(
            "register-comparison-bundle", "--registry", str(self.registry_path),
            "--bundle", str(self.inventory_path),
        )
        second_code, second = self.invoke(
            "register-comparison-bundle", "--registry", str(self.registry_path),
            "--bundle", str(self.inventory_path),
        )

        self.assertEqual((first_code, second_code), (0, 0))
        self.assertEqual(first["new_record_count"], 3)
        self.assertEqual(second["new_record_count"], 0)
        self.assertEqual(second["already_present_count"], 3)
        self.assertEqual(len(Registry(self.registry_path).entries()), 3)

    def test_registration_writes_a_reviewable_reference_file(self):
        Registry.initialize(self.registry_path)
        reference_path = self.root / "plan-reference.json"

        code, report = self.invoke(
            "register-comparison-bundle", "--registry", str(self.registry_path),
            "--bundle", str(self.inventory_path),
            "--reference-output", str(reference_path),
        )

        self.assertEqual(code, 0)
        self.assertEqual(
            json.loads(reference_path.read_text(encoding="utf-8")),
            report["plan_reference"],
        )
        self.assertEqual(report["plan_reference_file"], str(reference_path))
        review_code, review_report = self.invoke(
            "review-comparison", "--registry", str(self.registry_path),
            "--reference", str(reference_path),
        )
        self.assertEqual(review_code, 2)
        self.assertEqual(review_report["integrity_status"], "valid")
        self.assertEqual(review_report["formal_readiness"], "not_assessed")
        retry_code, retry_report = self.invoke(
            "register-comparison-bundle", "--registry", str(self.registry_path),
            "--bundle", str(self.inventory_path),
            "--reference-output", str(reference_path),
        )
        self.assertEqual(retry_code, 0)
        self.assertEqual(retry_report["status"], "already_registered")
        self.assertEqual(len(Registry(self.registry_path).entries()), 3)

    def test_readiness_draft_uses_registered_plan_and_current_profile_slots(self):
        Registry.initialize(self.registry_path)
        registered = self.invoke(
            "register-comparison-bundle", "--registry", str(self.registry_path),
            "--bundle", str(self.inventory_path),
        )[1]
        reference_path = self.root / "plan-reference.json"
        reference_path.write_text(
            json.dumps(registered["plan_reference"]), encoding="utf-8"
        )
        output_path = self.root / "readiness-draft.json"

        code, report = self.invoke(
            "draft-readiness", "--registry", str(self.registry_path),
            "--reference", str(reference_path), "--readiness-id", "team-readiness",
            "--output", str(output_path), "--unit-id", "sample-01",
            "--unit-id", "sample-02",
        )

        self.assertEqual(code, 0)
        self.assertFalse(report["readiness_assessed"])
        self.assertFalse(report["run_authorized"])
        draft = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(draft["comparison_plan"], registered["plan_reference"])
        self.assertEqual(
            {item["category"] for item in draft["evidence"]},
            set(REQUIRED_EVIDENCE_CATEGORIES),
        )
        self.assertEqual(
            {item["role_id"] for item in draft["roles"]},
            set(REQUIRED_HUMAN_ROLES),
        )
        self.assertTrue(all(
            item["artifact"]["sha256"] == "0" * 64
            and item["artifact"]["root_id"] == "TODO-evidence-root"
            for item in draft["evidence"]
        ))
        self.assertTrue(all(
            item["person_id"] == f"TODO-{item['role_id']}"
            for item in draft["roles"]
        ))
        assignments = draft["review_assignments"]
        self.assertEqual(len(assignments), 8)
        self.assertEqual({item["unit_id"] for item in assignments}, {"sample-01", "sample-02"})
        self.assertTrue(all(item["reviewer_id"].startswith("TODO-") for item in assignments))
        for unit_id in ("sample-01", "sample-02"):
            unit_assignments = [item for item in assignments if item["unit_id"] == unit_id]
            self.assertEqual({item["arm_id"] for item in unit_assignments},
                             {"current", "release-r2"})
            by_arm = {
                arm_id: {item["reviewer_id"] for item in unit_assignments
                         if item["arm_id"] == arm_id}
                for arm_id in ("current", "release-r2")
            }
            self.assertEqual([len(reviewers) for reviewers in by_arm.values()], [2, 2])
            self.assertFalse(by_arm["current"].intersection(by_arm["release-r2"]))
        self.assertEqual(report["status"], "draft_created")
        self.assertEqual(report["comparison_arm_ids"], ["current", "release-r2"])
        self.assertEqual(report["review_assignment_count"], 8)

    def test_readiness_reviewer_matrix_expands_for_multiple_candidate_versions(self):
        third_snapshot = SystemSnapshot(
            id="snapshot-r3", revision=1, system_id="team-r3",
            identity="org/service@r3", root_id="source-r3",
        )
        self.snapshots.append(third_snapshot)
        (self.bundle_dir / "system-snapshots" / "snapshot-r3.json").write_text(
            third_snapshot.model_dump_json(), encoding="utf-8"
        )
        arms = [arm.model_dump(mode="json") for arm in self.plan.arms]
        arms.append({
            "arm_id": "release-r3", "role": "candidate",
            "snapshot": record_ref(third_snapshot).model_dump(mode="json"),
        })
        self.plan = ComparisonPlan(
            id=self.plan.id, revision=1, mode="exploratory_screening", arms=arms,
        )
        self.write_bundle()
        Registry.initialize(self.registry_path)
        registered = self.invoke(
            "register-comparison-bundle", "--registry", str(self.registry_path),
            "--bundle", str(self.inventory_path),
        )[1]
        reference_path = self.root / "plan-reference.json"
        reference_path.write_text(
            json.dumps(registered["plan_reference"]), encoding="utf-8"
        )
        output_path = self.root / "readiness-draft.json"

        code, report = self.invoke(
            "draft-readiness", "--registry", str(self.registry_path),
            "--reference", str(reference_path), "--readiness-id", "team-readiness",
            "--output", str(output_path), "--unit-id", "sample-01",
        )

        self.assertEqual(code, 0)
        self.assertEqual(
            report["comparison_arm_ids"], ["current", "release-r2", "release-r3"],
        )
        self.assertEqual(report["review_assignment_count"], 6)
        draft = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(
            {item["arm_id"] for item in draft["review_assignments"]},
            {"current", "release-r2", "release-r3"},
        )
        self.assertEqual(len({
            item["reviewer_id"] for item in draft["review_assignments"]
        }), 6)

    def test_reimport_completes_a_matching_partial_prefix(self):
        registry = Registry.initialize(self.registry_path)
        registry.append(self.snapshots[0])

        code, report = self.invoke(
            "register-comparison-bundle", "--registry", str(self.registry_path),
            "--bundle", str(self.inventory_path),
        )

        self.assertEqual(code, 0)
        self.assertEqual(report["new_record_count"], 2)
        self.assertEqual(report["already_present_count"], 1)
        self.assertEqual(len(registry.entries()), 3)

    def test_bundle_conflict_is_detected_before_any_new_snapshot_is_appended(self):
        registry = Registry.initialize(self.registry_path)
        conflicting = self.snapshots[1].model_copy(update={"identity": "org/service@other"})
        registry.append(conflicting)

        code, report = self.invoke(
            "register-comparison-bundle", "--registry", str(self.registry_path),
            "--bundle", str(self.inventory_path),
        )

        self.assertEqual(code, 3)
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["error"], "RegistryError")
        self.assertEqual(len(registry.entries()), 1)

    def test_tampered_snapshot_fails_reference_validation_before_registration(self):
        Registry.initialize(self.registry_path)
        snapshot_path = self.bundle_dir / "system-snapshots" / "snapshot-r2.json"
        tampered = self.snapshots[1].model_copy(update={"identity": "org/service@tampered"})
        snapshot_path.write_text(tampered.model_dump_json(), encoding="utf-8")

        code, report = self.invoke(
            "register-comparison-bundle", "--registry", str(self.registry_path),
            "--bundle", str(self.inventory_path),
        )

        self.assertEqual(code, 3)
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(len(Registry(self.registry_path).entries()), 0)

    def test_bundle_rejects_snapshot_path_escape(self):
        Registry.initialize(self.registry_path)
        self.write_bundle({
            "team-base": "system-snapshots/snapshot-base.json",
            "team-r2": "../outside.json",
        })

        code, report = self.invoke(
            "register-comparison-bundle", "--registry", str(self.registry_path),
            "--bundle", str(self.inventory_path),
        )

        self.assertEqual(code, 3)
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(len(Registry(self.registry_path).entries()), 0)

    def test_bundle_path_resolution_failure_is_reported_as_invalid_input(self):
        target = self.bundle_dir / "loop.json"
        original_resolve = Path.resolve

        def resolve_with_loop(path, *args, **kwargs):
            if path == target:
                raise RuntimeError("symlink loop")
            return original_resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", resolve_with_loop):
            with self.assertRaisesRegex(ValueError, "comparison_bundle_file_missing"):
                _bundle_file(self.bundle_dir, "loop.json")


if __name__ == "__main__":
    unittest.main()

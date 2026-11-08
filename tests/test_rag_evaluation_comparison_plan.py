"""Portable comparison plans accept arbitrary arms without changing legacy records."""

import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from rag_evaluation.models import ComparisonPlan, Snapshot, SystemSnapshot, parse_record
from rag_evaluation.registry import Registry


def snapshot_ref(record_id):
    return {
        "kind": "system_snapshot",
        "id": record_id,
        "revision": 1,
        "sha256": "a" * 64,
    }


def screening_plan():
    return {
        "kind": "comparison_plan",
        "schema_version": "2",
        "id": "screening-01",
        "revision": 1,
        "mode": "exploratory_screening",
        "arms": [
            {"arm_id": "control", "role": "baseline", "snapshot": snapshot_ref("base")},
            {"arm_id": "candidate-alpha", "role": "candidate",
             "snapshot": snapshot_ref("alpha")},
            {"arm_id": "candidate-beta", "role": "candidate",
             "snapshot": snapshot_ref("beta")},
            {"arm_id": "candidate-gamma", "role": "candidate",
             "snapshot": snapshot_ref("gamma")},
            {"arm_id": "candidate-delta", "role": "candidate",
             "snapshot": snapshot_ref("delta")},
        ],
    }


class ComparisonPlanTests(unittest.TestCase):
    def test_distributed_examples_have_valid_schema_shapes(self):
        examples = Path(__file__).resolve().parents[1] / "examples"
        plan = ComparisonPlan.model_validate(
            json.loads((examples / "comparison-plan.json").read_text(encoding="utf-8"))
        )
        snapshot = SystemSnapshot.model_validate(
            json.loads((examples / "system-snapshot.json").read_text(encoding="utf-8"))
        )

        self.assertEqual(len(plan.comparisons), 2)
        self.assertFalse(snapshot.runtime_verified)

    def test_screening_accepts_many_candidates_and_marks_each_comparison_exploratory(self):
        plan = ComparisonPlan.model_validate(screening_plan())

        self.assertEqual(
            [(item["baseline_arm_id"], item["candidate_arm_id"], item["result_type"])
             for item in plan.comparisons],
            [
                ("control", "candidate-alpha", "exploratory"),
                ("control", "candidate-beta", "exploratory"),
                ("control", "candidate-gamma", "exploratory"),
                ("control", "candidate-delta", "exploratory"),
            ],
        )

    def test_confirmatory_plan_accepts_exactly_one_candidate(self):
        payload = screening_plan()
        payload["mode"] = "confirmatory"

        with self.assertRaises(ValidationError):
            ComparisonPlan.model_validate(payload)

        payload["arms"] = payload["arms"][:2]
        plan = ComparisonPlan.model_validate(payload)
        self.assertEqual(len(plan.comparisons), 1)
        self.assertEqual(plan.comparisons[0]["result_type"], "confirmatory_plan")

    def test_arm_ids_must_be_unique(self):
        payload = screening_plan()
        payload["arms"][2]["arm_id"] = payload["arms"][1]["arm_id"]

        with self.assertRaises(ValidationError):
            ComparisonPlan.model_validate(payload)

    def test_arms_cannot_alias_the_same_snapshot_reference(self):
        payload = screening_plan()
        payload["arms"][2]["snapshot"] = payload["arms"][1]["snapshot"]

        with self.assertRaises(ValidationError):
            ComparisonPlan.model_validate(payload)

    def test_portable_arm_cannot_reference_project_specific_v1_snapshot(self):
        payload = screening_plan()
        payload["arms"][0]["snapshot"]["kind"] = "snapshot"

        with self.assertRaises(ValidationError):
            ComparisonPlan.model_validate(payload)

    def test_plan_requires_exactly_one_baseline_and_at_least_one_candidate(self):
        payload = screening_plan()
        payload["arms"][2]["role"] = "baseline"

        with self.assertRaises(ValidationError):
            ComparisonPlan.model_validate(payload)

    def test_versioned_parser_round_trips_v2_without_rewriting_v1(self):
        payload = screening_plan()

        parsed = parse_record(payload)

        self.assertIsInstance(parsed, ComparisonPlan)
        self.assertEqual(parsed.model_dump(mode="json"), payload)

    def test_registry_persists_v2_plan_without_rewriting_legacy_record_shape(self):
        with tempfile.TemporaryDirectory(prefix="comparison-plan-") as temporary:
            registry = Registry.initialize(Path(temporary) / "registry")
            plan = ComparisonPlan.model_validate(screening_plan())
            for system_id in ("base", "alpha", "beta", "gamma", "delta"):
                registry.append(SystemSnapshot(
                    id=system_id, revision=1, system_id=system_id,
                    identity=f"team-system-{system_id}", root_id=system_id,
                ))

            registry.append(plan)
            restored = parse_record(registry.entries()[-1]["document"])

        self.assertEqual(restored, plan)
        self.assertEqual(restored.schema_version, "2")

    def test_v2_system_identity_is_not_limited_to_legacy_identity_enums(self):
        snapshot = SystemSnapshot(
            id="provider-model-2026", revision=1, system_id="provider-model",
            identity="organization:team-a/model-r42", root_id="evidence-root",
        )

        parsed = parse_record(snapshot.model_dump(mode="json"))

        self.assertEqual(parsed, snapshot)
        self.assertEqual(parsed.schema_version, "2")

    def test_legacy_v1_snapshot_still_parses_unchanged(self):
        legacy = Snapshot(
            id="legacy-snapshot", revision=1, arm="A",
            identity="local_reference", root_id="project",
        )

        parsed = parse_record(legacy.model_dump(mode="json"))

        self.assertEqual(parsed, legacy)
        self.assertEqual(parsed.schema_version, "1")


if __name__ == "__main__":
    unittest.main()

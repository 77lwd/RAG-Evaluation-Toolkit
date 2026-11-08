import unittest
from copy import deepcopy

from rag_evaluation.joint_power import JointPowerSpec, simulate_joint_gate_power


def pilot_records(severe_error=False):
    records = []
    for stratum in ("notes", "papers"):
        for group_index in range(4):
            group_id = f"{stratum}-{group_index}"
            for case_index, answerability in enumerate(("answerable", "insufficient_source")):
                is_answerable = answerability == "answerable"
                a = {
                    "supported_complete": False if is_answerable else None,
                    "correct_refusal": (
                        group_index % 2 == 0 if not is_answerable else None
                    ),
                    "false_refusal": False if is_answerable else None,
                    "citation_support": 0.90,
                    "important_claim_coverage": 0.95,
                    "latency_seconds": 1.0,
                    "cost": 0.01,
                    "serious_errors": 0,
                }
                b = {
                    "supported_complete": True if is_answerable else None,
                    "correct_refusal": (
                        group_index != 3 if not is_answerable else None
                    ),
                    "false_refusal": False if is_answerable else None,
                    "citation_support": 0.90,
                    "important_claim_coverage": 0.95,
                    "latency_seconds": 1.1,
                    "cost": 0.011,
                    "serious_errors": int(severe_error and group_index == 0 and case_index == 0),
                }
                records.append({
                    "case_id": f"{group_id}-{case_index}",
                    "source_group_id": group_id,
                    "stratum_id": stratum,
                    "weight": 1.0,
                    "answerability": answerability,
                    "A": a,
                    "B": b,
                })
    return records


def gate_thresholds():
    return {
        "primary_gain_minimum_point": 0.05,
        "correct_refusal_difference_lower": -0.03,
        "false_refusal_difference_upper": 0.03,
        "citation_support_difference_lower": -0.02,
        "important_claim_coverage_difference_lower": -0.02,
        "p95_latency_ratio_max": 1.25,
        "mean_per_task_cost_ratio_max": 1.30,
    }


class JointGatePowerTests(unittest.TestCase):
    def _spec(self, **overrides):
        values = {
            "pilot_records": pilot_records(),
            "stratum_weights": {"notes": 0.5, "papers": 0.5},
            "source_group_counts_by_stratum": {"notes": [4], "papers": [4]},
            "gate_thresholds": gate_thresholds(),
            "simulation_trials": 30,
            "bootstrap_resamples": 99,
            "confidence_level": 0.95,
            "target_power": 0.80,
            "seed": 7421,
            "audit_complete": True,
        }
        values.update(overrides)
        return JointPowerSpec(**values)

    def test_joint_gate_simulation_is_reproducible_and_covers_every_confirmatory_gate(self):
        first = simulate_joint_gate_power(self._spec())
        second = simulate_joint_gate_power(self._spec())

        self.assertEqual(first, second)
        self.assertEqual(first["scope"], "all_confirmatory_gates")
        self.assertFalse(first["formal_power_claim_permitted"])
        self.assertFalse(first["run_authorized"])
        self.assertEqual(first["scenarios"][0]["case_count_mean"], 16)
        self.assertEqual(first["scenarios"][0]["case_count_range"], [16, 16])
        self.assertEqual(first["scenarios"][0]["source_group_count"], 8)
        self.assertEqual(first["assumptions"]["gate_thresholds"], gate_thresholds())
        self.assertEqual(set(first["scenarios"][0]["gate_power"]), {
            "supported_complete_answer_rate", "correct_refusal_rate", "false_refusal_rate",
            "citation_support", "important_claim_coverage", "p95_latency",
            "mean_per_task_cost", "serious_errors",
        })

    def test_confirmed_severe_event_prevents_joint_pass(self):
        result = simulate_joint_gate_power(self._spec(pilot_records=pilot_records(True)))

        scenario = result["scenarios"][0]
        self.assertLess(scenario["gate_power"]["serious_errors"]["power"], 1)
        self.assertLessEqual(
            scenario["joint_gate_power"],
            scenario["gate_power"]["serious_errors"]["power"],
        )

    def test_incomplete_serious_event_audit_cannot_claim_joint_power(self):
        result = simulate_joint_gate_power(self._spec(audit_complete=False))

        self.assertFalse(result["formal_power_claim_permitted"])
        self.assertFalse(result["scenarios"][0]["joint_power_interpretable"])
        self.assertIn("serious_event_audit_incomplete", result["limitations"])

    def test_rejects_missing_confirmatory_scores_and_cross_stratum_groups(self):
        records = pilot_records()
        del records[0]["B"]["supported_complete"]
        with self.assertRaisesRegex(ValueError, "case_metric_missing"):
            self._spec(pilot_records=records)

        records = pilot_records()
        records[8]["source_group_id"] = records[0]["source_group_id"]
        with self.assertRaisesRegex(ValueError, "source_group_cross_stratum"):
            self._spec(pilot_records=records)

        records = pilot_records()
        records[0]["A"]["supported_complete"] = None
        with self.assertRaisesRegex(ValueError, "applicable_metric_missing"):
            self._spec(pilot_records=records)

    def test_group_resampling_preserves_all_rows_in_selected_source_groups(self):
        records = pilot_records()
        extra = deepcopy(records[0])
        extra["case_id"] = "notes-0-extra"
        records.append(extra)
        result = simulate_joint_gate_power(self._spec(pilot_records=records))

        low, high = result["scenarios"][0]["case_count_range"]
        self.assertEqual(low, 16)
        self.assertGreater(high, low)

    def test_rejects_invalid_source_group_grids(self):
        with self.assertRaisesRegex(ValueError, "scenario_grid_mismatch"):
            self._spec(source_group_counts_by_stratum={"notes": [4, 6], "papers": [4]})
        with self.assertRaisesRegex(ValueError, "sample_size_grid_must_be_increasing"):
            self._spec(source_group_counts_by_stratum={"notes": [4, 3], "papers": [4, 6]})

    def test_single_stratum_scenario_is_supported(self):
        records = [record for record in pilot_records() if record["stratum_id"] == "notes"]
        result = simulate_joint_gate_power(self._spec(
            pilot_records=records,
            stratum_weights={"notes": 1.0},
            source_group_counts_by_stratum={"notes": [4]},
        ))

        self.assertEqual(result["scenarios"][0]["source_group_counts_by_stratum"], {"notes": 4})

    def test_rejects_incomplete_or_invalid_gate_thresholds(self):
        thresholds = gate_thresholds()
        del thresholds["false_refusal_difference_upper"]
        with self.assertRaisesRegex(ValueError, "gate_thresholds_invalid"):
            self._spec(gate_thresholds=thresholds)

    def test_gate_threshold_inputs_control_joint_pass_classification(self):
        permissive = simulate_joint_gate_power(self._spec())
        strict_thresholds = gate_thresholds()
        strict_thresholds["correct_refusal_difference_lower"] = 0.5
        strict = simulate_joint_gate_power(self._spec(gate_thresholds=strict_thresholds))

        self.assertGreater(
            permissive["scenarios"][0]["gate_power"]["correct_refusal_rate"]["power"],
            strict["scenarios"][0]["gate_power"]["correct_refusal_rate"]["power"],
        )
        self.assertEqual(strict["scenarios"][0]["gate_power"]["correct_refusal_rate"]["power"], 0)

    def test_degenerate_bootstrap_interval_makes_scenario_unestimable(self):
        result = simulate_joint_gate_power(self._spec())
        scenario = result["scenarios"][0]

        self.assertEqual(scenario["valid_analysis_rate"], 0)
        self.assertFalse(scenario["joint_power_interpretable"])
        self.assertEqual(scenario["gate_power"]["citation_support"]["power"], 0)

        thresholds = gate_thresholds()
        thresholds["p95_latency_ratio_max"] = 0
        with self.assertRaisesRegex(ValueError, "gate_thresholds_invalid"):
            self._spec(gate_thresholds=thresholds)


if __name__ == "__main__":
    unittest.main()

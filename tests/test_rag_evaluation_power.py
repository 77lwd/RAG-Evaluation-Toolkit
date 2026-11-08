import unittest

from rag_evaluation.power import (
    PrimaryPowerSpec,
    PowerStratum,
    StratifiedPrimaryPowerSpec,
    minimum_zero_event_groups,
    _weighted_stratified_difference,
    zero_event_upper_bound,
    simulate_primary_power,
    simulate_stratified_primary_power,
)


class PrimaryPowerSimulationTests(unittest.TestCase):
    def _spec(self, **overrides):
        values = {
            "baseline_rate": 0.60,
            "candidate_rate": 0.70,
            "discordance_rate": 0.20,
            "source_group_icc": 0.05,
            "source_group_count": 8,
            "case_counts": [80, 120],
            "minimum_observed_gain": 0.05,
            "confidence_level": 0.95,
            "target_power": 0.80,
            "simulation_trials": 20,
            "bootstrap_resamples": 99,
            "seed": 12345,
        }
        values.update(overrides)
        return PrimaryPowerSpec(**values)

    def test_simulation_is_reproducible_and_clearly_limited_to_primary_gate(self):
        spec = self._spec()
        first = simulate_primary_power(spec)
        second = simulate_primary_power(spec)

        self.assertEqual(first, second)
        self.assertEqual(first["scope"], "primary_gain_gate_only")
        self.assertFalse(first["formal_power_claim_permitted"])
        self.assertEqual(
            [row["case_count"] for row in first["scenarios"]], [80, 120]
        )
        for row in first["scenarios"]:
            self.assertGreaterEqual(row["power"], 0)
            self.assertLessEqual(row["power"], 1)
            self.assertLessEqual(row["power_ci_95"][0], row["power"])
            self.assertGreaterEqual(row["power_ci_95"][1], row["power"])

    def test_rejects_infeasible_paired_marginals(self):
        with self.assertRaisesRegex(ValueError, "paired_marginals_infeasible"):
            self._spec(
                baseline_rate=0.10,
                candidate_rate=0.90,
                discordance_rate=0.20,
            )

    def test_rejects_paired_marginal_infeasibility_below_old_tolerance(self):
        with self.assertRaisesRegex(ValueError, "paired_marginals_infeasible"):
            self._spec(
                baseline_rate=0.50,
                candidate_rate=0.5000000000005,
                discordance_rate=0,
            )

    def test_accepts_feasible_zero_probability_boundary(self):
        spec = self._spec(
            baseline_rate=0.10,
            candidate_rate=0.90,
            discordance_rate=0.80,
        )
        self.assertEqual(spec._pair_probabilities()[1], 0)

    def test_rejects_case_counts_smaller_than_independent_source_groups(self):
        with self.assertRaisesRegex(ValueError, "case_count_below_source_groups"):
            self._spec(case_counts=[7])

    def test_rejects_invalid_cluster_correlation(self):
        with self.assertRaisesRegex(ValueError, "source_group_icc_invalid"):
            self._spec(source_group_icc=1.0)

    def test_null_scenario_has_bounded_false_pass_rate(self):
        result = simulate_primary_power(self._spec(
            baseline_rate=0.60,
            candidate_rate=0.60,
            discordance_rate=0.20,
            source_group_icc=0,
            source_group_count=16,
            case_counts=[160],
            minimum_observed_gain=0,
            simulation_trials=600,
            bootstrap_resamples=199,
            seed=9876,
        ))
        false_pass_rate = result["scenarios"][0]["power"]
        self.assertLess(false_pass_rate, 0.08)

    def test_rejects_boolean_values_as_numeric_rates(self):
        with self.assertRaisesRegex(ValueError, "baseline_rate_invalid"):
            self._spec(baseline_rate=True)

    def _stratified_spec(self, **overrides):
        values = {
            "strata": [
                PowerStratum(
                    stratum_id="course_notes", weight=0.7,
                    baseline_rate=0.60, candidate_rate=0.72,
                    discordance_rate=0.22, source_group_icc=0.05,
                    source_group_count=4, case_counts=[40, 60],
                ),
                PowerStratum(
                    stratum_id="papers", weight=0.3,
                    baseline_rate=0.45, candidate_rate=0.55,
                    discordance_rate=0.25, source_group_icc=0.1,
                    source_group_count=4, case_counts=[40, 60],
                ),
            ],
            "minimum_observed_gain": 0.05,
            "confidence_level": 0.95,
            "target_power": 0.80,
            "simulation_trials": 20,
            "bootstrap_resamples": 99,
            "seed": 12345,
        }
        values.update(overrides)
        return StratifiedPrimaryPowerSpec(**values)

    def test_stratified_simulation_is_reproducible_and_resamples_within_strata(self):
        spec = self._stratified_spec()
        first = simulate_stratified_primary_power(spec)
        second = simulate_stratified_primary_power(spec)

        self.assertEqual(first, second)
        self.assertEqual(first["scope"], "primary_gain_gate_only")
        self.assertFalse(first["formal_power_claim_permitted"])
        self.assertEqual(
            [row["case_count"] for row in first["scenarios"]], [80, 120]
        )
        self.assertEqual(first["assumptions"]["stratum_weights"], {
            "course_notes": 0.7, "papers": 0.3,
        })
        self.assertTrue(first["assumptions"]["bootstrap_within_each_stratum"])

    def test_stratified_probability_rejects_tiny_infeasible_paired_marginal(self):
        with self.assertRaisesRegex(ValueError, "paired_marginals_infeasible"):
            PowerStratum(
                stratum_id="course_notes", weight=0.7,
                baseline_rate=0.50, candidate_rate=0.5000000000005,
                discordance_rate=0, source_group_icc=0.05,
                source_group_count=4, case_counts=[40, 60],
            )

    def test_stratified_simulation_rejects_weights_that_do_not_sum_to_one(self):
        with self.assertRaisesRegex(ValueError, "stratum_weights_must_sum_to_one"):
            self._stratified_spec(strata=[
                self._stratified_spec().strata[0],
                PowerStratum(
                    stratum_id="papers", weight=0.4,
                    baseline_rate=0.45, candidate_rate=0.55,
                    discordance_rate=0.25, source_group_icc=0.1,
                    source_group_count=4, case_counts=[40, 60],
                ),
            ])

    def test_stratified_simulation_rejects_mismatched_sample_size_grids(self):
        with self.assertRaisesRegex(ValueError, "stratum_case_count_grid_mismatch"):
            self._stratified_spec(strata=[
                self._stratified_spec().strata[0],
                PowerStratum(
                    stratum_id="papers", weight=0.3,
                    baseline_rate=0.45, candidate_rate=0.55,
                    discordance_rate=0.25, source_group_icc=0.1,
                    source_group_count=4, case_counts=[40],
                ),
            ])

    def test_stratified_difference_uses_preregistered_stratum_weights(self):
        result = _weighted_stratified_difference([
            (0.75, [(10, 2, 4)]),
            (0.25, [(10, 6, 4)]),
        ])

        self.assertAlmostEqual(result, 0.10)

    def test_stratified_simulation_rejects_total_scenario_above_limit(self):
        strata = self._stratified_spec().strata
        oversized = [
            PowerStratum(**{
                **item.__dict__,
                "case_counts": [60000],
            })
            for item in strata
        ]
        with self.assertRaisesRegex(ValueError, "scenario_case_count_exceeds_limit"):
            self._stratified_spec(strata=oversized)


class RareEventPrecisionTests(unittest.TestCase):
    def test_zero_event_upper_bound_uses_independent_groups(self):
        self.assertAlmostEqual(
            zero_event_upper_bound(59, 0.95),
            0.049507609888227,
        )

    def test_required_groups_are_minimal_for_requested_precision(self):
        required = minimum_zero_event_groups(0.05, 0.95)

        self.assertEqual(required, 59)
        self.assertLessEqual(zero_event_upper_bound(required, 0.95), 0.05)
        self.assertGreater(zero_event_upper_bound(required - 1, 0.95), 0.05)

    def test_rejects_invalid_precision_inputs(self):
        with self.assertRaisesRegex(ValueError, "group_count_invalid"):
            zero_event_upper_bound(0, 0.95)
        with self.assertRaisesRegex(ValueError, "confidence_level_invalid"):
            zero_event_upper_bound(20, 1.0)
        with self.assertRaisesRegex(ValueError, "maximum_rate_invalid"):
            minimum_zero_event_groups(0, 0.95)

    def test_rejects_boolean_inputs(self):
        with self.assertRaisesRegex(ValueError, "group_count_invalid"):
            zero_event_upper_bound(True, 0.95)
        with self.assertRaisesRegex(ValueError, "maximum_rate_invalid"):
            minimum_zero_event_groups(True, 0.95)


if __name__ == "__main__":
    unittest.main()

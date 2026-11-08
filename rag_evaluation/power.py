"""Scenario-only power simulation for the paired primary binary endpoint."""

from dataclasses import asdict, dataclass
from decimal import Decimal
import math
import random
import re

from .artifacts import digest_json


_ENGINE_VERSION = "primary-power-scenario-v1"
_MAX_CASES_PER_SCENARIO = 100_000
_MAX_SIMULATION_TRIALS = 100_000
_MAX_BOOTSTRAP_RESAMPLES = 10_000
_MAX_RESAMPLED_GROUP_DRAWS = 20_000_000
_MC_Z_95 = 1.959963984540054
_PAIR_CATEGORIES = ("both_pass", "a_only", "b_only", "neither_pass")


@dataclass(frozen=True)
class PrimaryPowerSpec:
    baseline_rate: float
    candidate_rate: float
    discordance_rate: float
    source_group_icc: float
    source_group_count: int
    case_counts: tuple[int, ...] | list[int]
    minimum_observed_gain: float
    confidence_level: float
    target_power: float
    simulation_trials: int
    bootstrap_resamples: int
    seed: int

    def __post_init__(self):
        for name in ("baseline_rate", "candidate_rate", "discordance_rate",
                     "minimum_observed_gain", "confidence_level", "target_power"):
            value = getattr(self, name)
            if (type(value) not in (int, float)
                    or type(value) is float and not math.isfinite(value)
                    or not 0 <= value <= 1):
                raise ValueError(f"{name}_invalid")
        if not 0 < self.confidence_level < 1:
            raise ValueError("confidence_level_invalid")
        if not 0 < self.target_power < 1:
            raise ValueError("target_power_invalid")
        if (type(self.source_group_icc) not in (int, float)
                or type(self.source_group_icc) is float
                and not math.isfinite(self.source_group_icc)
                or not 0 <= self.source_group_icc < 1):
            raise ValueError("source_group_icc_invalid")
        if type(self.source_group_count) is not int or self.source_group_count < 2:
            raise ValueError("source_group_count_invalid")
        if (type(self.simulation_trials) is not int
                or not 20 <= self.simulation_trials <= _MAX_SIMULATION_TRIALS):
            raise ValueError("simulation_trials_invalid")
        if (type(self.bootstrap_resamples) is not int
                or not 99 <= self.bootstrap_resamples <= _MAX_BOOTSTRAP_RESAMPLES):
            raise ValueError("bootstrap_resamples_invalid")
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError("seed_invalid")
        try:
            counts = tuple(self.case_counts)
        except TypeError as error:
            raise ValueError("case_counts_invalid") from error
        if (not counts or any(type(n) is not int or not 0 < n <= _MAX_CASES_PER_SCENARIO
                              for n in counts)):
            raise ValueError("case_counts_invalid")
        if tuple(sorted(set(counts))) != counts:
            raise ValueError("case_counts_must_be_unique_and_increasing")
        if counts[0] < self.source_group_count:
            raise ValueError("case_count_below_source_groups")
        work = len(counts) * self.simulation_trials * self.bootstrap_resamples
        if work * self.source_group_count > _MAX_RESAMPLED_GROUP_DRAWS:
            raise ValueError("simulation_work_limit_exceeded")
        if sum(counts) * self.simulation_trials > _MAX_RESAMPLED_GROUP_DRAWS:
            raise ValueError("simulation_case_work_limit_exceeded")
        object.__setattr__(self, "case_counts", counts)
        if any(p < 0 for p in self._pair_probabilities()):
            raise ValueError("paired_marginals_infeasible")

    def _pair_probabilities(self):
        return _paired_probabilities(
            self.baseline_rate, self.candidate_rate, self.discordance_rate
        )


@dataclass(frozen=True)
class PowerStratum:
    stratum_id: str
    weight: float
    baseline_rate: float
    candidate_rate: float
    discordance_rate: float
    source_group_icc: float
    source_group_count: int
    case_counts: tuple[int, ...] | list[int]

    def __post_init__(self):
        if (not isinstance(self.stratum_id, str)
                or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,95}", self.stratum_id)):
            raise ValueError("stratum_id_invalid")
        if (type(self.weight) not in (int, float)
                or type(self.weight) is float and not math.isfinite(self.weight)
                or self.weight <= 0 or self.weight > 1):
            raise ValueError("stratum_weight_invalid")
        for name in ("baseline_rate", "candidate_rate", "discordance_rate"):
            value = getattr(self, name)
            if (type(value) not in (int, float)
                    or type(value) is float and not math.isfinite(value)
                    or not 0 <= value <= 1):
                raise ValueError(f"{name}_invalid")
        if (type(self.source_group_icc) not in (int, float)
                or type(self.source_group_icc) is float
                and not math.isfinite(self.source_group_icc)
                or not 0 <= self.source_group_icc < 1):
            raise ValueError("source_group_icc_invalid")
        if type(self.source_group_count) is not int or self.source_group_count < 2:
            raise ValueError("source_group_count_invalid")
        try:
            counts = tuple(self.case_counts)
        except TypeError as error:
            raise ValueError("case_counts_invalid") from error
        if (not counts or any(type(n) is not int or not 0 < n <= _MAX_CASES_PER_SCENARIO
                              for n in counts)):
            raise ValueError("case_counts_invalid")
        if tuple(sorted(set(counts))) != counts:
            raise ValueError("case_counts_must_be_unique_and_increasing")
        if counts[0] < self.source_group_count:
            raise ValueError("case_count_below_source_groups")
        _paired_probabilities(self.baseline_rate, self.candidate_rate, self.discordance_rate)
        object.__setattr__(self, "case_counts", counts)


@dataclass(frozen=True)
class StratifiedPrimaryPowerSpec:
    strata: tuple[PowerStratum, ...] | list[PowerStratum | dict]
    minimum_observed_gain: float
    confidence_level: float
    target_power: float
    simulation_trials: int
    bootstrap_resamples: int
    seed: int

    def __post_init__(self):
        try:
            strata = tuple(
                item if isinstance(item, PowerStratum) else PowerStratum(**item)
                for item in self.strata
            )
        except (TypeError, ValueError) as error:
            if isinstance(error, ValueError):
                raise
            raise ValueError("strata_invalid") from error
        if len(strata) < 2:
            raise ValueError("strata_insufficient")
        ids = [stratum.stratum_id for stratum in strata]
        if len(ids) != len(set(ids)):
            raise ValueError("stratum_id_duplicate")
        if sum((Decimal(str(stratum.weight)) for stratum in strata), Decimal(0)) != Decimal(1):
            raise ValueError("stratum_weights_must_sum_to_one")
        if len({len(stratum.case_counts) for stratum in strata}) != 1:
            raise ValueError("stratum_case_count_grid_mismatch")
        for scenario_index in range(len(strata[0].case_counts)):
            if sum(item.case_counts[scenario_index] for item in strata) > _MAX_CASES_PER_SCENARIO:
                raise ValueError("scenario_case_count_exceeds_limit")
        for name in ("minimum_observed_gain", "confidence_level", "target_power"):
            value = getattr(self, name)
            if (type(value) not in (int, float)
                    or type(value) is float and not math.isfinite(value)
                    or not 0 <= value <= 1):
                raise ValueError(f"{name}_invalid")
        if not 0 < self.confidence_level < 1:
            raise ValueError("confidence_level_invalid")
        if not 0 < self.target_power < 1:
            raise ValueError("target_power_invalid")
        if (type(self.simulation_trials) is not int
                or not 20 <= self.simulation_trials <= _MAX_SIMULATION_TRIALS):
            raise ValueError("simulation_trials_invalid")
        if (type(self.bootstrap_resamples) is not int
                or not 99 <= self.bootstrap_resamples <= _MAX_BOOTSTRAP_RESAMPLES):
            raise ValueError("bootstrap_resamples_invalid")
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError("seed_invalid")
        scenario_count = len(strata[0].case_counts)
        total_groups = sum(stratum.source_group_count for stratum in strata)
        if scenario_count * self.simulation_trials * self.bootstrap_resamples * total_groups > _MAX_RESAMPLED_GROUP_DRAWS:
            raise ValueError("simulation_work_limit_exceeded")
        if sum(sum(stratum.case_counts) for stratum in strata) * self.simulation_trials > _MAX_RESAMPLED_GROUP_DRAWS:
            raise ValueError("simulation_case_work_limit_exceeded")
        object.__setattr__(self, "strata", strata)


def _paired_probabilities(baseline_rate, candidate_rate, discordance_rate):
    p_a = Decimal(str(baseline_rate))
    p_b = Decimal(str(candidate_rate))
    q = Decimal(str(discordance_rate))
    probabilities = (
        (p_a + p_b - q) / 2,
        (p_a - p_b + q) / 2,
        (p_b - p_a + q) / 2,
        Decimal(1) - (p_a + p_b + q) / 2,
    )
    if any(probability < 0 for probability in probabilities):
        raise ValueError("paired_marginals_infeasible")
    return tuple(float(probability) for probability in probabilities)


def simulate_primary_power(spec: PrimaryPowerSpec):
    """Estimate primary-gate power under explicit paired and cluster assumptions.

    Cluster dependence is modeled as a Dirichlet-multinomial random effect on
    the four paired outcomes. Power is the chance that both the observed gain
    threshold and the grouped-bootstrap lower confidence bound pass.
    """
    rng = random.Random(spec.seed)
    pair_probabilities = spec._pair_probabilities()
    scenarios = []
    for case_count in spec.case_counts:
        group_sizes = _balanced_group_sizes(case_count, spec.source_group_count)
        passed = 0
        for _ in range(spec.simulation_trials):
            groups = [
                _simulate_group(size, pair_probabilities, spec.source_group_icc, rng)
                for size in group_sizes
            ]
            point = _paired_rate_difference(groups)
            lower = _group_bootstrap_lower_bound(
                groups, spec.confidence_level, spec.bootstrap_resamples, rng
            )
            if point >= spec.minimum_observed_gain and lower > 0:
                passed += 1
        power, interval = _wilson_interval(passed, spec.simulation_trials)
        scenarios.append({
            "case_count": case_count,
            "source_group_count": spec.source_group_count,
            "power": power,
            "power_ci_95": interval,
            "target_power_supported": interval[0] >= spec.target_power,
        })

    return {
        "schema_version": "1",
        "engine_version": _ENGINE_VERSION,
        "spec_sha256": digest_json(asdict(spec)),
        "result_type": "scenario_only_primary_power",
        "scope": "primary_gain_gate_only",
        "formal_power_claim_permitted": False,
        "run_authorized": False,
        "method": "dirichlet_multinomial_grouped_paired_bootstrap",
        "assumptions": {
            "baseline_rate": spec.baseline_rate,
            "candidate_rate": spec.candidate_rate,
            "discordance_rate": spec.discordance_rate,
            "paired_outcome_probabilities": dict(zip(_PAIR_CATEGORIES, pair_probabilities)),
            "source_group_icc": spec.source_group_icc,
            "source_group_count": spec.source_group_count,
            "source_groups_balanced_within_one_stratum": True,
            "source_group_icc_interpretation": (
                "within-group correlation of paired outcome-category indicators"
            ),
            "minimum_observed_gain": spec.minimum_observed_gain,
            "confidence_level": spec.confidence_level,
            "target_power": spec.target_power,
            "simulation_trials": spec.simulation_trials,
            "bootstrap_resamples": spec.bootstrap_resamples,
            "seed": spec.seed,
        },
        "limitations": [
            "does_not_model_secondary_quality_gates_or_severe_errors",
            "does_not_model_strata_or_preregistered_task_weights",
            "requires_independent_pilot_inputs_for_formal_sample_justification",
        ],
        "scenarios": scenarios,
    }


def simulate_stratified_primary_power(spec: StratifiedPrimaryPowerSpec):
    """Estimate primary-gate scenario power with preregistered stratum weights."""
    rng = random.Random(spec.seed)
    scenarios = []
    for scenario_index in range(len(spec.strata[0].case_counts)):
        groups_by_stratum = []
        for stratum in spec.strata:
            count = stratum.case_counts[scenario_index]
            sizes = _balanced_group_sizes(count, stratum.source_group_count)
            groups_by_stratum.append((
                stratum,
                _paired_probabilities(
                    stratum.baseline_rate,
                    stratum.candidate_rate,
                    stratum.discordance_rate,
                ),
                sizes,
            ))

        passed = 0
        for _ in range(spec.simulation_trials):
            observed = [
                (stratum.weight, [
                    _simulate_group(size, probabilities, stratum.source_group_icc, rng)
                    for size in sizes
                ])
                for stratum, probabilities, sizes in groups_by_stratum
            ]
            point = _weighted_stratified_difference(observed)
            lower = _stratified_bootstrap_lower_bound(
                observed, spec.confidence_level, spec.bootstrap_resamples, rng
            )
            if point >= spec.minimum_observed_gain and lower > 0:
                passed += 1

        power, interval = _wilson_interval(passed, spec.simulation_trials)
        scenarios.append({
            "case_count": sum(item[0].case_counts[scenario_index] for item in groups_by_stratum),
            "source_group_count": sum(item[0].source_group_count for item in groups_by_stratum),
            "case_counts_by_stratum": {
                item[0].stratum_id: item[0].case_counts[scenario_index]
                for item in groups_by_stratum
            },
            "power": power,
            "power_ci_95": interval,
            "target_power_supported": interval[0] >= spec.target_power,
        })

    return {
        "schema_version": "1",
        "engine_version": "stratified-primary-power-scenario-v1",
        "spec_sha256": digest_json(asdict(spec)),
        "result_type": "scenario_only_stratified_primary_power",
        "scope": "primary_gain_gate_only",
        "formal_power_claim_permitted": False,
        "run_authorized": False,
        "method": "weighted_grouped_paired_bootstrap_within_strata",
        "assumptions": {
            "stratum_weights": {item.stratum_id: item.weight for item in spec.strata},
            "source_groups_disjoint_across_strata": True,
            "strata": [{
                "stratum_id": item.stratum_id,
                "weight": item.weight,
                "baseline_rate": item.baseline_rate,
                "candidate_rate": item.candidate_rate,
                "discordance_rate": item.discordance_rate,
                "paired_outcome_probabilities": dict(zip(
                    _PAIR_CATEGORIES,
                    _paired_probabilities(
                        item.baseline_rate, item.candidate_rate, item.discordance_rate
                    ),
                )),
                "source_group_icc": item.source_group_icc,
                "source_group_count": item.source_group_count,
                "case_counts": list(item.case_counts),
            } for item in spec.strata],
            "bootstrap_within_each_stratum": True,
            "minimum_observed_gain": spec.minimum_observed_gain,
            "confidence_level": spec.confidence_level,
            "target_power": spec.target_power,
            "simulation_trials": spec.simulation_trials,
            "bootstrap_resamples": spec.bootstrap_resamples,
            "seed": spec.seed,
        },
        "limitations": [
            "does_not_model_secondary_quality_gates_or_severe_errors",
            "does_not_model_within_stratum_task_weights",
            "requires_independent_pilot_inputs_for_formal_sample_justification",
        ],
        "scenarios": scenarios,
    }


def _balanced_group_sizes(case_count, group_count):
    base, remainder = divmod(case_count, group_count)
    return [base + (index < remainder) for index in range(group_count)]


def _simulate_group(size, pair_probabilities, icc, rng):
    probabilities = pair_probabilities
    if icc:
        concentration = (1 - icc) / icc
        draws = [
            rng.gammavariate(probability * concentration, 1)
            if probability > 0 else 0
            for probability in pair_probabilities
        ]
        total = sum(draws)
        if total > 0:
            probabilities = tuple(value / total for value in draws)

    counts = [0, 0, 0, 0]
    for outcome in rng.choices(range(4), weights=probabilities, k=size):
        counts[outcome] += 1
    both, a_only, b_only, _ = counts
    return size, both + a_only, both + b_only


def _paired_rate_difference(groups):
    total = sum(group[0] for group in groups)
    return (sum(group[2] - group[1] for group in groups)) / total


def _weighted_stratified_difference(strata):
    return sum(weight * _paired_rate_difference(groups) for weight, groups in strata)


def _stratified_bootstrap_lower_bound(strata, confidence_level, resamples, rng):
    differences = []
    for _ in range(resamples):
        sample = []
        for weight, groups in strata:
            selected = [groups[index] for index in rng.choices(range(len(groups)), k=len(groups))]
            sample.append((weight, selected))
        differences.append(_weighted_stratified_difference(sample))
    differences.sort()
    alpha = (1 - confidence_level) / 2
    return differences[max(0, math.floor(alpha * (resamples - 1)))]


def _group_bootstrap_lower_bound(groups, confidence_level, resamples, rng):
    group_count = len(groups)
    differences = []
    for _ in range(resamples):
        selected = [groups[index] for index in rng.choices(range(group_count), k=group_count)]
        total = sum(group[0] for group in selected)
        differences.append(
            sum(group[2] - group[1] for group in selected) / total
        )
    differences.sort()
    alpha = (1 - confidence_level) / 2
    return differences[max(0, math.floor(alpha * (resamples - 1)))]


def _wilson_interval(successes, trials):
    z = _MC_Z_95
    proportion = successes / trials
    denominator = 1 + z * z / trials
    center = (proportion + z * z / (2 * trials)) / denominator
    margin = z * math.sqrt(
        proportion * (1 - proportion) / trials + z * z / (4 * trials * trials)
    ) / denominator
    return proportion, (max(0.0, center - margin), min(1.0, center + margin))


def zero_event_upper_bound(independent_group_count, confidence_level):
    """One-sided exact binomial upper bound after zero events in independent groups.

    The event is counted at most once per source group. This is a planning
    calculation, not a replacement for complete severe-event audit coverage.
    """
    if type(independent_group_count) is not int or independent_group_count < 1:
        raise ValueError("group_count_invalid")
    if (type(confidence_level) not in (int, float)
            or type(confidence_level) is float and not math.isfinite(confidence_level)
            or not 0 < confidence_level < 1):
        raise ValueError("confidence_level_invalid")
    return -math.expm1(math.log1p(-confidence_level) / independent_group_count)


def minimum_zero_event_groups(maximum_rate, confidence_level):
    """Groups needed for a zero-event upper bound no greater than maximum_rate."""
    if (type(maximum_rate) not in (int, float)
            or type(maximum_rate) is float and not math.isfinite(maximum_rate)
            or not 0 < maximum_rate < 1):
        raise ValueError("maximum_rate_invalid")
    if (type(confidence_level) not in (int, float)
            or type(confidence_level) is float and not math.isfinite(confidence_level)
            or not 0 < confidence_level < 1):
        raise ValueError("confidence_level_invalid")
    return math.ceil(math.log1p(-confidence_level) / math.log1p(-maximum_rate))

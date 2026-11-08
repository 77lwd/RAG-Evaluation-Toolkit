"""Scenario-only operating characteristics for the complete RAG gate set."""

from dataclasses import asdict, dataclass
from decimal import Decimal
import math
import random

from .artifacts import digest_json
from .power import _wilson_interval


_ENGINE_VERSION = "joint-gate-pilot-group-bootstrap-v1"
_METRICS = (
    "supported_complete_answer_rate", "correct_refusal_rate", "false_refusal_rate",
    "citation_support", "important_claim_coverage", "p95_latency",
    "mean_per_task_cost", "serious_errors",
)
_GATE_THRESHOLD_NAMES = {
    "primary_gain_minimum_point",
    "correct_refusal_difference_lower",
    "false_refusal_difference_upper",
    "citation_support_difference_lower",
    "important_claim_coverage_difference_lower",
    "p95_latency_ratio_max",
    "mean_per_task_cost_ratio_max",
}
_ANSWERABILITY = {"answerable", "insufficient_source", "partial", "ambiguous"}
_MAX_CASES = 100_000
_MAX_WORK = 20_000_000


@dataclass(frozen=True)
class JointPowerSpec:
    pilot_records: tuple[dict, ...] | list[dict]
    stratum_weights: dict[str, float]
    source_group_counts_by_stratum: dict[str, tuple[int, ...] | list[int]]
    gate_thresholds: dict[str, float]
    simulation_trials: int
    bootstrap_resamples: int
    confidence_level: float
    target_power: float
    seed: int
    audit_complete: bool

    def __post_init__(self):
        if not isinstance(self.pilot_records, (list, tuple)) or not self.pilot_records:
            raise ValueError("pilot_records_invalid")
        if not isinstance(self.stratum_weights, dict) or not self.stratum_weights:
            raise ValueError("stratum_weights_invalid")
        if set(self.source_group_counts_by_stratum) != set(self.stratum_weights):
            raise ValueError("group_count_strata_mismatch")
        weight_total = sum((Decimal(str(value)) for value in self.stratum_weights.values()), Decimal(0))
        if weight_total != Decimal(1):
            raise ValueError("stratum_weights_must_sum_to_one")
        for stratum, weight in self.stratum_weights.items():
            if (not isinstance(stratum, str) or not stratum
                    or type(weight) not in (int, float)
                    or type(weight) is float and not math.isfinite(weight)
                    or weight <= 0 or weight > 1):
                raise ValueError("stratum_weights_invalid")
        if (not isinstance(self.gate_thresholds, dict)
                or set(self.gate_thresholds) != _GATE_THRESHOLD_NAMES):
            raise ValueError("gate_thresholds_invalid")
        for name, value in self.gate_thresholds.items():
            if name.endswith("_ratio_max"):
                valid = _finite_number(value, 0) and value > 0
            elif name == "primary_gain_minimum_point":
                valid = _finite_number(value, 0, 1)
            else:
                valid = _finite_number(value, -1, 1)
            if not valid:
                raise ValueError("gate_thresholds_invalid")
        group_grids = {}
        for stratum in self.stratum_weights:
            groups = tuple(self.source_group_counts_by_stratum[stratum])
            if (not groups or any(type(value) is not int or value < 2 or value > _MAX_CASES
                                  for value in groups)):
                raise ValueError("sample_size_grid_invalid")
            if tuple(sorted(set(groups))) != groups:
                raise ValueError("sample_size_grid_must_be_increasing")
            group_grids[stratum] = groups
        if len({len(values) for values in group_grids.values()}) != 1:
            raise ValueError("scenario_grid_mismatch")
        if type(self.simulation_trials) is not int or not 20 <= self.simulation_trials <= 100_000:
            raise ValueError("simulation_trials_invalid")
        if type(self.bootstrap_resamples) is not int or not 99 <= self.bootstrap_resamples <= 10_000:
            raise ValueError("bootstrap_resamples_invalid")
        for field in ("confidence_level", "target_power"):
            value = getattr(self, field)
            if (type(value) not in (int, float)
                    or type(value) is float and not math.isfinite(value)
                    or not 0 < value < 1):
                raise ValueError(f"{field}_invalid")
        if type(self.seed) is not int or self.seed < 0:
            raise ValueError("seed_invalid")
        if type(self.audit_complete) is not bool:
            raise ValueError("audit_complete_invalid")

        normalized = []
        case_ids = set()
        groups_by_stratum = {name: {} for name in self.stratum_weights}
        group_strata = {}
        for record in self.pilot_records:
            _validate_record(record, case_ids, groups_by_stratum, group_strata)
            normalized.append(record)
        for stratum, groups in groups_by_stratum.items():
            if len(groups) < 2:
                raise ValueError("pilot_independent_groups_insufficient")
        max_group_sizes = {
            stratum: max(len(group) for group in groups.values())
            for stratum, groups in groups_by_stratum.items()
        }
        for index in range(len(next(iter(group_grids.values())))):
            work = self.simulation_trials * self.bootstrap_resamples * sum(
                groups[index] for groups in group_grids.values()
            )
            if work > _MAX_WORK:
                raise ValueError("simulation_work_limit_exceeded")
            case_work = self.simulation_trials * sum(
                groups[index] * max_group_sizes[stratum]
                for stratum, groups in group_grids.items()
            )
            if case_work > _MAX_WORK:
                raise ValueError("simulation_case_work_limit_exceeded")
        object.__setattr__(self, "pilot_records", tuple(normalized))
        object.__setattr__(self, "source_group_counts_by_stratum", group_grids)


def _finite_number(value, minimum, maximum=None):
    return (type(value) in (int, float)
            and (type(value) is not float or math.isfinite(value))
            and value >= minimum
            and (maximum is None or value <= maximum))


def _validate_record(record, case_ids, groups_by_stratum, group_strata):
    if not isinstance(record, dict):
        raise ValueError("pilot_record_invalid")
    required = {"case_id", "source_group_id", "stratum_id", "weight", "answerability", "A", "B"}
    if set(record) != required:
        raise ValueError("pilot_record_fields_invalid")
    case_id = record["case_id"]
    group_id = record["source_group_id"]
    stratum = record["stratum_id"]
    if (not isinstance(case_id, str) or not case_id or case_id in case_ids
            or not isinstance(group_id, str) or not group_id
            or stratum not in groups_by_stratum):
        raise ValueError("pilot_record_identity_invalid")
    case_ids.add(case_id)
    if not _finite_number(record["weight"], 0) or record["weight"] == 0:
        raise ValueError("case_weight_invalid")
    if record["answerability"] not in _ANSWERABILITY:
        raise ValueError("answerability_invalid")
    groups = groups_by_stratum[stratum]
    if group_id in group_strata and group_strata[group_id] != stratum:
        raise ValueError("source_group_cross_stratum")
    group_strata[group_id] = stratum
    groups.setdefault(group_id, []).append(record)
    for arm in ("A", "B"):
        scores = record[arm]
        fields = {
            "supported_complete", "correct_refusal", "false_refusal", "citation_support",
            "important_claim_coverage", "latency_seconds", "cost", "serious_errors",
        }
        if not isinstance(scores, dict):
            raise ValueError("case_metric_fields_invalid")
        if fields - scores.keys():
            raise ValueError("case_metric_missing")
        if scores.keys() - fields:
            raise ValueError("case_metric_fields_invalid")
        for metric in ("supported_complete", "correct_refusal", "false_refusal"):
            value = scores[metric]
            if value is not None and type(value) is not bool:
                raise ValueError("binary_metric_invalid")
            applicable = (
                metric in ("supported_complete", "false_refusal")
                and record["answerability"] == "answerable"
            ) or (
                metric == "correct_refusal"
                and record["answerability"] == "insufficient_source"
            )
            if (applicable and value is None) or (not applicable and value is not None):
                raise ValueError("applicable_metric_missing" if applicable else
                                 "nonapplicable_metric_present")
        for metric in ("citation_support", "important_claim_coverage"):
            value = scores[metric]
            if value is not None and not _finite_number(value, 0, 1):
                raise ValueError("rate_metric_invalid")
        if not _finite_number(scores["latency_seconds"], 0) or scores["latency_seconds"] == 0:
            raise ValueError("latency_invalid")
        if not _finite_number(scores["cost"], 0):
            raise ValueError("cost_invalid")
        if type(scores["serious_errors"]) is not int or scores["serious_errors"] < 0:
            raise ValueError("serious_error_count_invalid")


def _group_records(records, stratum):
    groups = {}
    for record in records:
        if record["stratum_id"] == stratum:
            groups.setdefault(record["source_group_id"], []).append(record)
    return [groups[key] for key in sorted(groups)]


def _draw_stratum(pilot_groups, group_count, rng):
    return [pilot_groups[rng.randrange(len(pilot_groups))] for _ in range(group_count)]


def _sample_groups(spec, scenario_index, rng):
    return {
        stratum: _draw_stratum(
            _group_records(spec.pilot_records, stratum),
            spec.source_group_counts_by_stratum[stratum][scenario_index],
            rng,
        )
        for stratum in sorted(spec.stratum_weights)
    }


def _bootstrap_groups(groups_by_stratum, rng):
    return {
        stratum: [groups[index] for index in [rng.randrange(len(groups)) for _ in groups]]
        for stratum, groups in groups_by_stratum.items()
    }


def _weighted_mean(records, arm, metric, stratum_weights, applicable=None):
    values_by_stratum = {}
    for stratum, target_weight in stratum_weights.items():
        selected = [record for record in records.get(stratum, ())
                    if (applicable is None or applicable(record))
                    and record[arm][metric] is not None]
        denominator = sum(record["weight"] for record in selected)
        if denominator <= 0:
            return None
        values_by_stratum[stratum] = sum(
            record["weight"] * float(record[arm][metric]) for record in selected
        ) / denominator
    return sum(stratum_weights[stratum] * value for stratum, value in values_by_stratum.items())


def _weighted_p95(records, arm, stratum_weights):
    weighted = []
    for stratum, target_weight in stratum_weights.items():
        rows = records.get(stratum, ())
        denominator = sum(record["weight"] for record in rows)
        if denominator <= 0:
            return None
        weighted.extend((
            record[arm]["latency_seconds"], target_weight * record["weight"] / denominator
        ) for record in rows)
    weighted.sort(key=lambda item: item[0])
    cumulative = 0.0
    for value, weight in weighted:
        cumulative += weight
        if cumulative >= 0.95:
            return value
    return weighted[-1][0] if weighted else None


def _flatten(groups_by_stratum):
    return {stratum: [record for group in groups for record in group]
            for stratum, groups in groups_by_stratum.items()}


def _statistics(groups_by_stratum, stratum_weights):
    records = _flatten(groups_by_stratum)
    answerable = lambda record: record["answerability"] == "answerable"
    unsupported = lambda record: record["answerability"] == "insufficient_source"
    stats = {}
    for metric, field, applicable in (
        ("supported_complete_answer_rate", "supported_complete", answerable),
        ("correct_refusal_rate", "correct_refusal", unsupported),
        ("false_refusal_rate", "false_refusal", answerable),
        ("citation_support", "citation_support", None),
        ("important_claim_coverage", "important_claim_coverage", None),
        ("mean_per_task_cost", "cost", None),
    ):
        a = _weighted_mean(records, "A", field, stratum_weights, applicable)
        b = _weighted_mean(records, "B", field, stratum_weights, applicable)
        stats[metric] = None if a is None or b is None else {"A": a, "B": b, "difference": b - a}
    p95_a = _weighted_p95(records, "A", stratum_weights)
    p95_b = _weighted_p95(records, "B", stratum_weights)
    stats["p95_latency"] = (
        None if p95_a is None or p95_b is None or p95_a <= 0
        else {"A": p95_a, "B": p95_b, "ratio": p95_b / p95_a}
    )
    cost = stats["mean_per_task_cost"]
    stats["mean_per_task_cost"] = (
        None if cost is None or cost["A"] <= 0
        else {**cost, "ratio": cost["B"] / cost["A"]}
    )
    stats["serious_errors"] = {
        arm: sum(record[arm]["serious_errors"] for rows in records.values() for record in rows)
        for arm in ("A", "B")
    }
    return stats


def _percentile_interval(values, confidence_level):
    if not values or any(not math.isfinite(value) for value in values):
        return None
    ordered = sorted(values)
    alpha = (1 - confidence_level) / 2
    interval = (
        ordered[math.floor(alpha * (len(ordered) - 1))],
        ordered[math.ceil((1 - alpha) * (len(ordered) - 1))],
    )
    return None if interval[0] == interval[1] else interval


def _trial_gate_report(point, bootstrap_stats, confidence_level, gate_thresholds):
    intervals = {}
    for metric in _METRICS[:-1]:
        point_value = point.get(metric)
        draws = [sample.get(metric) for sample in bootstrap_stats]
        if point_value is None or any(draw is None for draw in draws):
            intervals[metric] = None
            continue
        if metric in ("p95_latency", "mean_per_task_cost"):
            key = "ratio"
        else:
            key = "difference"
        interval = _percentile_interval([draw[key] for draw in draws], confidence_level)
        intervals[metric] = interval

    gate_checks = {}
    values = point
    ci = intervals
    gate_checks["supported_complete_answer_rate"] = (
        values["supported_complete_answer_rate"] is not None
        and values["supported_complete_answer_rate"]["difference"]
        >= gate_thresholds["primary_gain_minimum_point"]
        and ci["supported_complete_answer_rate"] is not None
        and ci["supported_complete_answer_rate"][0] > 0
    )
    for name, threshold_name in (
        ("correct_refusal_rate", "correct_refusal_difference_lower"),
        ("citation_support", "citation_support_difference_lower"),
        ("important_claim_coverage", "important_claim_coverage_difference_lower"),
    ):
        gate_checks[name] = (
            values[name] is not None and ci[name] is not None
            and ci[name][0] >= gate_thresholds[threshold_name]
        )
    gate_checks["false_refusal_rate"] = (
        values["false_refusal_rate"] is not None and ci["false_refusal_rate"] is not None
        and ci["false_refusal_rate"][1]
        <= gate_thresholds["false_refusal_difference_upper"]
    )
    gate_checks["p95_latency"] = (
        values["p95_latency"] is not None
        and values["p95_latency"]["ratio"] <= gate_thresholds["p95_latency_ratio_max"]
        and ci["p95_latency"] is not None
        and ci["p95_latency"][1] <= gate_thresholds["p95_latency_ratio_max"]
    )
    gate_checks["mean_per_task_cost"] = (
        values["mean_per_task_cost"] is not None
        and values["mean_per_task_cost"]["ratio"]
        <= gate_thresholds["mean_per_task_cost_ratio_max"]
        and ci["mean_per_task_cost"] is not None
        and ci["mean_per_task_cost"][1]
        <= gate_thresholds["mean_per_task_cost_ratio_max"]
    )
    gate_checks["serious_errors"] = (
        values["serious_errors"]["A"] == 0 and values["serious_errors"]["B"] == 0
    )
    estimable = all(intervals[metric] is not None for metric in _METRICS[:-1])
    return gate_checks, intervals, estimable


def simulate_joint_gate_power(spec: JointPowerSpec):
    """Estimate the chance all frozen gates pass under pilot-profile resampling.

    Pilot source groups are resampled with replacement inside each stratum,
    retaining every paired case in a selected group. Thus case counts vary with
    the empirical cluster-size distribution. This is an explicit empirical
    scenario model and is not a formal sample-size claim.
    """
    rng = random.Random(spec.seed)
    scenarios = []
    pilot_group_counts = {
        stratum: len(_group_records(spec.pilot_records, stratum))
        for stratum in spec.stratum_weights
    }
    for scenario_index in range(len(next(iter(spec.source_group_counts_by_stratum.values())))):
        joint_passes = 0
        valid_analyses = 0
        per_gate_passes = {metric: 0 for metric in _METRICS}
        observed_case_counts = []
        for _ in range(spec.simulation_trials):
            groups = _sample_groups(spec, scenario_index, rng)
            observed_case_counts.append(sum(
                len(group) for selected in groups.values() for group in selected
            ))
            point = _statistics(groups, spec.stratum_weights)
            bootstrap_stats = [
                _statistics(_bootstrap_groups(groups, rng), spec.stratum_weights)
                for _ in range(spec.bootstrap_resamples)
            ]
            gate_checks, _, estimable = _trial_gate_report(
                point, bootstrap_stats, spec.confidence_level, spec.gate_thresholds
            )
            gate_checks["serious_errors"] = (
                gate_checks["serious_errors"] and spec.audit_complete
            )
            for name, passed in gate_checks.items():
                per_gate_passes[name] += int(passed)
            if estimable and spec.audit_complete:
                valid_analyses += 1
            passed_jointly = estimable and spec.audit_complete and all(gate_checks.values())
            joint_passes += int(passed_jointly)

        joint_power, joint_interval = _wilson_interval(joint_passes, spec.simulation_trials)
        valid_rate, valid_interval = _wilson_interval(valid_analyses, spec.simulation_trials)
        gate_power = {}
        for metric, passed in per_gate_passes.items():
            estimate, interval = _wilson_interval(passed, spec.simulation_trials)
            gate_power[metric] = {"power": estimate, "power_ci_95": interval}
        groups_by_stratum = {
            stratum: spec.source_group_counts_by_stratum[stratum][scenario_index]
            for stratum in spec.stratum_weights
        }
        scenarios.append({
            "case_count_mean": sum(observed_case_counts) / len(observed_case_counts),
            "case_count_range": [min(observed_case_counts), max(observed_case_counts)],
            "source_group_count": sum(groups_by_stratum.values()),
            "source_group_counts_by_stratum": groups_by_stratum,
            "joint_gate_power": joint_power,
            "joint_gate_power_ci_95": joint_interval,
            "valid_analysis_rate": valid_rate,
            "valid_analysis_rate_ci_95": valid_interval,
            "target_power_supported": (
                joint_interval[0] >= spec.target_power and spec.audit_complete
            ),
            "joint_power_interpretable": spec.audit_complete and valid_analyses > 0,
            "gate_power": gate_power,
        })
    return {
        "schema_version": "1",
        "engine_version": _ENGINE_VERSION,
        "spec_sha256": digest_json(asdict(spec)),
        "result_type": "scenario_only_joint_gate_power",
        "scope": "all_confirmatory_gates",
        "formal_power_claim_permitted": False,
        "run_authorized": False,
        "method": "nested_stratified_grouped_paired_bootstrap",
        "assumptions": {
            "pilot_group_counts_by_stratum": pilot_group_counts,
            "stratum_weights": dict(spec.stratum_weights),
            "source_group_counts_by_stratum": {
                name: list(values) for name, values in spec.source_group_counts_by_stratum.items()
            },
            "confidence_level": spec.confidence_level,
            "target_power": spec.target_power,
            "simulation_trials": spec.simulation_trials,
            "bootstrap_resamples": spec.bootstrap_resamples,
            "seed": spec.seed,
            "audit_complete": spec.audit_complete,
            "gate_thresholds": dict(spec.gate_thresholds),
            "serious_error_maximum": 0,
        },
        "limitations": [
            "pilot_profiles_define_the_assumed_joint_effect_and_dependence",
            "resampling_cannot_represent_source_families_absent_from_the_pilot",
            "does_not_replace_independent_pilot_provenance_or_protocol_approval",
            "serious_error_gate_requires_complete_audit_and_is_not_a_risk_zero_proof",
        ] + ([] if spec.audit_complete else ["serious_event_audit_incomplete"]),
        "scenarios": scenarios,
    }

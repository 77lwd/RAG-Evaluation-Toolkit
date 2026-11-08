"""Metadata admission only: never execute an experiment or infer a quality win."""

from pydantic import BaseModel

from .artifacts import ArtifactError, digest_json, verify_artifact
from .dataset import audit_data_flow, audit_dataset
from .models import (
    ArtifactRef,
    Dataset,
    Protocol,
    RecordRef,
    Snapshot,
    SystemSnapshotRef,
    Thresholds,
    parse_record,
)
from .registry import RegistryError
from .roles import audit_roles


MATERIALS = {
    "code", "effective_config", "embedding", "generation", "prompts",
    "corpus", "index", "ingestion", "environment", "runtime",
    "application", "retrieval", "index_builder", "data_import",
    "request_runner", "retry_recovery", "metric_aggregation",
    "statistical_analysis", "report_generation",
}
RULES = {
    "scope_and_sampling", "split_policy", "gold_rubric", "metrics_and_statistics",
    "run_plan", "review_plan", "serious_errors", "sample_size_justification",
    "retention_and_recovery",
}
METRIC_CONTRACT_VERSION = "v0.5"
REQUIRED_METRIC_GATES = {
    "primary_answer": {
        "unit": "rate", "numerator_definition": "complete supported answers",
        "denominator_definition": "answerable tasks", "scorer": "agent_dual_blind",
        "aggregation": "weighted_case_mean", "na_policy": "not_allowed",
        "decision": "primary_gain", "threshold_ref": "primary_gain",
    },
    "correct_refusal": {
        "unit": "rate", "numerator_definition": "correct refusals",
        "denominator_definition": "insufficient-source tasks", "scorer": "agent_dual_blind",
        "aggregation": "weighted_case_mean", "na_policy": "not_allowed",
        "decision": "lower_bound_noninferiority", "threshold_ref": "correct_refusal_loss",
    },
    "false_refusal": {
        "unit": "rate", "numerator_definition": "false refusals",
        "denominator_definition": "answerable tasks", "scorer": "agent_dual_blind",
        "aggregation": "weighted_case_mean", "na_policy": "not_allowed",
        "decision": "upper_bound_noninferiority", "threshold_ref": "false_refusal_increase",
    },
    "citation_support": {
        "unit": "rate", "numerator_definition": "supported cited claims",
        "denominator_definition": "cited factual claims", "scorer": "agent_dual_blind",
        "aggregation": "per_case_macro", "na_policy": "not_applicable_reported",
        "decision": "lower_bound_noninferiority", "threshold_ref": "citation_support_loss",
    },
    "p95_latency": {
        "unit": "seconds", "numerator_definition": None,
        "denominator_definition": "all planned tasks", "scorer": "automatic",
        "aggregation": "all_planned_task_p95", "na_policy": "not_allowed",
        "decision": "ratio_max", "threshold_ref": "p95_ratio_max",
    },
    "per_task_cost": {
        "unit": "currency", "numerator_definition": "total task cost",
        "denominator_definition": "all planned tasks", "scorer": "automatic",
        "aggregation": "mean_per_task_all_planned", "na_policy": "not_allowed",
        "decision": "ratio_max", "threshold_ref": "mean_cost_ratio_max",
    },
    "serious_errors": {
        "unit": "count", "numerator_definition": "adjudicated serious events",
        "denominator_definition": "all protocol-defined audit opportunities",
        "scorer": "adjudicated", "aggregation": "adjudicated_event_count",
        "na_policy": "not_allowed", "decision": "zero_tolerance", "threshold_ref": None,
    },
}
IMPORTANT_CLAIM_GATE_FIELDS = {
    "unit": "rate", "numerator_definition": "supported important claims",
    "denominator_definition": "all important claims", "scorer": "agent_dual_blind",
    "aggregation": "per_case_macro", "na_policy": "not_applicable_reported",
    "decision": "lower_bound_noninferiority",
    "threshold_ref": "important_claim_coverage_threshold",
}
REQUIRED_METRIC_NAMES = REQUIRED_METRIC_GATES.keys() | {"important_claim_coverage"}
REQUIRED_STOP_CONDITIONS = frozenset({
    "suspected_privacy_or_unauthorized_access",
    "unauthorized_data_transfer",
    "evidence_integrity_failure",
    "runtime_identity_mismatch",
    "resource_limit_exceeded",
    "max_model_calls_reached",
    "manual_emergency_stop",
})


def record_ref(document):
    reference_type = SystemSnapshotRef if document.kind == "system_snapshot" else RecordRef
    return reference_type(kind=document.kind, id=document.id, revision=document.revision,
                          sha256=digest_json(document.model_dump(mode="json")))


def artifacts(value):
    """Traverse typed metadata, without interpreting or opening artifact contents."""
    if isinstance(value, ArtifactRef):
        yield value
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            yield from artifacts(getattr(value, name))
    elif isinstance(value, dict):
        for item in value.values():
            yield from artifacts(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from artifacts(item)


def check_protocol(registry, reference, roots, *, mode="formal", expected_head=None):
    if mode not in ("formal", "local"):
        raise ValueError("invalid_admission_mode")
    report = {
        "schema_version": "1", "status": "blocked", "mode": mode,
        "run_authorized": False, "conclusion_scope": "unresolved",
        "protocol": reference.model_dump(mode="json"),
        "registry_head": None, "issues": [], "dataset_summary": None,
    }

    def issue(code, location):
        item = {"code": code, "location": location}
        if item not in report["issues"]:
            report["issues"].append(item)

    try:
        rows = registry.entries(expected_head=expected_head)
    except RegistryError:
        issue("registry_invalid", "registry")
        report["status"] = "invalid"
        return report
    report["registry_head"] = rows[-1]["sha256"] if rows else "0" * 64
    records = [parse_record(row["document"]) for row in rows]
    indexed = {(record.kind, record.id, record.revision): record for record in records}
    latest = {(record.kind, record.id): record for record in records}

    def resolve(ref, kind, location):
        if ref is None or ref.kind != kind:
            issue("reference_unresolved", location)
            return None
        document = indexed.get((ref.kind, ref.id, ref.revision))
        if document is None or record_ref(document) != ref:
            issue("reference_unresolved", location)
            return None
        if latest[(ref.kind, ref.id)].revision != ref.revision:
            issue("reference_superseded", location)
        return document

    plan = resolve(reference, "protocol", "protocol")
    if not isinstance(plan, Protocol):
        return report
    if plan.experiment == "local_reference_replay":
        report["conclusion_scope"] = "local_reference_only"
        if mode != "local":
            issue("local_reference_not_production", "protocol.experiment")
    elif plan.experiment == "production_replay":
        report["conclusion_scope"] = "production_snapshot_comparison"
        if mode == "local":
            issue("experiment_scope_mismatch", "protocol.experiment")
    else:
        report["conclusion_scope"] = "new_source_capability_only"
        if mode == "local":
            issue("experiment_scope_mismatch", "protocol.experiment")
    catalog = resolve(plan.dataset, "dataset", "protocol.dataset")
    expected_arms = {"A", "B" if plan.phase == "H1" else "C"}
    if set(plan.snapshots) != expected_arms:
        issue("comparison_arms_invalid", "protocol.snapshots")
    snapshots = {}
    for arm, ref in plan.snapshots.items():
        snapshot = resolve(ref, "snapshot", f"snapshots.{arm}")
        if not isinstance(snapshot, Snapshot):
            continue
        snapshots[arm] = snapshot
        if snapshot.arm != arm:
            issue("snapshot_arm_mismatch", f"snapshots.{arm}")
        identity = {"A": "production", "B": "candidate", "C": "integrated"}[arm]
        if snapshot.identity != identity:
            is_local_reference = arm == "A" and snapshot.identity == "local_reference"
            if is_local_reference and plan.experiment == "local_reference_replay":
                pass
            else:
                if is_local_reference:
                    report["conclusion_scope"] = "invalid_scope_mismatch"
                issue("local_reference_not_production" if is_local_reference else
                      "production_identity_required" if arm == "A" else
                      "snapshot_identity_invalid", f"snapshots.{arm}")
        if snapshot.git_commit is None or snapshot.dirty is None or snapshot.git_branch is None:
            issue("checkout_identity_missing", f"snapshots.{arm}")
        if not snapshot.runtime_verified:
            issue("runtime_identity_unverified", f"snapshots.{arm}")
        for name in sorted(MATERIALS):
            if not snapshot.materials.get(name):
                issue("snapshot_material_missing", f"snapshots.{arm}.{name}")

    for name in sorted(RULES - plan.rules.keys()):
        issue("policy_missing", f"rules.{name}")
    for name, value in plan.thresholds.model_dump().items():
        if value is None:
            issue("threshold_missing", f"thresholds.{name}")
    budget = plan.budget
    if budget.monetary_limit is None:
        issue("budget_missing", "budget.monetary_limit")
    if budget.monetary_limit == "bounded":
        for name in ("per_task_cost", "total_cost", "currency"):
            if getattr(budget, name) is None:
                issue("budget_missing", f"budget.{name}")
    elif budget.monetary_limit == "unbounded":
        for name in ("per_task_cost", "total_cost"):
            if getattr(budget, name) is not None:
                issue("budget_unbounded_conflict", f"budget.{name}")
        if budget.currency is None:
            issue("budget_missing", "budget.currency")
    for name in ("absolute_p95_seconds", "memory_mb", "concurrency",
                 "task_timeout_seconds", "retries", "max_model_calls"):
        if getattr(budget, name) is None:
            issue("budget_missing", f"budget.{name}")
    for condition in sorted(REQUIRED_STOP_CONDITIONS - set(budget.stop_conditions)):
        issue("stop_condition_missing", f"budget.stop_conditions.{condition}")
    if plan.known_results_disclosure == "unknown":
        issue("results_disclosure_required", "known_results_disclosure")
    if plan.revision > 1 and (plan.supersedes is None or not plan.change_reason):
        issue("protocol_change_history_missing", "supersedes")
    if plan.supersedes is not None:
        previous = indexed.get(("protocol", plan.supersedes.id, plan.supersedes.revision))
        if (plan.supersedes.kind != "protocol" or previous is None
                or record_ref(previous) != plan.supersedes
                or previous.id != plan.id or previous.revision != plan.revision - 1):
            issue("protocol_change_reference_invalid", "supersedes")
    if mode == "formal" and (plan.synthetic or
                             isinstance(catalog, Dataset) and catalog.synthetic):
        issue("synthetic_not_formal", "protocol")
    report["issues"].extend(audit_roles(plan))

    analysis = plan.analysis
    if analysis.metric_contract_version != METRIC_CONTRACT_VERSION:
        issue("metric_contract_version_missing", "analysis.metric_contract_version")
    for name, expected in REQUIRED_METRIC_GATES.items():
        actual = analysis.metric_gates.get(name)
        if actual is None:
            issue("metric_gate_missing", f"analysis.metric_gates.{name}")
            continue
        if actual.model_dump(mode="json") != expected:
            issue("metric_gate_contract_mismatch", f"analysis.metric_gates.{name}")
        threshold_ref = actual.threshold_ref
        if threshold_ref is not None and (
            threshold_ref not in Thresholds.model_fields
            or getattr(plan.thresholds, threshold_ref) is None
        ):
            issue("metric_gate_threshold_unresolved",
                  f"analysis.metric_gates.{name}.threshold_ref")
    important_claim_gate = analysis.metric_gates.get("important_claim_coverage")
    if important_claim_gate is None:
        issue("metric_gate_missing", "analysis.metric_gates.important_claim_coverage")
    else:
        actual_fields = important_claim_gate.model_dump(mode="json")
        if any(actual_fields[field] != expected
               for field, expected in IMPORTANT_CLAIM_GATE_FIELDS.items()):
            issue("metric_gate_contract_mismatch",
                  "analysis.metric_gates.important_claim_coverage")
        threshold_ref = important_claim_gate.threshold_ref
        if threshold_ref is not None and (
            threshold_ref not in Thresholds.model_fields
            or getattr(plan.thresholds, threshold_ref) is None
        ):
            issue("metric_gate_threshold_unresolved",
                  "analysis.metric_gates.important_claim_coverage.threshold_ref")
    for name in analysis.metric_gates.keys() - REQUIRED_METRIC_NAMES:
        issue("metric_gate_unknown", f"analysis.metric_gates.{name}")
    if not 0 < analysis.confidence_level < 1:
        issue("confidence_level_invalid", "analysis.confidence_level")
    if analysis.target_power is None or not 0 < analysis.target_power < 1:
        issue("power_plan_missing", "analysis.target_power")
    if analysis.planned_source_groups is None or analysis.planned_source_groups < 2:
        issue("independent_groups_insufficient", "analysis.planned_source_groups")
    if not analysis.required_tags:
        issue("coverage_plan_missing", "analysis.required_tags")
    if not analysis.strata:
        issue("analysis_strata_plan_missing", "analysis.strata")
    for name in ("answerable", "insufficient_source", "partial", "ambiguous"):
        if analysis.planned_answerability.get(name, 0) == 0:
            issue("answerability_coverage_missing", f"analysis.planned_answerability.{name}")
    if isinstance(catalog, Dataset):
        summary = audit_dataset(
            catalog, [record for record in records if record.kind == "exposure"],
            phase=plan.phase,
            history=[record for record in records if isinstance(record, Dataset)
                     and record_ref(record) != plan.dataset],
        )
        report["issues"].extend(summary["issues"])
        report["dataset_summary"] = {key: value for key, value in summary.items() if key != "issues"}
        if analysis.planned_cases != summary["case_count"]:
            issue("sample_count_mismatch", "analysis.planned_cases")
        if analysis.planned_source_groups != summary["source_group_count"]:
            issue("source_group_count_mismatch", "analysis.planned_source_groups")
        planned_strata = {
            stratum_id: {
                "case_count": stratum.planned_cases,
                "source_group_count": stratum.planned_source_groups,
            }
            for stratum_id, stratum in analysis.strata.items()
        }
        if planned_strata != summary["strata"]:
            issue("analysis_strata_plan_mismatch", "analysis.strata")
        categories = {"answerable", "insufficient_source", "partial", "ambiguous"}
        if (set(analysis.planned_answerability) != categories or any(
            analysis.planned_answerability.get(name) != summary["answerability"].get(name, 0)
            for name in categories
        )):
            issue("answerability_plan_mismatch", "analysis.planned_answerability")
        if summary["answerability"].get("answerable", 0) == 0:
            issue("primary_denominator_empty", "dataset")
        for tag, minimum in analysis.required_tags.items():
            if summary["tags"].get(tag, 0) < minimum:
                issue("coverage_quota_unmet", f"analysis.required_tags.{tag}")
        if analysis.weighting == "equal_task" and any(
            case.weight != 1 for case in catalog.cases if case.partition == plan.phase
        ):
            issue("weighting_mismatch", "analysis.weighting")
        selected_source_ids = {
            source_id for case in catalog.cases if case.partition == plan.phase
            for source_id in case.source_ids
        }
        report["issues"].extend(audit_data_flow(
            catalog, plan.access, source_ids=selected_source_ids)["issues"])

    bound_approvals = []

    def approval(target, scope, actor):
        matching = [record for record in records if record.kind == "approval"
                    and record.target == target and record.scope == scope
                    and record.actor_id == actor]
        if not matching:
            issue("approval_missing", f"{target.kind}.{target.id}.{scope}")
            return
        final = matching[-1]
        bound_approvals.append(final)
        if final.actor_type != "human" or actor not in plan.people.human_ids:
            issue("approval_not_human", f"approvals.{final.id}")
        if final.decision != "approved":
            issue("approval_not_granted", f"approvals.{final.id}")
        if final.evidence.visibility != "public":
            issue("approval_receipt_not_public", f"approvals.{final.id}")

    for scope in ("scope", "quality", "budget", "roles", "access", "sampling", "protocol"):
        actor = plan.people.auditor if scope == "access" else plan.people.owner
        approval(reference, scope, actor)
    for arm, snapshot in snapshots.items():
        approval(plan.snapshots[arm], "identity", plan.people.auditor)

    # Restricted bodies remain in the custodian's storage. The receipt attests to
    # exact paths, sizes and hashes; this does not authenticate its human author.
    attested = {digest_json(ref.model_dump(mode="json"))
                for ref in plan.access.verified_restricted if ref.visibility == "restricted"}
    receipt = plan.access.verification_receipt
    if receipt is not None and receipt.visibility != "public":
        issue("access_receipt_not_public", "access.verification_receipt")
    checked = set()
    for document in [plan, catalog, *snapshots.values(), *bound_approvals]:
        for ref in artifacts(document):
            key = digest_json(ref.model_dump(mode="json"))
            if key in checked:
                continue
            checked.add(key)
            location = f"{ref.root_id}:{ref.path}"
            if ref.visibility == "restricted":
                if key not in attested:
                    issue("restricted_unattested", location)
            else:
                try:
                    verify_artifact(ref, roots)
                except (ArtifactError, ValueError, OSError):
                    issue("artifact_invalid", location)
    if not report["issues"]:
        report["status"] = ("ready_for_custodian_review" if mode == "formal"
                            else "local_validation_only")
    return report

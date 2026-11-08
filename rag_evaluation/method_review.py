"""Review method artifacts without granting protocol freeze or run permission."""

from .artifacts import ArtifactError, verify_artifact
from .gate import (
    IMPORTANT_CLAIM_GATE_FIELDS,
    METRIC_CONTRACT_VERSION,
    REQUIRED_METRIC_GATES,
    REQUIRED_METRIC_NAMES,
    RULES,
    record_ref,
)
from .models import Protocol, Thresholds, parse_record
from .registry import RegistryError


def _threshold_issues(plan):
    return [name for name, value in plan.thresholds.model_dump().items() if value is None]


def _method_gate_issues(plan):
    issues = []
    analysis = plan.analysis
    if analysis.metric_contract_version != METRIC_CONTRACT_VERSION:
        issues.append(("method_metric_contract_version_invalid",
                       "analysis.metric_contract_version"))

    for name, expected in REQUIRED_METRIC_GATES.items():
        actual = analysis.metric_gates.get(name)
        if actual is None:
            issues.append(("method_metric_gate_missing", f"analysis.metric_gates.{name}"))
            continue
        if actual.model_dump(mode="json") != expected:
            issues.append(("method_metric_gate_mismatch", f"analysis.metric_gates.{name}"))
        if actual.threshold_ref is not None and (
            actual.threshold_ref not in Thresholds.model_fields
            or getattr(plan.thresholds, actual.threshold_ref) is None
        ):
            issues.append(("method_metric_threshold_unresolved",
                           f"analysis.metric_gates.{name}.threshold_ref"))

    important = analysis.metric_gates.get("important_claim_coverage")
    if important is None:
        issues.append(("method_metric_gate_missing",
                       "analysis.metric_gates.important_claim_coverage"))
    else:
        actual = important.model_dump(mode="json")
        if (any(actual[field] != expected
                for field, expected in IMPORTANT_CLAIM_GATE_FIELDS.items())
                or important.decision == "report_only"):
            issues.append(("method_metric_gate_mismatch",
                           "analysis.metric_gates.important_claim_coverage"))
        if important.threshold_ref is not None and (
            important.threshold_ref not in Thresholds.model_fields
            or getattr(plan.thresholds, important.threshold_ref) is None
        ):
            issues.append(("method_metric_threshold_unresolved",
                           "analysis.metric_gates.important_claim_coverage.threshold_ref"))

    for name in analysis.metric_gates.keys() - REQUIRED_METRIC_NAMES:
        issues.append(("method_metric_gate_unknown", f"analysis.metric_gates.{name}"))
    return issues


def review_method_spec(registry, reference, roots, *, expected_head=None):
    """Check versioned method inputs only; never check or authorize an experiment run."""
    report = {
        "schema_version": "1",
        "status": "blocked",
        "method_artifacts_complete": False,
        "independent_signoff_required": True,
        "protocol_frozen": False,
        "run_authorized": False,
        "protocol": reference.model_dump(mode="json"),
        "registry_head": None,
        "execution_checks": "not_evaluated",
        "approval_checks": "not_evaluated",
        "issues": [],
    }

    def issue(code, location):
        item = {"code": code, "location": location}
        if item not in report["issues"]:
            report["issues"].append(item)

    if reference.kind != "protocol":
        issue("protocol_reference_required", "protocol")
        report["status"] = "invalid"
        return report

    try:
        rows = registry.entries(expected_head=expected_head)
    except RegistryError:
        issue("registry_invalid", "registry")
        report["status"] = "invalid"
        return report

    report["registry_head"] = rows[-1]["sha256"] if rows else "0" * 64
    records = [parse_record(row["document"]) for row in rows]
    matched = next((record for record in records
                    if record.kind == "protocol" and record_ref(record) == reference), None)
    if matched is None:
        issue("protocol_reference_unresolved", "protocol")
        report["status"] = "invalid"
        return report

    latest_revision = max(record.revision for record in records
                          if record.kind == "protocol" and record.id == matched.id)
    if latest_revision != matched.revision:
        issue("protocol_reference_superseded", "protocol")

    plan = matched
    for name in sorted(RULES):
        artifact = plan.rules.get(name)
        if artifact is None:
            issue("method_rule_missing", f"rules.{name}")
            continue
        try:
            verify_artifact(artifact, roots)
        except (ArtifactError, ValueError, OSError):
            issue("method_rule_artifact_invalid", f"rules.{name}")

    for name in _threshold_issues(plan):
        issue("method_threshold_missing", f"thresholds.{name}")
    for code, location in _method_gate_issues(plan):
        issue(code, location)
    if plan.known_results_disclosure == "unknown":
        issue("known_results_disclosure_required", "known_results_disclosure")

    if not report["issues"]:
        report["status"] = "ready_for_independent_method_review"
        report["method_artifacts_complete"] = True
    return report

"""Reference integrity review for portable multi-system comparison plans."""

from .gate import record_ref
from .models import parse_record
from .registry import RegistryError


REQUIRED_FORMAL_EVIDENCE = [
    "independent_holdout_or_sampling_justification",
    "reviewer_roles_and_independence",
    "access_and_permission_evidence",
    "system_identity_and_runtime_evidence",
    "calibration_and_quality_controls",
    "organizational_approvals",
]


def review_comparison(registry, reference, *, expected_head=None):
    report = {
        "schema_version": "1",
        "integrity_status": "invalid",
        "formal_readiness": "not_assessed",
        "resolved_system_count": 0,
        "required_evidence": REQUIRED_FORMAL_EVIDENCE,
        "issues": [],
        "registry_head": None,
        "run_authorized": False,
    }

    try:
        rows = registry.entries(expected_head=expected_head)
    except RegistryError:
        report["issues"].append("registry_invalid_or_anchor_mismatch")
        return report

    report["registry_head"] = rows[-1]["sha256"] if rows else "0" * 64
    records = [parse_record(row["document"]) for row in rows]
    plan_rows = [
        (row, record) for row, record in zip(rows, records)
        if record.kind == "comparison_plan" and record_ref(record) == reference
    ]
    if len(plan_rows) != 1:
        report["issues"].append("comparison_plan_reference_unresolved")
        return report

    plan_row, plan = plan_rows[0]
    if any(
        record.kind == "comparison_plan" and record.id == plan.id
        and record.revision > plan.revision
        for record in records
    ):
        report["issues"].append("comparison_plan_superseded")

    resolved = 0
    for arm in plan.arms:
        snapshot_ref = arm.snapshot
        matches = [
            record for record in records
            if record.kind == "system_snapshot" and record_ref(record) == snapshot_ref
        ]
        if len(matches) != 1:
            report["issues"].append("system_snapshot_reference_unresolved")
            continue
        resolved += 1
        if any(
            record.kind == "system_snapshot" and record.id == matches[0].id
            and record.revision > matches[0].revision
            for record in records
        ):
            report["issues"].append("system_snapshot_superseded")

    report["resolved_system_count"] = resolved
    if len(report["issues"]) == 0:
        report["integrity_status"] = "valid"
    elif all(issue.endswith("_superseded") for issue in report["issues"]):
        report["integrity_status"] = "stale"
    report["comparison_count"] = len(plan.comparisons)
    return report

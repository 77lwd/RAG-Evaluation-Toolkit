"""Check readiness-package structure without authenticating claims or authorizing runs."""

from .artifacts import ArtifactError, verify_artifact
from .comparison_review import review_comparison
from .gate import record_ref
from .models import parse_record
from .registry import RegistryError


REQUIRED_EVIDENCE_CATEGORIES = (
    "scope_and_sampling",
    "dataset_permission",
    "split_and_exposure_audit",
    "gold_rubric_and_calibration",
    "metrics_statistics_and_sample_size",
    "blind_review_plan",
    "access_and_data_flow",
    "system_runtime_identity",
    "run_safety_and_recovery",
    "serious_error_audit",
    "approvals_and_signoff",
)
REQUIRED_HUMAN_ROLES = (
    "evaluation_owner",
    "data_custodian",
    "independent_auditor",
    "annotator",
    "gold_reviewer",
    "adjudicator",
)


def review_readiness_submission(registry, reference, roots, *, expected_head=None):
    report = {
        "schema_version": "1",
        "status": "invalid",
        "formal_readiness": "not_assessed",
        "evidence_validity": "not_assessed",
        "human_identity_authentication": "not_performed",
        "required_evidence": list(REQUIRED_EVIDENCE_CATEGORIES),
        "missing_evidence": [],
        "missing_roles": [],
        "verified_evidence_count": 0,
        "issues": [],
        "registry_head": None,
        "run_authorized": False,
    }
    if reference.kind != "readiness_submission":
        report["issues"].append("readiness_submission_reference_required")
        return report

    try:
        rows = registry.entries(expected_head=expected_head)
    except RegistryError:
        report["issues"].append("registry_invalid")
        return report

    records = [parse_record(row["document"]) for row in rows]
    report["registry_head"] = rows[-1]["sha256"] if rows else "0" * 64
    submissions = [
        record for record in records
        if record.kind == "readiness_submission" and record_ref(record) == reference
    ]
    if len(submissions) != 1:
        report["issues"].append("readiness_submission_reference_unresolved")
        return report
    submission = submissions[0]

    latest_revision = max(
        record.revision for record in records
        if record.kind == "readiness_submission" and record.id == submission.id
    )
    if latest_revision != submission.revision:
        report["issues"].append("readiness_submission_superseded")

    comparison = review_comparison(
        registry, submission.comparison_plan, expected_head=expected_head,
    )
    if comparison["integrity_status"] != "valid":
        report["issues"].append("comparison_plan_invalid_or_stale")
    plan = next((
        record for record in records
        if record.kind == "comparison_plan"
        and record_ref(record) == submission.comparison_plan
    ), None)

    evidence_by_category = {item.category: item for item in submission.evidence}
    unknown_categories = sorted(
        set(evidence_by_category) - set(REQUIRED_EVIDENCE_CATEGORIES)
    )
    if unknown_categories:
        report["issues"].append("unknown_evidence_category")
    report["missing_evidence"] = [
        category for category in REQUIRED_EVIDENCE_CATEGORIES
        if category not in evidence_by_category
    ]
    if report["missing_evidence"]:
        report["issues"].append("required_evidence_missing")
    for category, item in evidence_by_category.items():
        try:
            verify_artifact(item.artifact, roots)
        except (ArtifactError, OSError, ValueError):
            report["issues"].append(f"evidence_integrity_unverified:{category}")
        else:
            report["verified_evidence_count"] += 1

    roles = {item.role_id: item.person_id for item in submission.roles}
    report["missing_roles"] = [role for role in REQUIRED_HUMAN_ROLES if role not in roles]
    if report["missing_roles"]:
        report["issues"].append("required_human_role_missing")
    if (
        any(person_id.startswith("TODO-") for person_id in roles.values())
        or any(item.reviewer_id.startswith("TODO-")
               for item in submission.review_assignments)
        or any(
            item.artifact.root_id.startswith("TODO-")
            or item.artifact.path.startswith("TODO/")
            or item.artifact.sha256 == "0" * 64
            for item in submission.evidence
        )
    ):
        report["issues"].append("readiness_placeholders_unreplaced")
    required_role_people = [roles[role] for role in REQUIRED_HUMAN_ROLES if role in roles]
    if len(required_role_people) != len(set(required_role_people)):
        report["issues"].append("required_human_roles_not_separated")

    human_reviewers = {
        assignment.reviewer_id for assignment in submission.review_assignments
        if assignment.actor_type == "human"
    }
    if len(human_reviewers) < 4:
        report["issues"].append("independent_human_reviewers_insufficient")
    if human_reviewers.intersection(required_role_people):
        report["issues"].append("reviewer_role_identity_overlap")

    if plan is not None:
        arm_ids = {arm.arm_id for arm in plan.arms}
        assignments_by_unit = {}
        for assignment in submission.review_assignments:
            if assignment.arm_id not in arm_ids:
                report["issues"].append("review_assignment_arm_unresolved")
                continue
            assignments_by_unit.setdefault(assignment.unit_id, {}).setdefault(
                assignment.arm_id, []
            ).append(assignment)

        for assignments_by_arm in assignments_by_unit.values():
            if set(assignments_by_arm) != arm_ids:
                report["issues"].append("review_assignment_coverage_incomplete")
            reviewers_by_arm = {}
            for arm_id, assignments in assignments_by_arm.items():
                human_assignments = [
                    assignment for assignment in assignments
                    if assignment.actor_type == "human"
                ]
                reviewer_ids = [item.reviewer_id for item in human_assignments]
                if len(reviewer_ids) < 2 or len(reviewer_ids) != len(set(reviewer_ids)):
                    report["issues"].append("two_independent_human_reviews_required_per_arm")
                reviewers_by_arm[arm_id] = set(reviewer_ids)

            arm_values = list(reviewers_by_arm.values())
            for index, reviewers in enumerate(arm_values):
                if any(reviewers.intersection(other) for other in arm_values[index + 1:]):
                    report["issues"].append("reviewer_cross_arm_assignment_conflict")

    report["issues"] = list(dict.fromkeys(report["issues"]))
    report["status"] = (
        "complete_for_independent_review" if not report["issues"] else "blocked"
    )
    report["reviewer_assignment_unit_count"] = len({
        assignment.unit_id for assignment in submission.review_assignments
    })
    return report

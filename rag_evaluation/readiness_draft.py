"""Create an explicit placeholder scaffold for a registered comparison plan."""

import os
from pathlib import Path

from .artifacts import canonical_bytes
from .comparison_review import review_comparison
from .gate import record_ref
from .models import ArtifactRef, ReadinessSubmission, parse_record
from .readiness_review import REQUIRED_EVIDENCE_CATEGORIES, REQUIRED_HUMAN_ROLES
from .registry import RegistryError


def create_readiness_draft(registry, plan_reference, readiness_id, output_path,
                           unit_ids=()):
    comparison = review_comparison(registry, plan_reference)
    if comparison["integrity_status"] != "valid":
        raise ValueError("comparison_plan_reference_invalid")

    rows = registry.entries()
    plan = None
    for row in rows:
        if row["document"].get("kind") != "comparison_plan":
            continue
        candidate = parse_record(row["document"])
        if record_ref(candidate) == plan_reference:
            plan = candidate
            break
    if plan is None:
        raise RegistryError("comparison_plan_reference_unresolved")
    unit_ids = list(unit_ids)
    if len(unit_ids) != len(set(unit_ids)):
        raise ValueError("readiness_unit_id_duplicate")

    assignments = [
        {
            "unit_id": unit_id,
            "arm_id": arm.arm_id,
            "reviewer_id": (
                f"TODO-reviewer-{unit_index + 1:03d}-"
                f"{arm_index + 1:03d}-{reviewer_index + 1:02d}"
            ),
            "actor_type": "human",
        }
        for unit_index, unit_id in enumerate(unit_ids)
        for arm_index, arm in enumerate(plan.arms)
        for reviewer_index in range(2)
    ]

    submission = ReadinessSubmission(
        id=readiness_id,
        revision=1,
        comparison_plan=plan_reference,
        evidence=[
            {
                "category": category,
                "artifact": ArtifactRef(
                    root_id="TODO-evidence-root",
                    path=f"TODO/{category}.md",
                    sha256="0" * 64,
                    size_bytes=0,
                    visibility="public",
                ),
            }
            for category in REQUIRED_EVIDENCE_CATEGORIES
        ],
        roles=[
            {"role_id": role, "person_id": f"TODO-{role}"}
            for role in REQUIRED_HUMAN_ROLES
        ],
        review_assignments=assignments,
    )
    output_path = Path(output_path)
    with output_path.open("xb") as stream:
        stream.write(canonical_bytes(submission.model_dump(mode="json")))
        stream.flush()
        os.fsync(stream.fileno())

    return {
        "status": "draft_created",
        "output": str(output_path),
        "readiness_id": submission.id,
        "comparison_plan": plan_reference.model_dump(mode="json"),
        "comparison_arm_ids": [arm.arm_id for arm in plan.arms],
        "required_evidence_count": len(REQUIRED_EVIDENCE_CATEGORIES),
        "required_role_count": len(REQUIRED_HUMAN_ROLES),
        "review_assignment_count": len(submission.review_assignments),
        "readiness_assessed": False,
        "run_authorized": False,
    }

"""Freeze and re-verify portable comparison metadata without authorizing execution."""

from .artifacts import digest_json
from .freeze import tool_digest
from .gate import record_ref
from .models import ComparisonFreeze, parse_record
from .readiness_review import review_readiness_submission
from .registry import RegistryError


class ComparisonFreezeError(ValueError):
    pass


def freeze_comparison(registry, readiness_reference, roots, *, freeze_id, actor_id,
                      expected_head=None):
    with registry.transaction():
        tool_before = tool_digest()
        report = review_readiness_submission(
            registry, readiness_reference, roots, expected_head=expected_head,
        )
        if report["status"] != "complete_for_independent_review":
            raise ComparisonFreezeError("readiness_blocked")

        rows = registry.entries(expected_head=report["registry_head"])
        readiness = next(
            parse_record(row["document"]) for row in rows
            if row["document"]["kind"] == "readiness_submission"
            and record_ref(parse_record(row["document"])) == readiness_reference
        )
        custodian = next(
            (role.person_id for role in readiness.roles
             if role.role_id == "data_custodian"), None,
        )
        if actor_id != custodian:
            raise ComparisonFreezeError("custodian_required")

        plan_reference = readiness.comparison_plan
        already_frozen = any(
            row["document"]["kind"] == "comparison_freeze"
            and row["document"]["comparison_plan"] == plan_reference.model_dump(mode="json")
            for row in rows
        )
        if already_frozen:
            raise ComparisonFreezeError("comparison_already_frozen")
        if tool_digest() != tool_before:
            raise ComparisonFreezeError("tool_changed_during_check")

        document = ComparisonFreeze(
            id=freeze_id,
            revision=1,
            comparison_plan=plan_reference,
            readiness_submission=readiness_reference,
            actor_id=actor_id,
            prior_head=report["registry_head"],
            check_sha256=digest_json(report),
            tool_sha256=tool_before,
        )
        return registry.append_locked(document)


class _PrefixRegistry:
    def __init__(self, rows):
        self.rows = rows

    def entries(self, expected_head=None):
        return self.rows


def verify_comparison_freeze(registry, reference, roots, *, expected_freeze_head):
    result = {
        "schema_version": "1",
        "status": "invalid",
        "run_authorized": False,
        "issues": [],
        "historical_readiness": None,
        "current_readiness": None,
    }

    def issue(code):
        item = {"code": code, "location": "comparison_freeze"}
        if item not in result["issues"]:
            result["issues"].append(item)

    if reference.kind != "comparison_freeze":
        issue("comparison_freeze_reference_required")
        return result
    try:
        rows = registry.entries()
    except RegistryError:
        issue("registry_invalid")
        return result

    match = None
    for index, row in enumerate(rows):
        document = row["document"]
        if (document["kind"] == "comparison_freeze"
                and document["id"] == reference.id
                and document["revision"] == reference.revision):
            match = (index, row, parse_record(document))
            break
    if match is None:
        issue("comparison_freeze_reference_unresolved")
        return result

    index, row, seal = match
    actual_reference = record_ref(seal)
    if actual_reference.sha256 != reference.sha256:
        issue("comparison_freeze_reference_digest_mismatch")
        return result
    if actual_reference != reference:
        issue("comparison_freeze_reference_binding_mismatch")
        return result
    if row["sha256"] != expected_freeze_head:
        issue("freeze_anchor_mismatch")
    if seal.prior_head != row["previous_sha256"]:
        issue("freeze_history_mismatch")
    if seal.tool_sha256 != tool_digest():
        issue("tool_identity_changed")

    historical = review_readiness_submission(
        _PrefixRegistry(rows[:index]), seal.readiness_submission, roots,
    )
    result["historical_readiness"] = historical
    if historical["status"] != "complete_for_independent_review":
        issue("freeze_check_mismatch")
    if historical["registry_head"] != seal.prior_head:
        issue("freeze_history_mismatch")
    if digest_json(historical) != seal.check_sha256:
        issue("freeze_check_mismatch")

    frozen_readiness = next((
        parse_record(item["document"]) for item in rows[:index]
        if item["document"]["kind"] == "readiness_submission"
        and record_ref(parse_record(item["document"])) == seal.readiness_submission
    ), None)
    if frozen_readiness is None:
        issue("freeze_binding_mismatch")
    elif frozen_readiness.comparison_plan != seal.comparison_plan:
        issue("freeze_binding_mismatch")
    if seal.prior_head != row["previous_sha256"]:
        issue("freeze_binding_mismatch")

    current = review_readiness_submission(
        registry, seal.readiness_submission, roots,
    )
    result["current_readiness"] = current
    if current["status"] != "complete_for_independent_review":
        issue("current_readiness_blocked")

    if not result["issues"]:
        result["status"] = "valid_metadata_seal"
    return result

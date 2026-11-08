"""Seal verified metadata under the journal lock; never authorize execution."""

import hashlib
import sys
from pathlib import Path

import pydantic

from .artifacts import digest_json
from .gate import check_protocol, record_ref
from .models import Freeze, parse_record
from .registry import RegistryError


class FreezeError(ValueError):
    pass


def tool_digest():
    package = Path(__file__).resolve().parent
    sources = {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
               for path in sorted(package.glob("*.py"))}
    return digest_json({"sources": sources, "python": sys.version,
                        "pydantic": pydantic.__version__})


def freeze_protocol(registry, reference, roots, *, freeze_id, actor_id,
                    mode="formal", expected_head=None):
    with registry.transaction():
        tool_before = tool_digest()
        report = check_protocol(registry, reference, roots, mode=mode,
                                expected_head=expected_head)
        if report["issues"]:
            raise FreezeError("admission_blocked")
        rows = registry.entries(expected_head=report["registry_head"])
        plan = next(parse_record(row["document"]) for row in rows
                    if row["document"]["kind"] == "protocol"
                    and record_ref(parse_record(row["document"])) == reference)
        if actor_id != plan.people.custodian:
            raise FreezeError("custodian_required")
        if any(row["document"]["kind"] == "freeze"
               and row["document"]["protocol"] == reference.model_dump(mode="json")
               for row in rows):
            raise FreezeError("protocol_already_frozen")
        if tool_digest() != tool_before:
            raise FreezeError("tool_changed_during_check")
        document = Freeze(
            id=freeze_id, revision=1, protocol=reference, dataset=plan.dataset,
            snapshots=plan.snapshots, actor_id=actor_id, prior_head=report["registry_head"],
            check_sha256=digest_json(report), tool_sha256=tool_before, mode=mode,
        )
        return registry.append_locked(document)


class _PrefixRegistry:
    def __init__(self, rows):
        self.rows = rows

    def entries(self, expected_head=None):
        return self.rows


def verify_freeze(registry, reference, roots, *, expected_freeze_head):
    result = {"schema_version": "1", "status": "invalid", "run_authorized": False,
              "issues": [], "current_admission": None}

    def issue(code):
        result["issues"].append({"code": code, "location": "freeze"})

    try:
        rows = registry.entries()
    except RegistryError:
        issue("registry_invalid")
        return result
    match = next(((index, row, parse_record(row["document"]))
                  for index, row in enumerate(rows) if row["document"]["kind"] == "freeze"
                  and record_ref(parse_record(row["document"])) == reference), None)
    if match is None:
        issue("freeze_reference_unresolved")
        return result
    index, row, document = match
    if row["sha256"] != expected_freeze_head:
        issue("freeze_anchor_mismatch")
    if document.prior_head != row["previous_sha256"]:
        issue("freeze_history_mismatch")
    if document.tool_sha256 != tool_digest():
        issue("tool_identity_changed")
    historical = check_protocol(_PrefixRegistry(rows[:index]), document.protocol, roots,
                                mode=document.mode)
    if historical["issues"] or digest_json(historical) != document.check_sha256:
        issue("freeze_check_mismatch")
    prior_plan = next((parse_record(item["document"]) for item in rows[:index]
                       if item["document"]["kind"] == "protocol"
                       and record_ref(parse_record(item["document"])) == document.protocol), None)
    if (prior_plan is None or document.dataset != prior_plan.dataset
            or document.snapshots != prior_plan.snapshots
            or document.actor_id != prior_plan.people.custodian):
        issue("freeze_binding_mismatch")
    current = check_protocol(registry, document.protocol, roots, mode=document.mode)
    result["current_admission"] = current
    if current["issues"]:
        issue("current_admission_blocked")
    if not result["issues"]:
        result["status"] = ("valid_metadata_seal" if document.mode == "formal"
                            else "valid_local_seal")
    return result

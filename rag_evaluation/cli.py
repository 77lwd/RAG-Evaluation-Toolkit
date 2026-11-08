"""Local metadata CLI. Exit 0 success, 2 blocked, 3 invalid input or evidence."""

import argparse
import json
import os
from pathlib import Path

from pydantic import ValidationError

from .artifacts import ArtifactError, canonical_bytes, fingerprint, load_json
from .bundle_registration import register_comparison_bundle
from .capture import capture_system_checkout
from .comparison_review import review_comparison
from .comparison_freeze import (
    ComparisonFreezeError,
    freeze_comparison,
    verify_comparison_freeze,
)
from .freeze import FreezeError, freeze_protocol, verify_freeze
from .gate import check_protocol, record_ref
from .inventory import collect_configured_inventory
from .joint_power import JointPowerSpec, simulate_joint_gate_power
from .method_review import review_method_spec
from .readiness_review import review_readiness_submission
from .models import RecordRef, parse_record
from .power import (
    PrimaryPowerSpec,
    StratifiedPrimaryPowerSpec,
    simulate_primary_power,
    simulate_stratified_primary_power,
)
from .readiness_draft import create_readiness_draft
from .registry import Registry, RegistryError


def _parser():
    parser = argparse.ArgumentParser(prog="python -m rag_evaluation")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "register", "list", "check", "review-method",
                 "review-comparison", "review-readiness", "freeze", "verify",
                 "freeze-comparison", "verify-comparison-freeze"):
        command = commands.add_parser(name)
        command.add_argument("--registry", required=True)
        if name == "register":
            command.add_argument("--document", required=True)
            command.add_argument("--reference-output")
        if name in ("check", "review-method", "review-comparison", "review-readiness",
                    "freeze", "verify", "freeze-comparison",
                    "verify-comparison-freeze"):
            command.add_argument("--reference", required=True)
            command.add_argument("--root", action="append", default=[], metavar="ID=PATH")
        if name in ("check", "review-method", "review-comparison", "review-readiness",
                    "verify", "freeze-comparison", "verify-comparison-freeze"):
            command.add_argument("--report")
        if name == "review-comparison":
            command.add_argument("--expected-head")
        if name == "review-readiness":
            command.add_argument("--expected-head")
        if name == "freeze-comparison":
            command.add_argument("--expected-head")
            command.add_argument("--freeze-id", required=True)
            command.add_argument("--actor-id", required=True)
        if name in ("check", "freeze"):
            command.add_argument("--mode", choices=("formal", "local"), default="formal")
            command.add_argument("--expected-head")
        if name == "review-method":
            command.add_argument("--expected-head")
        if name == "freeze":
            command.add_argument("--freeze-id", required=True)
            command.add_argument("--actor-id", required=True)
        if name == "verify":
            command.add_argument("--expected-freeze-head", required=True)
        if name == "verify-comparison-freeze":
            command.add_argument("--expected-freeze-head", required=True)
    command = commands.add_parser("validate")
    command.add_argument("--document", required=True)
    command = commands.add_parser("fingerprint")
    command.add_argument("--source-root", required=True)
    command.add_argument("--root-id", required=True)
    command.add_argument("--path", required=True)
    command.add_argument("--visibility", choices=("public", "restricted"), default="public")
    command = commands.add_parser("capture-system")
    command.add_argument("--source-root", required=True)
    command.add_argument("--root-id", required=True)
    command.add_argument("--snapshot-id", required=True)
    command.add_argument("--system-id", required=True)
    command.add_argument("--identity", required=True)
    command.add_argument("--revision", type=int, default=1)
    command.add_argument("--path", action="append", required=True)
    command.add_argument("--allowed-path", action="append", required=True)
    command = commands.add_parser("inventory-configured")
    command.add_argument("--config", required=True)
    command.add_argument("--output", required=True)
    command = commands.add_parser("register-comparison-bundle")
    command.add_argument("--registry", required=True)
    command.add_argument("--bundle", required=True)
    command.add_argument("--reference-output")
    command = commands.add_parser("draft-readiness")
    command.add_argument("--registry", required=True)
    command.add_argument("--reference", required=True)
    command.add_argument("--readiness-id", required=True)
    command.add_argument("--output", required=True)
    command.add_argument("--unit-id", action="append", default=[])
    command = commands.add_parser("power-simulate")
    command.add_argument("--spec", required=True)
    command.add_argument("--report")
    command = commands.add_parser("power-simulate-stratified")
    command.add_argument("--spec", required=True)
    command.add_argument("--report")
    command = commands.add_parser("power-simulate-joint")
    command.add_argument("--spec", required=True)
    command.add_argument("--report")
    return parser


def _roots(bindings):
    result = {}
    for binding in bindings:
        name, separator, path = binding.partition("=")
        if not separator or not name or not path or name in result:
            raise ValueError("invalid_root_binding")
        result[name] = path
    return result


def _write_reference_output(path, reference):
    path = Path(path)
    try:
        with path.open("xb") as stream:
            stream.write(canonical_bytes(reference))
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if load_json(path) != reference:
            raise ValueError("reference_output_conflict")
    return str(path)


def _dispatch(args):
    if args.command == "validate":
        document = parse_record(load_json(args.document, max_bytes=16_000_000))
        report = {
            "status": "valid",
            "kind": document.kind,
            "id": document.id,
            "revision": document.revision,
            "schema_version": document.schema_version,
            "run_authorized": False,
        }
        if document.kind == "comparison_plan":
            report["mode"] = document.mode
            report["comparisons"] = document.comparisons
        return 0, report
    if args.command in ("power-simulate", "power-simulate-stratified", "power-simulate-joint"):
        payload = load_json(args.spec, max_bytes=1_000_000)
        try:
            if args.command == "power-simulate-stratified":
                spec = StratifiedPrimaryPowerSpec(**payload)
                report = simulate_stratified_primary_power(spec)
            elif args.command == "power-simulate-joint":
                spec = JointPowerSpec(**payload)
                report = simulate_joint_gate_power(spec)
            else:
                spec = PrimaryPowerSpec(**payload)
                report = simulate_primary_power(spec)
        except (TypeError, ValueError) as error:
            raise ValueError("power_spec_invalid") from error
        return 0, report
    if args.command == "inventory-configured":
        result = collect_configured_inventory(args.config, args.output)
        report = {
            "status": "inventory_recorded",
            "inventory_id": result["inventory_id"],
            "output": str(Path(args.output) / "inventory.json"),
            "system_count": len(result["systems"]),
            "material_count": len(result["materials"]),
            "missing_material_count": len(result["missing_materials"]),
            "restricted_material_count": len(result["restricted_materials_not_read"]),
            "readiness_assessed": False,
            "run_authorized": False,
        }
        bundle = result.get("comparison_bundle")
        if bundle is not None:
            report["comparison_bundle"] = {
                "status": bundle["status"],
                "comparison_plan_file": str(
                    Path(args.output) / bundle["comparison_plan_file"]
                ),
                "system_snapshot_files": {
                    system_id: str(Path(args.output) / relative_path)
                    for system_id, relative_path in bundle["system_snapshot_files"].items()
                },
                "comparison_count": len(bundle["comparison_plan"]["arms"]) - 1,
                "registered": bundle["registered"],
                "readiness_assessed": bundle["readiness_assessed"],
                "run_authorized": bundle["run_authorized"],
            }
        return 0, report
    if args.command == "capture-system":
        snapshot = capture_system_checkout(
            args.source_root, snapshot_id=args.snapshot_id, system_id=args.system_id,
            identity=args.identity, root_id=args.root_id, paths=args.path,
            allowed_paths=args.allowed_path, revision=args.revision,
        )
        return 0, snapshot.model_dump(mode="json")
    if args.command == "fingerprint":
        return 0, fingerprint(args.source_root, args.path,
                              root_id=args.root_id,
                              visibility=args.visibility).model_dump(mode="json")
    if args.command == "init":
        Registry.initialize(args.registry)
        return 0, {"schema_version": "1", "status": "initialized", "run_authorized": False}
    registry = Registry(args.registry)
    if args.command == "register-comparison-bundle":
        report = register_comparison_bundle(registry, args.bundle)
        if args.reference_output:
            report["plan_reference_file"] = _write_reference_output(
                args.reference_output, report["plan_reference"],
            )
        return 0, report
    if args.command == "register":
        document = parse_record(load_json(args.document, max_bytes=16_000_000))
        if document.kind in ("freeze", "comparison_freeze"):
            raise ValueError("freeze_requires_admission")
        reference = record_ref(document) if document.kind in (
            "snapshot", "system_snapshot", "dataset", "protocol",
            "comparison_plan", "readiness_submission",
        ) else None
        if args.reference_output and reference is None:
            raise ValueError("reference_output_unsupported_for_record")
        row = registry.append(document)
        report = {
            "reference": reference.model_dump(mode="json") if reference else {
                "kind": document.kind, "id": document.id, "revision": document.revision,
            },
            "registry_head": row["sha256"],
            "run_authorized": False,
        }
        if args.reference_output:
            report["reference_file"] = _write_reference_output(
                args.reference_output, reference.model_dump(mode="json"),
            )
        return 0, report
    if args.command == "list":
        rows = registry.entries()
        return 0, {
            "records": [
                {"kind": row["document"]["kind"], "id": row["document"]["id"],
                 "revision": row["document"]["revision"], "entry_sha256": row["sha256"]}
                for row in rows
            ],
            "registry_head": rows[-1]["sha256"] if rows else "0" * 64,
            "run_authorized": False,
        }
    if args.command == "draft-readiness":
        reference = RecordRef.model_validate(load_json(args.reference))
        return 0, create_readiness_draft(
            registry, reference, args.readiness_id, args.output, args.unit_id,
        )
    reference = RecordRef.model_validate(load_json(args.reference))
    roots = _roots(args.root)
    if args.command == "review-comparison":
        report = review_comparison(
            registry, reference, expected_head=args.expected_head,
        )
        return (3 if report["integrity_status"] == "invalid" else 2), report
    if args.command == "review-readiness":
        report = review_readiness_submission(
            registry, reference, roots, expected_head=args.expected_head,
        )
        return (3 if report["status"] == "invalid" else 2), report
    if args.command == "freeze-comparison":
        row = freeze_comparison(
            registry, reference, roots, freeze_id=args.freeze_id,
            actor_id=args.actor_id, expected_head=args.expected_head,
        )
        return 0, {
            "reference": record_ref(parse_record(row["document"])).model_dump(mode="json"),
            "freeze_head": row["sha256"],
            "run_authorized": False,
        }
    if args.command == "verify-comparison-freeze":
        report = verify_comparison_freeze(
            registry, reference, roots,
            expected_freeze_head=args.expected_freeze_head,
        )
        return (0 if report["status"] == "valid_metadata_seal" else 3), report
    if args.command == "review-method":
        report = review_method_spec(
            registry, reference, roots, expected_head=args.expected_head,
        )
        return (3 if report["status"] == "invalid" else
                2 if report["issues"] else 0), report
    if args.command == "check":
        report = check_protocol(registry, reference, roots, mode=args.mode,
                                expected_head=args.expected_head)
        return (3 if report["status"] == "invalid" else 2 if report["issues"] else 0), report
    if args.command == "freeze":
        row = freeze_protocol(
            registry, reference, roots, freeze_id=args.freeze_id, actor_id=args.actor_id,
            mode=args.mode, expected_head=args.expected_head,
        )
        return 0, {"reference": record_ref(parse_record(row["document"])).model_dump(mode="json"),
                   "freeze_head": row["sha256"], "run_authorized": False}
    report = verify_freeze(registry, reference, roots,
                           expected_freeze_head=args.expected_freeze_head)
    return (0 if not report["issues"] else 3), report


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        code, report = _dispatch(args)
        if getattr(args, "report", None):
            with Path(args.report).open("xb") as stream:
                stream.write(canonical_bytes(report))
    except (ArtifactError, RegistryError, FreezeError, ComparisonFreezeError,
            ValidationError, ValueError, OSError) as error:
        # Pydantic exceptions can include input bodies; emit only a bounded code.
        code = 2 if (
            isinstance(error, FreezeError) and str(error) == "admission_blocked"
            or isinstance(error, ComparisonFreezeError)
            and str(error) == "readiness_blocked"
        ) else 3
        report = {"schema_version": "1", "status": "blocked" if code == 2 else "invalid",
                  "error": type(error).__name__, "run_authorized": False}
    print(json.dumps(report, ensure_ascii=True, sort_keys=True, allow_nan=False))
    return code

"""Validate and register the portable records emitted by configured inventory."""

from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from .artifacts import ArtifactError, load_json
from .gate import record_ref
from .models import ComparisonPlan, SystemSnapshot, parse_record
from .registry import Registry


def _bundle_file(root, relative_path):
    if (not isinstance(relative_path, str) or not relative_path
            or "\\" in relative_path or "\x00" in relative_path):
        raise ValueError("comparison_bundle_path_invalid")
    portable = PurePosixPath(relative_path)
    if (portable.is_absolute() or str(portable) != relative_path
            or ".." in portable.parts or "." in portable.parts):
        raise ValueError("comparison_bundle_path_invalid")
    try:
        path = (root / Path(*portable.parts)).resolve(strict=True)
    except (OSError, RuntimeError):
        raise ValueError("comparison_bundle_file_missing") from None
    if not path.is_file() or not path.is_relative_to(root):
        raise ValueError("comparison_bundle_path_invalid")
    return path


def register_comparison_bundle(registry, bundle_path):
    """Register generated snapshots and their plan after full bundle verification."""
    bundle_path = Path(bundle_path).resolve(strict=True)
    if bundle_path.is_dir():
        bundle_path = bundle_path / "inventory.json"
    bundle_path = bundle_path.resolve(strict=True)
    root = bundle_path.parent.resolve(strict=True)
    inventory = load_json(bundle_path, max_bytes=16_000_000)
    if (not isinstance(inventory, dict) or inventory.get("schema_version") != "1"
            or not isinstance(inventory.get("systems"), list)):
        raise ValueError("comparison_bundle_invalid")

    bundle = inventory.get("comparison_bundle")
    required_fields = {
        "status", "system_snapshot_files", "comparison_plan_file", "comparison_plan",
        "registered", "readiness_assessed", "run_authorized",
    }
    if (not isinstance(bundle, dict) or set(bundle) != required_fields
            or bundle["status"] != "draft" or bundle["registered"] is not False
            or bundle["readiness_assessed"] is not False
            or bundle["run_authorized"] is not False
            or not isinstance(bundle["system_snapshot_files"], dict)
            or not bundle["system_snapshot_files"]):
        raise ValueError("comparison_bundle_invalid")

    try:
        plan = ComparisonPlan.model_validate(bundle["comparison_plan"])
    except (TypeError, ValidationError) as error:
        raise ValueError("comparison_bundle_plan_invalid") from error
    plan_path = _bundle_file(root, bundle["comparison_plan_file"])
    try:
        disk_plan = ComparisonPlan.model_validate(load_json(plan_path, max_bytes=16_000_000))
    except (ArtifactError, TypeError, ValidationError) as error:
        raise ValueError("comparison_bundle_plan_invalid") from error
    if disk_plan != plan:
        raise ValueError("comparison_bundle_plan_mismatch")

    inventory_snapshots = {}
    try:
        for value in inventory["systems"]:
            snapshot = parse_record(value)
            if not isinstance(snapshot, SystemSnapshot):
                raise ValueError("comparison_bundle_snapshot_invalid")
            if snapshot.system_id in inventory_snapshots:
                raise ValueError("comparison_bundle_system_duplicate")
            inventory_snapshots[snapshot.system_id] = snapshot
    except (ArtifactError, TypeError, ValidationError) as error:
        raise ValueError("comparison_bundle_snapshot_invalid") from error

    snapshot_files = bundle["system_snapshot_files"]
    if set(snapshot_files) != set(inventory_snapshots):
        raise ValueError("comparison_bundle_snapshot_set_mismatch")
    snapshots_by_id = {}
    paths_seen = set()
    for system_id, relative_path in snapshot_files.items():
        path = _bundle_file(root, relative_path)
        if path in paths_seen:
            raise ValueError("comparison_bundle_snapshot_path_duplicate")
        paths_seen.add(path)
        try:
            snapshot = parse_record(load_json(path, max_bytes=16_000_000))
        except (ArtifactError, TypeError, ValidationError) as error:
            raise ValueError("comparison_bundle_snapshot_invalid") from error
        if (not isinstance(snapshot, SystemSnapshot)
                or snapshot.system_id != system_id
                or snapshot != inventory_snapshots[system_id]
                or snapshot.id in snapshots_by_id):
            raise ValueError("comparison_bundle_snapshot_mismatch")
        snapshots_by_id[snapshot.id] = snapshot

    registered_snapshots = []
    for arm in plan.arms:
        snapshot = snapshots_by_id.get(arm.snapshot.id)
        if snapshot is None or record_ref(snapshot) != arm.snapshot:
            raise ValueError("comparison_bundle_plan_reference_mismatch")
        if snapshot not in registered_snapshots:
            registered_snapshots.append(snapshot)

    documents = [*registered_snapshots, plan]
    result_rows = registry.append_many_idempotent(documents)
    entries = registry.entries()
    return {
        "status": "registered" if any(inserted for _, inserted in result_rows)
        else "already_registered",
        "records": [
            {
                "kind": row["document"]["kind"],
                "id": row["document"]["id"],
                "revision": row["document"]["revision"],
                "entry_sha256": row["sha256"],
                "status": "registered" if inserted else "already_present",
            }
            for row, inserted in result_rows
        ],
        "new_record_count": sum(inserted for _, inserted in result_rows),
        "already_present_count": sum(not inserted for _, inserted in result_rows),
        "plan_reference": record_ref(plan).model_dump(mode="json"),
        "registry_head": entries[-1]["sha256"] if entries else "0" * 64,
        "readiness_assessed": False,
        "run_authorized": False,
    }

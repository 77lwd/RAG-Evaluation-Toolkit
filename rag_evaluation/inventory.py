from datetime import datetime, timezone
from pathlib import Path

from pydantic import ValidationError

from .artifacts import canonical_bytes, fingerprint, load_json
from .capture import capture_system_checkout
from .gate import record_ref
from .models import ComparisonPlan, parse_record


def collect_configured_inventory(config_path, output):
    """Hash configured system source and historical asset metadata only."""
    config_path = Path(config_path).resolve(strict=True)
    config = load_json(config_path, max_bytes=1_000_000)
    required_config_fields = {"schema_version", "inventory_id", "systems", "materials"}
    if (not isinstance(config, dict)
            or not required_config_fields <= set(config)
            or set(config) - required_config_fields - {"comparison"}):
        raise ValueError("inventory_config_fields_invalid")
    if config["schema_version"] != "1":
        raise ValueError("inventory_config_version_unsupported")
    inventory_id = config["inventory_id"]
    systems = config["systems"]
    materials = config["materials"]
    if not isinstance(inventory_id, str) or not inventory_id.strip():
        raise ValueError("inventory_id_invalid")
    if not isinstance(systems, list) or not systems:
        raise ValueError("inventory_systems_required")
    if not isinstance(materials, list):
        raise ValueError("inventory_materials_invalid")

    config_root = config_path.parent

    def resolve_root(value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError("inventory_root_invalid")
        path = Path(value)
        if not path.is_absolute():
            path = config_root / path
        try:
            return path.resolve(strict=True)
        except OSError:
            raise ValueError("inventory_root_unavailable") from None

    def relative_paths(value, error_code):
        if (not isinstance(value, list) or not value
                or any(not isinstance(path, str) or not path.strip()
                       or Path(path).is_absolute() or ".." in Path(path).parts
                       for path in value)):
            raise ValueError(error_code)
        return value

    root_bindings = {}
    snapshots = []
    seen_system_ids, seen_snapshot_ids = set(), set()
    for item in systems:
        required = {"system_id", "identity", "root_id", "source_root", "snapshot_id",
                    "paths", "allowed_paths"}
        if not isinstance(item, dict) or set(item) != required:
            raise ValueError("inventory_system_config_invalid")
        if not all(isinstance(item[field], str) and item[field].strip()
                   for field in ("system_id", "identity", "root_id", "snapshot_id")):
            raise ValueError("inventory_system_config_invalid")
        if item["system_id"] in seen_system_ids or item["snapshot_id"] in seen_snapshot_ids:
            raise ValueError("inventory_system_id_duplicate")
        seen_system_ids.add(item["system_id"])
        seen_snapshot_ids.add(item["snapshot_id"])
        root = resolve_root(item["source_root"])
        root_id = item["root_id"]
        if not isinstance(root_id, str) or not root_id.strip():
            raise ValueError("inventory_root_id_invalid")
        if root_id in root_bindings and root_bindings[root_id] != root:
            raise ValueError("inventory_root_binding_conflict")
        root_bindings[root_id] = root
        snapshot = capture_system_checkout(
            root, snapshot_id=item["snapshot_id"], system_id=item["system_id"],
            identity=item["identity"], root_id=root_id,
            paths=relative_paths(item["paths"], "inventory_system_paths_invalid"),
            allowed_paths=relative_paths(
                item["allowed_paths"], "inventory_system_allowlist_invalid"),
        )
        snapshots.append(snapshot.model_dump(mode="json"))

    comparison_bundle = None
    if "comparison" in config:
        comparison = config["comparison"]
        if (not isinstance(comparison, dict)
                or set(comparison) != {"plan_id", "mode", "arms"}
                or not isinstance(comparison["plan_id"], str)
                or not comparison["plan_id"].strip()
                or not isinstance(comparison["arms"], list)):
            raise ValueError("inventory_comparison_config_invalid")
        snapshots_by_system = {
            snapshot["system_id"]: parse_record(snapshot) for snapshot in snapshots
        }
        arms = []
        for arm in comparison["arms"]:
            if not isinstance(arm, dict) or set(arm) != {"system_id", "arm_id", "role"}:
                raise ValueError("inventory_comparison_arm_invalid")
            system_id = arm["system_id"]
            if not isinstance(system_id, str) or system_id not in snapshots_by_system:
                raise ValueError("inventory_comparison_system_unresolved")
            arms.append({
                "arm_id": arm["arm_id"],
                "role": arm["role"],
                "snapshot": record_ref(snapshots_by_system[system_id]).model_dump(mode="json"),
            })
        try:
            plan = ComparisonPlan(
                id=comparison["plan_id"], revision=1, mode=comparison["mode"], arms=arms,
            )
        except (TypeError, ValidationError) as error:
            raise ValueError("inventory_comparison_invalid") from error
        snapshot_files = {
            system_id: f"system-snapshots/{snapshot.id}.json"
            for system_id, snapshot in snapshots_by_system.items()
        }
        comparison_bundle = {
            "status": "draft",
            "system_snapshot_files": snapshot_files,
            "comparison_plan_file": "comparison-plan.draft.json",
            "comparison_plan": plan.model_dump(mode="json"),
            "registered": False,
            "readiness_assessed": False,
            "run_authorized": False,
        }

    found, missing, restricted = [], [], []
    seen_asset_ids = set()
    for item in materials:
        required = {"asset_id", "root_id", "source_root", "path"}
        optional = {"visibility"}
        if (not isinstance(item, dict) or not required <= set(item)
                or set(item) - required - optional):
            raise ValueError("inventory_material_config_invalid")
        asset_id, root_id, relative = item["asset_id"], item["root_id"], item["path"]
        if not all(isinstance(value, str) and value.strip()
                   for value in (asset_id, root_id, relative)):
            raise ValueError("inventory_material_config_invalid")
        if Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("inventory_material_path_invalid")
        if asset_id in seen_asset_ids:
            raise ValueError("inventory_asset_id_duplicate")
        seen_asset_ids.add(asset_id)
        visibility = item.get("visibility", "public")
        if visibility not in ("public", "restricted"):
            raise ValueError("inventory_material_visibility_invalid")
        root = resolve_root(item["source_root"])
        if root_id in root_bindings and root_bindings[root_id] != root:
            raise ValueError("inventory_root_binding_conflict")
        root_bindings[root_id] = root
        if visibility == "restricted":
            restricted.append({"asset_id": asset_id, "root_id": root_id, "path": relative})
            continue
        candidate = root / relative
        if not candidate.is_file():
            missing.append({"asset_id": asset_id, "root_id": root_id, "path": relative})
            continue
        artifact = fingerprint(root, relative, root_id=root_id, visibility=visibility)
        found.append({"asset_id": asset_id, **artifact.model_dump(mode="json")})

    output = Path(output).resolve()
    if output.exists():
        raise ValueError("inventory_output_exists")
    if any(output == root or output.is_relative_to(root) or root.is_relative_to(output)
           for root in root_bindings.values()):
        raise ValueError("unsafe_inventory_output")

    result = {
        "schema_version": "1",
        "inventory_id": inventory_id,
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "scope": "configured_source_and_historical_asset_metadata_only",
        "roots": sorted(root_bindings),
        "systems": snapshots,
        "materials": found,
        "missing_materials": missing,
        "restricted_materials_not_read": restricted,
        "material_contents_parsed": False,
        "readiness_assessed": False,
        "run_authorized": False,
    }
    if comparison_bundle is not None:
        result["comparison_bundle"] = comparison_bundle
    output.mkdir(parents=True)
    with (output / "inventory.json").open("xb") as stream:
        stream.write(canonical_bytes(result))
    if comparison_bundle is not None:
        snapshot_dir = output / "system-snapshots"
        snapshot_dir.mkdir()
        for snapshot in snapshots:
            snapshot_path = snapshot_dir / f"{snapshot['id']}.json"
            with snapshot_path.open("xb") as stream:
                stream.write(canonical_bytes(snapshot))
        plan_path = output / comparison_bundle["comparison_plan_file"]
        with plan_path.open("xb") as stream:
            stream.write(canonical_bytes(comparison_bundle["comparison_plan"]))
    return result

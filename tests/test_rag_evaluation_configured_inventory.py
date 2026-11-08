"""Portable asset inventory accepts team-defined roots and never parses assets."""

import contextlib
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from rag_evaluation.cli import main
from rag_evaluation.gate import record_ref
from rag_evaluation.inventory import collect_configured_inventory
from rag_evaluation.models import ComparisonPlan, parse_record


class ConfiguredInventoryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="configured-inventory-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def create_checkout(self, name, files):
        root = self.root / name
        root.mkdir()
        for relative, body in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
        return root

    def create_git(self, root):
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email",
                        "test@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)

    def write_config(self, systems, materials, comparison=None):
        config = self.root / "inventory-config.json"
        payload = {
            "schema_version": "1",
            "inventory_id": "org-evaluation-assets",
            "systems": systems,
            "materials": materials,
        }
        if comparison is not None:
            payload["comparison"] = comparison
        config.write_text(json.dumps(payload), encoding="utf-8")
        return config

    def invoke(self, *arguments):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = main(list(arguments))
        return code, json.loads(output.getvalue())

    def test_custom_systems_and_materials_are_hashed_without_readiness_claims(self):
        systems = []
        for index, system_id in enumerate(("prod-rag", "candidate-2", "candidate-3")):
            root = self.create_checkout(system_id, {"src/app.py": f"answer={index}\n".encode()})
            self.create_git(root)
            systems.append({
                "system_id": system_id,
                "identity": f"team/{system_id}",
                "root_id": f"source-{system_id}",
                "source_root": str(root),
                "snapshot_id": f"snapshot-{system_id}",
                "paths": ["src/app.py"],
                "allowed_paths": ["src/app.py"],
            })
        material_root = self.create_checkout("history", {
            "reports/run.json": b'{"question":"must not be parsed"}',
        })
        materials = [
            {"asset_id": "prior-run", "root_id": "history", "source_root": str(material_root),
             "path": "reports/run.json"},
            {"asset_id": "not-yet-created", "root_id": "history",
             "source_root": str(material_root), "path": "reports/missing.json"},
            {"asset_id": "restricted-not-read", "root_id": "history",
             "source_root": str(material_root), "path": "reports/run.json",
             "visibility": "restricted"},
        ]
        config = self.write_config(systems, materials)
        output = self.root / "result"

        report = collect_configured_inventory(config, output)

        self.assertEqual([item["system_id"] for item in report["systems"]],
                         ["prod-rag", "candidate-2", "candidate-3"])
        self.assertTrue(all(item["runtime_verified"] is False for item in report["systems"]))
        self.assertEqual(report["materials"][0]["sha256"],
                         hashlib.sha256(b'{"question":"must not be parsed"}').hexdigest())
        self.assertEqual(report["missing_materials"], [{
            "asset_id": "not-yet-created", "root_id": "history",
            "path": "reports/missing.json",
        }])
        self.assertFalse(report["material_contents_parsed"])
        self.assertFalse(report["readiness_assessed"])
        self.assertEqual(report["restricted_materials_not_read"], [{
            "asset_id": "restricted-not-read", "root_id": "history",
            "path": "reports/run.json",
        }])
        self.assertEqual(report["roots"], [
            "history", "source-candidate-2", "source-candidate-3", "source-prod-rag",
        ])
        self.assertEqual(json.loads((output / "inventory.json").read_text(encoding="utf-8")),
                         report)

    def test_inventory_rejects_output_inside_any_source_root(self):
        source = self.create_checkout("source", {"service.py": b"x=1\n"})
        self.create_git(source)
        config = self.write_config([{
            "system_id": "system-x", "identity": "team/x", "root_id": "source-x",
            "source_root": str(source), "snapshot_id": "snapshot-x",
            "paths": ["service.py"], "allowed_paths": ["service.py"],
        }], [])

        with self.assertRaisesRegex(ValueError, "unsafe_inventory_output"):
            collect_configured_inventory(config, source / "inventory-output")

    def test_inventory_rejects_duplicate_paths_and_existing_output(self):
        source = self.create_checkout("source", {"service.py": b"x=1\n"})
        self.create_git(source)
        config = self.write_config([{
            "system_id": "system-x", "identity": "team/x", "root_id": "source-x",
            "source_root": str(source), "snapshot_id": "snapshot-x",
            "paths": ["service.py", "service.py"],
            "allowed_paths": ["service.py"],
        }], [])
        with self.assertRaisesRegex(ValueError, "capture_paths_required"):
            collect_configured_inventory(config, self.root / "invalid-result")

        config = self.write_config([{
            "system_id": "system-x", "identity": "team/x", "root_id": "source-x",
            "source_root": str(source), "snapshot_id": "snapshot-x",
            "paths": ["service.py"], "allowed_paths": ["service.py"],
        }], [])
        existing = self.root / "existing-result"
        existing.mkdir()
        with self.assertRaisesRegex(ValueError, "inventory_output_exists"):
            collect_configured_inventory(config, existing)

    def test_inventory_rejects_material_paths_outside_the_declared_root(self):
        source = self.create_checkout("source", {"service.py": b"x=1\n"})
        self.create_git(source)
        config = self.write_config([{
            "system_id": "system-x", "identity": "team/x", "root_id": "source-x",
            "source_root": str(source), "snapshot_id": "snapshot-x",
            "paths": ["service.py"], "allowed_paths": ["service.py"],
        }], [{
            "asset_id": "outside", "root_id": "source-x", "source_root": str(source),
            "path": "../outside-secret.json",
        }])

        with self.assertRaisesRegex(ValueError, "inventory_material_path_invalid"):
            collect_configured_inventory(config, self.root / "result")

    def test_configured_comparison_writes_snapshot_records_and_exploratory_plan(self):
        systems = []
        for system_id in ("service-base", "service-r7", "service-r8"):
            root = self.create_checkout(system_id, {"service.py": system_id.encode()})
            self.create_git(root)
            systems.append({
                "system_id": system_id,
                "identity": f"org/{system_id}",
                "root_id": f"source-{system_id}",
                "source_root": str(root),
                "snapshot_id": f"snapshot-{system_id}",
                "paths": ["service.py"],
                "allowed_paths": ["service.py"],
            })
        comparison = {
            "plan_id": "service-screening",
            "mode": "exploratory_screening",
            "arms": [
                {"system_id": "service-base", "arm_id": "live", "role": "baseline"},
                {"system_id": "service-r7", "arm_id": "release-r7", "role": "candidate"},
                {"system_id": "service-r8", "arm_id": "release-r8", "role": "candidate"},
            ],
        }
        config = self.write_config(systems, [], comparison)
        output = self.root / "result"

        report = collect_configured_inventory(config, output)

        bundle = report["comparison_bundle"]
        plan = bundle["comparison_plan"]
        self.assertEqual(plan["id"], "service-screening")
        self.assertEqual([arm["arm_id"] for arm in plan["arms"]],
                         ["live", "release-r7", "release-r8"])
        self.assertTrue(all(
            item["result_type"] == "exploratory"
            for item in ComparisonPlan.model_validate(plan).comparisons
        ))
        self.assertFalse(bundle["registered"])
        self.assertFalse(bundle["readiness_assessed"])
        self.assertFalse(bundle["run_authorized"])
        self.assertEqual(
            json.loads((output / bundle["comparison_plan_file"]).read_text(encoding="utf-8")),
            plan,
        )
        plan_record = ComparisonPlan.model_validate(plan)
        for system_id, relative in bundle["system_snapshot_files"].items():
            snapshot = parse_record(
                json.loads((output / relative).read_text(encoding="utf-8"))
            )
            self.assertEqual(snapshot.kind, "system_snapshot")
            self.assertFalse(snapshot.runtime_verified)
            arm = next(arm for arm in plan_record.arms if arm.snapshot.id == snapshot.id)
            self.assertEqual(arm.arm_id, next(
                item["arm_id"] for item in comparison["arms"]
                if item["system_id"] == system_id
            ))
            self.assertEqual(arm.snapshot, record_ref(snapshot))
        self.assertNotIn(str(self.root), json.dumps(bundle))

    def test_three_system_cli_workflow_reaches_readiness_and_blocks_placeholders(self):
        systems = []
        for index, system_id in enumerate(("service-base", "service-r7", "service-r8")):
            root = self.create_checkout(system_id, {"service.py": f"release={index}\n".encode()})
            self.create_git(root)
            systems.append({
                "system_id": system_id,
                "identity": f"org/{system_id}",
                "root_id": f"source-{system_id}",
                "source_root": str(root),
                "snapshot_id": f"snapshot-{system_id}",
                "paths": ["service.py"],
                "allowed_paths": ["service.py"],
            })
        comparison = {
            "plan_id": "service-screening",
            "mode": "exploratory_screening",
            "arms": [
                {"system_id": "service-base", "arm_id": "stable", "role": "baseline"},
                {"system_id": "service-r7", "arm_id": "release-r7", "role": "candidate"},
                {"system_id": "service-r8", "arm_id": "release-r8", "role": "candidate"},
            ],
        }
        config = self.write_config(systems, [], comparison)
        bundle_path = self.root / "generated-bundle"
        registry_path = self.root / "registry"
        plan_reference_path = self.root / "plan-reference.json"
        readiness_path = self.root / "readiness-draft.json"
        readiness_reference_path = self.root / "readiness-reference.json"

        inventory_code, inventory_report = self.invoke(
            "inventory-configured", "--config", str(config), "--output", str(bundle_path),
        )
        self.assertEqual(inventory_code, 0)
        self.assertEqual(inventory_report["system_count"], 3)
        self.assertFalse(inventory_report["readiness_assessed"])

        self.assertEqual(self.invoke("init", "--registry", str(registry_path))[0], 0)
        register_code, register_report = self.invoke(
            "register-comparison-bundle", "--registry", str(registry_path),
            "--bundle", str(bundle_path), "--reference-output", str(plan_reference_path),
        )
        self.assertEqual(register_code, 0)
        self.assertEqual(register_report["new_record_count"], 4)
        self.assertEqual(self.invoke(
            "review-comparison", "--registry", str(registry_path),
            "--reference", str(plan_reference_path),
        )[1]["integrity_status"], "valid")

        draft_code, draft_report = self.invoke(
            "draft-readiness", "--registry", str(registry_path),
            "--reference", str(plan_reference_path), "--readiness-id", "team-readiness",
            "--output", str(readiness_path), "--unit-id", "unit-001",
        )
        self.assertEqual(draft_code, 0)
        self.assertEqual(draft_report["comparison_arm_ids"], ["stable", "release-r7", "release-r8"])
        self.assertEqual(draft_report["review_assignment_count"], 6)

        readiness_register_code, readiness_register_report = self.invoke(
            "register", "--registry", str(registry_path), "--document", str(readiness_path),
            "--reference-output", str(readiness_reference_path),
        )
        self.assertEqual(readiness_register_code, 0)
        self.assertEqual(
            json.loads(readiness_reference_path.read_text(encoding="utf-8")),
            readiness_register_report["reference"],
        )
        readiness_review_code, readiness_review_report = self.invoke(
            "review-readiness", "--registry", str(registry_path),
            "--reference", str(readiness_reference_path),
            "--root", f"TODO-evidence-root={self.root}",
        )
        self.assertEqual(readiness_review_code, 2)
        self.assertEqual(readiness_review_report["status"], "blocked")
        self.assertIn("readiness_placeholders_unreplaced", readiness_review_report["issues"])
        self.assertEqual(readiness_review_report["formal_readiness"], "not_assessed")
        self.assertFalse(readiness_review_report["run_authorized"])

    def test_register_rejects_unsupported_reference_without_appending_record(self):
        registry_path = self.root / "registry"
        document_path = self.root / "exposure.json"
        reference_path = self.root / "unsupported-reference.json"
        document_path.write_text(json.dumps({
            "kind": "exposure",
            "id": "exposure-one",
            "revision": 1,
            "actor_id": "reviewer-one",
            "actor_role": "reviewer",
            "occurred_at": "2026-11-07T10:00:00+08:00",
            "access": "question",
            "asset_sha256": ["a" * 64],
        }), encoding="utf-8")

        self.assertEqual(self.invoke("init", "--registry", str(registry_path))[0], 0)
        code, report = self.invoke(
            "register", "--registry", str(registry_path), "--document", str(document_path),
            "--reference-output", str(reference_path),
        )

        self.assertEqual(code, 3)
        self.assertEqual(report["status"], "invalid")
        self.assertFalse(reference_path.exists())
        _, list_report = self.invoke("list", "--registry", str(registry_path))
        self.assertEqual(list_report["records"], [])

    def test_configured_comparison_rejects_unknown_system_and_ambiguous_confirmatory_mode(self):
        source = self.create_checkout("source", {"service.py": b"x=1\n"})
        self.create_git(source)
        systems = [{
            "system_id": "system-x", "identity": "org/x", "root_id": "source-x",
            "source_root": str(source), "snapshot_id": "snapshot-x",
            "paths": ["service.py"], "allowed_paths": ["service.py"],
        }]
        missing_system = {
            "plan_id": "bad-plan", "mode": "exploratory_screening", "arms": [
                {"system_id": "system-x", "arm_id": "base", "role": "baseline"},
                {"system_id": "unknown", "arm_id": "candidate", "role": "candidate"},
            ],
        }
        config = self.write_config(systems, [], missing_system)
        with self.assertRaisesRegex(ValueError, "inventory_comparison_system_unresolved"):
            collect_configured_inventory(config, self.root / "missing-system-result")
        self.assertFalse((self.root / "missing-system-result").exists())

        other_source = self.create_checkout("other-source", {"service.py": b"x=2\n"})
        self.create_git(other_source)
        third_source = self.create_checkout("third-source", {"service.py": b"x=3\n"})
        self.create_git(third_source)
        systems.append({
            "system_id": "system-y", "identity": "org/y", "root_id": "source-y",
            "source_root": str(other_source), "snapshot_id": "snapshot-y",
            "paths": ["service.py"], "allowed_paths": ["service.py"],
        })
        systems.append({
            "system_id": "system-z", "identity": "org/z", "root_id": "source-z",
            "source_root": str(third_source), "snapshot_id": "snapshot-z",
            "paths": ["service.py"], "allowed_paths": ["service.py"],
        })
        multiple_candidates = {
            "plan_id": "bad-confirm", "mode": "confirmatory", "arms": [
                {"system_id": "system-x", "arm_id": "base", "role": "baseline"},
                {"system_id": "system-y", "arm_id": "candidate-one", "role": "candidate"},
                {"system_id": "system-z", "arm_id": "candidate-two", "role": "candidate"},
            ],
        }
        config = self.write_config(systems, [], multiple_candidates)
        with self.assertRaisesRegex(ValueError, "inventory_comparison_invalid"):
            collect_configured_inventory(config, self.root / "invalid-confirm-result")
        self.assertFalse((self.root / "invalid-confirm-result").exists())


if __name__ == "__main__":
    unittest.main()

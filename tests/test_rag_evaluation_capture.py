"""Portable source capture uses team-defined identities and allowlists."""

import tempfile
import subprocess
import unittest
from pathlib import Path
from rag_evaluation.capture import capture_system_checkout


class PortableCheckoutCaptureTests(unittest.TestCase):
    def test_capture_accepts_team_defined_system_identity_and_paths(self):
        with tempfile.TemporaryDirectory(prefix="portable-capture-") as temporary:
            root = Path(temporary)
            (root / "service.py").write_text("answer = 42\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email",
                            "test@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(root), "add", "service.py"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "fixture"], check=True)

            result = capture_system_checkout(
                root, snapshot_id="team-system-r42", system_id="team-rag-r42",
                identity="acme/rag-service@release-42", root_id="team-source",
                paths=["service.py"], allowed_paths=["service.py"],
            )

        self.assertEqual(result.kind, "system_snapshot")
        self.assertEqual(result.system_id, "team-rag-r42")
        self.assertEqual(result.identity, "acme/rag-service@release-42")
        self.assertFalse(result.runtime_verified)
        self.assertEqual(len(result.git_commit), 40)
        self.assertTrue(result.git_branch)
        self.assertFalse(result.dirty)
        self.assertEqual([item.path for item in result.materials["code"]], ["service.py"])

    def test_capture_rejects_paths_outside_team_allowlist(self):
        with tempfile.TemporaryDirectory(prefix="portable-capture-") as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, "capture_path_not_allowed"):
                capture_system_checkout(
                    root, snapshot_id="team-system", system_id="team-rag",
                    identity="acme/rag", root_id="team-source", paths=[".env"],
                    allowed_paths=["service.py"],
                )

    def test_capture_rejects_empty_path_list(self):
        with tempfile.TemporaryDirectory(prefix="portable-capture-") as temporary:
            with self.assertRaisesRegex(ValueError, "capture_paths_required"):
                capture_system_checkout(
                    temporary, snapshot_id="team-system", system_id="team-rag",
                    identity="acme/rag", root_id="team-source", paths=[],
                    allowed_paths=["service.py"],
                )


if __name__ == "__main__":
    unittest.main()

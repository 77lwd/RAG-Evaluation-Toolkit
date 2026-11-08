"""Synthetic filesystem boundaries; never imports the production application."""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from rag_evaluation.artifacts import ArtifactError, fingerprint, load_json, verify_artifact
from rag_evaluation.models import ArtifactRef


class ArtifactTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.sample = self.root / "sample.txt"
        self.sample.write_bytes(b"synthetic\r\nsource\n")

    def test_digest_binds_raw_bytes_and_detects_modified_file(self):
        artifact = fingerprint(self.root, "sample.txt", root_id="open")
        self.assertEqual(artifact.sha256, hashlib.sha256(self.sample.read_bytes()).hexdigest())
        self.assertEqual(artifact.size_bytes, 18)
        self.assertIsNone(verify_artifact(artifact, {"open": self.root}))
        self.sample.write_bytes(b"synthetic\nsource\n")
        with self.assertRaisesRegex(ArtifactError, "artifact_changed"):
            verify_artifact(artifact, {"open": self.root})

    def test_restricted_reference_is_not_opened_even_if_root_is_present(self):
        reference = ArtifactRef(
            root_id="vault", path="not-readable-gold.json",
            sha256="a" * 64, size_bytes=10, visibility="restricted",
        )
        with self.assertRaisesRegex(ArtifactError, "restricted_requires_attestation"):
            verify_artifact(reference, {"vault": self.root})

    def test_fingerprint_can_mark_local_data_as_restricted(self):
        artifact = fingerprint(
            self.root, "sample.txt", root_id="vault", visibility="restricted")
        self.assertEqual(artifact.visibility, "restricted")
        with self.assertRaisesRegex(ArtifactError, "restricted_requires_attestation"):
            verify_artifact(artifact, {"vault": self.root})

    def test_path_traversal_absolute_and_windows_streams_are_rejected(self):
        for path in ("../sample.txt", "/sample.txt", "C:/sample.txt", "a:secret", "a\\b", "a/../b"):
            with self.subTest(path=path), self.assertRaises(ValidationError):
                ArtifactRef(root_id="open", path=path, sha256="a" * 64, size_bytes=1)

    def test_wrong_types_unknown_fields_and_non_hex_digest_are_rejected(self):
        base = dict(root_id="open", path="sample.txt", sha256="a" * 64, size_bytes=18)
        for change in ({"size_bytes": True}, {"size_bytes": "18"}, {"sha256": "z" * 64},
                       {"sha256": "A" * 64}, {"unexpected": True}):
            with self.subTest(change=change), self.assertRaises(ValidationError):
                ArtifactRef(**(base | change))

    def test_json_duplicate_keys_and_nonfinite_numbers_fail_closed(self):
        for raw in (b'{"approved":false,"approved":true}', b'{"value":NaN}',
                    b'{"value":Infinity}', b'{"value":1e999}', b'[]', b'\xff'):
            self.sample.write_bytes(raw)
            with self.subTest(raw=raw), self.assertRaises(ArtifactError):
                load_json(self.sample)

    def test_missing_root_file_and_oversized_json_have_distinct_codes(self):
        reference = fingerprint(self.root, "sample.txt", root_id="open")
        with self.assertRaisesRegex(ArtifactError, "unknown_root"):
            verify_artifact(reference, {})
        self.sample.unlink()
        with self.assertRaisesRegex(ArtifactError, "artifact_unavailable"):
            verify_artifact(reference, {"open": self.root})
        self.sample.write_text(json.dumps({"synthetic": "x" * 100}), encoding="utf-8")
        with self.assertRaisesRegex(ArtifactError, "json_too_large"):
            load_json(self.sample, max_bytes=20)


if __name__ == "__main__":
    unittest.main()

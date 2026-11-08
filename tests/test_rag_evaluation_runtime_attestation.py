import json
import hashlib
import os
from pathlib import Path
import tempfile
import sys
import types
import unittest
from unittest.mock import patch

from rag_evaluation.runtime_attestation import (
    RuntimeAttestationError,
    RuntimeAttestor,
    current_attestor,
    current_request_id,
)


class RuntimeAttestationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.output = Path(self.temp.name) / "receipts"
        self.root.mkdir()
        self.output.mkdir()
        self.source = self.root / "app.py"
        self.source.write_text("VALUE = 1\n", encoding="utf-8")
        self.receipt = self.output / "run.jsonl"
        self.attestors = []

    def tearDown(self):
        for attestor in self.attestors:
            attestor.close()
        self.temp.cleanup()

    def _attestor(self, path=None):
        attestor = RuntimeAttestor(path or self.receipt, "run-2026-11-06-a", self.root)
        self.attestors.append(attestor)
        return attestor

    def _events(self):
        return [json.loads(line) for line in self.receipt.read_text(encoding="utf-8").splitlines()]

    def test_receipt_path_must_be_new_absolute_and_outside_project(self):
        with self.assertRaisesRegex(RuntimeAttestationError, "receipt_path_must_be_absolute"):
            RuntimeAttestor("relative.jsonl", "run-1", self.root)
        with self.assertRaisesRegex(RuntimeAttestationError, "receipt_must_be_outside_project"):
            self._attestor(self.root / "receipt.jsonl")
        self.receipt.write_text("existing\n", encoding="utf-8")
        with self.assertRaisesRegex(RuntimeAttestationError, "receipt_already_exists"):
            self._attestor()

    def test_environment_opt_in_requires_both_run_id_and_receipt_path(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(RuntimeAttestor.from_environment(self.root))
        with patch.dict(os.environ, {"RAG_EVAL_RUN_ID": "run-1"}, clear=True):
            with self.assertRaisesRegex(RuntimeAttestationError, "evaluation_receipt_pair_required"):
                RuntimeAttestor.from_environment(self.root)

    def test_startup_and_request_events_capture_identity_without_question_or_secrets(self):
        attestor = self._attestor()
        loaded_module = types.ModuleType("synthetic_project_module")
        loaded_module.__file__ = str(self.source)
        with patch.dict(sys.modules, {"synthetic_project_module": loaded_module}):
            attestor.startup(
                embedding_model="embed-v1",
                generation_model="answer-v1",
                generation_endpoint="https://example.test/v1?token=secret",
                declared_data_roots={"database": "data/rag.db", "indexes": "data/indexes"},
            )
        index = self.root / "data" / "indexes" / "one.npz"
        index.parent.mkdir(parents=True)
        index.write_bytes(b"index bytes")
        corpus = self.root / "data" / "source.txt"
        corpus.write_text("private corpus", encoding="utf-8")

        with attestor.request_scope() as request_id:
            self.assertEqual(current_attestor(), attestor)
            self.assertEqual(current_request_id(), request_id)
            attestor.record_configuration({
                "values": {"mode": "semantic", "top_k": 3, "api_key": "secret"},
                "sources": {"mode": "service_default", "top_k": "workspace"},
                "settings_revision": 4,
            })
            attestor.record_data_scope(
                workspace_id="private-workspace",
                document_ids=["private-document"],
                artifacts={"corpus": [corpus], "index": [index]},
            )
            attestor.record_model_response("answer-v1", "answer-v1-served")

        self.assertIsNone(current_attestor())
        self.assertIsNone(current_request_id())
        content = self.receipt.read_text(encoding="utf-8")
        self.assertNotIn("secret", content)
        self.assertNotIn("private corpus", content)
        self.assertNotIn("private-workspace", content)
        self.assertNotIn("private-document", content)
        events = self._events()
        self.assertEqual(
            [event["event"] for event in events],
            ["startup", "request", "effective_config", "data_scope", "model_response", "request_result"],
        )
        startup = events[0]
        self.assertEqual(startup["run_id"], "run-2026-11-06-a")
        self.assertEqual(startup["models"]["embedding"], "embed-v1")
        self.assertEqual(startup["models"]["generation_requested"], "answer-v1")
        self.assertNotIn("token", startup["generation_endpoint"])
        modules = {item["path"]: item["sha256"] for item in startup["loaded_project_modules"]}
        self.assertIn("app.py", modules)
        self.assertEqual(startup["generation_endpoint"], "https://example.test")
        request_events = events[1:]
        self.assertTrue(all(event["request_id"] == request_id for event in request_events))
        config = events[2]["effective_config"]
        self.assertEqual(config["values"], {"mode": "semantic", "top_k": 3})
        self.assertEqual(config["sources"]["top_k"], "workspace")
        self.assertEqual(events[-2]["returned_model"], "answer-v1-served")
        self.assertEqual(events[-2]["request_id"], request_id)

    def test_effective_config_rejects_unexpected_values_and_sources(self):
        attestor = self._attestor()
        attestor.startup(
            embedding_model="embed-v1", generation_model="answer-v1",
            generation_endpoint="https://example.test/private/path",
        )
        with attestor.request_scope():
            with self.assertRaisesRegex(RuntimeAttestationError, "invalid_effective_config_value"):
                attestor.record_configuration({
                    "values": {"top_k": "api_key=secret"}, "sources": {},
                })
            with self.assertRaisesRegex(RuntimeAttestationError, "invalid_effective_config_source"):
                attestor.record_configuration({
                    "values": {"top_k": 3}, "sources": {"top_k": "secret"},
                })
        content = self.receipt.read_text(encoding="utf-8")
        self.assertNotIn("secret", content)
        self.assertNotIn("private/path", content)

    def test_request_scope_records_only_safe_error_type(self):
        attestor = self._attestor()
        attestor.startup(
            embedding_model="embed-v1", generation_model="answer-v1",
            generation_endpoint="https://example.test",
        )
        with self.assertRaisesRegex(RuntimeError, "private answer text"):
            with attestor.request_scope():
                raise RuntimeError("private answer text")
        result = self._events()[-1]
        self.assertEqual(result["event"], "request_result")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["error_type"], "RuntimeError")
        self.assertNotIn("private answer text", self.receipt.read_text(encoding="utf-8"))

    def test_data_scope_records_hashes_without_absolute_paths(self):
        attestor = self._attestor()
        attestor.startup(
            embedding_model="embed-v1", generation_model="answer-v1",
            generation_endpoint="https://example.test/v1",
        )
        artifact = self.root / "source.txt"
        artifact.write_bytes(b"source")
        with attestor.request_scope():
            attestor.record_data_scope(
                workspace_id="w", document_ids=["d"], artifacts={"corpus": [artifact]},
            )
        event = next(event for event in self._events() if event["event"] == "data_scope")
        self.assertEqual(event["artifacts"][0]["kind"], "corpus")
        self.assertEqual(event["artifacts"][0]["sha256"], hashlib.sha256(b"source").hexdigest())
        self.assertNotIn(str(self.root), json.dumps(event))


if __name__ == "__main__":
    unittest.main()

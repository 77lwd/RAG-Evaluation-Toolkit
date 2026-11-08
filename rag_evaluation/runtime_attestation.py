"""Opt-in, content-free runtime receipts for isolated evaluation runs."""

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import sys
import threading
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4


_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
_CONFIG_FIELDS = {"mode", "top_k", "per_document_k", "semantic_min_score"}
_CONFIG_SOURCES = {"service_default", "workspace", "request"}
_active_attestor = ContextVar("rag_eval_attestor", default=None)
_active_request_id = ContextVar("rag_eval_request_id", default=None)


class RuntimeAttestationError(ValueError):
    """Raised when an opt-in receipt cannot be written or verified."""


def current_attestor():
    return _active_attestor.get()


def current_request_id():
    return _active_request_id.get()


class RuntimeAttestor:
    def __init__(self, receipt_path, run_id, project_root):
        self.project_root = Path(project_root).resolve(strict=True)
        path = Path(receipt_path)
        if not path.is_absolute():
            raise RuntimeAttestationError("receipt_path_must_be_absolute")
        self.receipt_path = path.resolve(strict=False)
        if self.receipt_path == self.project_root or self.receipt_path.is_relative_to(
            self.project_root
        ):
            raise RuntimeAttestationError("receipt_must_be_outside_project")
        if not _RUN_ID.fullmatch(run_id):
            raise RuntimeAttestationError("invalid_evaluation_run_id")
        if not self.receipt_path.parent.is_dir():
            raise RuntimeAttestationError("receipt_parent_must_exist")
        if self.receipt_path.exists():
            raise RuntimeAttestationError("receipt_already_exists")
        self.run_id = run_id
        self._stream = None
        self._lock = threading.Lock()
        self._started = False

    @classmethod
    def from_environment(cls, project_root):
        path = os.environ.get("RAG_EVAL_RECEIPT_PATH")
        run_id = os.environ.get("RAG_EVAL_RUN_ID")
        if path is None and run_id is None:
            return None
        if not path or not run_id:
            raise RuntimeAttestationError("evaluation_receipt_pair_required")
        return cls(path, run_id, project_root)

    def startup(
        self, *, embedding_model, generation_model, generation_endpoint,
        declared_data_roots=None,
    ):
        if self._started:
            raise RuntimeAttestationError("runtime_receipt_already_started")
        self._write({
            "event": "startup",
            "run_id": self.run_id,
            "occurred_at": self._timestamp(),
            "git": self._git_identity(),
            "python": {
                "version": platform.python_version(),
                "implementation": platform.python_implementation(),
                "executable_name": Path(sys.executable).name,
            },
            "platform": platform.platform(),
            "loaded_project_modules": self._loaded_project_modules(),
            "models": {
                "embedding": self._safe_label(embedding_model),
                "generation_requested": self._safe_label(generation_model),
            },
            "generation_endpoint": self._safe_endpoint(generation_endpoint),
            "declared_data_roots": self._relative_roots(declared_data_roots or {}),
        })
        self._started = True

    @contextmanager
    def request_scope(self):
        if not self._started or self._stream is None:
            raise RuntimeAttestationError("runtime_receipt_not_started")
        if current_attestor() is not None:
            raise RuntimeAttestationError("nested_evaluation_request_scope")
        request_id = uuid4().hex
        attestor_token = _active_attestor.set(self)
        request_token = _active_request_id.set(request_id)
        try:
            self._write({
                "event": "request",
                "run_id": self.run_id,
                "request_id": request_id,
                "occurred_at": self._timestamp(),
            })
            try:
                yield request_id
            except BaseException as error:
                self._write({
                    "event": "request_result",
                    "run_id": self.run_id,
                    "request_id": request_id,
                    "occurred_at": self._timestamp(),
                    "status": "error",
                    "error_type": type(error).__name__,
                })
                raise
            else:
                self._write({
                    "event": "request_result",
                    "run_id": self.run_id,
                    "request_id": request_id,
                    "occurred_at": self._timestamp(),
                    "status": "completed",
                })
        finally:
            _active_request_id.reset(request_token)
            _active_attestor.reset(attestor_token)

    def record_configuration(self, config):
        request_id = self._require_request()
        values = config.get("values", {})
        sources = config.get("sources", {})
        settings_revision = config.get("settings_revision")
        safe_values = {}
        safe_sources = {}
        for key in sorted(_CONFIG_FIELDS):
            if key in values:
                value = values[key]
                if key == "mode":
                    valid = value == "semantic"
                elif key in {"top_k", "per_document_k"}:
                    valid = value is None or type(value) is int
                else:
                    valid = (
                        type(value) in {int, float}
                        and 0 <= value <= 1
                    )
                if not valid:
                    raise RuntimeAttestationError("invalid_effective_config_value")
                safe_values[key] = value
            if key in sources:
                if not isinstance(sources[key], str) or sources[key] not in _CONFIG_SOURCES:
                    raise RuntimeAttestationError("invalid_effective_config_source")
                safe_sources[key] = sources[key]
        self._write({
            "event": "effective_config",
            "run_id": self.run_id,
            "request_id": request_id,
            "occurred_at": self._timestamp(),
            "effective_config": {
                "values": safe_values,
                "sources": safe_sources,
                "settings_revision": settings_revision
                if type(settings_revision) is int else None,
            },
        })

    def record_data_scope(self, *, workspace_id, document_ids, artifacts):
        request_id = self._require_request()
        rows = []
        for kind, paths in sorted(artifacts.items()):
            for path in paths:
                rows.append({"kind": kind, **self._file_identity(path)})
        self._write({
            "event": "data_scope",
            "run_id": self.run_id,
            "request_id": request_id,
            "occurred_at": self._timestamp(),
            "workspace_sha256": self._opaque_id(workspace_id),
            "document_sha256": sorted(self._opaque_id(value) for value in document_ids),
            "artifacts": rows,
        })

    def record_model_response(self, requested_model, returned_model):
        request_id = self._require_request()
        self._write({
            "event": "model_response",
            "run_id": self.run_id,
            "request_id": request_id,
            "occurred_at": self._timestamp(),
            "requested_model": self._safe_label(requested_model),
            "returned_model": self._safe_label(returned_model) if returned_model else None,
        })

    def close(self):
        if self._stream is None:
            return
        try:
            self._write({
                "event": "shutdown", "run_id": self.run_id,
                "occurred_at": self._timestamp(),
            })
        finally:
            self._stream.close()
            self._stream = None

    def _require_request(self):
        if current_attestor() is not self or current_request_id() is None:
            raise RuntimeAttestationError("evaluation_request_scope_required")
        return current_request_id()

    def _write(self, event):
        data = json.dumps(event, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8") + b"\n"
        with self._lock:
            try:
                if self._stream is None:
                    self._stream = self.receipt_path.open("xb")
                self._stream.write(data)
                self._stream.flush()
                os.fsync(self._stream.fileno())
            except (OSError, ValueError) as error:
                if self._stream is not None and not self._started:
                    self._stream.close()
                    self._stream = None
                raise RuntimeAttestationError("runtime_receipt_write_failed") from error

    def _loaded_project_modules(self):
        modules = {}
        for module in tuple(sys.modules.values()):
            filename = getattr(module, "__file__", None)
            if not filename:
                continue
            try:
                path = Path(filename).resolve(strict=True)
                if not path.is_file() or not path.is_relative_to(self.project_root):
                    continue
                relative = path.relative_to(self.project_root).as_posix()
                modules[relative] = self._hash_file(path)
            except (OSError, RuntimeAttestationError) as error:
                try:
                    candidate = Path(filename).resolve(strict=False)
                except (OSError, TypeError, ValueError):
                    continue
                if candidate.is_relative_to(self.project_root):
                    raise RuntimeAttestationError(
                        "loaded_project_module_identity_incomplete"
                    ) from error
        return [{"path": path, "sha256": modules[path]} for path in sorted(modules)]

    def _git_identity(self):
        def git(*args):
            try:
                return subprocess.run(
                    ["git", "--no-optional-locks", "-C", str(self.project_root), *args],
                    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=3,
                    check=True,
                ).stdout.decode("utf-8", errors="strict").strip()
            except (OSError, subprocess.SubprocessError, UnicodeError):
                return None

        commit = git("rev-parse", "HEAD")
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
        status = git("status", "--porcelain", "--untracked-files=all")
        return {
            "commit": commit if commit and re.fullmatch(r"[0-9a-f]{40}", commit) else None,
            "branch": branch,
            "dirty": bool(status) if status is not None else None,
            "status_sha256": hashlib.sha256(status.encode("utf-8")).hexdigest()
            if status is not None else None,
        }

    def _file_identity(self, value):
        path = Path(value).resolve(strict=True)
        if not path.is_file():
            raise RuntimeAttestationError("runtime_artifact_not_file")
        try:
            relative = path.relative_to(self.project_root).as_posix()
            location = "project"
        except ValueError:
            relative = None
            location = "external"
        digest, size = self._hash_file(path, return_size=True)
        return {"location": location, "path": relative, "sha256": digest,
                "size_bytes": size}

    @staticmethod
    def _hash_file(path, *, return_size=False):
        try:
            before = path.stat()
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            after = path.stat()
        except OSError as error:
            raise RuntimeAttestationError("runtime_artifact_unavailable") from error
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise RuntimeAttestationError("runtime_artifact_changed_during_hash")
        return (digest.hexdigest(), after.st_size) if return_size else digest.hexdigest()

    @staticmethod
    def _relative_roots(roots):
        result = {}
        for name, value in sorted(roots.items()):
            path = Path(value)
            if path.is_absolute() or ".." in path.parts:
                raise RuntimeAttestationError("declared_data_root_must_be_relative")
            result[name] = path.as_posix()
        return result

    @staticmethod
    def _opaque_id(value):
        return hashlib.sha256(str(value).encode("utf-8")).hexdigest()

    @staticmethod
    def _safe_label(value):
        if not isinstance(value, str) or not value or len(value) > 200:
            raise RuntimeAttestationError("invalid_runtime_identity_label")
        return value

    @staticmethod
    def _safe_endpoint(value):
        try:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                raise ValueError()
            host = parsed.hostname
            if parsed.port:
                host = f"{host}:{parsed.port}"
            return urlunsplit((parsed.scheme, host, "", "", ""))
        except (AttributeError, TypeError, ValueError):
            raise RuntimeAttestationError("invalid_generation_endpoint") from None

    @staticmethod
    def _timestamp():
        return datetime.now(timezone.utc).isoformat()

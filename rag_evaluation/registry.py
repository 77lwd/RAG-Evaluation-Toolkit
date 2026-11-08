"""Single-operator immutable metadata journal, independently anchored by its head hash."""

import os
from contextlib import contextmanager
from pathlib import Path

from pydantic import ValidationError

from .artifacts import ArtifactError, canonical_bytes, digest_json, load_json
from .models import parse_record


class RegistryError(ValueError):
    pass


def _safe_root(root):
    root = Path(root).resolve()
    workspace = Path(__file__).resolve().parents[1]
    protected = [workspace / name for name in ("data", "uploads", ".git")]
    if root == workspace or ".git" in root.parts or any(
        root == path or root.is_relative_to(path) for path in protected
    ):
        raise RegistryError("unsafe_registry_root")
    return root


class Registry:
    def __init__(self, root):
        self.root = _safe_root(root)
        try:
            marker = load_json(self.root / "registry.json")
            if marker != {"schema_version": "1", "purpose": "rag-evaluation-governance"}:
                raise ValueError()
            if not (self.root / "records").is_dir():
                raise ValueError()
        except (ArtifactError, ValueError):
            raise RegistryError("registry_uninitialized") from None

    @classmethod
    def initialize(cls, root):
        root = _safe_root(root)
        if root.exists():
            raise RegistryError("registry_exists")
        root.mkdir(parents=True)
        (root / "records").mkdir()
        with (root / "registry.json").open("xb") as stream:
            stream.write(canonical_bytes({
                "schema_version": "1", "purpose": "rag-evaluation-governance",
            }))
            stream.flush()
            os.fsync(stream.fileno())
        return cls(root)

    def entries(self, expected_head=None):
        result, previous = [], "0" * 64
        versions = {}
        files = sorted((self.root / "records").iterdir())
        if len(files) > 10000 or sum(path.stat().st_size for path in files) > 64_000_000:
            raise RegistryError("registry_limit_exceeded")
        try:
            for sequence, path in enumerate(files, 1):
                if path.name != f"{sequence:08}.json" or path.is_symlink() or not path.is_file():
                    raise ValueError()
                row = load_json(path, max_bytes=16_000_000)
                if set(row) != {"sequence", "previous_sha256", "document", "sha256"}:
                    raise ValueError()
                if type(row["sequence"]) is not int or row["sequence"] != sequence:
                    raise ValueError()
                if row["previous_sha256"] != previous:
                    raise ValueError()
                unsigned = {key: value for key, value in row.items() if key != "sha256"}
                if digest_json(unsigned) != row["sha256"]:
                    raise ValueError()
                document = parse_record(row["document"])
                key = (document.kind, document.id)
                if document.revision != versions.get(key, 0) + 1:
                    raise ValueError()
                if document.kind in ("exposure", "freeze", "comparison_freeze") \
                        and document.revision != 1:
                    raise ValueError()
                versions[key] = document.revision
                previous = row["sha256"]
                result.append(row)
        except (OSError, ArtifactError, ValidationError, ValueError, TypeError):
            raise RegistryError("chain_invalid") from None
        if expected_head is not None and previous != expected_head:
            raise RegistryError("anchor_mismatch")
        return result

    @contextmanager
    def transaction(self):
        lock = self.root / ".writer.lock"
        try:
            stream = lock.open("xb")
        except FileExistsError:
            raise RegistryError("registry_busy") from None
        try:
            with stream:
                stream.write(str(os.getpid()).encode("ascii"))
                stream.flush()
                os.fsync(stream.fileno())
                yield
        finally:
            lock.unlink()

    def append(self, document):
        with self.transaction():
            return self.append_locked(document)

    def append_many_idempotent(self, documents):
        """Append a prevalidated batch, reusing exact latest records on retry."""
        documents = [parse_record(document.model_dump(mode="json"))
                     for document in documents]
        keys = [(document.kind, document.id) for document in documents]
        if not documents or len(keys) != len(set(keys)):
            raise RegistryError("batch_records_invalid")

        with self.transaction():
            rows = self.entries()
            latest = {}
            by_revision = {}
            for row in rows:
                key = (row["document"]["kind"], row["document"]["id"])
                latest[key] = row
                by_revision[(key, row["document"]["revision"])] = row

            results = {}
            pending = []
            for document, key in zip(documents, keys):
                current = latest.get(key)
                existing = by_revision.get((key, document.revision))
                if existing is not None:
                    if current is not existing:
                        raise RegistryError("batch_record_superseded")
                    if existing["document"] != document.model_dump(mode="json"):
                        raise RegistryError("revision_conflict")
                    results[key] = (existing, False)
                    continue
                expected_revision = current["document"]["revision"] + 1 if current else 1
                if document.revision != expected_revision:
                    raise RegistryError("revision_conflict")
                if document.kind in ("exposure", "freeze", "comparison_freeze") \
                        and document.revision != 1:
                    raise RegistryError(f"{document.kind}_is_append_only")
                pending.append((document, key))
                latest[key] = {"document": {"revision": document.revision}}

            for document, key in pending:
                row = self.append_locked(document)
                results[key] = (row, True)
            return [results[key] for key in keys]

    def append_locked(self, document):
        """Caller owns transaction(); freeze uses the same lock for check and append."""
        document = parse_record(document.model_dump(mode="json"))
        rows = self.entries()
        versions = [row["document"]["revision"] for row in rows
                    if (row["document"]["kind"], row["document"]["id"]) ==
                    (document.kind, document.id)]
        if document.kind in ("exposure", "freeze", "comparison_freeze") \
                and document.revision != 1:
            raise RegistryError(f"{document.kind}_is_append_only")
        if document.revision != (max(versions) + 1 if versions else 1):
            raise RegistryError("revision_conflict")
        unsigned = {
            "sequence": len(rows) + 1,
            "previous_sha256": rows[-1]["sha256"] if rows else "0" * 64,
            "document": document.model_dump(mode="json"),
        }
        row = unsigned | {"sha256": digest_json(unsigned)}
        path = self.root / "records" / f"{len(rows) + 1:08}.json"
        # Exclusive creation preserves partial writes as detectable failures.
        with path.open("xb") as stream:
            stream.write(canonical_bytes(row))
            stream.flush()
            os.fsync(stream.fileno())
        return row

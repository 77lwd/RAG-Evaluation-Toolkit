"""Read-only artifact verification with explicit roots and no restricted-content reads."""

import hashlib
import json
import math
from pathlib import Path

from .models import ArtifactRef


class ArtifactError(ValueError):
    pass


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest_json(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def load_json(path, max_bytes=2_000_000):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_json_key")
            result[key] = value
        return result

    def finite_float(text):
        value = float(text)
        if not math.isfinite(value):
            raise ValueError("nonfinite_json_number")
        return value

    def reject_constant(_):
        raise ValueError("nonfinite_json_number")

    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(max_bytes + 1)
        if len(raw) > max_bytes:
            raise ArtifactError("json_too_large")
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_float=finite_float, parse_constant=reject_constant)
        if not isinstance(value, dict):
            raise ValueError("json_object_required")
        return value
    except ArtifactError:
        raise
    except (OSError, ValueError, RecursionError) as error:
        raise ArtifactError("invalid_or_unavailable_json") from error


def _resolve(root, relative):
    root = Path(root).resolve(strict=True)
    resolved = (root / relative).resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ArtifactError("artifact_outside_root_or_not_file")
    return resolved


def fingerprint(root, relative, *, root_id, visibility="public"):
    ArtifactRef(root_id=root_id, path=relative, sha256="0" * 64, size_bytes=0,
                visibility=visibility)
    try:
        resolved = _resolve(root, relative)
        digest = hashlib.sha256()
        with resolved.open("rb") as stream:
            before = resolved.stat()
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
            after = resolved.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
        ):
            raise ArtifactError("artifact_changed_during_read")
        return ArtifactRef(root_id=root_id, path=relative, sha256=digest.hexdigest(),
                           size_bytes=after.st_size, visibility=visibility)
    except ArtifactError:
        raise
    except OSError as error:
        raise ArtifactError("artifact_unavailable") from error


def verify_artifact(reference, roots):
    if reference.visibility == "restricted":
        raise ArtifactError("restricted_requires_attestation")
    if reference.root_id not in roots:
        raise ArtifactError("unknown_root")
    actual = fingerprint(roots[reference.root_id], reference.path, root_id=reference.root_id)
    if (actual.sha256, actual.size_bytes) != (reference.sha256, reference.size_bytes):
        raise ArtifactError("artifact_changed")

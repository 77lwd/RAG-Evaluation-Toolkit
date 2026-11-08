"""Capture selected source bytes and Git identity, without running application code."""

import subprocess
from pathlib import Path

from .artifacts import fingerprint
from .models import SystemSnapshot


def capture_system_checkout(
    root, *, snapshot_id, system_id, identity, root_id, paths, allowed_paths,
    revision=1,
):
    """Capture explicitly allowlisted source files for a portable system snapshot."""
    if not paths or len(set(paths)) != len(paths):
        raise ValueError("capture_paths_required")
    if not allowed_paths or len(set(allowed_paths)) != len(allowed_paths):
        raise ValueError("capture_allowlist_required")
    if any(path not in allowed_paths for path in paths):
        raise ValueError("capture_path_not_allowed")

    root = Path(root).resolve(strict=True)

    def git(*arguments):
        try:
            result = subprocess.run(
                ["git", "--no-optional-locks", "-C", str(root), *arguments],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError("checkout_git_unavailable") from None
        if result.returncode:
            raise ValueError("checkout_git_unavailable")
        return result.stdout.decode("utf-8", errors="strict").strip()

    head = git("rev-parse", "HEAD")
    branch = git("rev-parse", "--abbrev-ref", "HEAD")
    before = git("status", "--porcelain", "--untracked-files=all")
    references = [fingerprint(root, path, root_id=root_id) for path in paths]
    after = git("status", "--porcelain", "--untracked-files=all")
    if head != git("rev-parse", "HEAD") or before != after:
        raise ValueError("checkout_changed_during_capture")
    for reference in references:
        if fingerprint(root, reference.path, root_id=root_id) != reference:
            raise ValueError("checkout_changed_during_capture")

    return SystemSnapshot(
        id=snapshot_id, revision=revision, system_id=system_id, identity=identity,
        root_id=root_id, git_commit=head, git_branch=branch, dirty=bool(before),
        runtime_verified=False, materials={"code": references},
        notes="Source observation only; runtime behavior is unverified.",
    )

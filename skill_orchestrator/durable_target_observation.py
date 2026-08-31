"""POSIX-only, read-only observation of one durable candidate target leaf.

The result is a point-in-time classification only.  It is never admission,
authorization, a reservation, or permission to mutate the target.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple

from .errors import SecurityError
from .transaction_journal import validate_target_key


OBSERVATION_STATUSES = ("absent", "present", "unsafe", "unstable", "unsupported")


@dataclass(frozen=True)
class DurableTargetObservation:
    """Immutable, non-authoritative metadata about one target leaf."""

    status: str
    reason_ids: Tuple[str, ...]


@dataclass(frozen=True)
class _LeafSnapshot:
    status: str
    identity: Optional[Tuple[int, int, int, int, int]]


def _supported() -> bool:
    if os.name != "posix" or not hasattr(os, "geteuid"):
        return False
    required_flags = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")
    if not all(hasattr(os, name) for name in required_flags):
        return False
    supports = getattr(os, "supports_dir_fd", set())
    return os.open in supports and os.stat in supports


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _root_identity(info: os.stat_result) -> Tuple[int, int]:
    return (info.st_dev, info.st_ino)


def _matches_expected_root_identity(value: Any, info: os.stat_result) -> bool:
    return (
        type(value) is dict
        and set(value) == {"kind", "device", "inode"}
        and value["kind"] == "posix-dev-ino"
        and type(value["device"]) is int
        and type(value["inode"]) is int
        and value["device"] >= 0
        and value["inode"] > 0
        and value["device"] == info.st_dev
        and value["inode"] == info.st_ino
    )


def _open_skills_root(skills_root: Any) -> Tuple[int, os.stat_result]:
    if not isinstance(skills_root, Path) or not skills_root.is_absolute() or ".." in skills_root.parts:
        raise SecurityError("durable target observation skills root is invalid")
    root = Path(os.path.abspath(os.fspath(skills_root)))
    if root == Path(root.anchor) or root == Path.home().resolve(strict=False):
        raise SecurityError("durable target observation skills root is too broad")

    descriptor = -1
    try:
        descriptor = os.open(root.anchor, _directory_flags())
        for part in root.parts[1:]:
            next_descriptor = os.open(part, _directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        info = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise SecurityError("durable target observation skills root is unsafe")
        return descriptor, info
    except SecurityError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise SecurityError("durable target observation skills root is unavailable") from exc


def _observe_leaf(root_fd: int, target_key: str) -> _LeafSnapshot:
    try:
        info = os.stat(target_key, dir_fd=root_fd, follow_symlinks=False)
    except FileNotFoundError:
        return _LeafSnapshot("absent", None)
    except OSError:
        return _LeafSnapshot("unsafe", None)

    identity = (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_rdev)
    if stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode):
        return _LeafSnapshot("present", identity)
    return _LeafSnapshot("unsafe", identity)


def _consistent(first: _LeafSnapshot, second: _LeafSnapshot) -> bool:
    return first.status == second.status and first.identity == second.identity


def observe_durable_target(
    skills_root: Path,
    expected_root_identity: Any,
    target_key: str,
) -> DurableTargetObservation:
    """Classify one direct target leaf without acquiring mutation authority.

    The observer detects some drift within this invocation, but a stable result
    can become stale immediately after return and must never be consumed as
    admission or publication authority.
    """

    if not _supported():
        return DurableTargetObservation("unsupported", ("platform.unsupported",))
    try:
        target_key = validate_target_key(target_key)
    except SecurityError:
        return DurableTargetObservation("unsafe", ("target.invalid",))

    first_fd = second_fd = -1
    try:
        first_fd, first_root = _open_skills_root(skills_root)
        if not _matches_expected_root_identity(expected_root_identity, first_root):
            return DurableTargetObservation("unsafe", ("skills-root.identity-mismatch",))
        first_leaf = _observe_leaf(first_fd, target_key)
        second_leaf = _observe_leaf(first_fd, target_key)
        if not _consistent(first_leaf, second_leaf):
            return DurableTargetObservation("unstable", ("target.changed-during-observation",))

        second_fd, second_root = _open_skills_root(skills_root)
        if _root_identity(first_root) != _root_identity(second_root):
            return DurableTargetObservation("unstable", ("skills-root.changed-during-observation",))
        if first_leaf.status == "absent":
            return DurableTargetObservation("absent", ("target.absent",))
        if first_leaf.status == "present":
            return DurableTargetObservation("present", ("target.present",))
        return DurableTargetObservation("unsafe", ("target.unsafe",))
    except SecurityError:
        return DurableTargetObservation("unsafe", ("skills-root.unsafe",))
    except OSError:
        return DurableTargetObservation("unsafe", ("target.unavailable",))
    finally:
        for descriptor in (second_fd, first_fd):
            if descriptor >= 0:
                os.close(descriptor)


__all__ = [
    "OBSERVATION_STATUSES",
    "DurableTargetObservation",
    "observe_durable_target",
]

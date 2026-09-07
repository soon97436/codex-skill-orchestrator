"""Read-only post-call identity evidence for one descriptor-relative target.

This private module does not publish, synchronize, authorize, or retain any
filesystem object.  It borrows one caller-owned destination-parent descriptor
and compares a no-follow target observation with the identity evidence from a
consumed owned-stage lease.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from typing import Any, Optional, Tuple

from .transaction_journal import validate_target_key
from .transactional_fs import _observe_consumed_stage_identity


_STATUSES = ("matched", "mismatched", "unavailable")


@dataclass(frozen=True)
class _PostCallTargetIdentityEvidence:
    """Minimal detached fact from one bracketed identity observation."""

    status: str

    def __post_init__(self) -> None:
        if type(self.status) is not str or self.status not in _STATUSES:
            raise ValueError("post-call target identity status is unsupported")


def _result(status: str) -> _PostCallTargetIdentityEvidence:
    return _PostCallTargetIdentityEvidence(status)


def _supported() -> bool:
    if os.name != "posix":
        return False
    supports_dir_fd = getattr(os, "supports_dir_fd", set())
    supports_follow = getattr(os, "supports_follow_symlinks", set())
    return os.stat in supports_dir_fd and os.stat in supports_follow


def _expected_identity(value: Any) -> Optional[Tuple[int, int]]:
    if (
        type(value) is tuple
        and len(value) == 2
        and type(value[0]) is int
        and value[0] >= 0
        and type(value[1]) is int
        and value[1] > 0
    ):
        return value
    return None


def _parent_identity(
    descriptor: int,
    expected: Tuple[int, int],
) -> Optional[Tuple[int, int]]:
    try:
        info = os.fstat(descriptor)
    except OSError:
        return None
    identity = (info.st_dev, info.st_ino)
    if not stat.S_ISDIR(info.st_mode) or identity != expected:
        return None
    return identity


def _observe_post_call_target_identity(
    lease: object,
    destination_parent_fd: object,
    expected_destination_parent_identity: object,
    target_leaf: object,
) -> _PostCallTargetIdentityEvidence:
    """Compare a borrowed-parent target leaf with one consumed retained stage.

    The caller must keep ``destination_parent_fd`` and ``lease`` live for the
    entire call and must ensure that this exact borrowed parent descriptor was
    used by the preceding publication operation.  This helper cannot establish
    that provenance independently.  Its result is point-in-time metadata and
    may become stale immediately after return.
    """

    unavailable = _result("unavailable")
    if not _supported():
        return unavailable
    if type(destination_parent_fd) is not int or destination_parent_fd < 0:
        return unavailable
    expected_parent = _expected_identity(expected_destination_parent_identity)
    if expected_parent is None:
        return unavailable
    try:
        target_leaf = validate_target_key(target_leaf)
    except Exception:
        return unavailable

    parent_before = _parent_identity(destination_parent_fd, expected_parent)
    if parent_before is None:
        return unavailable
    source_before = _observe_consumed_stage_identity(lease)
    if source_before.status != "matched":
        return unavailable

    target_missing = False
    target_info = None
    target_failed = False
    try:
        target_info = os.stat(
            target_leaf,
            dir_fd=destination_parent_fd,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        target_missing = True
    except OSError:
        target_failed = True

    source_after = _observe_consumed_stage_identity(lease)
    parent_after = _parent_identity(destination_parent_fd, expected_parent)
    if (
        source_after.status != "matched"
        or source_before.device != source_after.device
        or source_before.inode != source_after.inode
        or parent_after is None
        or parent_before != parent_after
    ):
        return unavailable
    if target_failed:
        return unavailable
    if target_missing:
        return _result("mismatched")
    if target_info is None:
        return unavailable
    if (
        not stat.S_ISDIR(target_info.st_mode)
        or target_info.st_dev != source_before.device
        or target_info.st_ino != source_before.inode
    ):
        return _result("mismatched")
    return _result("matched")

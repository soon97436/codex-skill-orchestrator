"""POSIX-only, read-only exact-manifest verification for one final target.

This module observes one direct ``<skills_root>/<target_key>`` directory.  A
verified result is informational only: it grants no ownership, admission,
authorization, publication, recovery, or mutation authority.
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .transaction_journal import normalize_exact_manifest, validate_target_key
from .transactional_fs import (
    COPY_CHUNK_BYTES,
    MAX_DECLARED_FILES,
    MAX_SINGLE_FILE_BYTES,
    MAX_TOTAL_CANDIDATE_BYTES,
    ExecutionLimits,
)


VERIFICATION_STATUSES = (
    "verified",
    "missing",
    "mismatch",
    "unsafe",
    "unstable",
    "unsupported",
)


@dataclass(frozen=True)
class DurableTargetVerification:
    """Detached, non-authoritative metadata from one target observation."""

    status: str
    file_count: int
    total_bytes: Optional[int]
    reason_ids: Tuple[str, ...]


@dataclass(frozen=True)
class _Metadata:
    device: int
    inode: int
    mode: int
    links: int
    size: int
    modified_ns: int
    changed_ns: int


class _UnsafeObservation(Exception):
    pass


class _UnstableTarget(Exception):
    pass


class _UnstableRoot(Exception):
    pass


def _result(
    status: str,
    reason_id: str,
    *,
    file_count: int = 0,
    total_bytes: Optional[int] = None,
) -> DurableTargetVerification:
    return DurableTargetVerification(status, file_count, total_bytes, (reason_id,))


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


def _file_flags() -> int:
    return os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | getattr(os, "O_NONBLOCK", 0)


def _metadata(info: os.stat_result) -> _Metadata:
    return _Metadata(
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _private_directory(info: os.stat_result, device: int) -> bool:
    return (
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.geteuid()
        and info.st_dev == device
        and stat.S_IMODE(info.st_mode) == 0o700
    )


def _private_file(info: os.stat_result, device: int) -> bool:
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.geteuid()
        and info.st_dev == device
        and info.st_nlink == 1
        and stat.S_IMODE(info.st_mode) == 0o600
    )


def _valid_expected_root_identity(value: Any, info: os.stat_result) -> bool:
    return (
        type(value) is dict
        and value
        == {
            "kind": "posix-dev-ino",
            "device": info.st_dev,
            "inode": info.st_ino,
        }
    )


def _open_skills_root(skills_root: Any) -> Tuple[int, os.stat_result]:
    if not isinstance(skills_root, Path) or not skills_root.is_absolute() or ".." in skills_root.parts:
        raise _UnsafeObservation()
    root = Path(os.path.abspath(os.fspath(skills_root)))
    if root == Path(root.anchor) or root == Path.home().resolve(strict=False):
        raise _UnsafeObservation()

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
            raise _UnsafeObservation()
        return descriptor, info
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        raise


def _select_limits(value: Optional[ExecutionLimits]) -> ExecutionLimits:
    selected = value if type(value) is ExecutionLimits else ExecutionLimits()
    if (
        type(selected.max_single_file_bytes) is not int
        or type(selected.max_total_candidate_bytes) is not int
        or type(selected.copy_chunk_bytes) is not int
        or selected.max_single_file_bytes <= 0
        or selected.max_total_candidate_bytes <= 0
        or selected.copy_chunk_bytes <= 0
        or selected.max_single_file_bytes > MAX_SINGLE_FILE_BYTES
        or selected.max_total_candidate_bytes > MAX_TOTAL_CANDIDATE_BYTES
        or selected.copy_chunk_bytes > COPY_CHUNK_BYTES
    ):
        raise _UnsafeObservation()
    return selected


def _normalized_manifest(value: Any, limits: ExecutionLimits) -> Dict[str, Tuple[str, int]]:
    try:
        normalized = normalize_exact_manifest(value)
    except Exception as exc:
        raise _UnsafeObservation() from exc
    if (
        not normalized
        or len(normalized) > MAX_DECLARED_FILES
        or sum(entry["size"] for entry in normalized) > limits.max_total_candidate_bytes
        or any(entry["size"] > limits.max_single_file_bytes for entry in normalized)
    ):
        raise _UnsafeObservation()
    return {entry["path"]: (entry["sha256"], entry["size"]) for entry in normalized}


def _read_regular_file(
    parent_fd: int,
    name: str,
    device: int,
    limits: ExecutionLimits,
) -> Tuple[str, int]:
    descriptor = -1
    try:
        descriptor = os.open(name, _file_flags(), dir_fd=parent_fd)
        before = os.fstat(descriptor)
        if not _private_file(before, device):
            raise _UnsafeObservation()
        digest = hashlib.sha256()
        total = 0
        while True:
            chunk = os.read(descriptor, limits.copy_chunk_bytes)
            if not chunk:
                break
            total += len(chunk)
            if total > limits.max_single_file_bytes:
                raise _UnsafeObservation()
            digest.update(chunk)
        after = os.fstat(descriptor)
        if _metadata(before) != _metadata(after) or total != after.st_size:
            raise _UnstableTarget()
        return digest.hexdigest(), total
    except FileNotFoundError as exc:
        raise _UnstableTarget() from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _walk_directory(
    directory_fd: int,
    prefix: Tuple[str, ...],
    *,
    device: int,
    limits: ExecutionLimits,
    records: List[Tuple[str, str, int]],
    directories: List[str],
) -> None:
    before = os.fstat(directory_fd)
    if not _private_directory(before, device):
        raise _UnsafeObservation()
    try:
        names = sorted(os.listdir(directory_fd))
    except OSError as exc:
        raise _UnsafeObservation() from exc
    for name in names:
        try:
            visible = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise _UnstableTarget() from exc
        relative = prefix + (name,)
        logical = "/".join(relative)
        if stat.S_ISLNK(visible.st_mode):
            raise _UnsafeObservation()
        if stat.S_ISDIR(visible.st_mode):
            if not _private_directory(visible, device):
                raise _UnsafeObservation()
            child_fd = -1
            try:
                child_fd = os.open(name, _directory_flags(), dir_fd=directory_fd)
                opened = os.fstat(child_fd)
                if _metadata(visible) != _metadata(opened):
                    raise _UnstableTarget()
                directories.append(logical)
                _walk_directory(
                    child_fd,
                    relative,
                    device=device,
                    limits=limits,
                    records=records,
                    directories=directories,
                )
                if _metadata(opened) != _metadata(os.fstat(child_fd)):
                    raise _UnstableTarget()
            except FileNotFoundError as exc:
                raise _UnstableTarget() from exc
            finally:
                if child_fd >= 0:
                    os.close(child_fd)
            continue
        if not stat.S_ISREG(visible.st_mode):
            raise _UnsafeObservation()
        digest, size = _read_regular_file(directory_fd, name, device, limits)
        records.append((logical, digest, size))
        if sum(item[2] for item in records) > limits.max_total_candidate_bytes:
            raise _UnsafeObservation()
    if _metadata(before) != _metadata(os.fstat(directory_fd)):
        raise _UnstableTarget()


def verify_durable_target(
    skills_root: Path,
    expected_root_identity: Any,
    target_key: Any,
    expected_manifest: Any,
    *,
    limits: Optional[ExecutionLimits] = None,
) -> DurableTargetVerification:
    """Observe whether one direct target exactly matches an expected manifest.

    ``verified`` means only that this call observed an exact, safe target.  The
    returned result contains no live descriptor or authority and may be stale
    immediately after return.
    """

    if not _supported():
        return _result("unsupported", "platform.unsupported")
    try:
        target_key = validate_target_key(target_key)
        selected_limits = _select_limits(limits)
        expected = _normalized_manifest(expected_manifest, selected_limits)
    except _UnsafeObservation:
        return _result("unsafe", "input.invalid")
    except Exception:
        return _result("unsafe", "input.invalid")

    root_fd = target_fd = second_root_fd = -1
    try:
        root_fd, root_before = _open_skills_root(skills_root)
        if not _valid_expected_root_identity(expected_root_identity, root_before):
            return _result("unsafe", "skills-root.identity-mismatch")

        try:
            visible_target = os.stat(target_key, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return _result("missing", "target.missing")
        if not _private_directory(visible_target, root_before.st_dev):
            return _result("unsafe", "target.unsafe")

        try:
            target_fd = os.open(target_key, _directory_flags(), dir_fd=root_fd)
        except FileNotFoundError as exc:
            raise _UnstableTarget() from exc
        target_before = os.fstat(target_fd)
        if _metadata(visible_target) != _metadata(target_before):
            raise _UnstableTarget()
        if not _private_directory(target_before, root_before.st_dev):
            raise _UnsafeObservation()

        records: List[Tuple[str, str, int]] = []
        directories: List[str] = []
        _walk_directory(
            target_fd,
            (),
            device=root_before.st_dev,
            limits=selected_limits,
            records=records,
            directories=directories,
        )
        records_after: List[Tuple[str, str, int]] = []
        directories_after: List[str] = []
        _walk_directory(
            target_fd,
            (),
            device=root_before.st_dev,
            limits=selected_limits,
            records=records_after,
            directories=directories_after,
        )
        if records != records_after or directories != directories_after:
            raise _UnstableTarget()
        actual = {path: (digest, size) for path, digest, size in records}
        implied_directories = {
            "/".join(parts[:index])
            for path in expected
            for parts in (path.split("/"),)
            for index in range(1, len(parts))
        }
        if actual != expected or set(directories) != implied_directories:
            return _result("mismatch", "target.mismatch")
        total_bytes = sum(size for _, _, size in records)
        if total_bytes > selected_limits.max_total_candidate_bytes:
            raise _UnsafeObservation()

        target_after = os.fstat(target_fd)
        try:
            visible_after = os.stat(target_key, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise _UnstableTarget() from exc
        if _metadata(target_before) != _metadata(target_after) or _metadata(target_before) != _metadata(visible_after):
            raise _UnstableTarget()
        if _metadata(root_before) != _metadata(os.fstat(root_fd)):
            raise _UnstableRoot()

        try:
            second_root_fd, root_after = _open_skills_root(skills_root)
        except Exception as exc:
            raise _UnstableRoot() from exc
        if _metadata(root_before) != _metadata(root_after):
            raise _UnstableRoot()
        return _result("verified", "target.verified", file_count=len(records), total_bytes=total_bytes)
    except _UnstableTarget:
        return _result("unstable", "target.changed-during-verification")
    except _UnstableRoot:
        return _result("unstable", "skills-root.changed-during-verification")
    except (_UnsafeObservation, OSError, ValueError):
        return _result("unsafe", "target.unsafe")
    finally:
        for descriptor in (second_root_fd, target_fd, root_fd):
            if descriptor >= 0:
                os.close(descriptor)


__all__ = [
    "DurableTargetVerification",
    "VERIFICATION_STATUSES",
    "verify_durable_target",
]

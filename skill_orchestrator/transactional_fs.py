"""Secure exact-file staging primitives for Phase 5E Increment 4B1.

The module creates and independently verifies a disposable stage.  It never
selects a target, installs or activates a skill, or grants runtime authority.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import stat
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Protocol, Tuple


MAX_SINGLE_FILE_BYTES = 1_048_576
MAX_TOTAL_CANDIDATE_BYTES = 8_388_608
COPY_CHUNK_BYTES = 65_536
MAX_DECLARED_FILES = 64
MAX_RELATIVE_PATH_UTF8_BYTES = 240
MAX_PATH_DEPTH = 16
MAX_SEGMENT_UTF8_BYTES = 100

STAGE_STATUSES = ("staged", "rejected", "invalid", "failed", "cleanup-required")
STAGE_OBSERVATION_STATUSES = ("matching", "missing", "unsafe", "unstable", "unsupported")
LEASE_STATES = ("active", "cleaned", "cleanup-required", "consumed", "tainted")
LEASE_TAINT_REASONS = (
    "native-outcome-indeterminate",
    "source-binding-lost",
    "post-rename-sync-failed",
)

REASON_IDS = (
    "phase5e.fs.input.invalid",
    "phase5e.fs.platform.unsupported",
    "phase5e.fs.source.unsafe",
    "phase5e.fs.stage.unsafe",
    "phase5e.fs.source.changed",
    "phase5e.fs.source.hash-mismatch",
    "phase5e.fs.resource.single-file-limit",
    "phase5e.fs.resource.total-limit",
    "phase5e.fs.stage.verification-failed",
    "phase5e.fs.operation.failed",
    "phase5e.fs.cleanup.required",
    "phase5e.fs.staged",
)

BASE_LIMITATIONS = (
    "phase5e.fs.limit.staged-not-installed",
    "phase5e.fs.limit.target-not-mutated",
    "phase5e.fs.limit.power-loss-durability-not-provided",
    "phase5e.fs.limit.remote-fetch-disabled",
    "phase5e.fs.limit.runtime-capability-enforcement-not-implemented",
)
WINDOWS_LIMITATION = "phase5e.fs.limit.windows-secure-staging-not-implemented"

_REASON_ORDER = {value: index for index, value in enumerate(REASON_IDS)}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", flags=re.ASCII)
_CANDIDATE_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$", flags=re.ASCII)
_RESERVED_BASENAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(("com%d" % index) for index in range(1, 10)),
    *(("lpt%d" % index) for index in range(1, 10)),
}


@dataclass(frozen=True)
class DeclaredFile:
    relative_path: str
    sha256: str


@dataclass(frozen=True)
class ExecutionLimits:
    max_single_file_bytes: int = MAX_SINGLE_FILE_BYTES
    max_total_candidate_bytes: int = MAX_TOTAL_CANDIDATE_BYTES
    copy_chunk_bytes: int = COPY_CHUNK_BYTES


@dataclass(frozen=True)
class StageRequest:
    source_root: Path
    staging_parent: Path
    candidate_key: str
    declared_files: Tuple[DeclaredFile, ...]
    limits: ExecutionLimits


@dataclass(frozen=True)
class StageResult:
    status: str
    stage_id: Optional[str]
    file_count: int
    total_bytes: Optional[int]
    manifest_digest: Optional[str]
    reason_ids: Tuple[str, ...]
    limitations: Tuple[str, ...]
    truncated: bool = False


@dataclass(frozen=True)
class StageLeaseOutcome:
    """A metadata result plus an optional live, module-owned stage lease."""

    result: StageResult
    lease: Optional["OwnedStageLease"]


@dataclass(frozen=True)
class LeaseCleanupResult:
    status: str


@dataclass(frozen=True)
class StageObservation:
    """A read-only classification of a stage; it never owns that stage."""

    status: str
    file_count: int
    total_bytes: Optional[int]


class OwnedStageLease:
    """Non-serializable, single-owner capability for one verified stage."""

    def __init__(
        self,
        adapter: "FilesystemAdapter",
        roots: "_RootHandles",
        stage: "_StageHandle",
        expected: Dict[str, Tuple[str, int]],
        limits: ExecutionLimits,
        manifest_digest: str,
        total_bytes: int,
    ) -> None:
        self.__adapter = adapter
        self.__roots = roots
        self.__stage = stage
        self.__expected = dict(expected)
        self.__limits = limits
        self.__manifest_digest = manifest_digest
        self.__total_bytes = total_bytes
        self.__state = "active"
        self.__taint_reason: Optional[str] = None

    def __repr__(self) -> str:
        return "OwnedStageLease(state=%r)" % self.__state

    def __reduce__(self):
        raise TypeError("owned stage leases are not serializable")

    def __reduce_ex__(self, protocol: int):
        raise TypeError("owned stage leases are not serializable")

    def __copy__(self):
        raise TypeError("owned stage leases are single-owner")

    def __deepcopy__(self, memo):
        raise TypeError("owned stage leases are single-owner")

    def _active(self) -> bool:
        return self.__state == "active"

    @property
    def state(self) -> str:
        """Return the bounded semantic lifecycle state."""

        return self.__state

    @property
    def taint_reason(self) -> Optional[str]:
        """Return the bounded reason retained after tainting lifecycle authority."""

        return self.__taint_reason

    def consume(self) -> None:
        """Irreversibly revoke live-stage cleanup and retry authority."""

        if self.__state != "active":
            raise RuntimeError("owned stage lease cannot be consumed from its current state")
        self.__state = "consumed"

    def taint(self, reason_id: str) -> None:
        """Irreversibly revoke ordinary cleanup and retry authority."""

        if type(reason_id) is not str or reason_id not in LEASE_TAINT_REASONS:
            raise ValueError("owned stage lease taint reason is unsupported")
        if self.__state == "active":
            if reason_id not in {"native-outcome-indeterminate", "source-binding-lost"}:
                raise RuntimeError("owned stage lease taint reason is invalid for its current state")
            self.__state = "tainted"
            self.__taint_reason = reason_id
            return
        if self.__state == "consumed":
            if reason_id != "post-rename-sync-failed":
                raise RuntimeError("owned stage lease taint reason is invalid for its current state")
            self.__state = "tainted"
            self.__taint_reason = reason_id
            return
        raise RuntimeError("owned stage lease cannot be tainted from its current state")

    def _matches_parent(self, device: int, inode: int) -> bool:
        if not self._active():
            return False
        try:
            parent = os.fstat(self.__roots.staging_parent_fd)
            return (parent.st_dev, parent.st_ino) == (device, inode)
        except OSError:
            return False

    def _revalidate(self) -> bool:
        if not self._active():
            return False
        try:
            records = self.__adapter.verify_stage(self.__stage, self.__expected, self.__limits)
            return (
                _manifest_digest(records) == self.__manifest_digest
                and sum(size for _, _, size in records) == self.__total_bytes
            )
        except Exception:
            return False

    def cleanup(self) -> LeaseCleanupResult:
        if self.__state == "cleaned":
            return LeaseCleanupResult("cleaned")
        if self.__state == "cleanup-required":
            return LeaseCleanupResult("cleanup-required")
        if self.__state != "active":
            raise RuntimeError("owned stage lease cannot be cleaned from its current state")
        try:
            self.__adapter.cleanup_stage(self.__stage)
            self.__adapter.close_roots(self.__roots)
        except Exception:
            self.__state = "cleanup-required"
            return LeaseCleanupResult("cleanup-required")
        self.__state = "cleaned"
        return LeaseCleanupResult("cleaned")

    def close(self) -> None:
        """Release owned descriptors without changing the stage namespace."""

        if self.__state == "active":
            raise RuntimeError("active owned stage lease cannot be closed")
        first_error: Optional[OSError] = None
        try:
            self.__adapter.close_stage(self.__stage)
        except OSError as error:
            first_error = error
        try:
            self.__adapter.close_roots(self.__roots)
        except OSError as error:
            if first_error is None:
                first_error = error
        if first_error is not None:
            raise first_error


@dataclass(frozen=True)
class _FileMetadata:
    device: int
    inode: int
    mode: int
    links: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass
class _RootHandles:
    source_fd: int
    staging_parent_fd: int
    source_absolute: str
    staging_absolute: str


@dataclass
class _StageHandle:
    roots: _RootHandles
    name: str
    stage_id: str
    fd: int
    device: int
    inode: int


@dataclass
class _OpenFile:
    fd: int
    metadata: _FileMetadata


class FilesystemAdapter(Protocol):
    """Filesystem boundary used by the deterministic staging coordinator."""

    def secure_staging_supported(self) -> bool: ...

    def open_roots(self, source_root: Path, staging_parent: Path) -> _RootHandles: ...

    def close_roots(self, roots: _RootHandles) -> None: ...

    def create_stage(self, roots: _RootHandles, candidate_key: str) -> _StageHandle: ...

    def close_stage(self, stage: _StageHandle) -> None: ...

    def open_source_file(self, roots: _RootHandles, relative_path: str) -> _OpenFile: ...

    def create_stage_file(self, stage: _StageHandle, relative_path: str) -> _OpenFile: ...

    def read(self, opened: _OpenFile, size: int) -> bytes: ...

    def write(self, opened: _OpenFile, data: bytes) -> int: ...

    def flush(self, opened: _OpenFile) -> None: ...

    def metadata(self, opened: _OpenFile) -> _FileMetadata: ...

    def close_file(self, opened: _OpenFile) -> None: ...

    def verify_stage(
        self,
        stage: _StageHandle,
        expected: Dict[str, Tuple[str, int]],
        limits: ExecutionLimits,
    ) -> Tuple[Tuple[str, str, int], ...]: ...

    def finalize_stage(self, stage: _StageHandle) -> None: ...

    def cleanup_stage(self, stage: _StageHandle) -> None: ...


class _InvalidInput(Exception):
    pass


class _Rejected(Exception):
    def __init__(self, reason_id: str) -> None:
        super().__init__()
        self.reason_id = reason_id


class _CleanupRequired(Exception):
    pass


def _metadata(value: os.stat_result) -> _FileMetadata:
    return _FileMetadata(
        device=value.st_dev,
        inode=value.st_ino,
        mode=value.st_mode,
        links=value.st_nlink,
        size=value.st_size,
        modified_ns=value.st_mtime_ns,
        changed_ns=value.st_ctime_ns,
    )


def _directory_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )


def _leaf_read_flags() -> int:
    return (
        os.O_RDONLY
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )


def _leaf_write_flags() -> int:
    return (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )


class RealFilesystemAdapter:
    """POSIX descriptor-relative adapter; Windows deliberately fails closed."""

    def __init__(self, platform_name: Optional[str] = None) -> None:
        self.platform_name = sys.platform if platform_name is None else platform_name

    def secure_staging_supported(self) -> bool:
        if self.platform_name == "win32":
            return False
        required_flags = all(
            hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY", "O_CLOEXEC")
        )
        supports = getattr(os, "supports_dir_fd", set())
        return required_flags and all(
            operation in supports for operation in (os.open, os.mkdir, os.stat, os.unlink, os.rmdir)
        )

    def _open_absolute_directory(self, value: Path, reason_id: str) -> Tuple[int, str]:
        if not isinstance(value, Path):
            raise _Rejected(reason_id)
        if not value.is_absolute() or ".." in value.parts:
            raise _Rejected(reason_id)
        absolute = os.path.abspath(os.fspath(value))
        anchor, remainder = os.path.splitdrive(absolute)
        if not remainder.startswith(os.sep):
            raise _Rejected(reason_id)
        root = anchor + os.sep
        components = [item for item in remainder.split(os.sep) if item]
        if not components:
            raise _Rejected(reason_id)
        try:
            current = os.open(root, _directory_flags())
        except OSError as error:
            raise _Rejected(reason_id) from error
        try:
            for component in components:
                next_fd = os.open(component, _directory_flags(), dir_fd=current)
                os.close(current)
                current = next_fd
            info = os.fstat(current)
            if not stat.S_ISDIR(info.st_mode):
                raise _Rejected(reason_id)
            return current, absolute
        except _Rejected:
            os.close(current)
            raise
        except OSError as error:
            os.close(current)
            raise _Rejected(reason_id) from error

    def open_roots(self, source_root: Path, staging_parent: Path) -> _RootHandles:
        source_fd = -1
        staging_fd = -1
        try:
            source_fd, source_absolute = self._open_absolute_directory(
                source_root, "phase5e.fs.source.unsafe"
            )
            staging_fd, staging_absolute = self._open_absolute_directory(
                staging_parent, "phase5e.fs.stage.unsafe"
            )
            common = os.path.commonpath((source_absolute, staging_absolute))
            if common in (source_absolute, staging_absolute):
                raise _Rejected("phase5e.fs.stage.unsafe")
            source_info = os.fstat(source_fd)
            staging_info = os.fstat(staging_fd)
            if (source_info.st_dev, source_info.st_ino) == (
                staging_info.st_dev,
                staging_info.st_ino,
            ):
                raise _Rejected("phase5e.fs.stage.unsafe")
            return _RootHandles(
                source_fd=source_fd,
                staging_parent_fd=staging_fd,
                source_absolute=source_absolute,
                staging_absolute=staging_absolute,
            )
        except Exception:
            if source_fd >= 0:
                os.close(source_fd)
            if staging_fd >= 0:
                os.close(staging_fd)
            raise

    def close_roots(self, roots: _RootHandles) -> None:
        for field in ("source_fd", "staging_parent_fd"):
            descriptor = getattr(roots, field)
            if descriptor >= 0:
                os.close(descriptor)
                setattr(roots, field, -1)

    def create_stage(self, roots: _RootHandles, candidate_key: str) -> _StageHandle:
        stage_id = secrets.token_hex(16)
        name = ".%s.cso-stage-%s" % (candidate_key, stage_id)
        os.mkdir(name, mode=0o700, dir_fd=roots.staging_parent_fd)
        descriptor = -1
        try:
            descriptor = os.open(name, _directory_flags(), dir_fd=roots.staging_parent_fd)
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode):
                raise _Rejected("phase5e.fs.stage.unsafe")
            os.fchmod(descriptor, 0o700)
            return _StageHandle(
                roots=roots,
                name=name,
                stage_id=stage_id,
                fd=descriptor,
                device=info.st_dev,
                inode=info.st_ino,
            )
        except Exception as error:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                os.rmdir(name, dir_fd=roots.staging_parent_fd)
            except OSError:
                raise _CleanupRequired() from error
            raise

    def close_stage(self, stage: _StageHandle) -> None:
        if stage.fd >= 0:
            os.close(stage.fd)
            stage.fd = -1

    def _open_parent(self, root_fd: int, parts: Tuple[str, ...], create: bool) -> int:
        current = os.dup(root_fd)
        try:
            for component in parts:
                if create:
                    try:
                        os.mkdir(component, mode=0o700, dir_fd=current)
                    except FileExistsError:
                        pass
                next_fd = os.open(component, _directory_flags(), dir_fd=current)
                os.close(current)
                current = next_fd
            return current
        except Exception:
            os.close(current)
            raise

    def open_source_file(self, roots: _RootHandles, relative_path: str) -> _OpenFile:
        parts = tuple(relative_path.split("/"))
        try:
            parent = self._open_parent(roots.source_fd, parts[:-1], create=False)
            try:
                descriptor = os.open(parts[-1], _leaf_read_flags(), dir_fd=parent)
            finally:
                os.close(parent)
        except OSError as error:
            raise _Rejected("phase5e.fs.source.unsafe") from error
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
            os.close(descriptor)
            raise _Rejected("phase5e.fs.source.unsafe")
        return _OpenFile(descriptor, _metadata(info))

    def create_stage_file(self, stage: _StageHandle, relative_path: str) -> _OpenFile:
        parts = tuple(relative_path.split("/"))
        parent = self._open_parent(stage.fd, parts[:-1], create=True)
        try:
            descriptor = os.open(parts[-1], _leaf_write_flags(), 0o600, dir_fd=parent)
        finally:
            os.close(parent)
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            os.close(descriptor)
            raise _Rejected("phase5e.fs.stage.unsafe")
        os.fchmod(descriptor, 0o600)
        return _OpenFile(descriptor, _metadata(info))

    def read(self, opened: _OpenFile, size: int) -> bytes:
        return os.read(opened.fd, size)

    def write(self, opened: _OpenFile, data: bytes) -> int:
        return os.write(opened.fd, data)

    def flush(self, opened: _OpenFile) -> None:
        os.fsync(opened.fd)

    def metadata(self, opened: _OpenFile) -> _FileMetadata:
        return _metadata(os.fstat(opened.fd))

    def close_file(self, opened: _OpenFile) -> None:
        if opened.fd >= 0:
            os.close(opened.fd)
            opened.fd = -1

    def _verified_file(self, parent_fd: int, name: str, limits: ExecutionLimits) -> Tuple[str, int]:
        descriptor = os.open(name, _leaf_read_flags(), dir_fd=parent_fd)
        opened = _OpenFile(descriptor, _metadata(os.fstat(descriptor)))
        try:
            before = opened.metadata
            if not stat.S_ISREG(before.mode) or before.links != 1:
                raise _Rejected("phase5e.fs.stage.verification-failed")
            digest = hashlib.sha256()
            total = 0
            while True:
                chunk = self.read(opened, limits.copy_chunk_bytes)
                if not chunk:
                    break
                total += len(chunk)
                if total > limits.max_single_file_bytes:
                    raise _Rejected("phase5e.fs.stage.verification-failed")
                digest.update(chunk)
            after = self.metadata(opened)
            if before != after or total != after.size:
                raise _Rejected("phase5e.fs.stage.verification-failed")
            return digest.hexdigest(), total
        finally:
            self.close_file(opened)

    def verify_stage(
        self,
        stage: _StageHandle,
        expected: Dict[str, Tuple[str, int]],
        limits: ExecutionLimits,
    ) -> Tuple[Tuple[str, str, int], ...]:
        self._assert_stage_identity(stage)
        records: List[Tuple[str, str, int]] = []
        directories = set()

        def visit(directory_fd: int, prefix: Tuple[str, ...]) -> None:
            for name in sorted(os.listdir(directory_fd)):
                info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                relative = prefix + (name,)
                logical = "/".join(relative)
                if stat.S_ISLNK(info.st_mode):
                    raise _Rejected("phase5e.fs.stage.verification-failed")
                if stat.S_ISDIR(info.st_mode):
                    directories.add(logical)
                    child = os.open(name, _directory_flags(), dir_fd=directory_fd)
                    try:
                        visit(child, relative)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    digest, size = self._verified_file(directory_fd, name, limits)
                    records.append((logical, digest, size))
                else:
                    raise _Rejected("phase5e.fs.stage.verification-failed")

        visit(stage.fd, ())
        expected_directories = {
            "/".join(parts[:index])
            for path in expected
            for parts in (path.split("/"),)
            for index in range(1, len(parts))
        }
        actual = {path: (digest, size) for path, digest, size in records}
        if actual != expected or directories != expected_directories:
            raise _Rejected("phase5e.fs.stage.verification-failed")
        if sum(size for _, _, size in records) > limits.max_total_candidate_bytes:
            raise _Rejected("phase5e.fs.stage.verification-failed")
        self._assert_stage_identity(stage)
        return tuple(sorted(records))

    def _synchronize_directory(self, descriptor: int) -> None:
        """Synchronize one already-open private stage directory."""

        os.fsync(descriptor)

    def _finalize_stage_directory(self, descriptor: int) -> None:
        """Synchronize nested stage directories bottom-up without reopening paths."""

        for name in sorted(os.listdir(descriptor)):
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                continue
            child = os.open(name, _directory_flags(), dir_fd=descriptor)
            try:
                opened = os.fstat(child)
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise _Rejected("phase5e.fs.stage.verification-failed")
                self._finalize_stage_directory(child)
            finally:
                os.close(child)
        self._synchronize_directory(descriptor)

    def finalize_stage(self, stage: _StageHandle) -> None:
        """Complete the required pre-publication stage directory sync sequence."""

        self._assert_stage_identity(stage)
        self._finalize_stage_directory(stage.fd)
        self._assert_stage_identity(stage)
        self._synchronize_directory(stage.roots.staging_parent_fd)

    def _assert_stage_identity(self, stage: _StageHandle) -> None:
        current = os.stat(
            stage.name,
            dir_fd=stage.roots.staging_parent_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != (stage.device, stage.inode)
        ):
            raise _Rejected("phase5e.fs.stage.verification-failed")

    def _remove_contents(self, directory_fd: int) -> None:
        for name in sorted(os.listdir(directory_fd)):
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
                child = os.open(name, _directory_flags(), dir_fd=directory_fd)
                try:
                    self._remove_contents(child)
                finally:
                    os.close(child)
                os.rmdir(name, dir_fd=directory_fd)
            else:
                os.unlink(name, dir_fd=directory_fd)

    def cleanup_stage(self, stage: _StageHandle) -> None:
        self._assert_stage_identity(stage)
        self._remove_contents(stage.fd)
        self.close_stage(stage)
        current = os.stat(stage.name, dir_fd=stage.roots.staging_parent_fd, follow_symlinks=False)
        if (current.st_dev, current.st_ino) != (stage.device, stage.inode):
            raise OSError()
        os.rmdir(stage.name, dir_fd=stage.roots.staging_parent_fd)


def _portable_path(value: object) -> bool:
    if type(value) is not str or not value or "\x00" in value:
        return False
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    if len(encoded) > MAX_RELATIVE_PATH_UTF8_BYTES:
        return False
    if unicodedata.normalize("NFC", value) != value:
        return False
    if value.startswith("/") or "\\" in value or ":" in value:
        return False
    parts = value.split("/")
    if len(parts) > MAX_PATH_DEPTH or any(not part or part in {".", ".."} for part in parts):
        return False
    for part in parts:
        try:
            if len(part.encode("utf-8")) > MAX_SEGMENT_UTF8_BYTES:
                return False
        except UnicodeEncodeError:
            return False
        if part.endswith((".", " ")):
            return False
        if part.split(".", 1)[0].casefold() in _RESERVED_BASENAMES:
            return False
    return True


def _validate_request(request: object) -> Tuple[StageRequest, Tuple[DeclaredFile, ...]]:
    if type(request) is not StageRequest:
        raise _InvalidInput()
    if not isinstance(request.source_root, Path) or not isinstance(request.staging_parent, Path):
        raise _InvalidInput()
    if "\x00" in os.fspath(request.source_root) or "\x00" in os.fspath(request.staging_parent):
        raise _InvalidInput()
    key = request.candidate_key
    if (
        type(key) is not str
        or not _CANDIDATE_KEY_RE.fullmatch(key)
        or ".." in key
        or key.endswith((".", " "))
        or "\x00" in key
    ):
        raise _InvalidInput()
    if type(request.declared_files) is not tuple:
        raise _InvalidInput()
    if not 1 <= len(request.declared_files) <= MAX_DECLARED_FILES:
        raise _InvalidInput()
    limits = request.limits
    if (
        type(limits) is not ExecutionLimits
        or type(limits.copy_chunk_bytes) is not int
        or type(limits.max_single_file_bytes) is not int
        or type(limits.max_total_candidate_bytes) is not int
        or not (
            0
            < limits.copy_chunk_bytes
            <= limits.max_single_file_bytes
            <= limits.max_total_candidate_bytes
        )
        or limits.copy_chunk_bytes > COPY_CHUNK_BYTES
        or limits.max_single_file_bytes > MAX_SINGLE_FILE_BYTES
        or limits.max_total_candidate_bytes > MAX_TOTAL_CANDIDATE_BYTES
    ):
        raise _InvalidInput()
    paths = []
    normalized = []
    for declared in request.declared_files:
        if (
            type(declared) is not DeclaredFile
            or not _portable_path(declared.relative_path)
            or type(declared.sha256) is not str
            or not _SHA256_RE.fullmatch(declared.sha256)
        ):
            raise _InvalidInput()
        paths.append(declared.relative_path)
        normalized.append(unicodedata.normalize("NFC", declared.relative_path).casefold())
    if len(set(paths)) != len(paths) or len(set(normalized)) != len(normalized):
        raise _InvalidInput()
    normalized_parts = [tuple(path.split("/")) for path in normalized]
    for index, parts in enumerate(normalized_parts):
        for other_index, other in enumerate(normalized_parts):
            if index != other_index and len(parts) < len(other) and other[: len(parts)] == parts:
                raise _InvalidInput()
    return request, tuple(sorted(request.declared_files, key=lambda item: item.relative_path))


def _ordered_reasons(values: Iterable[str]) -> Tuple[str, ...]:
    return tuple(
        sorted(
            {value for value in values if value in _REASON_ORDER},
            key=_REASON_ORDER.__getitem__,
        )
    )


def _result(
    status: str,
    reason_id: str,
    *,
    file_count: int = 0,
    total_bytes: Optional[int] = None,
    manifest_digest: Optional[str] = None,
    stage_id: Optional[str] = None,
    windows_unsupported: bool = False,
) -> StageResult:
    limitations = BASE_LIMITATIONS + ((WINDOWS_LIMITATION,) if windows_unsupported else ())
    return StageResult(
        status=status,
        stage_id=stage_id,
        file_count=file_count,
        total_bytes=total_bytes,
        manifest_digest=manifest_digest,
        reason_ids=_ordered_reasons((reason_id,)),
        limitations=limitations,
        truncated=False,
    )


def _source_unchanged(before: _FileMetadata, after: _FileMetadata, copied: int) -> bool:
    return (
        before == after
        and stat.S_ISREG(after.mode)
        and after.links == 1
        and copied == after.size
    )


def _manifest_digest(records: Tuple[Tuple[str, str, int], ...]) -> str:
    payload = [
        {"path": path, "sha256": digest, "size": size}
        for path, digest, size in sorted(records)
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(b"cso-stage-manifest-v1\0" + encoded).hexdigest()


def _valid_observation_stage_name(value: object) -> bool:
    if type(value) is not str or not value or "/" in value or "\\" in value:
        return False
    if ".." in value or value.endswith((".", " ")):
        return False
    try:
        value.encode("ascii")
    except UnicodeEncodeError:
        return False
    return all(character.isalnum() or character in "._-" for character in value)


def _private_observation_directory(info: os.stat_result, device: int) -> bool:
    return (
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.geteuid()
        and info.st_dev == device
        and stat.S_IMODE(info.st_mode) == 0o700
    )


def _private_observation_file(info: os.stat_result, device: int) -> bool:
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.geteuid()
        and info.st_dev == device
        and info.st_nlink == 1
        and stat.S_IMODE(info.st_mode) == 0o600
    )


class _ObservationUnsafe(Exception):
    pass


class _ObservationUnstable(Exception):
    pass


def inspect_declared_stage(
    skills_root: Path,
    stage_name: str,
    manifest: Any,
    expected_root_identity: Any,
    *,
    limits: Optional[ExecutionLimits] = None,
) -> StageObservation:
    """Read and verify one existing stage without creating or owning it.

    The returned classification is observational only.  In particular, a
    matching result does not recreate an ``OwnedStageLease`` or grant any
    authority to publish, clean up, or otherwise mutate the stage.
    """

    try:
        from .transaction_journal import normalize_exact_manifest

        normalized = normalize_exact_manifest(manifest)
    except Exception:
        return StageObservation("unsafe", 0, None)
    selected_limits = limits if type(limits) is ExecutionLimits else ExecutionLimits()
    if (
        not _valid_observation_stage_name(stage_name)
        or not normalized
        or len(normalized) > MAX_DECLARED_FILES
        or sum(entry["size"] for entry in normalized) > selected_limits.max_total_candidate_bytes
        or any(entry["size"] > selected_limits.max_single_file_bytes for entry in normalized)
    ):
        return StageObservation("unsafe", 0, None)

    adapter = RealFilesystemAdapter()
    if not adapter.secure_staging_supported():
        return StageObservation("unsupported", 0, None)
    root_fd = parent_fd = stage_fd = -1
    try:
        canonical_root = Path(os.path.realpath(os.fspath(skills_root)))
        root_fd, _ = adapter._open_absolute_directory(
            canonical_root, "phase5e.fs.stage.unsafe"
        )
        root_before = os.fstat(root_fd)
        if (
            not isinstance(expected_root_identity, dict)
            or expected_root_identity != {
                "kind": "posix-dev-ino",
                "device": root_before.st_dev,
                "inode": root_before.st_ino,
            }
            or not stat.S_ISDIR(root_before.st_mode)
            or root_before.st_uid != os.geteuid()
            or root_before.st_mode & 0o022
        ):
            raise _ObservationUnsafe()
        try:
            visible_parent = os.stat(
                ".cso-staging", dir_fd=root_fd, follow_symlinks=False
            )
        except FileNotFoundError:
            return StageObservation("missing", 0, None)
        if not _private_observation_directory(visible_parent, root_before.st_dev):
            raise _ObservationUnsafe()
        parent_fd = os.open(".cso-staging", _directory_flags(), dir_fd=root_fd)
        parent_before = os.fstat(parent_fd)
        if _metadata(visible_parent) != _metadata(parent_before):
            raise _ObservationUnstable()
        try:
            visible_stage = os.stat(stage_name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return StageObservation("missing", 0, None)
        if not _private_observation_directory(visible_stage, parent_before.st_dev):
            raise _ObservationUnsafe()
        stage_fd = os.open(stage_name, _directory_flags(), dir_fd=parent_fd)
        stage_before = os.fstat(stage_fd)
        if (
            _metadata(visible_stage) != _metadata(stage_before)
            or not _private_observation_directory(stage_before, parent_before.st_dev)
        ):
            raise _ObservationUnstable()

        expected = {
            entry["path"]: (entry["sha256"], entry["size"])
            for entry in normalized
        }
        records: List[Tuple[str, str, int]] = []

        def inspect_directory(directory_fd: int, prefix: Tuple[str, ...]) -> None:
            before = os.fstat(directory_fd)
            if not _private_observation_directory(before, parent_before.st_dev):
                raise _ObservationUnsafe()
            for name in sorted(os.listdir(directory_fd)):
                visible = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                relative = prefix + (name,)
                if stat.S_ISLNK(visible.st_mode):
                    raise _ObservationUnsafe()
                if stat.S_ISDIR(visible.st_mode):
                    if not _private_observation_directory(visible, parent_before.st_dev):
                        raise _ObservationUnsafe()
                    child_fd = -1
                    try:
                        child_fd = os.open(name, _directory_flags(), dir_fd=directory_fd)
                        opened = os.fstat(child_fd)
                        if _metadata(visible) != _metadata(opened):
                            raise _ObservationUnstable()
                        inspect_directory(child_fd, relative)
                    finally:
                        if child_fd >= 0:
                            os.close(child_fd)
                    continue
                if not _private_observation_file(visible, parent_before.st_dev):
                    raise _ObservationUnsafe()
                leaf_fd = -1
                try:
                    leaf_fd = os.open(name, _leaf_read_flags(), dir_fd=directory_fd)
                    opened = os.fstat(leaf_fd)
                    if _metadata(visible) != _metadata(opened):
                        raise _ObservationUnstable()
                    digest = hashlib.sha256()
                    total = 0
                    while True:
                        chunk = os.read(leaf_fd, selected_limits.copy_chunk_bytes)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > selected_limits.max_single_file_bytes:
                            raise _ObservationUnsafe()
                        digest.update(chunk)
                    after = os.fstat(leaf_fd)
                    if _metadata(opened) != _metadata(after) or total != after.st_size:
                        raise _ObservationUnstable()
                    records.append(("/".join(relative), digest.hexdigest(), total))
                finally:
                    if leaf_fd >= 0:
                        os.close(leaf_fd)
            if _metadata(before) != _metadata(os.fstat(directory_fd)):
                raise _ObservationUnstable()

        inspect_directory(stage_fd, ())
        actual = {path: (digest, size) for path, digest, size in records}
        if actual != expected or sum(size for _, _, size in records) > selected_limits.max_total_candidate_bytes:
            raise _ObservationUnsafe()
        stage_after = os.fstat(stage_fd)
        visible_after = os.stat(stage_name, dir_fd=parent_fd, follow_symlinks=False)
        parent_after = os.fstat(parent_fd)
        root_after = os.fstat(root_fd)
        if (
            _metadata(stage_before) != _metadata(stage_after)
            or _metadata(stage_before) != _metadata(visible_after)
            or _metadata(parent_before) != _metadata(parent_after)
            or _metadata(root_before) != _metadata(root_after)
        ):
            raise _ObservationUnstable()
        return StageObservation("matching", len(records), sum(size for _, _, size in records))
    except _ObservationUnstable:
        return StageObservation("unstable", 0, None)
    except (_ObservationUnsafe, _Rejected, OSError, ValueError):
        return StageObservation("unsafe", 0, None)
    finally:
        if stage_fd >= 0:
            os.close(stage_fd)
        if parent_fd >= 0:
            os.close(parent_fd)
        if root_fd >= 0:
            os.close(root_fd)


def stage_declared_candidate(
    request: StageRequest,
    *,
    fs: Optional[FilesystemAdapter] = None,
) -> StageResult:
    """Create and verify a disposable exact-file stage.

    All returned diagnostics are fixed metadata.  Exceptions and supplied path,
    hash, and candidate values are never included in the result.
    """

    try:
        valid_request, declared_files = _validate_request(request)
    except Exception:
        return _result("invalid", "phase5e.fs.input.invalid")

    adapter: FilesystemAdapter = fs if fs is not None else RealFilesystemAdapter()
    if not adapter.secure_staging_supported():
        windows_unsupported = (
            isinstance(adapter, RealFilesystemAdapter)
            and adapter.platform_name == "win32"
        )
        return _result(
            "rejected",
            "phase5e.fs.platform.unsupported",
            file_count=len(declared_files),
            windows_unsupported=windows_unsupported,
        )

    roots: Optional[_RootHandles] = None
    stage: Optional[_StageHandle] = None
    failure_status = "failed"
    failure_reason = "phase5e.fs.operation.failed"
    try:
        roots = adapter.open_roots(valid_request.source_root, valid_request.staging_parent)
        stage = adapter.create_stage(roots, valid_request.candidate_key)
        expected: Dict[str, Tuple[str, int]] = {}
        total = 0
        for declared in declared_files:
            source = adapter.open_source_file(roots, declared.relative_path)
            destination: Optional[_OpenFile] = None
            digest = hashlib.sha256()
            copied = 0
            before = source.metadata
            try:
                destination = adapter.create_stage_file(stage, declared.relative_path)
                while True:
                    chunk = adapter.read(source, valid_request.limits.copy_chunk_bytes)
                    if not chunk:
                        break
                    copied += len(chunk)
                    total += len(chunk)
                    if copied > valid_request.limits.max_single_file_bytes:
                        failure_status = "rejected"
                        failure_reason = "phase5e.fs.resource.single-file-limit"
                        raise _Rejected(failure_reason)
                    if total > valid_request.limits.max_total_candidate_bytes:
                        failure_status = "rejected"
                        failure_reason = "phase5e.fs.resource.total-limit"
                        raise _Rejected(failure_reason)
                    digest.update(chunk)
                    offset = 0
                    while offset < len(chunk):
                        written = adapter.write(destination, chunk[offset:])
                        if type(written) is not int or written <= 0:
                            raise OSError()
                        offset += written
                adapter.flush(destination)
                after = adapter.metadata(source)
                if not _source_unchanged(before, after, copied):
                    failure_status = "rejected"
                    failure_reason = "phase5e.fs.source.changed"
                    raise _Rejected(failure_reason)
                actual_digest = digest.hexdigest()
                if actual_digest != declared.sha256:
                    failure_status = "rejected"
                    failure_reason = "phase5e.fs.source.hash-mismatch"
                    raise _Rejected(failure_reason)
                expected[declared.relative_path] = (actual_digest, copied)
            finally:
                if destination is not None:
                    adapter.close_file(destination)
                adapter.close_file(source)

        verified = adapter.verify_stage(stage, expected, valid_request.limits)
        adapter.finalize_stage(stage)
        digest_value = _manifest_digest(verified)
        stage_id = stage.stage_id
        adapter.close_stage(stage)
        adapter.close_roots(roots)
        stage = None
        roots = None
        return _result(
            "staged",
            "phase5e.fs.staged",
            file_count=len(declared_files),
            total_bytes=total,
            manifest_digest=digest_value,
            stage_id=stage_id,
        )
    except _Rejected as error:
        failure_status = "rejected"
        failure_reason = error.reason_id
    except _CleanupRequired:
        failure_status = "cleanup-required"
        failure_reason = "phase5e.fs.cleanup.required"
    except Exception:
        failure_status = "failed"
        failure_reason = "phase5e.fs.operation.failed"

    if stage is not None:
        try:
            adapter.cleanup_stage(stage)
        except Exception:
            failure_status = "cleanup-required"
            failure_reason = "phase5e.fs.cleanup.required"
    if roots is not None:
        try:
            adapter.close_roots(roots)
        except Exception:
            if failure_status != "cleanup-required":
                failure_status = "cleanup-required"
                failure_reason = "phase5e.fs.cleanup.required"
    return _result(
        failure_status,
        failure_reason,
        file_count=len(declared_files),
    )


def stage_declared_candidate_owned(
    request: StageRequest,
    *,
    fs: Optional[FilesystemAdapter] = None,
) -> StageLeaseOutcome:
    """Stage exact declared files while retaining module-owned live identity."""

    try:
        valid_request, declared_files = _validate_request(request)
    except Exception:
        return StageLeaseOutcome(_result("invalid", "phase5e.fs.input.invalid"), None)

    adapter: FilesystemAdapter = fs if fs is not None else RealFilesystemAdapter()
    if not adapter.secure_staging_supported():
        windows_unsupported = (
            isinstance(adapter, RealFilesystemAdapter)
            and adapter.platform_name == "win32"
        )
        return StageLeaseOutcome(
            _result(
                "rejected",
                "phase5e.fs.platform.unsupported",
                file_count=len(declared_files),
                windows_unsupported=windows_unsupported,
            ),
            None,
        )

    roots: Optional[_RootHandles] = None
    stage: Optional[_StageHandle] = None
    failure_status = "failed"
    failure_reason = "phase5e.fs.operation.failed"
    try:
        roots = adapter.open_roots(valid_request.source_root, valid_request.staging_parent)
        stage = adapter.create_stage(roots, valid_request.candidate_key)
        expected: Dict[str, Tuple[str, int]] = {}
        total = 0
        for declared in declared_files:
            source = adapter.open_source_file(roots, declared.relative_path)
            destination: Optional[_OpenFile] = None
            digest = hashlib.sha256()
            copied = 0
            before = source.metadata
            try:
                destination = adapter.create_stage_file(stage, declared.relative_path)
                while True:
                    chunk = adapter.read(source, valid_request.limits.copy_chunk_bytes)
                    if not chunk:
                        break
                    copied += len(chunk)
                    total += len(chunk)
                    if copied > valid_request.limits.max_single_file_bytes:
                        failure_status = "rejected"
                        failure_reason = "phase5e.fs.resource.single-file-limit"
                        raise _Rejected(failure_reason)
                    if total > valid_request.limits.max_total_candidate_bytes:
                        failure_status = "rejected"
                        failure_reason = "phase5e.fs.resource.total-limit"
                        raise _Rejected(failure_reason)
                    digest.update(chunk)
                    offset = 0
                    while offset < len(chunk):
                        written = adapter.write(destination, chunk[offset:])
                        if type(written) is not int or written <= 0:
                            raise OSError()
                        offset += written
                adapter.flush(destination)
                if not _source_unchanged(before, adapter.metadata(source), copied):
                    failure_status = "rejected"
                    failure_reason = "phase5e.fs.source.changed"
                    raise _Rejected(failure_reason)
                actual_digest = digest.hexdigest()
                if actual_digest != declared.sha256:
                    failure_status = "rejected"
                    failure_reason = "phase5e.fs.source.hash-mismatch"
                    raise _Rejected(failure_reason)
                expected[declared.relative_path] = (actual_digest, copied)
            finally:
                if destination is not None:
                    adapter.close_file(destination)
                adapter.close_file(source)
        verified = adapter.verify_stage(stage, expected, valid_request.limits)
        adapter.finalize_stage(stage)
        digest_value = _manifest_digest(verified)
        stage_id = stage.stage_id
        if roots.source_fd >= 0:
            os.close(roots.source_fd)
            roots.source_fd = -1
        lease = OwnedStageLease(
            adapter, roots, stage, expected, valid_request.limits, digest_value, total
        )
        return StageLeaseOutcome(
            _result(
                "staged",
                "phase5e.fs.staged",
                file_count=len(declared_files),
                total_bytes=total,
                manifest_digest=digest_value,
                stage_id=stage_id,
            ),
            lease,
        )
    except _Rejected as error:
        failure_status = "rejected"
        failure_reason = error.reason_id
    except _CleanupRequired:
        failure_status = "cleanup-required"
        failure_reason = "phase5e.fs.cleanup.required"
    except Exception:
        failure_status = "failed"
        failure_reason = "phase5e.fs.operation.failed"
    if stage is not None:
        try:
            adapter.cleanup_stage(stage)
        except Exception:
            failure_status = "cleanup-required"
            failure_reason = "phase5e.fs.cleanup.required"
    if roots is not None:
        try:
            adapter.close_roots(roots)
        except Exception:
            failure_status = "cleanup-required"
            failure_reason = "phase5e.fs.cleanup.required"
    return StageLeaseOutcome(_result(failure_status, failure_reason, file_count=len(declared_files)), None)


def revalidate_owned_stage(lease: object) -> bool:
    return type(lease) is OwnedStageLease and lease._revalidate()


def owned_stage_matches_parent(lease: object, device: int, inode: int) -> bool:
    return (
        type(lease) is OwnedStageLease
        and type(device) is int
        and type(inode) is int
        and lease._matches_parent(device, inode)
    )

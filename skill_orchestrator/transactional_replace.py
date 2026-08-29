"""Target-bound verified staging only; final target replacement is not implemented."""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol, Tuple

from .transactional_fs import (
    DeclaredFile,
    ExecutionLimits,
    OwnedStageLease,
    StageRequest,
    owned_stage_matches_parent,
    revalidate_owned_stage,
    stage_declared_candidate_owned,
)


STATUSES = ("prepared", "rejected", "invalid", "failed", "cleanup-required")
TARGET_STATES = ("absent", "existing-unowned", "unsafe", "invalid")
_RESERVED = {"con", "prn", "aux", "nul", *("com%d" % item for item in range(1, 10)), *("lpt%d" % item for item in range(1, 10))}
_REASONS = {
    "input.invalid",
    "platform.unsupported",
    "skills-root.unsafe",
    "target-key.invalid",
    "target.exists",
    "staging-namespace.unsafe",
    "staging-namespace.cross-device",
    "stage.rejected",
    "stage.failed",
    "lease.binding-mismatch",
    "lease.revalidation-failed",
    "target.appeared",
    "cleanup.required",
    "prepared",
}
_LIMITATIONS = (
    "phase5e.replace.limit.prepared-stage-not-installed",
    "phase5e.replace.limit.final-target-not-mutated",
    "phase5e.replace.limit.not-execution-authority",
    "phase5e.replace.limit.final-target-race-not-reserved",
    "phase5e.replace.limit.shared-mutation-lock-not-integrated",
    "phase5e.replace.limit.transaction-journal-not-implemented",
    "phase5e.replace.limit.installed-state-not-implemented",
    "phase5e.replace.limit.crash-recovery-not-implemented",
    "phase5e.replace.limit.windows-secure-mutation-not-implemented",
    "phase5e.replace.limit.remote-fetch-disabled",
    "phase5e.replace.limit.runtime-capability-enforcement-not-implemented",
    "phase5e.replace.limit.activation-post-install-not-implemented",
)


@dataclass(frozen=True)
class TargetStageRequest:
    skills_root: Path
    target_key: str
    source_root: Path
    declared_files: Tuple[DeclaredFile, ...]
    limits: ExecutionLimits


@dataclass(frozen=True)
class TargetStageResult:
    status: str
    target_state: str
    stage_id: Optional[str]
    file_count: int
    total_bytes: Optional[int]
    manifest_digest: Optional[str]
    reason_ids: Tuple[str, ...]
    limitations: Tuple[str, ...] = _LIMITATIONS
    truncated: bool = False


@dataclass(frozen=True)
class TargetStageOutcome:
    result: TargetStageResult
    lease: Optional[OwnedStageLease]


class TargetFilesystemAdapter(Protocol):
    def target_state(self, root_fd: int, target_key: str) -> str: ...


class RealTargetFilesystemAdapter:
    def target_state(self, root_fd: int, target_key: str) -> str:
        try:
            os.stat(target_key, dir_fd=root_fd, follow_symlinks=False)
        except FileNotFoundError:
            return "absent"
        except OSError:
            return "unsafe"
        return "existing"


class _UnsafeNamespace(Exception):
    pass


class _CrossDeviceNamespace(Exception):
    pass


def _result(status: str, target_state: str, reason: str, *, stage=None) -> TargetStageOutcome:
    return TargetStageOutcome(
        TargetStageResult(
            status=status if status in STATUSES else "invalid",
            target_state=target_state if target_state in TARGET_STATES else "invalid",
            stage_id=None if stage is None else stage.stage_id,
            file_count=0 if stage is None else stage.file_count,
            total_bytes=None if stage is None else stage.total_bytes,
            manifest_digest=None if stage is None else stage.manifest_digest,
            reason_ids=("phase5e.replace." + (reason if reason in _REASONS else "input.invalid"),),
        ),
        None,
    )


def _valid_key(value: object) -> bool:
    if type(value) is not str or not value or value in {".", ".."} or ".." in value:
        return False
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError:
        return False
    if len(encoded) > 100 or value[0] not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
        return False
    if any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-" for char in value):
        return False
    if value.endswith((".", " ")) or value.split(".", 1)[0].casefold() in _RESERVED:
        return False
    return True


def _directory_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _open_root(value: Path) -> Tuple[int, Tuple[int, int]]:
    if not value.is_absolute() or value == Path(value.anchor) or value == Path.home():
        raise OSError()
    descriptor = os.open(value.anchor, _directory_flags())
    try:
        for part in value.parts[1:]:
            next_descriptor = os.open(part, _directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise OSError()
        return descriptor, (info.st_dev, info.st_ino)
    except Exception:
        os.close(descriptor)
        raise


def _open_namespace(root_fd: int, root_identity: Tuple[int, int]) -> Tuple[int, Tuple[int, int]]:
    try:
        os.mkdir(".cso-staging", 0o700, dir_fd=root_fd)
    except FileExistsError:
        pass
    except OSError as error:
        raise _UnsafeNamespace() from error
    try:
        descriptor = os.open(".cso-staging", _directory_flags(), dir_fd=root_fd)
    except OSError as error:
        raise _UnsafeNamespace() from error
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode):
            raise _UnsafeNamespace()
        if info.st_dev != root_identity[0]:
            raise _CrossDeviceNamespace()
        return descriptor, (info.st_dev, info.st_ino)
    except Exception:
        os.close(descriptor)
        raise


def _source_overlaps(request: TargetStageRequest) -> bool:
    if not isinstance(request.source_root, Path):
        return True
    source = os.path.abspath(os.fspath(request.source_root))
    root = os.path.abspath(os.fspath(request.skills_root))
    return source == root or source.startswith(root + os.sep)


def prepare_target_bound_stage(
    request: TargetStageRequest,
    *,
    fs: Optional[TargetFilesystemAdapter] = None,
) -> TargetStageOutcome:
    """Prepare one exact verified stage; never create or mutate the final target."""

    if os.name == "nt":
        return _result("rejected", "invalid", "platform.unsupported")
    if type(request) is not TargetStageRequest or not isinstance(request.skills_root, Path):
        return _result("invalid", "invalid", "input.invalid")
    if not _valid_key(request.target_key):
        return _result("invalid", "invalid", "target-key.invalid")
    if _source_overlaps(request):
        return _result("rejected", "unsafe", "skills-root.unsafe")

    root_fd = -1
    namespace_fd = -1
    lease: Optional[OwnedStageLease] = None
    adapter: TargetFilesystemAdapter = fs if fs is not None else RealTargetFilesystemAdapter()
    try:
        root_fd, root_identity = _open_root(request.skills_root)
        initial_target = adapter.target_state(root_fd, request.target_key)
        if initial_target != "absent":
            return _result(
                "rejected",
                "existing-unowned" if initial_target == "existing" else "unsafe",
                "target.exists",
            )
        namespace_fd, namespace_identity = _open_namespace(root_fd, root_identity)
        stage_request = StageRequest(
            source_root=request.source_root,
            staging_parent=request.skills_root / ".cso-staging",
            candidate_key=request.target_key,
            declared_files=request.declared_files,
            limits=request.limits,
        )
        stage_outcome = stage_declared_candidate_owned(stage_request)
        if stage_outcome.lease is None:
            status = "rejected" if stage_outcome.result.status == "rejected" else stage_outcome.result.status
            reason = "stage.rejected" if status == "rejected" else "stage.failed"
            return _result(status, "absent", reason, stage=stage_outcome.result)
        lease = stage_outcome.lease
        if not owned_stage_matches_parent(lease, *namespace_identity) or not revalidate_owned_stage(lease):
            cleaned = lease.cleanup()
            return _result(
                "cleanup-required" if cleaned.status != "cleaned" else "rejected",
                "absent",
                "cleanup.required" if cleaned.status != "cleaned" else "lease.binding-mismatch",
                stage=stage_outcome.result,
            )
        final_target = adapter.target_state(root_fd, request.target_key)
        if final_target != "absent":
            cleaned = lease.cleanup()
            return _result(
                "cleanup-required" if cleaned.status != "cleaned" else "rejected",
                "existing-unowned" if final_target == "existing" else "unsafe",
                "cleanup.required" if cleaned.status != "cleaned" else "target.appeared",
                stage=stage_outcome.result,
            )
        return TargetStageOutcome(
            TargetStageResult(
                "prepared", "absent", stage_outcome.result.stage_id,
                stage_outcome.result.file_count, stage_outcome.result.total_bytes,
                stage_outcome.result.manifest_digest, ("phase5e.replace.prepared",),
            ),
            lease,
        )
    except _CrossDeviceNamespace:
        return _result("rejected", "unsafe", "staging-namespace.cross-device")
    except _UnsafeNamespace:
        return _result("rejected", "unsafe", "staging-namespace.unsafe")
    except OSError:
        return _result("rejected", "unsafe", "skills-root.unsafe")
    except Exception:
        return _result("failed", "unsafe", "stage.failed")
    finally:
        if namespace_fd >= 0:
            os.close(namespace_fd)
        if root_fd >= 0:
            os.close(root_fd)


__all__ = [
    "STATUSES", "TARGET_STATES", "TargetStageOutcome", "TargetStageRequest",
    "TargetStageResult", "prepare_target_bound_stage",
]

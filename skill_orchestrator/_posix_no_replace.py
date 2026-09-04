"""Private POSIX directory-leaf no-replace rename primitive.

This module is deliberately not a CSO publication coordinator.  It accepts
only borrowed directory descriptors and one POSIX pathname component per side.
It has no path, stage, target, lease, lock, journal, authorization, recovery,
engine, or CLI integration.
"""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import sys
from dataclasses import dataclass


STATUSES = (
    "succeeded",
    "destination-exists",
    "source-missing",
    "source-invalid",
    "invalid-endpoint",
    "cross-device",
    "unsupported-platform",
    "unsupported-syscall",
    "unsupported-filesystem",
    "permission-denied",
    "indeterminate",
)
MUTATION_CERTAINTIES = ("succeeded", "no-mutation", "indeterminate")
PLATFORMS = ("darwin", "linux", "unsupported")
REASON_IDS = (
    "native-success",
    "unsupported-platform",
    "invalid-source-parent",
    "invalid-destination-parent",
    "cross-device-precheck",
    "same-source-destination",
    "source-missing-precheck",
    "source-not-directory",
    "source-symlink",
    "source-unavailable",
    "unsupported-syscall",
    "native-destination-exists",
    "native-source-missing",
    "native-cross-device",
    "native-unsupported",
    "native-permission-denied",
    "native-symlink-refused",
    "native-indeterminate",
)

_RENAME_EXCL = 0x00000004
_RENAME_NOFOLLOW_ANY = 0x00000010
_RENAME_NOREPLACE = 0x00000001


@dataclass(frozen=True)
class NativeNoReplaceResult:
    """Bounded facts from one native rename attempt, never publication state."""

    platform: str
    status: str
    attempted: bool
    mutation_certainty: str
    reason_id: str

    def __post_init__(self) -> None:
        if self.platform not in PLATFORMS:
            raise ValueError("platform is unsupported")
        if self.status not in STATUSES:
            raise ValueError("status is unsupported")
        if type(self.attempted) is not bool:
            raise TypeError("attempted must be a bool")
        if self.mutation_certainty not in MUTATION_CERTAINTIES:
            raise ValueError("mutation_certainty is unsupported")
        if self.reason_id not in REASON_IDS:
            raise ValueError("reason_id is unsupported")


def _result(
    platform: str,
    status: str,
    attempted: bool,
    mutation_certainty: str,
    reason_id: str,
) -> NativeNoReplaceResult:
    return NativeNoReplaceResult(
        platform=platform,
        status=status,
        attempted=attempted,
        mutation_certainty=mutation_certainty,
        reason_id=reason_id,
    )


def _descriptor(value: object, label: str) -> int:
    if type(value) is not int:
        raise TypeError("%s must be an int file descriptor" % label)
    if value < 0:
        raise ValueError("%s must be a non-negative file descriptor" % label)
    return value


def _leaf_bytes(value: object, label: str) -> bytes:
    if type(value) is not str:
        raise TypeError("%s must be a str" % label)
    if value in ("", ".", "..") or "/" in value or "\x00" in value:
        raise ValueError("%s must be one POSIX pathname component" % label)
    try:
        encoded = value.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ValueError("%s must be ASCII" % label) from exc
    if not 1 <= len(encoded) <= 255:
        raise ValueError("%s must be between 1 and 255 bytes" % label)
    return encoded


def _parent_info(descriptor: int, *, source: bool):
    try:
        info = os.fstat(descriptor)
    except OSError:
        return None, _result(
            "darwin" if sys.platform == "darwin" else "linux",
            "invalid-endpoint",
            False,
            "no-mutation",
            "invalid-source-parent" if source else "invalid-destination-parent",
        )
    if not stat.S_ISDIR(info.st_mode):
        return None, _result(
            "darwin" if sys.platform == "darwin" else "linux",
            "invalid-endpoint",
            False,
            "no-mutation",
            "invalid-source-parent" if source else "invalid-destination-parent",
        )
    return info, None


def _source_precheck(source_parent_fd: int, source_leaf: str, platform: str):
    try:
        info = os.stat(source_leaf, dir_fd=source_parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        return _result(
            platform, "source-missing", False, "no-mutation", "source-missing-precheck"
        )
    except OSError:
        return _result(platform, "source-invalid", False, "no-mutation", "source-unavailable")
    if stat.S_ISLNK(info.st_mode):
        return _result(platform, "source-invalid", False, "no-mutation", "source-symlink")
    if not stat.S_ISDIR(info.st_mode):
        return _result(
            platform, "source-invalid", False, "no-mutation", "source-not-directory"
        )
    return None


def _native_function(name: str):
    try:
        library = ctypes.CDLL(None, use_errno=True)
        function = getattr(library, name)
    except (AttributeError, OSError):
        return None
    function.restype = ctypes.c_int
    function.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    return function


def _darwin_result(error_number: int) -> NativeNoReplaceResult:
    if error_number == errno.EEXIST:
        return _result("darwin", "destination-exists", True, "no-mutation", "native-destination-exists")
    if error_number == errno.ENOENT:
        return _result("darwin", "source-missing", True, "no-mutation", "native-source-missing")
    if error_number == errno.EXDEV:
        return _result("darwin", "cross-device", True, "no-mutation", "native-cross-device")
    if error_number in (errno.EINVAL, errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)):
        return _result("darwin", "unsupported-filesystem", True, "no-mutation", "native-unsupported")
    if error_number in (errno.EACCES, errno.EPERM, errno.EROFS, errno.EBUSY):
        return _result("darwin", "permission-denied", True, "no-mutation", "native-permission-denied")
    if error_number == errno.ELOOP:
        return _result("darwin", "indeterminate", True, "no-mutation", "native-symlink-refused")
    return _result("darwin", "indeterminate", True, "indeterminate", "native-indeterminate")


def _linux_result(error_number: int) -> NativeNoReplaceResult:
    if error_number == errno.ENOSYS:
        return _result("linux", "unsupported-syscall", True, "no-mutation", "unsupported-syscall")
    if error_number == errno.EEXIST:
        return _result("linux", "destination-exists", True, "indeterminate", "native-destination-exists")
    if error_number == errno.ENOENT:
        return _result("linux", "source-missing", True, "indeterminate", "native-source-missing")
    if error_number == errno.EXDEV:
        return _result("linux", "cross-device", True, "indeterminate", "native-cross-device")
    if error_number in (errno.EINVAL, errno.ENOTSUP, getattr(errno, "EOPNOTSUPP", errno.ENOTSUP)):
        return _result("linux", "unsupported-filesystem", True, "indeterminate", "native-unsupported")
    if error_number in (errno.EACCES, errno.EPERM, errno.EROFS):
        return _result("linux", "permission-denied", True, "indeterminate", "native-permission-denied")
    return _result("linux", "indeterminate", True, "indeterminate", "native-indeterminate")


def _move_directory_leaf_no_replace(
    source_parent_fd: int,
    source_leaf: str,
    destination_parent_fd: int,
    destination_leaf: str,
) -> NativeNoReplaceResult:
    """Move one directory leaf without overwrite, or return bounded lower-level facts.

    The parent descriptors are borrowed.  Source validation is necessarily
    TOCTOU-sensitive and does not bind the source name to its observed inode.
    A successful result establishes only that the native namespace syscall
    returned success; it is not durable publication, verification, or commit.
    """

    if sys.platform not in ("darwin", "linux"):
        return _result("unsupported", "unsupported-platform", False, "no-mutation", "unsupported-platform")

    platform = sys.platform
    source_parent_fd = _descriptor(source_parent_fd, "source_parent_fd")
    destination_parent_fd = _descriptor(destination_parent_fd, "destination_parent_fd")
    source_leaf_bytes = _leaf_bytes(source_leaf, "source_leaf")
    destination_leaf_bytes = _leaf_bytes(destination_leaf, "destination_leaf")

    source_parent, failure = _parent_info(source_parent_fd, source=True)
    if failure is not None:
        return failure
    destination_parent, failure = _parent_info(destination_parent_fd, source=False)
    if failure is not None:
        return failure
    if source_parent.st_dev != destination_parent.st_dev:
        return _result(platform, "cross-device", False, "no-mutation", "cross-device-precheck")
    if (
        source_parent.st_dev == destination_parent.st_dev
        and source_parent.st_ino == destination_parent.st_ino
        and source_leaf == destination_leaf
    ):
        return _result(platform, "source-invalid", False, "no-mutation", "same-source-destination")

    failure = _source_precheck(source_parent_fd, source_leaf, platform)
    if failure is not None:
        return failure

    if platform == "darwin":
        function = _native_function("renameatx_np")
        if function is None:
            return _result(platform, "unsupported-syscall", False, "no-mutation", "unsupported-syscall")
        flags = _RENAME_EXCL | _RENAME_NOFOLLOW_ANY
    else:
        function = _native_function("renameat2")
        if function is None:
            return _result(platform, "unsupported-syscall", False, "no-mutation", "unsupported-syscall")
        flags = _RENAME_NOREPLACE

    ctypes.set_errno(0)
    result = function(
        source_parent_fd,
        source_leaf_bytes,
        destination_parent_fd,
        destination_leaf_bytes,
        flags,
    )
    if result == 0:
        return _result(platform, "succeeded", True, "succeeded", "native-success")
    error_number = ctypes.get_errno()
    if platform == "darwin":
        return _darwin_result(error_number)
    return _linux_result(error_number)


__all__ = [
    "MUTATION_CERTAINTIES",
    "PLATFORMS",
    "REASON_IDS",
    "STATUSES",
    "NativeNoReplaceResult",
]

"""Shared, fail-fast mutation locks for the install and skills domains."""

from __future__ import annotations

import json
import os
import secrets
import stat
import sys
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any, Iterable, List, Optional, Tuple

from .errors import OperationError, SecurityError


_CONTENTION_MESSAGE = "another orchestrator mutation is in progress"
_INSTALL_STATE = "state"
_SKILLS_STATE = ".cso-state"
_LOCK_NAME = "mutation.lock"


def _is_reparse_info(info: os.stat_result) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def _current_uid() -> Optional[int]:
    if hasattr(os, "geteuid"):
        return os.geteuid()
    if hasattr(os, "getuid"):
        return os.getuid()
    return None


def _identity(info: os.stat_result) -> Tuple[int, int]:
    return (getattr(info, "st_dev", -1), getattr(info, "st_ino", -1))


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return _identity(left) == _identity(right)


def _allowed_macos_alias(path: Path, *, is_candidate: bool) -> bool:
    if is_candidate or sys.platform != "darwin":
        return False
    aliases = {
        Path("/tmp"): Path("/private/tmp"),
        Path("/var"): Path("/private/var"),
    }
    expected = aliases.get(path)
    return expected is not None and Path(os.path.realpath(os.fspath(path))) == expected


def _check_existing_ancestors(path: Path) -> None:
    cursor = Path(os.path.abspath(os.fspath(path)))
    candidate = cursor
    while True:
        try:
            info = os.lstat(os.fspath(cursor))
        except FileNotFoundError:
            info = None
        except OSError as exc:
            raise SecurityError("mutation lock namespace is unavailable") from exc
        if info is not None and _is_reparse_info(info):
            if not _allowed_macos_alias(cursor, is_candidate=(cursor == candidate)):
                raise SecurityError("mutation lock namespace contains a symlink or reparse point")
        if cursor.parent == cursor:
            break
        cursor = cursor.parent


def _validate_root(root: Path) -> Path:
    try:
        candidate = Path(root).expanduser()
    except (OSError, RuntimeError, TypeError) as exc:
        raise SecurityError("mutation lock root is invalid") from exc
    if not candidate.is_absolute():
        raise SecurityError("mutation lock root must be absolute")
    _check_existing_ancestors(candidate)
    try:
        before_open = os.lstat(os.fspath(candidate))
    except OSError as exc:
        raise SecurityError("mutation lock root is unavailable") from exc
    if _is_reparse_info(before_open) or not stat.S_ISDIR(before_open.st_mode):
        raise SecurityError("mutation lock root is not a safe directory")
    canonical = Path(os.path.realpath(os.fspath(candidate)))
    if canonical == Path(canonical.anchor) or canonical == Path.home().resolve(strict=False):
        raise SecurityError("mutation lock root is too broad")
    _check_existing_ancestors(canonical)
    try:
        canonical_info = os.lstat(os.fspath(canonical))
    except OSError as exc:
        raise SecurityError("mutation lock root is unavailable") from exc
    if _is_reparse_info(canonical_info) or not stat.S_ISDIR(canonical_info.st_mode):
        raise SecurityError("mutation lock root is not a safe directory")
    if os.name != "nt":
        uid = _current_uid()
        if uid is not None and canonical_info.st_uid != uid:
            raise SecurityError("mutation lock root is not owned by the current user")
        if stat.S_IMODE(canonical_info.st_mode) & 0o022:
            raise SecurityError("mutation lock root is group or world writable")
    return canonical


def _open_directory_flags() -> int:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    return flags


def _open_root(root: Path) -> Tuple[int, os.stat_result]:
    expected = os.lstat(os.fspath(root))
    flags = _open_directory_flags()
    fd: Optional[int] = None
    try:
        if os.name == "nt":
            fd = os.open(os.fspath(root), flags)
        else:
            fd = os.open(os.fspath(root.anchor), flags)
            for component in root.parts[1:]:
                next_fd = os.open(component, flags, dir_fd=fd)
                os.close(fd)
                fd = next_fd
    except OSError as exc:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        raise SecurityError("mutation lock root cannot be opened safely") from exc
    try:
        actual = os.fstat(fd)
        if not _same_identity(expected, actual) or _is_reparse_info(actual) or not stat.S_ISDIR(actual.st_mode):
            raise SecurityError("mutation lock root changed during lock setup")
        if os.name != "nt":
            uid = _current_uid()
            if uid is not None and actual.st_uid != uid:
                raise SecurityError("mutation lock root is not owned by the current user")
            if stat.S_IMODE(actual.st_mode) & 0o022:
                raise SecurityError("mutation lock root is group or world writable")
        return fd, actual
    except Exception:
        os.close(fd)
        raise


def _stat_at(parent_fd: int, name: str) -> os.stat_result:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise SecurityError("mutation lock namespace is unavailable") from exc


def _validate_state(info: os.stat_result, *, skills_state: bool) -> None:
    if _is_reparse_info(info) or not stat.S_ISDIR(info.st_mode):
        raise SecurityError("mutation lock state namespace is not a safe directory")
    if os.name == "nt":
        return
    uid = _current_uid()
    if uid is not None and info.st_uid != uid:
        raise SecurityError("mutation lock state namespace is not owned by the current user")
    mode = stat.S_IMODE(info.st_mode)
    if skills_state:
        if mode & 0o077:
            raise SecurityError("skills mutation state namespace is not private")
    elif mode & 0o022:
        raise SecurityError("install mutation state namespace is group or world writable")


def _open_state_posix(root_fd: int, state_name: str, *, skills_state: bool) -> int:
    created = False
    try:
        info = _stat_at(root_fd, state_name)
    except FileNotFoundError:
        try:
            os.mkdir(state_name, 0o700 if skills_state else 0o755, dir_fd=root_fd)
        except FileExistsError:
            pass
        except OSError as mkdir_error:
            raise SecurityError("mutation lock state namespace cannot be created") from mkdir_error
        else:
            created = True
        info = _stat_at(root_fd, state_name)
    _validate_state(info, skills_state=skills_state)
    try:
        fd = os.open(state_name, _open_directory_flags(), dir_fd=root_fd)
    except OSError as exc:
        raise SecurityError("mutation lock state namespace cannot be opened safely") from exc
    try:
        actual = os.fstat(fd)
        if not _same_identity(info, actual):
            raise SecurityError("mutation lock state namespace changed during lock setup")
        if created and skills_state:
            os.fchmod(fd, 0o700)
            actual = os.fstat(fd)
        _validate_state(actual, skills_state=skills_state)
        return fd
    except Exception:
        os.close(fd)
        raise


def _validate_lock(info: os.stat_result) -> None:
    if _is_reparse_info(info) or not stat.S_ISREG(info.st_mode):
        raise SecurityError("mutation lock file is not a safe regular file")
    if os.name == "nt":
        return
    uid = _current_uid()
    if uid is not None and info.st_uid != uid:
        raise SecurityError("mutation lock file is not owned by the current user")
    if info.st_nlink != 1:
        raise SecurityError("mutation lock file has unexpected links")
    if stat.S_IMODE(info.st_mode) != 0o600:
        raise SecurityError("mutation lock file does not have mode 0600")


def _open_lock_posix(state_fd: int) -> int:
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    created = False
    try:
        visible = os.stat(_LOCK_NAME, dir_fd=state_fd, follow_symlinks=False)
        _validate_lock(visible)
    except FileNotFoundError:
        try:
            fd = os.open(
                _LOCK_NAME,
                flags | os.O_CREAT | os.O_EXCL,
                0o600,
                dir_fd=state_fd,
            )
        except FileExistsError:
            try:
                fd = os.open(_LOCK_NAME, flags, dir_fd=state_fd)
            except OSError as exc:
                raise SecurityError("mutation lock file cannot be opened safely") from exc
        except OSError as exc:
            raise SecurityError("mutation lock file cannot be created safely") from exc
        else:
            created = True
    except SecurityError:
        raise
    except OSError as exc:
        raise SecurityError("mutation lock file is unavailable") from exc
    else:
        try:
            fd = os.open(_LOCK_NAME, flags, dir_fd=state_fd)
        except OSError as exc:
            raise SecurityError("mutation lock file cannot be opened safely") from exc
    try:
        actual = os.fstat(fd)
        if created:
            os.fchmod(fd, 0o600)
            actual = os.fstat(fd)
        visible = os.stat(_LOCK_NAME, dir_fd=state_fd, follow_symlinks=False)
        if not _same_identity(actual, visible):
            raise SecurityError("mutation lock file changed during lock setup")
        _validate_lock(actual)
        return fd
    except Exception:
        os.close(fd)
        raise


def _check_windows_path(path: Path, *, directory: bool) -> Optional[os.stat_result]:
    _check_existing_ancestors(path)
    try:
        info = os.lstat(os.fspath(path))
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise SecurityError("mutation lock namespace is unavailable") from exc
    if _is_reparse_info(info) or (directory and not stat.S_ISDIR(info.st_mode)):
        raise SecurityError("mutation lock namespace is not safe")
    if not directory and not stat.S_ISREG(info.st_mode):
        raise SecurityError("mutation lock file is not a safe regular file")
    return info


def _prepare_windows_state(root: Path, state_name: str, *, skills_state: bool) -> Path:
    state_path = root / state_name
    info = _check_windows_path(state_path, directory=True)
    created = False
    if info is None:
        try:
            state_path.mkdir(mode=0o700 if skills_state else 0o755)
        except FileExistsError:
            pass
        except OSError as exc:
            raise SecurityError("mutation lock state namespace cannot be created") from exc
        else:
            created = True
        info = _check_windows_path(state_path, directory=True)
    if info is None:
        raise SecurityError("mutation lock state namespace is unavailable")
    _validate_state(info, skills_state=skills_state)
    if created and skills_state:
        try:
            state_path.chmod(0o700)
        except OSError as exc:
            raise SecurityError("skills mutation state namespace cannot be made private") from exc
    return state_path


def _open_lock_windows(state_path: Path) -> int:
    lock_path = state_path / _LOCK_NAME
    visible = _check_windows_path(lock_path, directory=False)
    flags = os.O_RDWR | os.O_CREAT
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    created = False
    try:
        if visible is None:
            try:
                fd = os.open(os.fspath(lock_path), flags | os.O_EXCL, 0o600)
                created = True
            except FileExistsError:
                fd = os.open(os.fspath(lock_path), flags)
        else:
            fd = os.open(os.fspath(lock_path), flags)
    except OSError as exc:
        raise SecurityError("mutation lock file cannot be opened safely") from exc
    try:
        actual = os.fstat(fd)
        if created:
            try:
                os.chmod(os.fspath(lock_path), 0o600)
            except OSError:
                pass
            actual = os.fstat(fd)
        current = _check_windows_path(lock_path, directory=False)
        if current is None or not _same_identity(actual, current):
            raise SecurityError("mutation lock file changed during lock setup")
        _validate_lock(actual)
        return fd
    except Exception:
        os.close(fd)
        raise


def _is_contention(exc: OSError) -> bool:
    return getattr(exc, "errno", None) in {13, 11, 35, 36}


def _acquire_os_lock(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        if _is_contention(exc):
            raise OperationError(_CONTENTION_MESSAGE) from exc
        raise OperationError("could not acquire the orchestrator mutation lock") from exc


def _release_os_lock(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)
    except OSError:
        pass


def _write_all(fd: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        offset += os.write(fd, payload[offset:])


def _write_payload(fd: int) -> None:
    payload = json.dumps(
        {"pid": os.getpid(), "token": secrets.token_hex(16)},
        separators=(",", ":"),
    ).encode("ascii")
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    _write_all(fd, payload)
    os.fsync(fd)


class _MutationResource:
    def __init__(self, root: Path, state_name: str, *, skills_state: bool) -> None:
        self.root = root
        self.state_name = state_name
        self.skills_state = skills_state
        self.key = os.path.normcase(os.fspath(root / state_name / _LOCK_NAME))
        self.root_fd: Optional[int] = None
        self.state_fd: Optional[int] = None
        self.lock_fd: Optional[int] = None
        self.state_path: Optional[Path] = None
        self.root_identity: Optional[Tuple[int, int]] = None
        self.locked = False

    def acquire(self) -> None:
        root_fd: Optional[int] = None
        state_fd: Optional[int] = None
        lock_fd: Optional[int] = None
        locked = False
        try:
            _validate_root(self.root)
            if os.name == "nt":
                state_path = _prepare_windows_state(
                    self.root,
                    self.state_name,
                    skills_state=self.skills_state,
                )
                lock_fd = _open_lock_windows(state_path)
            else:
                root_fd, _ = _open_root(self.root)
                state_fd = _open_state_posix(
                    root_fd,
                    self.state_name,
                    skills_state=self.skills_state,
                )
                lock_fd = _open_lock_posix(state_fd)
            if os.name == "nt" and os.fstat(lock_fd).st_size == 0:
                os.write(lock_fd, b"\0")
            elif os.name != "nt" and os.fstat(lock_fd).st_size == 0:
                os.write(lock_fd, b"\0")
            _acquire_os_lock(lock_fd)
            locked = True
            _write_payload(lock_fd)
            self.root_fd = root_fd
            self.state_fd = state_fd
            self.lock_fd = lock_fd
            self.state_path = state_path if os.name == "nt" else None
            self.root_identity = _identity(os.fstat(root_fd)) if root_fd is not None else None
            self.locked = True
        except Exception:
            if locked and lock_fd is not None:
                _release_os_lock(lock_fd)
            for fd in (lock_fd, state_fd, root_fd):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            raise

    def release(self) -> None:
        lock_fd = self.lock_fd
        state_fd = self.state_fd
        root_fd = self.root_fd
        self.lock_fd = None
        self.state_fd = None
        self.root_fd = None
        self.state_path = None
        self.root_identity = None
        self.locked = False
        if lock_fd is not None:
            _release_os_lock(lock_fd)
        for fd in (lock_fd, state_fd, root_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass


class MutationLockSet(AbstractContextManager):
    """Acquire one or more fixed-domain mutation locks in total order."""

    def __init__(self, resources: Iterable[_MutationResource]) -> None:
        self._resources = _deduplicate_and_sort(resources)
        self._acquired: List[_MutationResource] = []
        self._skills_lock_token: Optional[object] = None
        self._skills_lock_proof: Optional["_HeldSkillsLock"] = None

    @classmethod
    def for_engine(cls, install_root: Path, skills_root: Path) -> "MutationLockSet":
        install = _validate_root(Path(install_root))
        skills = _validate_root(Path(skills_root))
        resources = (
            _MutationResource(install, _INSTALL_STATE, skills_state=False),
            _MutationResource(skills, _SKILLS_STATE, skills_state=True),
        )
        return cls(_deduplicate_and_sort(resources))

    @classmethod
    def for_skills(cls, skills_root: Path) -> "MutationLockSet":
        skills = _validate_root(Path(skills_root))
        return cls(
            _deduplicate_and_sort(
                (_MutationResource(skills, _SKILLS_STATE, skills_state=True),)
            )
        )

    def __enter__(self) -> "MutationLockSet":
        acquired: List[_MutationResource] = []
        try:
            for resource in self._resources:
                resource.acquire()
                acquired.append(resource)
        except Exception:
            for resource in reversed(acquired):
                resource.release()
            raise
        self._acquired = acquired
        self._skills_lock_token = object()
        self._skills_lock_proof = None
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        acquired = self._acquired
        self._acquired = []
        self._skills_lock_token = None
        self._skills_lock_proof = None
        for resource in reversed(acquired):
            resource.release()

    def _held_skills_lock(self) -> "_HeldSkillsLock":
        """Return an internal proof for the currently held skills lock.

        The proof is deliberately not part of the public lock API.  It is
        valid only for this active lock-set entry and is tied to the exact
        skills-domain resource selected by the lock-set factory.
        """

        token = self._skills_lock_token
        if token is None or not self._acquired:
            raise OperationError("skills mutation lock is not actively held")
        for resource in self._acquired:
            if resource.skills_state and resource.locked:
                if self._skills_lock_proof is None:
                    self._skills_lock_proof = _HeldSkillsLock(self, resource, token)
                return self._skills_lock_proof
        raise OperationError("skills mutation lock is not part of this lock set")


class _HeldSkillsLock:
    """Opaque, live-only proof of one active skills mutation-lock ownership."""

    __slots__ = ("__owner", "__resource", "__token")

    def __init__(
        self,
        owner: Any,
        resource: Any,
        token: Any,
    ) -> None:
        self.__owner = owner
        self.__resource = resource
        self.__token = token

    def __reduce__(self):
        raise TypeError("held skills locks are not serializable")

    def __reduce_ex__(self, protocol: int):
        raise TypeError("held skills locks are not serializable")

    def __copy__(self):
        raise TypeError("held skills locks are single-owner")

    def __deepcopy__(self, memo: Any):
        raise TypeError("held skills locks are single-owner")

    def _components(self) -> Tuple[Any, Any, Any]:
        return self.__owner, self.__resource, self.__token


def _open_held_skills_root(
    proof: Any,
    expected_identity: Any,
) -> Tuple[int, os.stat_result]:
    """Duplicate the root descriptor behind a validated live skills proof.

    This is package-private so durable journal persistence can use the exact
    descriptor owned by the outer lock instead of reopening a caller path.
    """

    if type(proof) is not _HeldSkillsLock:
        raise SecurityError("held skills lock proof is invalid")
    try:
        owner, resource, token = proof._components()
    except AttributeError as exc:
        raise SecurityError("held skills lock proof is invalid") from exc

    if type(owner) is not MutationLockSet or type(resource) is not _MutationResource:
        raise SecurityError("held skills lock proof is invalid")
    if owner._skills_lock_token is not token or token is None:
        raise SecurityError("held skills lock proof is inactive")
    if owner._skills_lock_proof is not proof:
        raise SecurityError("held skills lock proof is not the active owner proof")
    if not any(item is resource for item in owner._acquired):
        raise SecurityError("held skills lock proof is not owned by this lock set")
    if not resource.skills_state or not resource.locked or resource.root_fd is None:
        raise SecurityError("held skills lock proof is not a skills lock")
    if (
        type(expected_identity) is not dict
        or set(expected_identity) != {"kind", "device", "inode"}
        or expected_identity.get("kind") != "posix-dev-ino"
        or type(expected_identity.get("device")) is not int
        or type(expected_identity.get("inode")) is not int
    ):
        raise SecurityError("held skills lock root identity is invalid")

    try:
        current = os.fstat(resource.root_fd)
    except OSError as exc:
        raise SecurityError("held skills lock root descriptor is unavailable") from exc
    if (
        not stat.S_ISDIR(current.st_mode)
        or _is_reparse_info(current)
        or resource.root_identity != _identity(current)
        or (expected_identity["device"], expected_identity["inode"])
        != _identity(current)
    ):
        raise SecurityError("held skills lock root identity does not match")
    if os.name != "nt":
        uid = _current_uid()
        if uid is not None and current.st_uid != uid:
            raise SecurityError("held skills lock root is not owned by the current user")
        if stat.S_IMODE(current.st_mode) & 0o022:
            raise SecurityError("held skills lock root is group or world writable")

    try:
        duplicate = os.dup(resource.root_fd)
    except OSError as exc:
        raise SecurityError("held skills lock root descriptor cannot be duplicated") from exc
    try:
        duplicate_info = os.fstat(duplicate)
        if _identity(duplicate_info) != _identity(current):
            raise SecurityError("held skills lock root descriptor changed during duplication")
        return duplicate, duplicate_info
    except Exception:
        os.close(duplicate)
        raise


def _deduplicate_and_sort(resources: Iterable[_MutationResource]) -> Tuple[_MutationResource, ...]:
    unique = {}
    for resource in resources:
        unique.setdefault(resource.key, resource)
    return tuple(unique[key] for key in sorted(unique))

"""POSIX-only durable candidate-journal persistence and read-only recovery scan.

This foundation persists only validated transaction-journal documents below the
canonical skills state namespace.  It never inspects or mutates a candidate
final target, a stage, or installed state; journal data is not authority.
"""

from __future__ import annotations

import json
import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .errors import IntegrityError, OperationError, SecurityError, ValidationError
from .mutation_lock import MutationLockSet
from .transaction_journal import (
    TERMINAL_PHASES,
    validate_journal_document,
    validate_phase_transition,
    validate_transaction_id,
)


STATE_NAME = ".cso-state"
TRANSACTIONS_NAME = "transactions"
LOCK_NAME = "mutation.lock"
JOURNAL_NAME = "journal.json"
TEMPORARY_JOURNAL_NAME = "journal.json.tmp"
MAX_JOURNAL_BYTES = 4 * 1024 * 1024

SCAN_STATUSES = ("clean", "recovery-required", "unsupported")
RECORD_STATUSES = ("terminal", "recovery-required")

_IMMUTABLE_FIELDS = (
    "schema_version",
    "transaction_id",
    "operation",
    "target_key",
    "skills_root_identity",
    "plan_digest",
    "source_identity_digest",
    "provenance_trust_digest",
    "capability_policy_digest",
    "admission_digest",
    "new_manifest",
    "new_manifest_digest",
    "installed_state_before_digest",
)
_SET_ONCE_FIELDS = (
    "previous_target",
    "stage_binding",
    "quarantine_binding",
    "installed_state_after_digest",
)


@dataclass(frozen=True)
class RecoveryRecord:
    """A path-free, non-authoritative classification of one journal entry."""

    transaction_id: Optional[str]
    phase: Optional[str]
    status: str
    reason_ids: Tuple[str, ...]


@dataclass(frozen=True)
class RecoveryScan:
    """A read-only recovery assessment; it never repairs or changes state."""

    status: str
    records: Tuple[RecoveryRecord, ...]
    reason_ids: Tuple[str, ...]


def _posix_supported() -> bool:
    required_flags = ("O_DIRECTORY", "O_NOFOLLOW", "O_CLOEXEC")
    if (
        os.name != "posix"
        or not all(hasattr(os, name) for name in required_flags)
        or not hasattr(os, "fsync")
    ):
        return False
    return all(
        operation in getattr(os, "supports_dir_fd", set())
        for operation in (os.open, os.mkdir, os.stat, os.rename)
    )


def _directory_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _file_read_flags() -> int:
    return os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC


def _file_write_flags() -> int:
    return os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _private_directory(info: os.stat_result, *, device: int) -> bool:
    return (
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.geteuid()
        and not (stat.S_IMODE(info.st_mode) & 0o077)
        and info.st_dev == device
    )


def _private_regular_file(info: os.stat_result) -> bool:
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.geteuid()
        and info.st_nlink == 1
        and stat.S_IMODE(info.st_mode) == 0o600
    )


def _open_skills_root(skills_root: Path) -> Tuple[int, os.stat_result]:
    if not isinstance(skills_root, Path) or not skills_root.is_absolute() or ".." in skills_root.parts:
        raise SecurityError("durable journal skills root is invalid")
    canonical_root = _canonical_root(skills_root)
    if canonical_root == Path(canonical_root.anchor) or canonical_root == Path.home().resolve(strict=False):
        raise SecurityError("durable journal skills root is too broad")
    descriptor = -1
    try:
        descriptor = os.open(canonical_root.anchor, _directory_flags())
        for part in canonical_root.parts[1:]:
            next_descriptor = os.open(part, _directory_flags(), dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        info = os.fstat(descriptor)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise SecurityError("durable journal skills root is unsafe")
        return descriptor, info
    except SecurityError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise SecurityError("durable journal skills root is unavailable") from exc


def _canonical_root(skills_root: Path) -> Path:
    """Reject aliases except the platform's fixed macOS system aliases."""

    absolute = Path(os.path.abspath(os.fspath(skills_root)))
    cursor = absolute
    while True:
        try:
            info = os.lstat(cursor)
        except OSError as exc:
            raise SecurityError("durable journal skills root is unavailable") from exc
        if stat.S_ISLNK(info.st_mode):
            aliases = {Path("/tmp"): Path("/private/tmp"), Path("/var"): Path("/private/var")}
            expected = aliases.get(cursor)
            if (
                sys.platform != "darwin"
                or expected is None
                or Path(os.path.realpath(os.fspath(cursor))) != expected
            ):
                raise SecurityError("durable journal skills root contains a symlink")
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    return Path(os.path.realpath(os.fspath(absolute)))


def _stat_at(parent_fd: int, name: str) -> os.stat_result:
    try:
        return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except OSError:
        raise


def _open_existing_directory(parent_fd: int, name: str, *, device: int) -> Tuple[int, os.stat_result]:
    visible = _stat_at(parent_fd, name)
    if not _private_directory(visible, device=device):
        raise SecurityError("durable journal namespace is unsafe")
    try:
        descriptor = os.open(name, _directory_flags(), dir_fd=parent_fd)
    except OSError as exc:
        raise SecurityError("durable journal namespace is unavailable") from exc
    try:
        actual = os.fstat(descriptor)
        if not _same_identity(visible, actual) or not _private_directory(actual, device=device):
            raise SecurityError("durable journal namespace changed during open")
        return descriptor, actual
    except Exception:
        os.close(descriptor)
        raise


def _open_or_create_directory(parent_fd: int, name: str, *, device: int) -> Tuple[int, os.stat_result]:
    created = False
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        created = True
    except FileExistsError:
        pass
    except OSError as exc:
        raise SecurityError("durable journal namespace cannot be created") from exc
    if created:
        try:
            os.fsync(parent_fd)
        except OSError as exc:
            raise OperationError("durable journal namespace cannot be synchronized") from exc
    return _open_existing_directory(parent_fd, name, device=device)


def _validate_root_identity(document: Dict[str, Any], root_info: os.stat_result) -> None:
    identity = document["skills_root_identity"]
    if identity != {"kind": "posix-dev-ino", "device": root_info.st_dev, "inode": root_info.st_ino}:
        raise IntegrityError("durable journal skills root identity does not match")


def _canonical_bytes(document: Dict[str, Any]) -> bytes:
    payload = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    if len(payload) > MAX_JOURNAL_BYTES:
        raise ValidationError("durable journal exceeds persistence size limit")
    return payload


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if type(written) is not int or written <= 0:
            raise OSError("durable journal write failed")
        offset += written


def _write_journal_atomically(transaction_fd: int, document: Dict[str, Any]) -> None:
    payload = _canonical_bytes(document)
    descriptor = -1
    try:
        descriptor = os.open(TEMPORARY_JOURNAL_NAME, _file_write_flags(), 0o600, dir_fd=transaction_fd)
        os.fchmod(descriptor, 0o600)
        _write_all(descriptor, payload)
        os.fsync(descriptor)
        actual = os.fstat(descriptor)
        if not _private_regular_file(actual):
            raise SecurityError("durable journal temporary file is unsafe")
    except FileExistsError as exc:
        raise IntegrityError("durable journal has an unfinished temporary file") from exc
    except OSError as exc:
        raise OperationError("durable journal write failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        os.rename(
            TEMPORARY_JOURNAL_NAME,
            JOURNAL_NAME,
            src_dir_fd=transaction_fd,
            dst_dir_fd=transaction_fd,
        )
        os.fsync(transaction_fd)
    except (TypeError, OSError) as exc:
        raise OperationError("durable journal atomic replace failed") from exc


def _load_journal(transaction_fd: int, *, root_info: os.stat_result) -> Dict[str, Any]:
    try:
        visible = _stat_at(transaction_fd, JOURNAL_NAME)
    except FileNotFoundError as exc:
        raise IntegrityError("durable journal is missing") from exc
    if not _private_regular_file(visible) or visible.st_size > MAX_JOURNAL_BYTES:
        raise IntegrityError("durable journal file is unsafe")
    descriptor = -1
    try:
        descriptor = os.open(JOURNAL_NAME, _file_read_flags(), dir_fd=transaction_fd)
        actual = os.fstat(descriptor)
        if not _same_identity(visible, actual) or not _private_regular_file(actual):
            raise IntegrityError("durable journal changed during read")
        chunks = []
        remaining = MAX_JOURNAL_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        if len(raw) > MAX_JOURNAL_BYTES:
            raise IntegrityError("durable journal exceeds persistence size limit")
    except OSError as exc:
        raise IntegrityError("durable journal cannot be read safely") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        parsed = json.loads(raw.decode("utf-8"), object_pairs_hook=_reject_duplicate_keys)
        normalized = validate_journal_document(parsed)
    except (UnicodeDecodeError, json.JSONDecodeError, ValidationError, SecurityError) as exc:
        raise IntegrityError("durable journal is corrupt") from exc
    _validate_root_identity(normalized, root_info)
    return normalized


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError("duplicate durable journal key")
        result[key] = value
    return result


def _validate_update(current: Dict[str, Any], next_document: Dict[str, Any]) -> None:
    validate_phase_transition(current["phase"], next_document["phase"])
    for field in _IMMUTABLE_FIELDS:
        if current[field] != next_document[field]:
            raise IntegrityError("durable journal immutable field changed")
    for field in _SET_ONCE_FIELDS:
        if current[field] is not None and current[field] != next_document[field]:
            raise IntegrityError("durable journal established field changed")


def _prepare_write(skills_root: Path, document: Any, *, creating: bool) -> Dict[str, Any]:
    if not _posix_supported():
        raise SecurityError("durable candidate journals are unsupported on this platform")
    normalized = validate_journal_document(document)
    if creating and normalized["phase"] != "PREPARING":
        raise ValidationError("durable journal must begin in PREPARING")
    root_fd, root_info = _open_skills_root(skills_root)
    try:
        _validate_root_identity(normalized, root_info)
    finally:
        os.close(root_fd)
    return normalized


def create_durable_journal(skills_root: Path, document: Any) -> Dict[str, Any]:
    """Persist a new PREPARING journal, without inspecting a candidate target."""

    normalized = _prepare_write(skills_root, document, creating=True)
    with MutationLockSet.for_skills(skills_root):
        root_fd, root_info = _open_skills_root(skills_root)
        state_fd = transactions_fd = transaction_fd = -1
        try:
            _validate_root_identity(normalized, root_info)
            state_fd, _ = _open_or_create_directory(root_fd, STATE_NAME, device=root_info.st_dev)
            transactions_fd, _ = _open_or_create_directory(state_fd, TRANSACTIONS_NAME, device=root_info.st_dev)
            transaction_id = validate_transaction_id(normalized["transaction_id"])
            try:
                os.mkdir(transaction_id, 0o700, dir_fd=transactions_fd)
            except FileExistsError as exc:
                raise IntegrityError("durable journal transaction already exists") from exc
            try:
                os.fsync(transactions_fd)
            except OSError as exc:
                raise OperationError("durable journal transaction cannot be synchronized") from exc
            transaction_fd, _ = _open_existing_directory(transactions_fd, transaction_id, device=root_info.st_dev)
            _assert_empty_directory(transaction_fd)
            _write_journal_atomically(transaction_fd, normalized)
            os.fsync(transactions_fd)
            return normalized
        finally:
            for descriptor in (transaction_fd, transactions_fd, state_fd, root_fd):
                if descriptor >= 0:
                    os.close(descriptor)


def advance_durable_journal(skills_root: Path, document: Any) -> Dict[str, Any]:
    """Atomically advance one existing journal along its closed schema transition."""

    normalized = _prepare_write(skills_root, document, creating=False)
    with MutationLockSet.for_skills(skills_root):
        root_fd, root_info = _open_skills_root(skills_root)
        state_fd = transactions_fd = transaction_fd = -1
        try:
            _validate_root_identity(normalized, root_info)
            state_fd, _ = _open_existing_directory(root_fd, STATE_NAME, device=root_info.st_dev)
            transactions_fd, _ = _open_existing_directory(state_fd, TRANSACTIONS_NAME, device=root_info.st_dev)
            transaction_id = validate_transaction_id(normalized["transaction_id"])
            transaction_fd, _ = _open_existing_directory(transactions_fd, transaction_id, device=root_info.st_dev)
            _assert_transaction_leaves(transaction_fd)
            current = _load_journal(transaction_fd, root_info=root_info)
            if current["transaction_id"] != transaction_id:
                raise IntegrityError("durable journal transaction directory mismatch")
            _validate_update(current, normalized)
            _write_journal_atomically(transaction_fd, normalized)
            return normalized
        finally:
            for descriptor in (transaction_fd, transactions_fd, state_fd, root_fd):
                if descriptor >= 0:
                    os.close(descriptor)


def load_durable_journal(skills_root: Path, transaction_id: str) -> Dict[str, Any]:
    """Read one validated journal without creating, locking, or repairing state."""

    if not _posix_supported():
        raise SecurityError("durable candidate journals are unsupported on this platform")
    transaction_id = validate_transaction_id(transaction_id)
    root_fd = state_fd = transactions_fd = transaction_fd = -1
    try:
        root_fd, root_info = _open_skills_root(skills_root)
        state_fd, _ = _open_existing_directory(root_fd, STATE_NAME, device=root_info.st_dev)
        transactions_fd, _ = _open_existing_directory(
            state_fd, TRANSACTIONS_NAME, device=root_info.st_dev
        )
        transaction_fd, _ = _open_existing_directory(
            transactions_fd, transaction_id, device=root_info.st_dev
        )
        _assert_transaction_leaves(transaction_fd)
        document = _load_journal(transaction_fd, root_info=root_info)
        if document["transaction_id"] != transaction_id:
            raise IntegrityError("durable journal transaction directory mismatch")
        return document
    finally:
        for descriptor in (transaction_fd, transactions_fd, state_fd, root_fd):
            if descriptor >= 0:
                os.close(descriptor)


def _assert_transaction_leaves(transaction_fd: int) -> None:
    try:
        names = os.listdir(transaction_fd)
    except OSError as exc:
        raise IntegrityError("durable journal transaction cannot be enumerated") from exc
    if set(names) != {JOURNAL_NAME}:
        raise IntegrityError("durable journal transaction has unexpected leaves")


def _assert_empty_directory(descriptor: int) -> None:
    try:
        names = os.listdir(descriptor)
    except OSError as exc:
        raise IntegrityError("durable journal transaction cannot be enumerated") from exc
    if names:
        raise IntegrityError("durable journal transaction has unexpected leaves")


def scan_durable_journals(skills_root: Path) -> RecoveryScan:
    """Read-only scan; unsafe, corrupt, and incomplete journals require recovery."""

    if not _posix_supported():
        return RecoveryScan("unsupported", (), ("platform.unsupported",))
    root_fd = state_fd = transactions_fd = -1
    records = []
    try:
        root_fd, root_info = _open_skills_root(skills_root)
        try:
            state_fd, _ = _open_existing_directory(root_fd, STATE_NAME, device=root_info.st_dev)
        except FileNotFoundError:
            return RecoveryScan("clean", (), ())
        try:
            state_names = set(os.listdir(state_fd))
        except OSError:
            return _unsafe_scan("state-namespace.unreadable")
        if not state_names.issubset({LOCK_NAME, TRANSACTIONS_NAME}):
            return _unsafe_scan("state-namespace.unexpected-leaf")
        if LOCK_NAME in state_names:
            lock_info = _stat_at(state_fd, LOCK_NAME)
            if not _private_regular_file(lock_info):
                return _unsafe_scan("state-lock.unsafe")
        if TRANSACTIONS_NAME not in state_names:
            return RecoveryScan("clean", (), ())
        transactions_fd, _ = _open_existing_directory(state_fd, TRANSACTIONS_NAME, device=root_info.st_dev)
        try:
            transaction_names = sorted(os.listdir(transactions_fd))
        except OSError:
            return _unsafe_scan("transactions.unreadable")
        for transaction_id in transaction_names:
            records.append(_scan_transaction(transactions_fd, transaction_id, root_info))
    except (OSError, SecurityError):
        return _unsafe_scan("namespace.unsafe")
    finally:
        for descriptor in (transactions_fd, state_fd, root_fd):
            if descriptor >= 0:
                os.close(descriptor)
    if any(record.status == "recovery-required" for record in records):
        return RecoveryScan("recovery-required", tuple(records), ("recovery.required",))
    return RecoveryScan("clean", tuple(records), ())


def _scan_transaction(transactions_fd: int, transaction_id: str, root_info: os.stat_result) -> RecoveryRecord:
    transaction_fd = -1
    try:
        validate_transaction_id(transaction_id)
        transaction_fd, _ = _open_existing_directory(transactions_fd, transaction_id, device=root_info.st_dev)
    except (OSError, SecurityError, ValidationError):
        return RecoveryRecord(None, None, "recovery-required", ("transaction.unsafe",))
    try:
        _assert_transaction_leaves(transaction_fd)
        document = _load_journal(transaction_fd, root_info=root_info)
        if document["transaction_id"] != transaction_id:
            raise IntegrityError("durable journal transaction directory mismatch")
        if document["phase"] in TERMINAL_PHASES:
            return RecoveryRecord(transaction_id, document["phase"], "terminal", ())
        return RecoveryRecord(transaction_id, document["phase"], "recovery-required", ("transaction.incomplete",))
    except (IntegrityError, OSError, SecurityError, ValidationError):
        return RecoveryRecord(transaction_id, None, "recovery-required", ("transaction.unsafe",))
    finally:
        if transaction_fd >= 0:
            os.close(transaction_fd)


def _unsafe_scan(reason_id: str) -> RecoveryScan:
    return RecoveryScan(
        "recovery-required",
        (RecoveryRecord(None, None, "recovery-required", (reason_id,)),),
        ("recovery.required",),
    )


__all__ = [
    "MAX_JOURNAL_BYTES",
    "RecoveryRecord",
    "RecoveryScan",
    "advance_durable_journal",
    "create_durable_journal",
    "load_durable_journal",
    "scan_durable_journals",
]

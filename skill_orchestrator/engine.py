"""Transactional install, activation, audit, rollback, and deterministic routing."""

from __future__ import annotations

import copy
import json
import os
import secrets
import shutil
import sys
import tempfile
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from . import __version__
from .errors import IntegrityError, OperationError, SecurityError, ValidationError
from .validation import (
    canonical_json,
    is_reparse_point,
    load_json,
    load_profiles,
    resolve_profile,
    safe_join,
    tree_manifest,
    validate_project,
    validate_registry,
)


ROUTER_SKILL_ID = "codex-skill-orchestrator"
STATE_SCHEMA_VERSION = 1
APP_DIRECTORIES = ("skill_orchestrator", "profiles", "registry", "schemas", "security", "router")
APP_FILES = (".gitattributes", "LICENSE", "THIRD_PARTY.md")
LAUNCHER_FILES = {
    "installer/cso.py": "bin/cso.py",
    "installer/cso.ps1": "bin/cso.ps1",
    "installer/python-discovery.ps1": "bin/python-discovery.ps1",
    "installer/cso": "bin/cso",
}


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def default_install_root() -> Path:
    override = os.environ.get("CSO_HOME")
    if override:
        return Path(override).expanduser()
    home = Path.home()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA")
        return (Path(base) if base else home / "AppData" / "Local") / "codex-skill-orchestrator"
    state_home = os.environ.get("XDG_STATE_HOME")
    return (Path(state_home).expanduser() if state_home else home / ".local" / "state") / "codex-skill-orchestrator"


def default_skills_dir() -> Path:
    codex_home = os.environ.get("CODEX_HOME")
    root = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
    return root / "skills"


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _check_existing_ancestors(path: Path, label: str) -> None:
    # Inspect the lexical path before resolve() so a symlink supplied by the
    # caller cannot disappear during canonicalization.
    cursor = Path(os.path.abspath(str(path.expanduser())))
    existing: List[Path] = []
    while True:
        if cursor.exists() or is_reparse_point(cursor):
            existing.append(cursor)
        if cursor.parent == cursor:
            break
        cursor = cursor.parent
    for item in existing:
        if is_reparse_point(item):
            if _is_macos_system_temp_alias(item, cursor=Path(os.path.abspath(str(path.expanduser())))):
                continue
            raise SecurityError(f"{label} traverses a symlink or reparse point")


def _is_macos_system_temp_alias(item: Path, *, cursor: Path) -> bool:
    if sys.platform != "darwin":
        return False
    temporary_root = Path(os.path.abspath(tempfile.gettempdir()))
    try:
        cursor.relative_to(temporary_root)
    except ValueError:
        return False
    aliases = {
        Path("/var"): Path("/private/var"),
        Path("/tmp"): Path("/private/tmp"),
    }
    expected = aliases.get(item)
    return expected is not None and item.resolve(strict=True) == expected


def validate_destination_roots(
    source_root: Path,
    install_root: Path,
    skills_dir: Path,
    *,
    include_app: bool,
) -> Dict[str, Path]:
    source = source_root.resolve(strict=False)
    install_input = install_root.expanduser()
    skills_input = skills_dir.expanduser()
    _check_existing_ancestors(install_input, "install root")
    _check_existing_ancestors(skills_input, "skills directory")
    install = install_input.resolve(strict=False)
    skills = skills_input.resolve(strict=False)
    home = Path.home().resolve(strict=False)
    for value, label in ((install, "install root"), (skills, "skills directory")):
        if value == Path(value.anchor) or value == home:
            raise SecurityError(f"{label} is too broad")
        _check_existing_ancestors(value, label)
    if _is_relative_to(skills, source) or _is_relative_to(source, skills):
        raise SecurityError("skills directory overlaps the source project")
    installed_source = source == (install / "app").resolve(strict=False)
    if include_app and not installed_source and (_is_relative_to(install, source) or _is_relative_to(source, install)):
        raise SecurityError("install root overlaps the source project")
    if _is_relative_to(install, skills) or _is_relative_to(skills, install):
        raise SecurityError("install root and skills directory must be disjoint")
    return {"source": source, "install": install, "skills": skills}


def _state_path(install_root: Path) -> Path:
    return install_root / "state" / "state.json"


def _load_state(install_root: Path) -> Optional[Dict[str, Any]]:
    path = _state_path(install_root)
    if not path.exists():
        return None
    state = load_json(path)
    if not isinstance(state, dict) or state.get("schema_version") != STATE_SCHEMA_VERSION:
        raise ValidationError("installed state has an unsupported schema")
    return state


def _write_json_atomic(path: Path, document: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(4)}")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(canonical_json(document))
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class MutationLock(AbstractContextManager):
    def __init__(self, install_root: Path) -> None:
        self.path = install_root / "state" / "mutation.lock"
        self.fd: Optional[int] = None
        self.token = secrets.token_hex(16)

    def __enter__(self) -> "MutationLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fd = os.open(str(self.path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.fstat(self.fd).st_size == 0:
                os.write(self.fd, b"\0")
            os.lseek(self.fd, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(self.fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self.fd)
            self.fd = None
            raise OperationError("another orchestrator mutation is in progress") from exc
        try:
            payload = json.dumps({"pid": os.getpid(), "token": self.token}, separators=(",", ":")).encode("ascii")
            os.ftruncate(self.fd, 0)
            os.lseek(self.fd, 0, os.SEEK_SET)
            os.write(self.fd, payload)
            os.fsync(self.fd)
        except OSError as exc:
            self._release()
            raise OperationError("could not initialize the orchestrator mutation lock") from exc
        return self

    def _release(self) -> None:
        if self.fd is None:
            return
        fd = self.fd
        self.fd = None
        try:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                # Closing the descriptor also releases the OS lock. Do not turn
                # a committed mutation into a reported failure during cleanup.
                pass
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        self._release()


def _transaction_id() -> str:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{secrets.token_hex(4)}"


def _iter_payload_files(source: Path, *, skip_python_cache: bool) -> Iterable[Path]:
    if not source.is_dir() or is_reparse_point(source):
        raise SecurityError("payload source is missing or unsafe")
    for current, dirs, files in os.walk(source, topdown=True, followlinks=False):
        current_path = Path(current)
        dirs[:] = sorted(
            name
            for name in dirs
            if not (skip_python_cache and name == "__pycache__")
        )
        for name in dirs:
            if is_reparse_point(current_path / name):
                raise SecurityError("payload contains a symlink or reparse point")
        for name in sorted(files):
            if skip_python_cache and name.endswith((".pyc", ".pyo")):
                continue
            path = current_path / name
            if is_reparse_point(path) or not path.is_file():
                raise SecurityError("payload contains a non-regular file")
            yield path


def _copy_clean_tree(source: Path, destination: Path, *, skip_python_cache: bool = False) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for source_file in _iter_payload_files(source, skip_python_cache=skip_python_cache):
        relative = source_file.relative_to(source)
        destination_file = destination / relative
        destination_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, destination_file, follow_symlinks=False)


def _copy_exact_tree(source: Path, destination: Path) -> Dict[str, str]:
    expected = tree_manifest(source)
    _copy_clean_tree(source, destination, skip_python_cache=False)
    actual = tree_manifest(destination)
    if actual != expected:
        raise IntegrityError("copied backup does not match its source")
    return expected


def _build_app_stage(source_root: Path, stage: Path) -> Dict[str, str]:
    stage.mkdir(parents=True, exist_ok=False)
    for directory in APP_DIRECTORIES:
        _copy_clean_tree(source_root / directory, stage / directory, skip_python_cache=True)
    for filename in APP_FILES:
        source = source_root / filename
        if not source.is_file() or is_reparse_point(source):
            raise SecurityError(f"required app file is missing or unsafe: {filename}")
        shutil.copy2(source, stage / filename, follow_symlinks=False)
    for source_relative, destination_relative in LAUNCHER_FILES.items():
        source = safe_join(source_root, source_relative, "launcher source")
        destination = safe_join(stage, destination_relative, "launcher destination")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not source.is_file() or is_reparse_point(source):
            raise SecurityError(f"required launcher is missing or unsafe: {source_relative}")
        shutil.copy2(source, destination, follow_symlinks=False)
        if destination.name == "cso":
            destination.chmod(destination.stat().st_mode | 0o111)
    return tree_manifest(stage)


def _active_profile_document(profile: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "generated_by": f"codex-skill-orchestrator/{__version__}",
        "profile": copy.deepcopy(dict(profile)),
    }


def _build_skill_stage(
    source_root: Path,
    stage: Path,
    profile: Mapping[str, Any],
    registry_entry: Mapping[str, Any],
) -> Dict[str, str]:
    stage.mkdir(parents=True, exist_ok=False)
    source_dir = safe_join(source_root, registry_entry["source"]["path"], "router source")
    for file_entry in registry_entry["files"]:
        source_file = safe_join(source_dir, file_entry["path"], "router file")
        destination = safe_join(stage, file_entry["path"], "router destination")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, destination, follow_symlinks=False)
    active_profile = stage / "references" / "active-profile.json"
    active_profile.parent.mkdir(parents=True, exist_ok=True)
    with active_profile.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical_json(_active_profile_document(profile)))
    return tree_manifest(stage)


def _component_target(component: Mapping[str, Any], install_root: Path, skills_dir: Path) -> Path:
    root_kind = component["root"]
    if root_kind == "install_root":
        root = install_root
    elif root_kind == "skills_dir":
        root = skills_dir
    else:
        raise ValidationError("transaction contains an unknown root kind")
    return safe_join(root, component["target"], "transaction target")


def _atomic_replace(stage: Path, target: Path, transaction_id: str) -> None:
    old = target.parent / f".{target.name}.cso-old-{transaction_id}"
    if old.exists():
        raise OperationError("transaction quarantine path already exists")
    moved_old = False
    try:
        if target.exists():
            os.replace(target, old)
            moved_old = True
        os.replace(stage, target)
    except OSError as exc:
        if moved_old and not target.exists() and old.exists():
            os.replace(old, target)
        raise OperationError("atomic component replacement failed") from exc
    if old.exists():
        try:
            shutil.rmtree(old)
        except OSError:
            # The new target is already committed. Antivirus or file indexing may
            # temporarily hold the quarantine on Windows; stale quarantine is
            # safer than reporting a failed transaction with new bytes installed.
            pass


def _remove_component_atomically(target: Path, transaction_id: str) -> None:
    quarantine = target.parent / f".{target.name}.cso-remove-{transaction_id}"
    if quarantine.exists():
        raise OperationError("transaction removal path already exists")
    os.replace(target, quarantine)
    try:
        shutil.rmtree(quarantine)
    except OSError:
        # Removal is logically complete once the managed path no longer exists.
        # A locked quarantine can be cleaned on a later maintenance pass.
        pass


def _restore_component(
    component: Mapping[str, Any],
    transaction_dir: Path,
    install_root: Path,
    skills_dir: Path,
    transaction_id: str,
) -> None:
    target = _component_target(component, install_root, skills_dir)
    if component["previous_exists"]:
        backup = safe_join(transaction_dir, component["backup"], "transaction backup")
        expected = component["previous_files"]
        if tree_manifest(backup) != expected:
            raise IntegrityError("rollback backup checksum mismatch")
        stage = target.parent / f".{target.name}.cso-restore-{transaction_id}"
        _copy_exact_tree(backup, stage)
        _atomic_replace(stage, target, transaction_id + "-restore")
    elif target.exists():
        _remove_component_atomically(target, transaction_id + "-restore")


def _restore_snapshot(snapshot: Path, target: Path, transaction_id: str) -> None:
    stage = target.parent / f".{target.name}.cso-compensate-{transaction_id}"
    if stage.exists():
        raise OperationError("rollback compensation stage already exists")
    _copy_exact_tree(snapshot, stage)
    _atomic_replace(stage, target, transaction_id + "-compensate")


def _discard_tree_best_effort(path: Path) -> None:
    if path.exists():
        try:
            shutil.rmtree(path)
        except OSError:
            pass


def _discard_owned_tree(path: Path, label: str) -> None:
    if not path.exists():
        return
    try:
        shutil.rmtree(path)
    except OSError as exc:
        raise OperationError(f"could not remove {label}") from exc


def _restore_state_document(state_path: Path, document: Optional[Mapping[str, Any]]) -> None:
    if document is None:
        try:
            state_path.unlink()
        except FileNotFoundError:
            pass
    else:
        _write_json_atomic(state_path, document)


def _recover_preparing_transactions(install_root: Path, skills_dir: Path) -> None:
    transactions_root = install_root / "state" / "transactions"
    if not transactions_root.exists():
        return
    if not transactions_root.is_dir() or is_reparse_point(transactions_root):
        raise SecurityError("transaction root is not a safe directory")

    for transaction_dir in sorted(path for path in transactions_root.iterdir() if path.is_dir()):
        if is_reparse_point(transaction_dir):
            raise SecurityError("transaction directory is a symlink or reparse point")
        manifest_path = transaction_dir / "manifest.json"
        if not manifest_path.is_file() or is_reparse_point(manifest_path):
            continue
        manifest = load_json(manifest_path)
        if not isinstance(manifest, dict) or manifest.get("status") != "preparing":
            continue
        transaction_id = manifest.get("transaction")
        if not isinstance(transaction_id, str) or transaction_id != transaction_dir.name:
            raise IntegrityError("preparing transaction id does not match its directory")
        components = manifest.get("components")
        if not isinstance(components, list) or not components:
            raise ValidationError("preparing transaction has invalid components")
        previous_state = manifest.get("previous_state")
        if previous_state is not None and (
            not isinstance(previous_state, dict)
            or previous_state.get("schema_version") != STATE_SCHEMA_VERSION
        ):
            raise ValidationError("preparing transaction has invalid previous state")

        current_state = _load_state(install_root)
        recovery_id = _transaction_id()
        recovery_dir = transaction_dir / "recovery-attempts" / recovery_id
        snapshots_dir = recovery_dir / "current"
        snapshots_dir.mkdir(parents=True, exist_ok=False)
        prepared = []
        seen_names = set()
        try:
            for component in components:
                if not isinstance(component, dict):
                    raise ValidationError("preparing transaction contains an invalid component")
                name = component.get("name")
                if not isinstance(name, str) or not name or name in seen_names:
                    raise ValidationError("preparing transaction contains invalid component names")
                seen_names.add(name)
                target = _component_target(component, install_root, skills_dir)
                target_exists = target.exists()
                current_files = tree_manifest(target) if target_exists else {}
                previous_exists_value = component.get("previous_exists")
                if not isinstance(previous_exists_value, bool):
                    raise ValidationError("preparing transaction has invalid previous-exists state")
                previous_exists = previous_exists_value
                previous_files = component.get("previous_files")
                installed_files = component.get("installed_files")
                if not isinstance(previous_files, dict) or not isinstance(installed_files, dict):
                    raise ValidationError("preparing transaction contains invalid file manifests")
                if previous_exists:
                    backup_relative = component.get("backup")
                    if not isinstance(backup_relative, str) or not backup_relative:
                        raise ValidationError("preparing transaction has invalid backup metadata")
                    backup = safe_join(transaction_dir, backup_relative, "recovery backup")
                    if tree_manifest(backup) != previous_files:
                        raise IntegrityError("preparing transaction backup checksum mismatch")
                    if target_exists and current_files not in (previous_files, installed_files):
                        raise IntegrityError(f"recovery conflict: managed {name} changed after interruption")
                elif target_exists and current_files != installed_files:
                    raise IntegrityError(f"recovery conflict: unmanaged {name} appeared after interruption")

                expected_stage_name = f".{target.name}.cso-stage-{transaction_id}"
                if component.get("stage_name") != expected_stage_name:
                    raise IntegrityError("preparing transaction stage name is inconsistent")
                original_stage = target.parent / expected_stage_name
                if original_stage.exists() and tree_manifest(original_stage) != installed_files:
                    raise IntegrityError("preparing transaction stage checksum mismatch")
                original_quarantine = target.parent / f".{target.name}.cso-old-{transaction_id}"
                if original_quarantine.exists():
                    if not previous_exists or tree_manifest(original_quarantine) != previous_files:
                        raise IntegrityError("preparing transaction quarantine checksum mismatch")

                snapshot = safe_join(snapshots_dir, name, "recovery current snapshot")
                if target_exists:
                    _copy_exact_tree(target, snapshot)
                prepared.append(
                    {
                        "component": component,
                        "target": target,
                        "target_existed": target_exists,
                        "snapshot": snapshot,
                        "original_stage": original_stage,
                        "original_quarantine": original_quarantine,
                    }
                )

            applied = []
            state_path = _state_path(install_root)
            try:
                for item in reversed(prepared):
                    component = item["component"]
                    target = item["target"]
                    previous_files = component["previous_files"]
                    current_files = tree_manifest(target) if target.exists() else {}
                    if component["previous_exists"]:
                        if not target.exists() or current_files != previous_files:
                            backup = safe_join(transaction_dir, component["backup"], "recovery backup")
                            _restore_snapshot(backup, target, recovery_id)
                            applied.append(item)
                    elif target.exists():
                        _remove_component_atomically(target, recovery_id + "-recover")
                        applied.append(item)

                _restore_state_document(state_path, previous_state)
                manifest["status"] = "recovered"
                _write_json_atomic(manifest_path, manifest)
            except Exception as original_error:
                compensation_errors = []
                for item in reversed(applied):
                    try:
                        target = item["target"]
                        if item["target_existed"]:
                            _restore_snapshot(item["snapshot"], target, recovery_id + "-undo")
                        elif target.exists():
                            _remove_component_atomically(target, recovery_id + "-undo")
                    except Exception as compensation_error:
                        compensation_errors.append(str(compensation_error))
                try:
                    _restore_state_document(state_path, current_state)
                except Exception as compensation_error:
                    compensation_errors.append(str(compensation_error))
                manifest["status"] = "preparing"
                try:
                    _write_json_atomic(manifest_path, manifest)
                except Exception as compensation_error:
                    compensation_errors.append(str(compensation_error))
                if compensation_errors:
                    raise OperationError(
                        "interrupted-transaction recovery failed and compensation was incomplete: "
                        + "; ".join(compensation_errors)
                    ) from original_error
                raise

            for item in prepared:
                _discard_tree_best_effort(item["original_stage"])
                _discard_tree_best_effort(item["original_quarantine"])
        finally:
            _discard_tree_best_effort(recovery_dir)


def _finish_rolling_back_component(
    component: Mapping[str, Any],
    transaction_dir: Path,
    install_root: Path,
    skills_dir: Path,
    attempt_id: str,
) -> None:
    target = _component_target(component, install_root, skills_dir)
    installed_files = component.get("installed_files")
    previous_files = component.get("previous_files")
    previous_exists = component.get("previous_exists")
    if not isinstance(installed_files, dict) or not isinstance(previous_files, dict):
        raise ValidationError("rolling-back transaction has invalid file manifests")
    if not isinstance(previous_exists, bool):
        raise ValidationError("rolling-back transaction has invalid previous-exists state")

    restore_stage = target.parent / f".{target.name}.cso-restore-{attempt_id}"
    old_quarantine = target.parent / f".{target.name}.cso-old-{attempt_id}-restore"
    remove_quarantine = target.parent / f".{target.name}.cso-remove-{attempt_id}-restore"
    current_exists = target.exists()
    current_files = tree_manifest(target) if current_exists else {}

    if previous_exists:
        backup_relative = component.get("backup")
        if not isinstance(backup_relative, str) or not backup_relative:
            raise ValidationError("rolling-back transaction has invalid backup metadata")
        backup = safe_join(transaction_dir, backup_relative, "rollback recovery backup")
        if tree_manifest(backup) != previous_files:
            raise IntegrityError("rollback recovery backup checksum mismatch")
        if current_exists and current_files not in (installed_files, previous_files):
            raise IntegrityError("rollback recovery conflict: managed component was modified")
        if old_quarantine.exists() and tree_manifest(old_quarantine) != installed_files:
            raise IntegrityError("rollback recovery quarantine checksum mismatch")
        if remove_quarantine.exists():
            raise IntegrityError("rollback recovery found an unexpected removal quarantine")

        if restore_stage.exists() and tree_manifest(restore_stage) != previous_files:
            # The stage is transaction-owned. It is safe to rebuild only while
            # the target/quarantine pair proves that activation has not crossed
            # into an unknown state.
            stage_is_rebuildable = (
                (current_exists and current_files in (installed_files, previous_files) and not old_quarantine.exists())
                or (not current_exists and old_quarantine.exists())
            )
            if not stage_is_rebuildable:
                raise IntegrityError("rollback recovery stage checksum mismatch")
            _discard_owned_tree(restore_stage, "partial rollback stage")

        if current_exists and current_files == previous_files:
            _discard_tree_best_effort(restore_stage)
            _discard_tree_best_effort(old_quarantine)
            return
        if not current_exists:
            if not old_quarantine.exists():
                raise IntegrityError("rollback recovery cannot prove an interrupted replacement")
            if not restore_stage.exists():
                _copy_exact_tree(backup, restore_stage)
            os.replace(restore_stage, target)
            _discard_tree_best_effort(old_quarantine)
            return
        if old_quarantine.exists():
            raise IntegrityError("rollback recovery found an ambiguous replacement state")
        if not restore_stage.exists():
            _copy_exact_tree(backup, restore_stage)
        _atomic_replace(restore_stage, target, attempt_id + "-restore")
        return

    if restore_stage.exists() or old_quarantine.exists():
        raise IntegrityError("rollback recovery found unexpected restore artifacts")
    if current_exists and current_files != installed_files:
        raise IntegrityError("rollback recovery conflict: managed component was modified")
    if remove_quarantine.exists() and tree_manifest(remove_quarantine) != installed_files:
        raise IntegrityError("rollback recovery removal quarantine checksum mismatch")
    if not current_exists:
        _discard_tree_best_effort(remove_quarantine)
        return
    if remove_quarantine.exists():
        raise IntegrityError("rollback recovery found an ambiguous removal state")
    _remove_component_atomically(target, attempt_id + "-restore")


def _recover_rolling_back_transactions(install_root: Path, skills_dir: Path) -> List[Dict[str, Any]]:
    transactions_root = install_root / "state" / "transactions"
    if not transactions_root.exists():
        return []
    if not transactions_root.is_dir() or is_reparse_point(transactions_root):
        raise SecurityError("transaction root is not a safe directory")

    pending: List[tuple[Path, Path, Dict[str, Any]]] = []
    for transaction_dir in sorted(path for path in transactions_root.iterdir() if path.is_dir()):
        if is_reparse_point(transaction_dir):
            raise SecurityError("transaction directory is a symlink or reparse point")
        manifest_path = transaction_dir / "manifest.json"
        if not manifest_path.is_file() or is_reparse_point(manifest_path):
            continue
        manifest = load_json(manifest_path)
        if isinstance(manifest, dict) and manifest.get("status") == "rolling_back":
            pending.append((transaction_dir, manifest_path, manifest))
    if len(pending) > 1:
        raise IntegrityError("multiple interrupted rollbacks require manual inspection")

    recovered: List[Dict[str, Any]] = []
    for transaction_dir, manifest_path, manifest in pending:
        transaction_id = manifest.get("transaction")
        if not isinstance(transaction_id, str) or transaction_id != transaction_dir.name:
            raise IntegrityError("rolling-back transaction id does not match its directory")
        components = manifest.get("components")
        if not isinstance(components, list) or not components:
            raise ValidationError("rolling-back transaction has invalid components")
        names = [component.get("name") for component in components if isinstance(component, dict)]
        if len(names) != len(components) or any(not isinstance(name, str) or not name for name in names):
            raise ValidationError("rolling-back transaction has invalid component names")
        if len(set(names)) != len(names):
            raise ValidationError("rolling-back transaction has duplicate components")
        previous_state = manifest.get("previous_state")
        if previous_state is not None and (
            not isinstance(previous_state, dict)
            or previous_state.get("schema_version") != STATE_SCHEMA_VERSION
        ):
            raise ValidationError("rolling-back transaction has invalid previous state")
        rollback_attempt = manifest.get("rollback_attempt")
        if not isinstance(rollback_attempt, dict) or rollback_attempt.get("status") != "rolling_back":
            raise ValidationError("rolling-back transaction has invalid attempt metadata")
        attempt_id = rollback_attempt.get("id")
        if not isinstance(attempt_id, str) or not attempt_id:
            raise ValidationError("rolling-back transaction has invalid attempt id")
        attempt_dir = safe_join(transaction_dir / "rollback-attempts", attempt_id, "rollback attempt")
        snapshots_dir = attempt_dir / "current"
        if not snapshots_dir.is_dir() or is_reparse_point(snapshots_dir):
            raise IntegrityError("rolling-back transaction snapshots are missing or unsafe")
        for component in components:
            snapshot = safe_join(snapshots_dir, component["name"], "rollback current snapshot")
            if tree_manifest(snapshot) != component.get("installed_files"):
                raise IntegrityError("rolling-back transaction snapshot checksum mismatch")

        for component in reversed(components):
            _finish_rolling_back_component(
                component,
                transaction_dir,
                install_root,
                skills_dir,
                attempt_id,
            )
        _restore_state_document(_state_path(install_root), previous_state)
        rollback_attempt["status"] = "recovered"
        manifest["status"] = "rolled_back"
        _write_json_atomic(manifest_path, manifest)
        _discard_tree_best_effort(attempt_dir)
        recovered.append({"transaction": transaction_id, "components": components})
    return recovered


def plan_install(
    profile_name: str,
    install_root: Path,
    skills_dir: Path,
    *,
    source_root: Optional[Path] = None,
    include_app: bool = True,
) -> Dict[str, Any]:
    source = (source_root or project_root()).resolve(strict=False)
    validate_project(source)
    profile = resolve_profile(source, profile_name)
    roots = validate_destination_roots(source, install_root, skills_dir, include_app=include_app)
    actions: List[Dict[str, Any]] = []
    if include_app:
        actions.append({"action": "replace", "target": "<INSTALL_ROOT>/app", "exists": (roots["install"] / "app").exists()})
    actions.append(
        {
            "action": "replace",
            "target": f"<SKILLS_DIR>/{ROUTER_SKILL_ID}",
            "exists": (roots["skills"] / ROUTER_SKILL_ID).exists(),
        }
    )
    actions.append({"action": "activate", "profile": profile["id"]})
    return {
        "schema_version": 1,
        "command": "install" if include_app else "activate",
        "profile": profile["id"],
        "network": "disabled",
        "actions": actions,
    }


def _prepare_component(
    name: str,
    root_kind: str,
    target_relative: str,
    target: Path,
    stage: Path,
    transaction_dir: Path,
    installed_files: Dict[str, str],
) -> Dict[str, Any]:
    previous_exists = target.exists()
    if previous_exists and (not target.is_dir() or is_reparse_point(target)):
        raise SecurityError("managed destination exists but is not a safe directory")
    previous_files = tree_manifest(target) if previous_exists else {}
    backup_relative: Optional[str] = None
    if previous_exists:
        backup_relative = f"backups/{name}"
        backup = safe_join(transaction_dir, backup_relative, "backup destination")
        backup.parent.mkdir(parents=True, exist_ok=True)
        _copy_exact_tree(target, backup)
    return {
        "name": name,
        "root": root_kind,
        "target": target_relative,
        "stage_name": stage.name,
        "previous_exists": previous_exists,
        "previous_files": previous_files,
        "backup": backup_relative,
        "installed_files": installed_files,
    }


def apply_profile(
    profile_name: str,
    install_root: Path,
    skills_dir: Path,
    *,
    source_root: Optional[Path] = None,
    include_app: bool = True,
    dry_run: bool = False,
) -> Dict[str, Any]:
    source = (source_root or project_root()).resolve(strict=False)
    project = validate_project(source)
    profile = resolve_profile(source, profile_name)
    roots = validate_destination_roots(source, install_root, skills_dir, include_app=include_app)
    plan = plan_install(profile["id"], roots["install"], roots["skills"], source_root=source, include_app=include_app)
    if dry_run:
        return {**plan, "dry_run": True, "changed": False}

    registry_entry = project["registry"][ROUTER_SKILL_ID]
    roots["install"].mkdir(parents=True, exist_ok=True)
    roots["skills"].mkdir(parents=True, exist_ok=True)
    with MutationLock(roots["install"]):
        _recover_preparing_transactions(roots["install"], roots["skills"])
        _recover_rolling_back_transactions(roots["install"], roots["skills"])
        previous_state = _load_state(roots["install"])
        if not include_app and previous_state is None:
            raise OperationError("activate requires an existing installation")
        transaction_id = _transaction_id()
        transaction_dir = roots["install"] / "state" / "transactions" / transaction_id
        transaction_dir.mkdir(parents=True, exist_ok=False)

        stages: List[Dict[str, Any]] = []
        try:
            if include_app:
                app_target = roots["install"] / "app"
                app_stage = roots["install"] / f".app.cso-stage-{transaction_id}"
                app_files = _build_app_stage(source, app_stage)
                stages.append(
                    {
                        "name": "app",
                        "root": "install_root",
                        "target_relative": "app",
                        "target": app_target,
                        "stage": app_stage,
                        "installed_files": app_files,
                    }
                )

            skill_target = roots["skills"] / ROUTER_SKILL_ID
            skill_stage = roots["skills"] / f".{ROUTER_SKILL_ID}.cso-stage-{transaction_id}"
            skill_files = _build_skill_stage(source, skill_stage, profile, registry_entry)
            stages.append(
                {
                    "name": "router",
                    "root": "skills_dir",
                    "target_relative": ROUTER_SKILL_ID,
                    "target": skill_target,
                    "stage": skill_stage,
                    "installed_files": skill_files,
                }
            )

            state_profile_matches = previous_state is not None and previous_state.get("active_profile") == profile["id"]
            changed_stages = []
            for item in stages:
                current = tree_manifest(item["target"]) if item["target"].exists() else {}
                if current != item["installed_files"]:
                    changed_stages.append(item)
                else:
                    shutil.rmtree(item["stage"])
            stages = changed_stages
            if not stages and state_profile_matches:
                shutil.rmtree(transaction_dir)
                return {**plan, "dry_run": False, "changed": False, "transaction": None}

            components = []
            for item in stages:
                components.append(
                    _prepare_component(
                        item["name"],
                        item["root"],
                        item["target_relative"],
                        item["target"],
                        item["stage"],
                        transaction_dir,
                        item["installed_files"],
                    )
                )

            manifest = {
                "schema_version": 1,
                "transaction": transaction_id,
                "command": "install" if include_app else "activate",
                "status": "preparing",
                "profile": profile["id"],
                "previous_state": previous_state,
                "components": components,
            }
            manifest_path = transaction_dir / "manifest.json"
            _write_json_atomic(manifest_path, manifest)

            applied: List[Dict[str, Any]] = []
            state_path = _state_path(roots["install"])
            try:
                by_name = {item["name"]: item for item in stages}
                for component in components:
                    item = by_name[component["name"]]
                    _atomic_replace(item["stage"], item["target"], transaction_id)
                    applied.append(component)

                new_state = copy.deepcopy(previous_state) if previous_state else {
                    "schema_version": STATE_SCHEMA_VERSION,
                    "orchestrator_version": __version__,
                    "components": {},
                }
                new_state["orchestrator_version"] = __version__
                new_state["active_profile"] = profile["id"]
                new_state["last_transaction"] = transaction_id
                new_state.setdefault("components", {})
                for component in components:
                    new_state["components"][component["name"]] = {
                        "root": component["root"],
                        "target": component["target"],
                        "files": component["installed_files"],
                    }
                _write_json_atomic(state_path, new_state)
                manifest["status"] = "committed"
                _write_json_atomic(manifest_path, manifest)
            except Exception:
                for component in reversed(applied):
                    _restore_component(component, transaction_dir, roots["install"], roots["skills"], transaction_id + "-failed")
                if previous_state is None:
                    try:
                        state_path.unlink()
                    except FileNotFoundError:
                        pass
                else:
                    _write_json_atomic(state_path, previous_state)
                manifest["status"] = "failed"
                _write_json_atomic(manifest_path, manifest)
                raise
        except Exception:
            for item in stages:
                stage = item.get("stage")
                if isinstance(stage, Path) and stage.exists():
                    shutil.rmtree(stage)
            raise

    return {**plan, "dry_run": False, "changed": True, "transaction": transaction_id}


def rollback(
    install_root: Path,
    skills_dir: Path,
    *,
    source_root: Optional[Path] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    source = (source_root or project_root()).resolve(strict=False)
    roots = validate_destination_roots(source, install_root, skills_dir, include_app=False)
    def load_context() -> tuple[Dict[str, Any], str, Path, Path, Dict[str, Any], List[Dict[str, Any]]]:
        current_state = _load_state(roots["install"])
        if current_state is None or not current_state.get("last_transaction"):
            raise OperationError("no committed transaction is available for rollback")
        current_transaction = current_state["last_transaction"]
        if not isinstance(current_transaction, str) or not current_transaction:
            raise ValidationError("installed state has an invalid transaction id")
        current_dir = roots["install"] / "state" / "transactions" / current_transaction
        current_manifest_path = current_dir / "manifest.json"
        current_manifest = load_json(current_manifest_path)
        if (
            not isinstance(current_manifest, dict)
            or current_manifest.get("status") != "committed"
            or current_manifest.get("transaction") != current_transaction
        ):
            raise IntegrityError("rollback transaction is not committed or is inconsistent")
        current_components = current_manifest.get("components")
        if not isinstance(current_components, list):
            raise ValidationError("rollback transaction has invalid components")
        component_names = [
            component.get("name") for component in current_components if isinstance(component, dict)
        ]
        if len(component_names) != len(current_components) or any(
            not isinstance(name, str) or not name for name in component_names
        ):
            raise ValidationError("rollback transaction has invalid component names")
        if len(set(component_names)) != len(component_names):
            raise ValidationError("rollback transaction contains duplicate components")
        for component in current_components:
            target = _component_target(component, roots["install"], roots["skills"])
            current = tree_manifest(target)
            if current != component.get("installed_files"):
                raise IntegrityError(
                    f"rollback conflict: managed {component.get('name', 'component')} changed after installation"
                )
            if component.get("previous_exists"):
                backup = safe_join(current_dir, component.get("backup"), "rollback backup")
                if tree_manifest(backup) != component.get("previous_files"):
                    raise IntegrityError("rollback backup does not match the transaction manifest")
        return (
            current_state,
            current_transaction,
            current_dir,
            current_manifest_path,
            current_manifest,
            current_components,
        )

    def make_result(transaction: str, components: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "command": "rollback",
            "transaction": transaction,
            "dry_run": dry_run,
            "actions": [
                {
                    "action": "restore" if component["previous_exists"] else "remove",
                    "target": f"<{component['root'].upper()}>/{component['target']}",
                }
                for component in reversed(components)
            ],
        }

    if dry_run:
        _, transaction_id, _, _, _, components = load_context()
        return make_result(transaction_id, components)

    roots["install"].mkdir(parents=True, exist_ok=True)
    with MutationLock(roots["install"]):
        _recover_preparing_transactions(roots["install"], roots["skills"])
        recovered_rollbacks = _recover_rolling_back_transactions(roots["install"], roots["skills"])
        if recovered_rollbacks:
            recovered = recovered_rollbacks[-1]
            result = make_result(recovered["transaction"], recovered["components"])
            result["recovered"] = True
            return result
        current_state, transaction_id, transaction_dir, manifest_path, manifest, components = load_context()
        result = make_result(transaction_id, components)
        attempt_id = _transaction_id()
        attempt_dir = transaction_dir / "rollback-attempts" / attempt_id
        current_snapshots = attempt_dir / "current"
        current_snapshots.mkdir(parents=True, exist_ok=False)
        for component in components:
            target = _component_target(component, roots["install"], roots["skills"])
            snapshot = safe_join(current_snapshots, component["name"], "rollback current snapshot")
            _copy_exact_tree(target, snapshot)

        manifest["status"] = "rolling_back"
        manifest["rollback_attempt"] = {"id": attempt_id, "status": "rolling_back"}
        _write_json_atomic(manifest_path, manifest)

        state_path = _state_path(roots["install"])
        applied: List[Mapping[str, Any]] = []
        try:
            for component in reversed(components):
                _restore_component(component, transaction_dir, roots["install"], roots["skills"], attempt_id)
                applied.append(component)
            previous_state = manifest.get("previous_state")
            if previous_state is None:
                try:
                    state_path.unlink()
                except FileNotFoundError:
                    pass
            else:
                _write_json_atomic(state_path, previous_state)
            manifest["rollback_attempt"]["status"] = "completed"
            manifest["status"] = "rolled_back"
            _write_json_atomic(manifest_path, manifest)
        except Exception as original_error:
            compensation_errors = []
            for component in reversed(applied):
                try:
                    target = _component_target(component, roots["install"], roots["skills"])
                    snapshot = safe_join(current_snapshots, component["name"], "rollback current snapshot")
                    _restore_snapshot(snapshot, target, attempt_id)
                except Exception as compensation_error:
                    compensation_errors.append(str(compensation_error))
            try:
                _write_json_atomic(state_path, current_state)
            except Exception as compensation_error:
                compensation_errors.append(str(compensation_error))
            if not compensation_errors:
                for component in components:
                    try:
                        target = _component_target(component, roots["install"], roots["skills"])
                        if tree_manifest(target) != component.get("installed_files"):
                            compensation_errors.append(
                                f"managed {component.get('name', 'component')} was not restored"
                            )
                    except Exception as compensation_error:
                        compensation_errors.append(str(compensation_error))
            if compensation_errors:
                manifest["status"] = "rolling_back"
                manifest["rollback_attempt"]["status"] = "rolling_back"
            else:
                manifest["status"] = "committed"
                manifest["rollback_attempt"]["status"] = "compensated"
            try:
                _write_json_atomic(manifest_path, manifest)
            except Exception as compensation_error:
                compensation_errors.append(str(compensation_error))
            if compensation_errors:
                raise OperationError(
                    "rollback failed and compensation was incomplete: " + "; ".join(compensation_errors)
                ) from original_error
            raise
        finally:
            if manifest.get("status") != "rolling_back":
                _discard_tree_best_effort(attempt_dir)
    return result


def audit(
    install_root: Path,
    skills_dir: Path,
    *,
    source_root: Optional[Path] = None,
) -> Dict[str, Any]:
    source = (source_root or project_root()).resolve(strict=False)
    findings: List[Dict[str, str]] = []
    profiles = load_profiles(source)
    registry = validate_registry(source)
    roots = validate_destination_roots(source, install_root, skills_dir, include_app=False)
    state = _load_state(roots["install"])
    installation = "not-installed"
    active_profile: Optional[str] = None
    if state is not None:
        installation = "installed"
        active_profile = state.get("active_profile")
        if active_profile not in profiles:
            findings.append({"code": "unknown-active-profile", "message": "state references an unknown profile"})
        components = state.get("components")
        if not isinstance(components, dict):
            findings.append({"code": "invalid-components", "message": "state components are invalid"})
        else:
            for name, component in sorted(components.items()):
                try:
                    target = _component_target(component, roots["install"], roots["skills"])
                    actual = tree_manifest(target)
                    if actual != component.get("files"):
                        findings.append({"code": "checksum-mismatch", "message": f"managed component changed: {name}"})
                except (ValidationError, SecurityError) as exc:
                    findings.append({"code": "unsafe-component", "message": f"{name}: {exc}"})
        router = roots["skills"] / ROUTER_SKILL_ID / "references" / "active-profile.json"
        if router.is_file():
            try:
                active_document = load_json(router)
                installed_profile = active_document.get("profile", {}).get("id")
                if installed_profile != active_profile:
                    findings.append({"code": "profile-drift", "message": "router profile differs from state"})
            except ValidationError as exc:
                findings.append({"code": "invalid-active-profile", "message": str(exc)})
        else:
            findings.append({"code": "missing-active-profile", "message": "installed router profile is missing"})

    transactions_root = roots["install"] / "state" / "transactions"
    if transactions_root.exists():
        if not transactions_root.is_dir() or is_reparse_point(transactions_root):
            findings.append({"code": "unsafe-transactions", "message": "transaction root is not a safe directory"})
        else:
            for transaction_dir in sorted(path for path in transactions_root.iterdir() if path.is_dir()):
                manifest_path = transaction_dir / "manifest.json"
                if not manifest_path.is_file() or is_reparse_point(manifest_path):
                    continue
                try:
                    transaction_manifest = load_json(manifest_path)
                    if isinstance(transaction_manifest, dict):
                        transaction_status = transaction_manifest.get("status")
                        if transaction_status == "preparing":
                            findings.append(
                                {
                                    "code": "interrupted-transaction",
                                    "message": f"transaction requires recovery: {transaction_dir.name}",
                                }
                            )
                        elif transaction_status == "rolling_back":
                            findings.append(
                                {
                                    "code": "interrupted-rollback",
                                    "message": f"rollback requires recovery: {transaction_dir.name}",
                                }
                            )
                except ValidationError as exc:
                    findings.append(
                        {
                            "code": "invalid-transaction-manifest",
                            "message": f"{transaction_dir.name}: {exc}",
                        }
                    )

    third_party = [entry["id"] for entry in registry.values() if entry["provenance"]["third_party"]]
    return {
        "schema_version": 1,
        "status": "clean" if not findings else "findings",
        "project": {
            "profiles": len(profiles),
            "registry_skills": len(registry),
            "third_party_skills": third_party,
            "network_sources_enabled": False,
        },
        "installation": installation,
        "active_profile": active_profile,
        "findings": findings,
    }


def route_task(
    task: str,
    profile_name: str,
    *,
    source_root: Optional[Path] = None,
) -> Dict[str, Any]:
    source = (source_root or project_root()).resolve(strict=False)
    profile = resolve_profile(source, profile_name)
    normalized_task = task.casefold()
    matches = []
    for route in profile["routes"]:
        matched_keywords = [keyword for keyword in route["keywords"] if keyword.casefold() in normalized_task]
        if matched_keywords:
            matches.append(
                {
                    "intent": route["intent"],
                    "score": len(matched_keywords),
                    "priority": route["priority"],
                    "matched_keywords": matched_keywords,
                    "capability_hints": route["capability_hints"],
                    "guidance": route["guidance"],
                }
            )
    matches.sort(key=lambda item: (-item["score"], -item["priority"], item["intent"]))
    selected = matches[: profile["policy"]["max_active_routes"]]
    return {
        "schema_version": 1,
        "profile": profile["id"],
        "reasoning_hint": profile["policy"]["reasoning_hint"],
        "prefer_explicit_invocation": profile["policy"]["prefer_explicit_invocation"],
        "selected_routes": selected,
        "fallback": "host-builtins" if not selected else None,
    }


def profile_catalog(*, source_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    source = (source_root or project_root()).resolve(strict=False)
    profiles = load_profiles(source)
    return [
        {
            "id": profile_id,
            "name": profiles[profile_id]["name"],
            "description": profiles[profile_id]["description"],
        }
        for profile_id in sorted(profiles)
    ]

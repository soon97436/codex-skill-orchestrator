"""Public-contract tests for read-only durable recovery assessment."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
import os
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skill_orchestrator.durable_journal import (
    JOURNAL_NAME,
    advance_durable_journal,
    create_durable_journal,
)
from skill_orchestrator.durable_recovery_assessment import (
    assess_durable_recovery,
)
import skill_orchestrator.durable_recovery_assessment as durable_recovery_assessment
from skill_orchestrator.transaction_journal import manifest_digest


TRANSACTION_ID = "0123456789abcdef0123456789abcdef"
STAGE_NAME = ".safe-skill.cso-stage-0123456789abcdef0123456789abcdef"


def _manifest(payload=b"safe declared bytes\n"):
    return [{"path": "nested/SKILL.md", "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}]


def _document(root: Path, manifest, **overrides):
    info = root.stat()
    document = {
        "schema_version": 1,
        "transaction_id": TRANSACTION_ID,
        "operation": "candidate-install",
        "phase": "PREPARING",
        "target_key": "safe-skill",
        "skills_root_identity": {"kind": "posix-dev-ino", "device": info.st_dev, "inode": info.st_ino},
        "plan_digest": "a" * 64,
        "source_identity_digest": "b" * 64,
        "provenance_trust_digest": "c" * 64,
        "capability_policy_digest": "d" * 64,
        "admission_digest": "e" * 64,
        "new_manifest": manifest,
        "new_manifest_digest": manifest_digest(manifest),
        "previous_target": None,
        "stage_binding": None,
        "quarantine_binding": None,
        "installed_state_before_digest": None,
        "installed_state_after_digest": None,
        "cleanup_status": "none",
        "reason_ids": [],
    }
    document.update(overrides)
    return document


def _snapshot(root: Path):
    records = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        info = path.lstat()
        records.append((path.relative_to(root).as_posix(), info.st_mode, path.read_bytes() if path.is_file() else None))
    return records


@unittest.skipUnless(os.name == "posix", "durable recovery assessment is POSIX-only")
class DurableRecoveryAssessmentTests(unittest.TestCase):
    def _root(self, base: Path) -> Path:
        root = base / "skills"
        root.mkdir(mode=0o700)
        return root

    def _prepared(self, root: Path, payload=b"safe declared bytes\n"):
        manifest = _manifest(payload)
        stage = root / ".cso-staging" / STAGE_NAME
        leaf = stage / "nested" / "SKILL.md"
        leaf.parent.mkdir(parents=True, mode=0o700)
        (root / ".cso-staging").chmod(0o700)
        stage.chmod(0o700)
        leaf.parent.chmod(0o700)
        leaf.write_bytes(payload)
        leaf.chmod(0o600)
        create_durable_journal(root, _document(root, manifest))
        advance_durable_journal(
            root,
            _document(
                root,
                manifest,
                phase="PREPARED",
                stage_binding={"relative_name": STAGE_NAME, "manifest_digest": manifest_digest(manifest)},
            ),
        )
        return manifest, stage, leaf

    def test_clean_root_is_read_only_and_installed_state_is_not_implemented(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            before = _snapshot(root)
            result = assess_durable_recovery(root)
            self.assertEqual(result.status, "clean")
            self.assertEqual(result.records, ())
            self.assertEqual(result.installed_state_capability, "not-implemented")
            self.assertFalse(result.truncated)
            self.assertEqual(_snapshot(root), before)
            self.assertFalse((root / ".cso-state").exists())
            self.assertFalse((root / ".cso-staging").exists())

    def test_terminal_journal_never_observes_a_stage(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            manifest = _manifest()
            create_durable_journal(root, _document(root, manifest))
            advance_durable_journal(root, _document(root, manifest, phase="ABORTED"))
            result = assess_durable_recovery(root)
            self.assertEqual(result.status, "clean")
            self.assertEqual(result.records[0].journal_status, "terminal")
            self.assertEqual(result.records[0].stage_status, "not-applicable")
            self.assertEqual(result.records[0].installed_state_capability, "not-implemented")

    def test_nonterminal_without_stage_binding_is_not_applicable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            create_durable_journal(root, _document(root, _manifest()))
            result = assess_durable_recovery(root)
            self.assertEqual(result.status, "recovery-required")
            self.assertEqual(result.records[0].journal_status, "recovery-required")
            self.assertEqual(result.records[0].stage_status, "not-applicable")

    def test_stage_observation_statuses_propagate_without_authority(self):
        cases = ("matching", "missing", "unsafe")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = self._root(Path(temporary))
                _, stage, leaf = self._prepared(root)
                if case == "missing":
                    stage.rename(stage.with_name(stage.name + "-displaced"))
                elif case == "unsafe":
                    (stage / "extra.txt").write_bytes(b"extra")
                    (stage / "extra.txt").chmod(0o600)
                result = assess_durable_recovery(root)
                record = result.records[0]
                self.assertEqual(result.status, "recovery-required")
                self.assertEqual(record.stage_status, case)
                self.assertEqual(record.installed_state_capability, "not-implemented")
                self.assertFalse(hasattr(result, "authorized"))
                self.assertFalse(hasattr(record, "recoverable"))
                self.assertFalse(hasattr(record, "lease"))

    def test_concurrent_stage_drift_propagates_unstable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            _, stage, _ = self._prepared(root)
            stage_inode = stage.stat().st_ino
            original_fstat = os.fstat
            changed = {"done": False}

            def unstable_fstat(descriptor):
                result = original_fstat(descriptor)
                if result.st_ino == stage_inode and not changed["done"]:
                    changed["done"] = True
                    os.utime(stage, None)
                return result

            with patch("skill_orchestrator.transactional_fs.os.fstat", side_effect=unstable_fstat):
                result = assess_durable_recovery(root)
            self.assertEqual(result.records[0].stage_status, "unstable")

    def test_unsafe_journal_inputs_remain_fail_closed(self):
        cases = ("corrupt", "duplicate", "root-mismatch", "unexpected-leaf")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = self._root(Path(temporary))
                manifest = _manifest()
                create_durable_journal(root, _document(root, manifest))
                journal = root / ".cso-state" / "transactions" / TRANSACTION_ID / JOURNAL_NAME
                if case == "corrupt":
                    journal.write_bytes(b"\\xff")
                elif case == "duplicate":
                    journal.write_bytes(b'{"schema_version":1,"schema_version":1}')
                elif case == "root-mismatch":
                    document = _document(root, manifest)
                    document["skills_root_identity"]["inode"] += 1
                    journal.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")), encoding="utf-8")
                else:
                    (root / ".cso-state" / "unexpected").write_bytes(b"unsafe")
                journal.chmod(0o600)
                result = assess_durable_recovery(root)
                self.assertEqual(result.status, "recovery-required")
                self.assertEqual(result.installed_state_capability, "not-implemented")

    def test_matching_assessment_never_touches_target_or_acquires_lock(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            self._prepared(root)
            target = root / "safe-skill"
            target.mkdir()
            (target / "marker").write_bytes(b"must remain untouched")
            before = _snapshot(root)
            original_stat = os.stat
            original_open = os.open
            original_lstat = os.lstat

            def guarded_stat(name, *args, **kwargs):
                if name == "safe-skill":
                    raise AssertionError("candidate target was stat'ed")
                return original_stat(name, *args, **kwargs)

            def guarded_open(name, *args, **kwargs):
                if name == "safe-skill":
                    raise AssertionError("candidate target was opened")
                return original_open(name, *args, **kwargs)

            def guarded_lstat(name, *args, **kwargs):
                if os.fspath(name) == os.fspath(target):
                    raise AssertionError("candidate target was lstat'ed")
                return original_lstat(name, *args, **kwargs)

            with patch("skill_orchestrator.durable_journal.MutationLockSet.for_skills", side_effect=AssertionError("lock acquired")), patch(
                "skill_orchestrator.durable_journal._posix_supported", return_value=True
            ), patch(
                "skill_orchestrator.transactional_fs.RealFilesystemAdapter.secure_staging_supported",
                return_value=True,
            ), patch(
                "skill_orchestrator.transactional_fs.os.stat", side_effect=guarded_stat
            ), patch("skill_orchestrator.transactional_fs.os.open", side_effect=guarded_open), patch(
                "skill_orchestrator.durable_recovery_assessment.os.lstat", side_effect=guarded_lstat
            ), patch.object(socket, "socket", side_effect=AssertionError("network used")):
                result = assess_durable_recovery(root)

            self.assertEqual(result.records[0].stage_status, "matching")
            self.assertEqual(_snapshot(root), before)
            self.assertEqual((target / "marker").read_bytes(), b"must remain untouched")

    def test_repeated_stable_assessments_are_equal(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            self._prepared(root)
            self.assertEqual(assess_durable_recovery(root), assess_durable_recovery(root))

    def test_module_has_no_execution_or_legacy_integration_imports(self):
        source = inspect.getsource(durable_recovery_assessment)
        imported = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        for forbidden in (
            "subprocess",
            "socket",
            "engine",
            "cli",
            "transactional_replace",
            "installed_state",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(any(forbidden in module for module in imported))


class WindowsFailClosedContractTests(unittest.TestCase):
    def test_windows_returns_before_candidate_state_stage_or_target_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "skills"
            root.mkdir(mode=0o700)
            with patch("skill_orchestrator.durable_recovery_assessment.os.name", "nt"), patch(
                "skill_orchestrator.durable_recovery_assessment.scan_durable_journals",
                side_effect=AssertionError("state accessed"),
            ):
                result = assess_durable_recovery(root)
            self.assertEqual(result.status, "unsupported")
            self.assertEqual(result.installed_state_capability, "not-implemented")
            self.assertFalse((root / ".cso-state").exists())
            self.assertFalse((root / ".cso-staging").exists())
            self.assertFalse((root / "safe-skill").exists())


if __name__ == "__main__":
    unittest.main()

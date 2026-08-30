"""Public-contract tests for read-only durable stage observation."""

from __future__ import annotations

import hashlib
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
from skill_orchestrator.durable_stage_reopen import observe_durable_stage
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


@unittest.skipUnless(os.name == "posix", "durable stage observation is POSIX-only")
class DurableStageObservationTests(unittest.TestCase):
    def _root(self, base: Path) -> Path:
        root = base / "skills"
        root.mkdir(mode=0o700)
        return root

    def _prepared(self, root: Path, payload=b"safe declared bytes\n"):
        manifest = _manifest(payload)
        namespace = root / ".cso-staging"
        stage = namespace / STAGE_NAME
        leaf = stage / "nested" / "SKILL.md"
        leaf.parent.mkdir(parents=True, mode=0o700)
        namespace.chmod(0o700)
        stage.chmod(0o700)
        leaf.parent.chmod(0o700)
        leaf.write_bytes(payload)
        leaf.chmod(0o600)
        create_durable_journal(root, _document(root, manifest))
        prepared = _document(
            root,
            manifest,
            phase="PREPARED",
            stage_binding={"relative_name": STAGE_NAME, "manifest_digest": manifest_digest(manifest)},
        )
        advance_durable_journal(root, prepared)
        return manifest, stage, leaf

    def test_matching_is_read_only_non_authoritative_and_never_accesses_target(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            _, _, _ = self._prepared(root)
            target = root / "safe-skill"
            target.mkdir()
            marker = target / "marker"
            marker.write_bytes(b"target must remain untouched")
            before = _snapshot(root)
            original_open = os.open
            original_stat = os.stat

            def guarded_open(name, *args, **kwargs):
                if name == "safe-skill":
                    raise AssertionError("candidate target was opened")
                return original_open(name, *args, **kwargs)

            def guarded_stat(name, *args, **kwargs):
                if name == "safe-skill":
                    raise AssertionError("candidate target was stat'ed")
                return original_stat(name, *args, **kwargs)

            with patch("skill_orchestrator.transactional_fs.os.open", side_effect=guarded_open), patch(
                "skill_orchestrator.transactional_fs.os.stat", side_effect=guarded_stat
            ), patch(
                "skill_orchestrator.durable_journal.MutationLockSet.for_skills",
                side_effect=AssertionError("observation acquired a lock"),
            ), patch(
                "skill_orchestrator.durable_journal._posix_supported", return_value=True
            ), patch(
                "skill_orchestrator.transactional_fs.RealFilesystemAdapter.secure_staging_supported",
                return_value=True,
            ), patch.object(socket, "socket", side_effect=AssertionError("network used")):
                result = observe_durable_stage(root, TRANSACTION_ID)

            self.assertEqual(result.status, "matching")
            self.assertEqual(result.reason_ids, ("stage.matching",))
            self.assertEqual(_snapshot(root), before)
            self.assertEqual(marker.read_bytes(), b"target must remain untouched")
            self.assertFalse(hasattr(result, "lease"))

    def test_missing_stage_and_missing_staging_namespace_are_read_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            manifest = _manifest()
            create_durable_journal(root, _document(root, manifest))
            advance_durable_journal(
                root,
                _document(root, manifest, phase="PREPARED", stage_binding={"relative_name": STAGE_NAME, "manifest_digest": manifest_digest(manifest)}),
            )
            before = _snapshot(root)
            result = observe_durable_stage(root, TRANSACTION_ID)
            self.assertEqual(result.status, "missing")
            self.assertEqual(_snapshot(root), before)
            self.assertFalse((root / ".cso-staging").exists())

    def test_structure_content_and_permission_failures_are_unsafe(self):
        cases = ("substituted", "extra", "missing", "symlink", "hardlink", "fifo", "permission")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = self._root(Path(temporary))
                _, stage, leaf = self._prepared(root)
                if case == "substituted":
                    displaced = stage.with_name(stage.name + "-displaced")
                    stage.rename(displaced)
                    leaf = stage / "nested" / "SKILL.md"
                    leaf.parent.mkdir(parents=True, mode=0o700)
                    stage.chmod(0o700)
                    leaf.parent.chmod(0o700)
                    leaf.write_bytes(b"substituted bytes")
                    leaf.chmod(0o600)
                elif case == "extra":
                    extra = stage / "extra.txt"
                    extra.write_bytes(b"extra")
                    extra.chmod(0o600)
                elif case == "missing":
                    leaf.unlink()
                elif case == "symlink":
                    if not hasattr(os, "symlink"):
                        self.skipTest("symlink unavailable")
                    leaf.unlink()
                    leaf.symlink_to(root / "outside")
                elif case == "hardlink":
                    if not hasattr(os, "link"):
                        self.skipTest("hardlink unavailable")
                    alias = stage / "nested" / "alias"
                    os.link(leaf, alias)
                elif case == "fifo":
                    if not hasattr(os, "mkfifo"):
                        self.skipTest("FIFO unavailable")
                    leaf.unlink()
                    os.mkfifo(leaf)
                else:
                    leaf.chmod(0o644)
                self.assertEqual(observe_durable_stage(root, TRANSACTION_ID).status, "unsafe")

    def test_manifest_binding_journal_corruption_and_root_mismatch_are_unsafe(self):
        cases = ("binding", "utf8", "duplicate", "root")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = self._root(Path(temporary))
                manifest, _, _ = self._prepared(root)
                journal = root / ".cso-state" / "transactions" / TRANSACTION_ID / JOURNAL_NAME
                if case == "binding":
                    document = _document(
                        root,
                        manifest,
                        phase="PREPARED",
                        stage_binding={"relative_name": STAGE_NAME, "manifest_digest": "0" * 64},
                    )
                    journal.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")), encoding="utf-8")
                elif case == "utf8":
                    journal.write_bytes(b"\xff")
                elif case == "duplicate":
                    journal.write_bytes(b'{"schema_version":1,"schema_version":1}')
                else:
                    document = _document(root, manifest)
                    document["skills_root_identity"]["inode"] += 1
                    journal.write_text(json.dumps(document, sort_keys=True, separators=(",", ":")), encoding="utf-8")
                journal.chmod(0o600)
                self.assertEqual(observe_durable_stage(root, TRANSACTION_ID).status, "unsafe")

    def test_nonterminal_without_binding_is_not_applicable_and_repeated_stable_reads_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            manifest = _manifest()
            create_durable_journal(root, _document(root, manifest))
            self.assertEqual(observe_durable_stage(root, TRANSACTION_ID).status, "not-applicable")

        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            self._prepared(root)
            self.assertEqual(observe_durable_stage(root, TRANSACTION_ID).status, "matching")
            self.assertEqual(observe_durable_stage(root, TRANSACTION_ID).status, "matching")

    def test_stage_change_during_observation_is_unstable(self):
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
                result = observe_durable_stage(root, TRANSACTION_ID)
            self.assertEqual(result.status, "unstable")


class WindowsFailClosedContractTests(unittest.TestCase):
    def test_windows_returns_before_state_or_staging_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "skills"
            root.mkdir(mode=0o700)
            with patch("skill_orchestrator.durable_stage_reopen.os.name", "nt"):
                result = observe_durable_stage(root, TRANSACTION_ID)
            self.assertEqual(result.status, "unsupported")
            self.assertFalse((root / ".cso-state").exists())
            self.assertFalse((root / ".cso-staging").exists())
            self.assertFalse((root / "safe-skill").exists())


if __name__ == "__main__":
    unittest.main()

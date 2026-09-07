"""Contracts for descriptor-safe post-call target identity evidence."""

from __future__ import annotations

import ast
import copy
import os
import pickle
import stat
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import skill_orchestrator._post_call_target_identity as identity
from skill_orchestrator._posix_no_replace import _move_directory_leaf_no_replace
from skill_orchestrator._post_call_target_identity import (
    _PostCallTargetIdentityEvidence,
    _observe_post_call_target_identity,
)
from skill_orchestrator.transactional_fs import (
    ExecutionLimits,
    OwnedStageLease,
    RealFilesystemAdapter,
    _RetainedStageIdentityEvidence,
    _RootHandles,
    _StageHandle,
)


def _info(*, directory: bool = True, device: int = 11, inode: int = 22):
    return SimpleNamespace(
        st_mode=stat.S_IFDIR if directory else stat.S_IFREG,
        st_dev=device,
        st_ino=inode,
    )


def _source(status: str = "matched", device: int = 31, inode: int = 41):
    if status == "matched":
        return _RetainedStageIdentityEvidence(status, device, inode)
    return _RetainedStageIdentityEvidence("unavailable", None, None)


class _LeaseAdapter:
    def cleanup_stage(self, stage) -> None:
        stage.fd = -1

    def close_stage(self, stage) -> None:
        stage.fd = -1

    def close_roots(self, roots) -> None:
        roots.source_fd = -1
        roots.staging_parent_fd = -1

    def verify_stage(self, stage, expected, limits):
        del stage, expected, limits
        return ()


def _lease():
    roots = _RootHandles(-1, 52, "/source", "/staging")
    stage = _StageHandle(roots, ".demo.cso-stage", "token", 51, 31, 41)
    lease = OwnedStageLease(
        _LeaseAdapter(),
        roots,
        stage,
        {"SKILL.md": ("a" * 64, 1)},
        ExecutionLimits(),
        "b" * 64,
        1,
    )
    lease.consume()
    return lease, roots, stage


class PostCallTargetIdentityPortableTests(unittest.TestCase):
    def _observe(
        self,
        *,
        sources=None,
        parents=None,
        target=None,
        target_error=None,
        lease=None,
        descriptor=7,
        expected=(11, 22),
        leaf="safe-skill",
    ):
        lease = object() if lease is None else lease
        sources = [_source(), _source()] if sources is None else sources
        parents = [_info(), _info()] if parents is None else parents
        target = _info(device=31, inode=41) if target is None else target
        with patch.object(identity, "_supported", return_value=True), patch.object(
            identity, "_observe_consumed_stage_identity", side_effect=sources
        ) as source_observer, patch.object(
            identity.os, "fstat", side_effect=parents
        ) as parent_observer, patch.object(
            identity.os,
            "stat",
            side_effect=target_error,
            return_value=target,
        ) as target_observer:
            result = _observe_post_call_target_identity(
                lease, descriptor, expected, leaf
            )
        return result, source_observer, parent_observer, target_observer

    def test_result_is_frozen_bounded_copyable_and_authority_free(self):
        for status in ("matched", "mismatched", "unavailable"):
            result = _PostCallTargetIdentityEvidence(status)
            self.assertEqual(copy.copy(result), result)
            self.assertEqual(copy.deepcopy(result), result)
            self.assertEqual(pickle.loads(pickle.dumps(result)), result)
            self.assertEqual(set(result.__dict__), {"status"})
            for field in (
                "device", "inode", "fd", "path", "target", "lease", "close",
                "lock", "authorization", "native_result", "journal",
            ):
                self.assertFalse(hasattr(result, field))
        with self.assertRaises(FrozenInstanceError):
            _PostCallTargetIdentityEvidence("matched").status = "unavailable"
        for value in ("verified", "", None, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                _PostCallTargetIdentityEvidence(value)

    def test_exact_source_and_target_identity_matches(self):
        result, sources, parents, target = self._observe()
        self.assertEqual(result.status, "matched")
        self.assertEqual(sources.call_count, 2)
        self.assertEqual(parents.call_count, 2)
        target.assert_called_once_with("safe-skill", dir_fd=7, follow_symlinks=False)

    def test_source_unavailable_before_target_fails_closed(self):
        result, sources, parents, target = self._observe(sources=[_source("unavailable")])
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(sources.call_count, 1)
        self.assertEqual(parents.call_count, 1)
        target.assert_not_called()

    def test_source_unavailable_after_target_overrides_match_or_mismatch(self):
        for target in (_info(device=31, inode=41), _info(device=31, inode=99)):
            with self.subTest(target=target):
                result, _, _, _ = self._observe(
                    sources=[_source(), _source("unavailable")], target=target
                )
                self.assertEqual(result.status, "unavailable")

    def test_source_identity_bracket_drift_is_unavailable(self):
        result, _, _, _ = self._observe(sources=[_source(), _source(inode=42)])
        self.assertEqual(result.status, "unavailable")

    def test_invalid_descriptor_expected_identity_and_leaf_fail_before_observation(self):
        invalid = (
            {"descriptor": True},
            {"descriptor": -1},
            {"expected": [11, 22]},
            {"expected": (True, 22)},
            {"expected": (11, 0)},
            {"leaf": ""},
            {"leaf": "."},
            {"leaf": ".."},
            {"leaf": "/absolute"},
            {"leaf": "a/b"},
            {"leaf": "a\\b"},
            {"leaf": "a\x00b"},
        )
        for values in invalid:
            with self.subTest(values=values), patch.object(
                identity, "_supported", return_value=True
            ), patch.object(identity.os, "fstat", side_effect=AssertionError), patch.object(
                identity, "_observe_consumed_stage_identity", side_effect=AssertionError
            ):
                self.assertEqual(
                    _observe_post_call_target_identity(
                        object(),
                        values.get("descriptor", 7),
                        values.get("expected", (11, 22)),
                        values.get("leaf", "safe-skill"),
                    ).status,
                    "unavailable",
                )

    def test_parent_fstat_failure_non_directory_and_identity_mismatch_are_unavailable(self):
        cases = (
            OSError("closed"),
            _info(directory=False),
            _info(device=12),
            _info(inode=23),
        )
        for parent in cases:
            with self.subTest(parent=parent):
                side_effect = parent if isinstance(parent, OSError) else [parent]
                result, sources, _, target = self._observe(parents=side_effect)
                self.assertEqual(result.status, "unavailable")
                sources.assert_not_called()
                target.assert_not_called()

    def test_parent_post_check_loss_overrides_target_mismatch(self):
        for parent_after in (OSError("closed"), _info(inode=23)):
            with self.subTest(parent_after=parent_after):
                result, _, _, _ = self._observe(
                    parents=[_info(), parent_after],
                    target=_info(device=31, inode=99),
                )
                self.assertEqual(result.status, "unavailable")

    def test_target_counterevidence_is_mismatched_after_valid_bracket(self):
        cases = (
            _info(device=32, inode=41),
            _info(device=31, inode=42),
            _info(directory=False, device=31, inode=41),
            SimpleNamespace(st_mode=stat.S_IFLNK, st_dev=31, st_ino=41),
        )
        for target in cases:
            with self.subTest(target=target):
                result, _, _, _ = self._observe(target=target)
                self.assertEqual(result.status, "mismatched")

    def test_target_absence_is_mismatched_only_after_valid_post_checks(self):
        result, sources, parents, _ = self._observe(target_error=FileNotFoundError())
        self.assertEqual(result.status, "mismatched")
        self.assertEqual(sources.call_count, 2)
        self.assertEqual(parents.call_count, 2)

        result, _, _, _ = self._observe(
            sources=[_source(), _source("unavailable")],
            target_error=FileNotFoundError(),
        )
        self.assertEqual(result.status, "unavailable")

    def test_unexpected_target_stat_failure_is_unavailable_after_bracket(self):
        result, sources, parents, _ = self._observe(target_error=PermissionError())
        self.assertEqual(result.status, "unavailable")
        self.assertEqual(sources.call_count, 2)
        self.assertEqual(parents.call_count, 2)

    def test_observation_does_not_mutate_lease_or_use_descriptor_ownership_or_mutation(self):
        lease, roots, stage = _lease()
        before = (lease.state, lease.taint_reason, roots.staging_parent_fd, stage.fd)
        with patch.object(identity, "_supported", return_value=True), patch.object(
            identity, "_observe_consumed_stage_identity", side_effect=[_source(), _source()]
        ), patch.object(identity.os, "fstat", side_effect=[_info(), _info()]), patch.object(
            identity.os, "stat", return_value=_info(device=31, inode=41)
        ), patch.object(identity.os, "close", side_effect=AssertionError("close")), patch.object(
            identity.os, "dup", side_effect=AssertionError("dup")
        ), patch.object(identity.os, "rename", side_effect=AssertionError("rename")), patch.object(
            identity.os, "replace", side_effect=AssertionError("replace")
        ):
            first = _observe_post_call_target_identity(lease, 7, (11, 22), "safe-skill")
        self.assertEqual(first.status, "matched")
        self.assertEqual(
            (lease.state, lease.taint_reason, roots.staging_parent_fd, stage.fd), before
        )

    def test_repeated_observation_is_read_only(self):
        lease, _, _ = _lease()
        before = (lease.state, lease.taint_reason)
        first, _, _, _ = self._observe(lease=lease)
        second, _, _, _ = self._observe(lease=lease)
        self.assertEqual(first, second)
        self.assertEqual((lease.state, lease.taint_reason), before)

    def test_module_has_no_native_state_lock_recovery_or_publication_integration(self):
        source = Path(identity.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imports = []
        calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(item.name for item in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                calls.add(node.func.attr)
        for forbidden in (
            "_posix_no_replace", "publication_outcome", "durable_journal",
            "mutation_lock", "installed_state", "recovery", "engine", "cli",
        ):
            self.assertFalse(any(forbidden in module for module in imports))
        self.assertFalse(
            calls
            & {"open", "close", "dup", "rename", "replace", "mkdir", "rmdir", "unlink", "fsync"}
        )


class WindowsFailClosedTests(unittest.TestCase):
    def test_windows_is_unavailable_before_lease_descriptor_or_filesystem_access(self):
        with patch.object(identity.os, "name", "nt"), patch.object(
            identity, "_observe_consumed_stage_identity", side_effect=AssertionError("lease")
        ), patch.object(identity.os, "fstat", side_effect=AssertionError("fstat")), patch.object(
            identity.os, "stat", side_effect=AssertionError("stat")
        ):
            result = _observe_post_call_target_identity(object(), -1, (), "")
        self.assertEqual(result.status, "unavailable")


@unittest.skipUnless(sys.platform in ("darwin", "linux"), "requires POSIX native publication")
class PostCallTargetIdentityPosixTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="cso post-call identity ")
        self.root = Path(self.temporary.name)
        self.source_parent = self.root / "source-parent"
        self.destination_parent = self.root / "destination-parent"
        self.source_parent.mkdir()
        self.destination_parent.mkdir()
        self.source_parent_fd = os.open(
            self.source_parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        self.destination_parent_fd = os.open(
            self.destination_parent,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        self.stage_name = ".demo.cso-stage-token"
        self.target_name = "safe-skill"
        stage_path = self.source_parent / self.stage_name
        stage_path.mkdir(mode=0o700)
        self.stage_fd = os.open(
            stage_path,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        stage_info = os.fstat(self.stage_fd)
        roots = _RootHandles(-1, self.source_parent_fd, str(self.root / "unused"), str(self.source_parent))
        stage = _StageHandle(
            roots, self.stage_name, "token", self.stage_fd, stage_info.st_dev, stage_info.st_ino
        )
        self.lease = OwnedStageLease(
            RealFilesystemAdapter(), roots, stage,
            {"SKILL.md": ("a" * 64, 1)}, ExecutionLimits(), "b" * 64, 1,
        )
        parent_info = os.fstat(self.destination_parent_fd)
        self.expected_parent = (parent_info.st_dev, parent_info.st_ino)
        native = _move_directory_leaf_no_replace(
            self.source_parent_fd,
            self.stage_name,
            self.destination_parent_fd,
            self.target_name,
        )
        self.assertEqual(native.status, "succeeded")
        self.lease.consume()

    def tearDown(self):
        self.lease.close()
        os.close(self.destination_parent_fd)
        self.temporary.cleanup()

    def _observe(self, *, descriptor=None, expected=None):
        return _observe_post_call_target_identity(
            self.lease,
            self.destination_parent_fd if descriptor is None else descriptor,
            self.expected_parent if expected is None else expected,
            self.target_name,
        )

    def test_real_native_publication_matches_and_borrowed_descriptors_remain_open(self):
        self.assertEqual(self._observe().status, "matched")
        os.fstat(self.destination_parent_fd)
        os.fstat(self.source_parent_fd)
        os.fstat(self.stage_fd)

    def test_real_replacement_missing_symlink_and_non_directory_are_mismatched(self):
        target = self.destination_parent / self.target_name
        replacement = self.destination_parent / "replacement"
        replacement.mkdir()
        target.rmdir()
        replacement.rename(target)
        self.assertEqual(self._observe().status, "mismatched")

        target.rmdir()
        self.assertEqual(self._observe().status, "mismatched")

        target.symlink_to("missing")
        self.assertEqual(self._observe().status, "mismatched")
        target.unlink()

        target.write_bytes(b"not a directory")
        self.assertEqual(self._observe().status, "mismatched")

    def test_retained_parent_fd_stays_scoped_to_original_directory_after_path_replacement(self):
        displaced = self.root / "destination-parent-displaced"
        self.destination_parent.rename(displaced)
        self.destination_parent.mkdir()
        (self.destination_parent / self.target_name).mkdir()
        self.assertEqual(self._observe().status, "matched")

    def test_wrong_parent_descriptor_is_unavailable_and_remains_open(self):
        wrong = self.root / "wrong-parent"
        wrong.mkdir()
        wrong_fd = os.open(
            wrong,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            self.assertEqual(self._observe(descriptor=wrong_fd).status, "unavailable")
            os.fstat(wrong_fd)
        finally:
            os.close(wrong_fd)


if __name__ == "__main__":
    unittest.main()

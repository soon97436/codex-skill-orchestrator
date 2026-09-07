"""Contracts for package-private retained-stage identity evidence."""

from __future__ import annotations

import copy
import os
import pickle
import stat
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import skill_orchestrator.transactional_fs as transactional_fs
from skill_orchestrator.transactional_fs import (
    ExecutionLimits,
    OwnedStageLease,
    _RetainedStageIdentityEvidence,
    _RootHandles,
    _StageHandle,
    _observe_consumed_stage_identity,
)


class _LeaseAdapter:
    def __init__(self, *, cleanup_fails: bool = False) -> None:
        self.cleanup_fails = cleanup_fails
        self.close_stage_calls = 0
        self.close_roots_calls = 0

    def cleanup_stage(self, stage) -> None:
        if self.cleanup_fails:
            raise OSError("forced cleanup failure")
        stage.fd = -1

    def close_stage(self, stage) -> None:
        if stage.fd >= 0:
            self.close_stage_calls += 1
            os.close(stage.fd)
            stage.fd = -1

    def close_roots(self, roots) -> None:
        self.close_roots_calls += 1
        for field in ("source_fd", "staging_parent_fd"):
            descriptor = getattr(roots, field)
            if descriptor >= 0:
                os.close(descriptor)
                setattr(roots, field, -1)

    def verify_stage(self, stage, expected, limits):
        del stage, expected, limits
        return ()


def _stat_result(*, directory: bool, device: int, inode: int) -> SimpleNamespace:
    return SimpleNamespace(
        st_mode=stat.S_IFDIR if directory else stat.S_IFREG,
        st_dev=device,
        st_ino=inode,
    )


class RetainedStageIdentityEvidencePortableTests(unittest.TestCase):
    """Portable contract tests: no directory descriptor is opened."""

    _DESCRIPTOR = 41
    _DEVICE = 101
    _INODE = 202

    def _lease(self, *, adapter=None, descriptor=None, device=None, inode=None):
        adapter = _LeaseAdapter() if adapter is None else adapter
        roots = _RootHandles(-1, -1, "/private/source", "/private/staging")
        stage = _StageHandle(
            roots,
            ".demo.cso-stage-token",
            "token",
            self._DESCRIPTOR if descriptor is None else descriptor,
            self._DEVICE if device is None else device,
            self._INODE if inode is None else inode,
        )
        return OwnedStageLease(
            adapter,
            roots,
            stage,
            {"SKILL.md": ("a" * 64, 1)},
            ExecutionLimits(),
            "b" * 64,
            1,
        ), adapter, roots, stage

    def _consumed_lease(self, **kwargs):
        lease, adapter, roots, stage = self._lease(**kwargs)
        lease.consume()
        return lease, adapter, roots, stage

    def _matching_stat(self) -> SimpleNamespace:
        return _stat_result(directory=True, device=self._DEVICE, inode=self._INODE)

    def test_consumed_lease_returns_retained_fstat_identity(self):
        lease, adapter, _, stage = self._consumed_lease()
        before_state = lease.state
        before_reason = lease.taint_reason

        with patch("skill_orchestrator.transactional_fs.os.fstat", return_value=self._matching_stat()):
            evidence = _observe_consumed_stage_identity(lease)

        self.assertEqual((evidence.status, evidence.device, evidence.inode), ("matched", self._DEVICE, self._INODE))
        self.assertEqual(lease.state, before_state)
        self.assertEqual(lease.taint_reason, before_reason)
        self.assertEqual(stage.fd, self._DESCRIPTOR)
        self.assertEqual(adapter.close_stage_calls, 0)

    def test_repeated_observation_is_read_only_and_idempotent(self):
        lease, adapter, roots, stage = self._consumed_lease()
        with patch("skill_orchestrator.transactional_fs.os.fstat", return_value=self._matching_stat()) as observed:
            first = _observe_consumed_stage_identity(lease)
            second = _observe_consumed_stage_identity(lease)

        self.assertEqual(first, second)
        self.assertEqual(observed.call_count, 2)
        self.assertEqual(lease.state, "consumed")
        self.assertIsNone(lease.taint_reason)
        self.assertEqual(stage.fd, self._DESCRIPTOR)
        self.assertEqual(roots.staging_parent_fd, -1)
        self.assertEqual(adapter.close_stage_calls, 0)
        self.assertEqual(adapter.close_roots_calls, 0)

    def test_non_consumed_and_tainted_leases_fail_closed_without_state_change(self):
        active, _, _, _ = self._lease()
        cleaned, _, _, _ = self._lease()
        cleaned.cleanup()
        required, _, _, _ = self._lease(adapter=_LeaseAdapter(cleanup_fails=True))
        required.cleanup()
        tainted, _, _, _ = self._consumed_lease()
        tainted.taint("post-rename-sync-failed")

        for lease in (active, cleaned, required, tainted):
            with self.subTest(state=lease.state):
                before = (lease.state, lease.taint_reason)
                evidence = _observe_consumed_stage_identity(lease)
                self.assertEqual((evidence.status, evidence.device, evidence.inode), ("unavailable", None, None))
                self.assertEqual((lease.state, lease.taint_reason), before)

    def test_consumed_sentinel_descriptor_fails_closed_without_close(self):
        lease, adapter, _, stage = self._consumed_lease(descriptor=-1)

        self.assertEqual(_observe_consumed_stage_identity(lease).status, "unavailable")
        self.assertEqual(stage.fd, -1)
        self.assertEqual(adapter.close_stage_calls, 0)

    def test_fstat_failure_non_directory_and_identity_mismatches_fail_closed(self):
        lease, _, _, _ = self._consumed_lease()
        with patch("skill_orchestrator.transactional_fs.os.fstat", side_effect=OSError):
            self.assertEqual(_observe_consumed_stage_identity(lease).status, "unavailable")

        non_directory, _, _, _ = self._consumed_lease()
        with patch(
            "skill_orchestrator.transactional_fs.os.fstat",
            return_value=_stat_result(directory=False, device=self._DEVICE, inode=self._INODE),
        ):
            self.assertEqual(_observe_consumed_stage_identity(non_directory).status, "unavailable")

        device_mismatch, _, _, _ = self._consumed_lease(device=self._DEVICE + 1)
        inode_mismatch, _, _, _ = self._consumed_lease(inode=self._INODE + 1)
        with patch("skill_orchestrator.transactional_fs.os.fstat", return_value=self._matching_stat()):
            self.assertEqual(_observe_consumed_stage_identity(device_mismatch).status, "unavailable")
            self.assertEqual(_observe_consumed_stage_identity(inode_mismatch).status, "unavailable")

    def test_result_invariants_are_immutable_and_do_not_expose_capabilities(self):
        matched = _RetainedStageIdentityEvidence("matched", 1, 2)
        unavailable = _RetainedStageIdentityEvidence("unavailable", None, None)
        self.assertEqual((matched.status, matched.device, matched.inode), ("matched", 1, 2))
        self.assertEqual((unavailable.status, unavailable.device, unavailable.inode), ("unavailable", None, None))
        with self.assertRaises(FrozenInstanceError):
            matched.device = 3
        for values in (("matched", None, None), ("unavailable", 1, 2), ("other", None, None)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                _RetainedStageIdentityEvidence(*values)
        for name in ("fd", "path", "stage_name", "target", "close", "borrow"):
            with self.subTest(name=name):
                self.assertFalse(hasattr(matched, name))

    def test_observation_has_no_namespace_mutation_and_lease_restrictions_remain(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            stage = root / "staging" / ".demo.cso-stage-token"
            stage.mkdir(parents=True)
            lease, _, _, _ = self._consumed_lease()
            before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
            with patch("skill_orchestrator.transactional_fs.os.fstat", return_value=self._matching_stat()):
                self.assertEqual(_observe_consumed_stage_identity(lease).status, "matched")
            after = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
            self.assertEqual(after, before)

        active, _, _, _ = self._lease()
        with self.assertRaises(TypeError):
            copy.copy(active)
        with self.assertRaises(TypeError):
            copy.deepcopy(active)
        with self.assertRaises(TypeError):
            pickle.dumps(active)

    def test_helper_is_internal_and_does_not_use_target_or_descriptor_transfer_operations(self):
        source = Path(transactional_fs.__file__).read_text(encoding="utf-8")
        helper = source[source.index("def _observe_consumed_stage_identity"):]
        self.assertNotIn("os.close", helper)
        self.assertNotIn("os.dup", helper)
        self.assertNotIn("os.open", helper)
        self.assertNotIn("os.stat", helper)
        self.assertNotIn("target", helper)
        self.assertNotIn("durable_target", helper)
        self.assertNotIn("publication_outcome", helper)

    @unittest.skipUnless(os.name == "posix", "requires a POSIX directory file descriptor")
    def test_posix_directory_fd_returns_real_retained_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            staging = Path(temporary) / "staging"
            stage_path = staging / ".demo.cso-stage-token"
            stage_path.mkdir(parents=True)
            parent_fd = os.open(staging, os.O_RDONLY)
            stage_fd = os.open(stage_path, os.O_RDONLY)
            stage_info = os.fstat(stage_fd)
            adapter = _LeaseAdapter()
            roots = _RootHandles(-1, parent_fd, "/private/source", "/private/staging")
            stage = _StageHandle(roots, ".demo.cso-stage-token", "token", stage_fd, stage_info.st_dev, stage_info.st_ino)
            lease = OwnedStageLease(adapter, roots, stage, {"SKILL.md": ("a" * 64, 1)}, ExecutionLimits(), "b" * 64, 1)
            lease.consume()

            evidence = _observe_consumed_stage_identity(lease)

            self.assertEqual((evidence.status, evidence.device, evidence.inode), ("matched", stage_info.st_dev, stage_info.st_ino))
            self.assertEqual(stage.fd, stage_fd)
            self.assertEqual(adapter.close_stage_calls, 0)
            lease.close()

    @unittest.skipUnless(os.name == "posix", "requires a POSIX directory file descriptor")
    def test_posix_closed_directory_fd_is_unavailable_without_extra_close(self):
        with tempfile.TemporaryDirectory() as temporary:
            staging = Path(temporary) / "staging"
            stage_path = staging / ".demo.cso-stage-token"
            stage_path.mkdir(parents=True)
            parent_fd = os.open(staging, os.O_RDONLY)
            stage_fd = os.open(stage_path, os.O_RDONLY)
            stage_info = os.fstat(stage_fd)
            adapter = _LeaseAdapter()
            roots = _RootHandles(-1, parent_fd, "/private/source", "/private/staging")
            stage = _StageHandle(roots, ".demo.cso-stage-token", "token", stage_fd, stage_info.st_dev, stage_info.st_ino)
            lease = OwnedStageLease(adapter, roots, stage, {"SKILL.md": ("a" * 64, 1)}, ExecutionLimits(), "b" * 64, 1)
            lease.consume()
            lease.close()

            self.assertEqual(lease.state, "consumed")
            self.assertEqual(_observe_consumed_stage_identity(lease).status, "unavailable")
            self.assertEqual(stage.fd, -1)
            self.assertEqual(adapter.close_stage_calls, 1)


if __name__ == "__main__":
    unittest.main()

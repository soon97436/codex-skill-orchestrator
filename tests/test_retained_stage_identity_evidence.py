"""Contracts for package-private retained-stage identity evidence."""

from __future__ import annotations

import copy
import os
import pickle
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
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


class RetainedStageIdentityEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.staging = self.root / "staging"
        self.stage_path = self.staging / ".demo.cso-stage-token"
        self.staging.mkdir()
        self.stage_path.mkdir()
        self.parent_fd = os.open(self.staging, os.O_RDONLY)
        self.stage_fd = os.open(self.stage_path, os.O_RDONLY)

    def tearDown(self) -> None:
        for descriptor in (getattr(self, "stage_fd", -1), getattr(self, "parent_fd", -1)):
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        self.temporary.cleanup()

    def _lease(self, *, adapter=None, descriptor=None, device=None, inode=None):
        adapter = _LeaseAdapter() if adapter is None else adapter
        info = os.fstat(self.stage_fd)
        roots = _RootHandles(-1, self.parent_fd, "/private/source", "/private/staging")
        stage = _StageHandle(
            roots,
            ".demo.cso-stage-token",
            "token",
            self.stage_fd if descriptor is None else descriptor,
            info.st_dev if device is None else device,
            info.st_ino if inode is None else inode,
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

    def test_consumed_lease_returns_retained_fstat_identity(self):
        lease, _, _, stage = self._consumed_lease()
        before_state = lease.state
        before_reason = lease.taint_reason

        evidence = _observe_consumed_stage_identity(lease)
        current = os.fstat(stage.fd)

        self.assertEqual(evidence.status, "matched")
        self.assertEqual((evidence.device, evidence.inode), (current.st_dev, current.st_ino))
        self.assertEqual((evidence.device, evidence.inode), (stage.device, stage.inode))
        self.assertEqual(lease.state, before_state)
        self.assertEqual(lease.taint_reason, before_reason)
        self.assertEqual(stage.fd, self.stage_fd)

    def test_repeated_observation_is_read_only_and_idempotent(self):
        lease, adapter, roots, stage = self._consumed_lease()
        first = _observe_consumed_stage_identity(lease)
        second = _observe_consumed_stage_identity(lease)

        self.assertEqual(first, second)
        self.assertEqual(lease.state, "consumed")
        self.assertIsNone(lease.taint_reason)
        self.assertEqual(stage.fd, self.stage_fd)
        self.assertEqual(roots.staging_parent_fd, self.parent_fd)
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

    def test_consumed_closed_or_invalid_descriptor_fails_closed_without_close(self):
        invalid, invalid_adapter, _, invalid_stage = self._consumed_lease(descriptor=-1)
        self.assertEqual(_observe_consumed_stage_identity(invalid).status, "unavailable")
        self.assertEqual(invalid_stage.fd, -1)
        self.assertEqual(invalid_adapter.close_stage_calls, 0)

        closed, closed_adapter, _, closed_stage = self._consumed_lease()
        closed.close()
        self.stage_fd = -1
        self.parent_fd = -1
        self.assertEqual(closed.state, "consumed")
        self.assertEqual(_observe_consumed_stage_identity(closed).status, "unavailable")
        self.assertEqual(closed_stage.fd, -1)
        self.assertEqual(closed_adapter.close_stage_calls, 1)

    def test_fstat_failure_non_directory_and_identity_mismatches_fail_closed(self):
        lease, _, _, _ = self._consumed_lease()
        with patch("skill_orchestrator.transactional_fs.os.fstat", side_effect=OSError):
            self.assertEqual(_observe_consumed_stage_identity(lease).status, "unavailable")

        file_path = self.root / "not-a-directory"
        file_path.write_bytes(b"fixture")
        file_fd = os.open(file_path, os.O_RDONLY)
        self.addCleanup(os.close, file_fd)
        file_info = os.fstat(file_fd)
        non_directory, _, _, _ = self._consumed_lease(
            descriptor=file_fd, device=file_info.st_dev, inode=file_info.st_ino
        )
        self.assertEqual(_observe_consumed_stage_identity(non_directory).status, "unavailable")

        device_mismatch, _, _, _ = self._consumed_lease(device=os.fstat(self.stage_fd).st_dev + 1)
        inode_mismatch, _, _, _ = self._consumed_lease(inode=os.fstat(self.stage_fd).st_ino + 1)
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
        lease, _, _, _ = self._consumed_lease()
        before = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
        self.assertEqual(_observe_consumed_stage_identity(lease).status, "matched")
        after = sorted(path.relative_to(self.root).as_posix() for path in self.root.rglob("*"))
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


if __name__ == "__main__":
    unittest.main()

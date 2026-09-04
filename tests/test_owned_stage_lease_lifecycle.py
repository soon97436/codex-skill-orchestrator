"""Platform-neutral lifecycle contracts for one owned stage lease."""

from __future__ import annotations

import copy
import pickle
import unittest
from unittest.mock import patch

from skill_orchestrator.transactional_fs import (
    ExecutionLimits,
    OwnedStageLease,
    _RootHandles,
    _StageHandle,
)


class _LeaseAdapter:
    def __init__(self, *, cleanup_fails: bool = False, partial_cleanup: bool = False) -> None:
        self.cleanup_fails = cleanup_fails
        self.partial_cleanup = partial_cleanup
        self.cleanup_calls = 0
        self.close_stage_calls = 0
        self.close_root_calls = []
        self.verify_calls = 0

    def cleanup_stage(self, stage) -> None:
        self.cleanup_calls += 1
        if self.partial_cleanup:
            stage.fd = -1
            raise OSError("forced partial cleanup failure")
        if self.cleanup_fails:
            raise OSError("forced cleanup failure")
        stage.fd = -1

    def close_stage(self, stage) -> None:
        if stage.fd >= 0:
            self.close_stage_calls += 1
            stage.fd = -1

    def close_roots(self, roots) -> None:
        for field in ("source_fd", "staging_parent_fd"):
            descriptor = getattr(roots, field)
            if descriptor >= 0:
                self.close_root_calls.append((field, descriptor))
                setattr(roots, field, -1)

    def verify_stage(self, stage, expected, limits):
        del stage, expected, limits
        self.verify_calls += 1
        return ()


class OwnedStageLeaseLifecycleTests(unittest.TestCase):
    def _lease(self, *, adapter=None):
        adapter = _LeaseAdapter() if adapter is None else adapter
        roots = _RootHandles(-1, 102, "/private/source", "/private/staging")
        stage = _StageHandle(roots, ".demo.cso-stage-token", "token", 101, 7, 9)
        lease = OwnedStageLease(
            adapter,
            roots,
            stage,
            {"SKILL.md": ("a" * 64, 1)},
            ExecutionLimits(),
            "b" * 64,
            1,
        )
        return lease, adapter, roots, stage

    def test_initial_state_and_reason_are_bounded(self) -> None:
        lease, _, _, _ = self._lease()
        self.assertEqual(lease.state, "active")
        self.assertIsNone(lease.taint_reason)
        self.assertFalse(hasattr(lease, "__enter__"))
        self.assertFalse(hasattr(lease, "__exit__"))

    def test_active_cleanup_preserves_current_success_and_idempotency(self) -> None:
        lease, adapter, roots, stage = self._lease()

        self.assertEqual(lease.cleanup().status, "cleaned")
        self.assertEqual(lease.state, "cleaned")
        self.assertEqual(lease.cleanup().status, "cleaned")
        self.assertEqual(adapter.cleanup_calls, 1)
        self.assertEqual(stage.fd, -1)
        self.assertEqual(roots.staging_parent_fd, -1)

    def test_cleanup_failure_preserves_cleanup_required_without_retry(self) -> None:
        lease, adapter, _, _ = self._lease(adapter=_LeaseAdapter(cleanup_fails=True))

        self.assertEqual(lease.cleanup().status, "cleanup-required")
        self.assertEqual(lease.state, "cleanup-required")
        self.assertIsNone(lease.taint_reason)
        self.assertEqual(lease.cleanup().status, "cleanup-required")
        self.assertEqual(adapter.cleanup_calls, 1)

    def test_consume_revokes_active_only_operations_without_closing_metadata(self) -> None:
        lease, adapter, roots, stage = self._lease()
        expected = lease._OwnedStageLease__expected
        digest = lease._OwnedStageLease__manifest_digest

        lease.consume()

        self.assertEqual(lease.state, "consumed")
        self.assertIsNone(lease.taint_reason)
        self.assertEqual(stage.fd, 101)
        self.assertEqual(roots.staging_parent_fd, 102)
        self.assertEqual(expected, {"SKILL.md": ("a" * 64, 1)})
        self.assertEqual(digest, "b" * 64)
        self.assertFalse(lease._revalidate())
        self.assertFalse(lease._matches_parent(7, 9))
        self.assertEqual(adapter.verify_calls, 0)
        with self.assertRaises(RuntimeError):
            lease.consume()

    def test_consumed_sync_failure_taints_before_cleanup_or_close(self) -> None:
        lease, adapter, roots, stage = self._lease()
        lease.consume()
        self.assertEqual(lease.state, "consumed")
        self.assertIsNone(lease.taint_reason)

        lease.taint("post-rename-sync-failed")

        self.assertEqual(lease.state, "tainted")
        self.assertEqual(lease.taint_reason, "post-rename-sync-failed")
        with self.assertRaises(RuntimeError):
            lease.taint("post-rename-sync-failed")
        with self.assertRaises(RuntimeError):
            lease.cleanup()
        self.assertEqual(adapter.cleanup_calls, 0)
        lease.close()
        lease.close()
        self.assertEqual(stage.fd, -1)
        self.assertEqual(roots.staging_parent_fd, -1)

    def test_active_taints_are_terminal(self) -> None:
        for reason in ("native-outcome-indeterminate", "source-binding-lost"):
            with self.subTest(reason=reason):
                lease, _, _, _ = self._lease()
                lease.taint(reason)
                self.assertEqual(lease.state, "tainted")
                self.assertEqual(lease.taint_reason, reason)
                self.assertFalse(lease._revalidate())
                self.assertFalse(lease._matches_parent(7, 9))
                with self.assertRaises(RuntimeError):
                    lease.consume()
                with self.assertRaises(RuntimeError):
                    lease.taint(reason)

    def test_invalid_and_cross_state_taints_fail_without_mutation(self) -> None:
        lease, _, _, _ = self._lease()
        with self.assertRaises(ValueError):
            lease.taint("unbounded")
        self.assertEqual(lease.state, "active")
        self.assertIsNone(lease.taint_reason)
        with self.assertRaises(RuntimeError):
            lease.taint("post-rename-sync-failed")

        lease.consume()
        for reason in ("native-outcome-indeterminate", "source-binding-lost"):
            with self.subTest(reason=reason), self.assertRaises(RuntimeError):
                lease.taint(reason)

    def test_terminal_states_cannot_consume_or_taint(self) -> None:
        cleaned, _, _, _ = self._lease()
        cleaned.cleanup()
        required, _, _, _ = self._lease(adapter=_LeaseAdapter(cleanup_fails=True))
        required.cleanup()
        for lease in (cleaned, required):
            with self.subTest(state=lease.state):
                with self.assertRaises(RuntimeError):
                    lease.consume()
                with self.assertRaises(RuntimeError):
                    lease.taint("native-outcome-indeterminate")
                self.assertFalse(lease._revalidate())
                self.assertFalse(lease._matches_parent(7, 9))

    def test_every_non_tainted_state_has_no_taint_reason(self) -> None:
        active, _, _, _ = self._lease()
        consumed, _, _, _ = self._lease()
        consumed.consume()
        cleaned, _, _, _ = self._lease()
        cleaned.cleanup()
        required, _, _, _ = self._lease(adapter=_LeaseAdapter(cleanup_fails=True))
        required.cleanup()

        for lease in (active, consumed, cleaned, required):
            with self.subTest(state=lease.state):
                self.assertNotEqual(lease.state, "tainted")
                self.assertIsNone(lease.taint_reason)

    def test_consumed_and_tainted_cleanup_fail_before_adapter_cleanup(self) -> None:
        consumed, consumed_adapter, _, _ = self._lease()
        consumed.consume()
        tainted, tainted_adapter, _, _ = self._lease()
        tainted.taint("native-outcome-indeterminate")

        for lease, adapter in ((consumed, consumed_adapter), (tainted, tainted_adapter)):
            with self.subTest(state=lease.state), self.assertRaises(RuntimeError):
                lease.cleanup()
            self.assertEqual(adapter.cleanup_calls, 0)

    def test_close_is_terminal_only_descriptor_release_and_idempotent(self) -> None:
        for transition in ("consumed", "tainted"):
            with self.subTest(transition=transition):
                lease, adapter, roots, stage = self._lease()
                if transition == "consumed":
                    lease.consume()
                else:
                    lease.taint("native-outcome-indeterminate")

                lease.close()
                lease.close()

                self.assertEqual(lease.state, transition)
                self.assertEqual(stage.fd, -1)
                self.assertEqual(roots.staging_parent_fd, -1)
                self.assertEqual(adapter.cleanup_calls, 0)
                self.assertEqual(adapter.close_stage_calls, 1)
                self.assertEqual(adapter.close_root_calls, [("staging_parent_fd", 102)])

    def test_close_preserves_tainted_reason_and_closes_cleanup_required(self) -> None:
        consumed, _, consumed_roots, consumed_stage = self._lease()
        consumed.consume()
        consumed.taint("post-rename-sync-failed")
        consumed.close()
        self.assertEqual(consumed.state, "tainted")
        self.assertEqual(consumed.taint_reason, "post-rename-sync-failed")
        self.assertEqual(consumed_stage.fd, -1)
        self.assertEqual(consumed_roots.staging_parent_fd, -1)

        required, adapter, roots, stage = self._lease(
            adapter=_LeaseAdapter(partial_cleanup=True)
        )
        self.assertEqual(required.cleanup().status, "cleanup-required")
        self.assertEqual(stage.fd, -1)
        required.close()
        required.close()
        self.assertEqual(required.state, "cleanup-required")
        self.assertEqual(adapter.close_stage_calls, 0)
        self.assertEqual(adapter.close_root_calls, [("staging_parent_fd", 102)])
        self.assertEqual(roots.staging_parent_fd, -1)

    def test_cleaned_close_is_safe_and_active_close_is_rejected(self) -> None:
        cleaned, cleaned_adapter, _, _ = self._lease()
        cleaned.cleanup()
        cleaned.close()
        cleaned.close()
        self.assertEqual(cleaned_adapter.cleanup_calls, 1)

        active, active_adapter, _, _ = self._lease()
        with self.assertRaises(RuntimeError):
            active.close()
        self.assertEqual(active_adapter.close_stage_calls, 0)
        self.assertEqual(active_adapter.close_root_calls, [])

    def test_lifecycle_operations_are_native_publication_free(self) -> None:
        lease, _, _, _ = self._lease()
        with patch("skill_orchestrator.transactional_fs.os.rename", side_effect=AssertionError), patch(
            "skill_orchestrator.transactional_fs.os.replace", side_effect=AssertionError
        ):
            lease.consume()
            lease.taint("post-rename-sync-failed")
            lease.close()

    def test_copy_and_pickle_rejections_remain(self) -> None:
        lease, _, _, _ = self._lease()
        with self.assertRaises(TypeError):
            copy.copy(lease)
        with self.assertRaises(TypeError):
            copy.deepcopy(lease)
        with self.assertRaises(TypeError):
            pickle.dumps(lease)


if __name__ == "__main__":
    unittest.main()

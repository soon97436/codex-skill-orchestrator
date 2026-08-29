"""Focused public-contract tests for Phase 5E target-bound staging."""

from __future__ import annotations

import hashlib
import copy
import pickle
import tempfile
import unittest
from pathlib import Path

from skill_orchestrator.transactional_fs import (
    DeclaredFile,
    ExecutionLimits,
    StageRequest,
    revalidate_owned_stage,
    stage_declared_candidate,
)
from skill_orchestrator.transactional_replace import TargetStageRequest, prepare_target_bound_stage


class _TargetAppearsOnFinalCheck:
    """Deterministic target-inspection seam; no timing or global monkeypatch."""

    def __init__(self) -> None:
        self._states = iter(("absent", "existing"))

    def target_state(self, root_fd: int, target_key: str) -> str:
        del root_fd, target_key
        return next(self._states)


class TargetBoundStageTests(unittest.TestCase):
    def _request(self, base: Path, *, target_key: str = "safe-skill") -> TargetStageRequest:
        source = base / "source"
        skills = base / "skills"
        source.mkdir(mode=0o700)
        skills.mkdir(mode=0o700)
        payload = b"safe declared bytes\n"
        (source / "SKILL.md").write_bytes(payload)
        return TargetStageRequest(
            skills_root=skills,
            target_key=target_key,
            source_root=source,
            declared_files=(DeclaredFile("SKILL.md", hashlib.sha256(payload).hexdigest()),),
            limits=ExecutionLimits(),
        )

    def test_absent_target_returns_prepared_owned_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            request = self._request(base)

            outcome = prepare_target_bound_stage(request)

            self.assertEqual(outcome.result.status, "prepared")
            self.assertEqual(outcome.result.target_state, "absent")
            self.assertIsNotNone(outcome.lease)
            self.assertFalse((request.skills_root / "safe-skill").exists())
            self.assertTrue((request.skills_root / ".cso-staging").is_dir())
            self.assertEqual(outcome.lease.cleanup().status, "cleaned")

    def test_lease_is_nonserializable_noncopiable_and_stage_result_is_not_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            request = self._request(Path(temporary).resolve())
            outcome = prepare_target_bound_stage(request)
            self.assertTrue(revalidate_owned_stage(outcome.lease))
            self.assertFalse(revalidate_owned_stage(outcome.result))
            self.assertNotIn(str(request.skills_root), repr(outcome.lease))
            with self.assertRaises(TypeError):
                pickle.dumps(outcome.lease)
            with self.assertRaises(TypeError):
                copy.copy(outcome.lease)
            with self.assertRaises(TypeError):
                copy.deepcopy(outcome.lease)
            self.assertEqual(outcome.lease.cleanup().status, "cleaned")
            self.assertFalse(revalidate_owned_stage(outcome.lease))
            self.assertEqual(outcome.lease.cleanup().status, "cleaned")

    def test_existing_targets_are_rejected_and_untouched(self) -> None:
        for kind in ("directory", "file"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                request = self._request(Path(temporary).resolve())
                target = request.skills_root / request.target_key
                if kind == "directory":
                    target.mkdir()
                else:
                    target.write_bytes(b"unowned")
                outcome = prepare_target_bound_stage(request)
                self.assertEqual(outcome.result.status, "rejected")
                self.assertEqual(outcome.result.target_state, "existing-unowned")
                self.assertIsNone(outcome.lease)
                self.assertTrue(target.exists())

    def test_target_appearing_at_final_recheck_rejects_and_cleans_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            request = self._request(Path(temporary).resolve())

            outcome = prepare_target_bound_stage(
                request, fs=_TargetAppearsOnFinalCheck()
            )

            self.assertEqual(outcome.result.status, "rejected")
            self.assertEqual(outcome.result.target_state, "existing-unowned")
            self.assertEqual(outcome.result.reason_ids, ("phase5e.replace.target.appeared",))
            self.assertIsNone(outcome.lease)
            self.assertFalse((request.skills_root / request.target_key).exists())
            self.assertEqual(list((request.skills_root / ".cso-staging").iterdir()), [])

    def test_unsafe_target_keys_are_invalid(self) -> None:
        keys = (
            ".", "..", "a/b", "a\\b", "a:b", "name.", "name ", "CON",
            "COM1", "COM9", "LPT1", "lpt9", "a..b",
        )
        for key in keys:
            with self.subTest(key=key), tempfile.TemporaryDirectory() as temporary:
                outcome = prepare_target_bound_stage(self._request(Path(temporary).resolve(), target_key=key))
                self.assertEqual(outcome.result.status, "invalid")
                self.assertIsNone(outcome.lease)

    def test_unsafe_skills_root_and_namespace_are_rejected_before_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            request = self._request(Path(temporary).resolve())
            request.skills_root.chmod(0o777)
            outcome = prepare_target_bound_stage(request)
            self.assertEqual(outcome.result.status, "rejected")
            self.assertEqual(outcome.result.reason_ids, ("phase5e.replace.skills-root.unsafe",))
            self.assertFalse((request.skills_root / ".cso-staging").exists())

        with tempfile.TemporaryDirectory() as temporary:
            request = self._request(Path(temporary).resolve())
            outside = request.skills_root.parent / "outside"
            outside.mkdir()
            (request.skills_root / ".cso-staging").symlink_to(outside, target_is_directory=True)
            outcome = prepare_target_bound_stage(request)
            self.assertEqual(outcome.result.status, "rejected")
            self.assertEqual(
                outcome.result.reason_ids,
                ("phase5e.replace.staging-namespace.unsafe",),
            )
            self.assertFalse((request.skills_root / request.target_key).exists())

    def test_stage_revalidation_and_cleanup_are_bound_to_the_owned_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            request = self._request(Path(temporary).resolve())
            outcome = prepare_target_bound_stage(request)
            namespace = request.skills_root / ".cso-staging"
            self.assertTrue(revalidate_owned_stage(outcome.lease))
            stage = next(namespace.iterdir())
            (stage / "SKILL.md").write_bytes(b"mutated")
            self.assertFalse(revalidate_owned_stage(outcome.lease))
            self.assertEqual(outcome.lease.cleanup().status, "cleaned")
            self.assertEqual(list(namespace.iterdir()), [])
            self.assertFalse((request.skills_root / request.target_key).exists())

    def test_legacy_stager_contract_remains_separate_from_owned_lease(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            request = self._request(base)
            legacy_parent = request.skills_root / ".legacy-stage"
            legacy_parent.mkdir(mode=0o700)
            legacy = stage_declared_candidate(
                StageRequest(request.source_root, legacy_parent, request.target_key, request.declared_files, request.limits)
            )
            self.assertEqual(legacy.status, "staged")
            self.assertIsNotNone(legacy.stage_id)
            self.assertFalse(revalidate_owned_stage(legacy))

    def test_existing_staging_namespace_is_reused_without_target_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            request = self._request(Path(temporary).resolve())
            namespace = request.skills_root / ".cso-staging"
            namespace.mkdir(mode=0o700)
            outcome = prepare_target_bound_stage(request)
            self.assertEqual(outcome.result.status, "prepared")
            self.assertTrue(namespace.is_dir())
            self.assertFalse((request.skills_root / request.target_key).exists())
            self.assertEqual(outcome.lease.cleanup().status, "cleaned")


if __name__ == "__main__":
    unittest.main()

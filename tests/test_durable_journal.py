"""Public-contract tests for the 4C2 durable journal and recovery foundation."""

from __future__ import annotations

import os
import json
import copy
import pickle
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skill_orchestrator.durable_journal import (
    JOURNAL_NAME,
    TEMPORARY_JOURNAL_NAME,
    _advance_durable_journal_while_holding_skills_lock,
    _create_durable_journal_while_holding_skills_lock,
    advance_durable_journal,
    create_durable_journal,
    load_durable_journal,
    scan_durable_journals,
)
from skill_orchestrator.errors import IntegrityError, OperationError, SecurityError, ValidationError
from skill_orchestrator.mutation_lock import MutationLockSet, _HeldSkillsLock, _MutationResource
import skill_orchestrator.durable_journal as durable_journal_module
import skill_orchestrator.mutation_lock as mutation_lock_module
from skill_orchestrator.transaction_journal import manifest_digest


def _digest(fill: str) -> str:
    return fill * 64


def _document(root: Path, **overrides):
    info = root.stat()
    document = {
        "schema_version": 1,
        "transaction_id": "0123456789abcdef0123456789abcdef",
        "operation": "candidate-install",
        "phase": "PREPARING",
        "target_key": "safe-skill",
        "skills_root_identity": {
            "kind": "posix-dev-ino",
            "device": info.st_dev,
            "inode": info.st_ino,
        },
        "plan_digest": _digest("a"),
        "source_identity_digest": _digest("b"),
        "provenance_trust_digest": _digest("c"),
        "capability_policy_digest": _digest("d"),
        "admission_digest": _digest("e"),
        "new_manifest": [],
        "new_manifest_digest": manifest_digest([]),
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


def _tree_snapshot(root: Path):
    records = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        info = path.lstat()
        if path.is_file():
            records.append((relative, "file", info.st_mode, path.read_bytes()))
        else:
            records.append((relative, "directory", info.st_mode, None))
    return records


def _try_skills_lock(skills_root: Path):
    script = "\n".join(
        (
            "from pathlib import Path",
            "from skill_orchestrator.errors import OperationError",
            "from skill_orchestrator.mutation_lock import MutationLockSet",
            f"root = Path({str(skills_root)!r})",
            "try:",
            "    with MutationLockSet.for_skills(root):",
            "        pass",
            "except OperationError as exc:",
            "    raise SystemExit(17 if str(exc) == 'another orchestrator mutation is in progress' else 18)",
        )
    )
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(Path(__file__).resolve().parents[1]), environment.get("PYTHONPATH")) if part
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


@unittest.skipUnless(os.name == "posix", "durable journal is POSIX-only")
class DurableJournalTests(unittest.TestCase):
    def _skills_root(self, base: Path) -> Path:
        root = base / "skills"
        root.parent.mkdir(parents=True, exist_ok=True)
        root.mkdir(mode=0o700)
        return root

    def _prepared(self, root: Path):
        return _document(
            root,
            phase="PREPARED",
            stage_binding={"relative_name": "stage-1", "manifest_digest": manifest_digest([])},
        )

    def test_create_advance_and_terminal_scan_do_not_touch_final_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._skills_root(Path(temporary))
            target = root / "safe-skill"
            target.mkdir()
            (target / "marker.txt").write_bytes(b"pre-existing target bytes")
            before_target = _tree_snapshot(target)

            created = create_durable_journal(root, _document(root))
            self.assertEqual(created["phase"], "PREPARING")
            incomplete = scan_durable_journals(root)
            self.assertEqual(incomplete.status, "recovery-required")
            self.assertEqual(incomplete.records[0].reason_ids, ("transaction.incomplete",))

            advance_durable_journal(root, _document(root, phase="ABORTED"))
            complete = scan_durable_journals(root)

            self.assertEqual(complete.status, "clean")
            self.assertEqual(complete.records[0].phase, "ABORTED")
            self.assertEqual(_tree_snapshot(target), before_target)
            self.assertFalse((root / ".cso-staging").exists())

    def test_private_create_uses_active_skills_lock_without_reacquiring(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._skills_root(Path(temporary))
            with MutationLockSet.for_skills(root) as locks:
                proof = locks._held_skills_lock()
                with patch.object(
                    durable_journal_module.MutationLockSet,
                    "for_skills",
                    side_effect=AssertionError("private path reacquired lock"),
                ):
                    created = _create_durable_journal_while_holding_skills_lock(
                        proof, _document(root)
                    )
                self.assertEqual(created["phase"], "PREPARING")
                self.assertEqual(
                    load_durable_journal(root, created["transaction_id"])["phase"],
                    "PREPARING",
                )
                contender = _try_skills_lock(root)
                self.assertEqual(contender.returncode, 17, contender.stderr)

    def test_private_advance_works_for_engine_lock_and_preserves_outer_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            install = base / "install"
            root = self._skills_root(base)
            install.mkdir()
            with MutationLockSet.for_engine(install, root) as locks:
                proof = locks._held_skills_lock()
                created = _create_durable_journal_while_holding_skills_lock(
                    proof, _document(root)
                )
                prepared = self._prepared(root)
                advanced = _advance_durable_journal_while_holding_skills_lock(
                    proof, prepared
                )
                self.assertEqual(advanced["phase"], "PREPARED")
                self.assertEqual(
                    load_durable_journal(root, advanced["transaction_id"])["phase"],
                    "PREPARED",
                )
                self.assertEqual(advanced["transaction_id"], created["transaction_id"])
                contender = _try_skills_lock(root)
                self.assertEqual(contender.returncode, 17, contender.stderr)

    def test_private_advance_works_for_skills_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._skills_root(Path(temporary))
            create_durable_journal(root, _document(root))
            with MutationLockSet.for_skills(root) as locks:
                advanced = _advance_durable_journal_while_holding_skills_lock(
                    locks._held_skills_lock(), self._prepared(root)
                )
                self.assertEqual(advanced["phase"], "PREPARED")


    def test_private_failure_does_not_release_outer_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._skills_root(Path(temporary))
            with MutationLockSet.for_skills(root) as locks:
                proof = locks._held_skills_lock()
                with patch.object(
                    durable_journal_module,
                    "_write_journal_atomically",
                    side_effect=OperationError("forced journal failure"),
                ):
                    with self.assertRaises(OperationError):
                        _create_durable_journal_while_holding_skills_lock(
                            proof, _document(root)
                        )
                contender = _try_skills_lock(root)
                self.assertEqual(contender.returncode, 17, contender.stderr)

    def test_private_proof_is_live_nonserializable_and_single_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._skills_root(Path(temporary))
            with MutationLockSet.for_skills(root) as locks:
                proof = locks._held_skills_lock()
                with self.assertRaises(TypeError):
                    copy.copy(proof)
                with self.assertRaises(TypeError):
                    copy.deepcopy(proof)
                with self.assertRaises(TypeError):
                    pickle.dumps(proof)
                created = _create_durable_journal_while_holding_skills_lock(
                    proof, _document(root)
                )
                self.assertEqual(created["phase"], "PREPARING")

            with self.assertRaises(SecurityError):
                _create_durable_journal_while_holding_skills_lock(
                    proof, _document(root, transaction_id="fedcba9876543210fedcba9876543210")
                )
            self.assertEqual(
                load_durable_journal(root, created["transaction_id"])["phase"],
                "PREPARING",
            )

    def test_private_path_rejects_forged_plain_data_and_install_only_proofs_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = self._skills_root(base)
            forged = object.__new__(_HeldSkillsLock)
            for proof in (forged, {"active": True}, _HeldSkillsLock(None, None, None)):
                with self.subTest(proof_type=type(proof).__name__):
                    with self.assertRaises(SecurityError):
                        _create_durable_journal_while_holding_skills_lock(
                            proof, _document(root)
                        )
                    self.assertFalse((root / ".cso-state").exists())

            install = base / "install"
            install.mkdir()
            resource = _MutationResource(install.resolve(), "state", skills_state=False)
            install_only = MutationLockSet((resource,))
            with install_only:
                with self.assertRaises(OperationError):
                    install_only._held_skills_lock()
            self.assertFalse((root / ".cso-state").exists())

    def test_private_path_rejects_wrong_root_identity_and_root_substitution_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root_a = self._skills_root(base / "a")
            root_b = self._skills_root(base / "b")
            with MutationLockSet.for_skills(root_a) as locks:
                proof = locks._held_skills_lock()
                with self.assertRaises(SecurityError):
                    _create_durable_journal_while_holding_skills_lock(
                        proof, _document(root_b)
                    )
                mismatched = _document(root_a)
                mismatched["skills_root_identity"]["inode"] += 1
                with self.assertRaises(SecurityError):
                    _create_durable_journal_while_holding_skills_lock(proof, mismatched)
                self.assertFalse((root_b / ".cso-state").exists())

    def test_private_path_rejects_descriptor_failure_before_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._skills_root(Path(temporary))
            with MutationLockSet.for_skills(root) as locks:
                proof = locks._held_skills_lock()
                owner, resource, token = proof._components()
                forged_copy = _HeldSkillsLock(owner, resource, token)
                with self.assertRaises(SecurityError):
                    _create_durable_journal_while_holding_skills_lock(
                        forged_copy, _document(root)
                    )
                before = _tree_snapshot(root)
                with patch.object(
                    mutation_lock_module.os,
                    "dup",
                    side_effect=OSError("forced descriptor failure"),
                ):
                    with self.assertRaises(SecurityError):
                        _create_durable_journal_while_holding_skills_lock(
                            proof, _document(root)
                        )
                self.assertEqual(_tree_snapshot(root), before)

    def test_private_path_preserves_validation_and_temp_collision_behavior(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._skills_root(Path(temporary))
            with MutationLockSet.for_skills(root) as locks:
                proof = locks._held_skills_lock()
                before = _tree_snapshot(root)
                with self.assertRaises(ValidationError):
                    _create_durable_journal_while_holding_skills_lock(
                        proof, _document(root, phase="PUBLISHED")
                    )
                self.assertEqual(_tree_snapshot(root), before)

                created = _create_durable_journal_while_holding_skills_lock(
                    proof, _document(root)
                )
                transaction = (
                    root
                    / ".cso-state"
                    / "transactions"
                    / created["transaction_id"]
                )
                temporary_journal = transaction / TEMPORARY_JOURNAL_NAME
                temporary_journal.write_bytes(b"leftover")
                temporary_journal.chmod(0o600)
                with self.assertRaises(IntegrityError):
                    _advance_durable_journal_while_holding_skills_lock(
                        proof, self._prepared(root)
                    )
                self.assertEqual(temporary_journal.read_bytes(), b"leftover")

    def test_private_path_preserves_malformed_journal_and_transition_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._skills_root(Path(temporary))
            with MutationLockSet.for_skills(root) as locks:
                proof = locks._held_skills_lock()
                created = _create_durable_journal_while_holding_skills_lock(
                    proof, _document(root)
                )
                transaction = (
                    root
                    / ".cso-state"
                    / "transactions"
                    / created["transaction_id"]
                )
                journal = transaction / JOURNAL_NAME
                original = journal.read_bytes()
                journal.write_bytes(b"not-json")
                journal.chmod(0o600)
                with self.assertRaises(IntegrityError):
                    _advance_durable_journal_while_holding_skills_lock(
                        proof, self._prepared(root)
                    )
                journal.write_bytes(original)
                journal.chmod(0o600)

                invalid = self._prepared(root)
                invalid["phase"] = "PUBLISHED"
                with self.assertRaises(ValidationError):
                    _advance_durable_journal_while_holding_skills_lock(proof, invalid)

    def test_public_advance_still_contends_under_an_independent_outer_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._skills_root(Path(temporary))
            create_durable_journal(root, _document(root))
            with MutationLockSet.for_skills(root):
                with self.assertRaises(OperationError):
                    advance_durable_journal(root, self._prepared(root))

    def test_scan_is_zero_write_even_for_corrupt_transaction(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._skills_root(Path(temporary))
            create_durable_journal(root, _document(root))
            transaction = root / ".cso-state" / "transactions" / "0123456789abcdef0123456789abcdef"
            (transaction / TEMPORARY_JOURNAL_NAME).write_bytes(b"interrupted")
            (transaction / TEMPORARY_JOURNAL_NAME).chmod(0o600)
            before = _tree_snapshot(root)

            result = scan_durable_journals(root)

            self.assertEqual(result.status, "recovery-required")
            self.assertEqual(result.records[0].reason_ids, ("transaction.unsafe",))
            self.assertEqual(_tree_snapshot(root), before)

    def test_scan_of_missing_state_is_zero_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._skills_root(Path(temporary))
            before = _tree_snapshot(root)

            result = scan_durable_journals(root)

            self.assertEqual(result.status, "clean")
            self.assertEqual(result.records, ())
            self.assertEqual(_tree_snapshot(root), before)
            self.assertFalse((root / ".cso-state").exists())
            self.assertFalse((root / ".cso-staging").exists())

    def test_unsafe_namespace_corrupt_json_and_duplicate_keys_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._skills_root(Path(temporary))
            create_durable_journal(root, _document(root))
            transaction = root / ".cso-state" / "transactions" / "0123456789abcdef0123456789abcdef"
            journal = transaction / JOURNAL_NAME
            journal.write_bytes(b'{"schema_version":1,"schema_version":1}')
            journal.chmod(0o600)
            before = _tree_snapshot(root)

            result = scan_durable_journals(root)

            self.assertEqual(result.status, "recovery-required")
            self.assertEqual(result.records[0].reason_ids, ("transaction.unsafe",))
            self.assertEqual(_tree_snapshot(root), before)

    def test_scan_classifies_unexpected_state_and_illegal_transaction_names(self) -> None:
        for kind in ("state-leaf", "transaction-name"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = self._skills_root(Path(temporary))
                create_durable_journal(root, _document(root))
                state = root / ".cso-state"
                if kind == "state-leaf":
                    unexpected = state / "unexpected"
                else:
                    unexpected = state / "transactions" / "not-a-transaction"
                unexpected.mkdir(mode=0o700) if kind == "transaction-name" else unexpected.write_bytes(b"unexpected")
                if kind == "state-leaf":
                    unexpected.chmod(0o600)
                before = _tree_snapshot(root)

                result = scan_durable_journals(root)

                self.assertEqual(result.status, "recovery-required")
                self.assertEqual(_tree_snapshot(root), before)

    def test_scan_classifies_invalid_utf8_and_root_identity_mismatch(self) -> None:
        for kind in ("utf8", "root-identity"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = self._skills_root(Path(temporary))
                create_durable_journal(root, _document(root))
                journal = (
                    root / ".cso-state" / "transactions" / "0123456789abcdef0123456789abcdef" / JOURNAL_NAME
                )
                if kind == "utf8":
                    journal.write_bytes(b"\xff")
                else:
                    altered = _document(root)
                    altered["skills_root_identity"]["inode"] += 1
                    journal.write_text(json.dumps(altered, sort_keys=True, separators=(",", ":")), encoding="utf-8")
                journal.chmod(0o600)
                before = _tree_snapshot(root)

                result = scan_durable_journals(root)

                self.assertEqual(result.status, "recovery-required")
                self.assertEqual(result.records[0].reason_ids, ("transaction.unsafe",))
                self.assertEqual(_tree_snapshot(root), before)

    def test_scan_classifies_symlink_leaf_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._skills_root(Path(temporary))
            create_durable_journal(root, _document(root))
            transaction = root / ".cso-state" / "transactions" / "0123456789abcdef0123456789abcdef"
            outside = root.parent / "outside"
            outside.mkdir(mode=0o700)
            (transaction / "unexpected-link").symlink_to(outside, target_is_directory=True)
            before = _tree_snapshot(root)

            result = scan_durable_journals(root)

            self.assertEqual(result.status, "recovery-required")
            self.assertEqual(result.records[0].reason_ids, ("transaction.unsafe",))
            self.assertEqual(_tree_snapshot(root), before)

    def test_root_identity_and_authorization_fields_are_never_persisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._skills_root(Path(temporary))
            mismatched = _document(root)
            mismatched["skills_root_identity"]["inode"] += 1
            with self.assertRaises(IntegrityError):
                create_durable_journal(root, mismatched)
            self.assertFalse((root / ".cso-state").exists())

            forbidden = _document(root, authorization="not-an-authority")
            with self.assertRaises(ValidationError):
                create_durable_journal(root, forbidden)
            self.assertFalse((root / ".cso-state").exists())

    def test_journal_cannot_supply_authorization_or_execution_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._skills_root(Path(temporary))
            for field in ("authorization", "execution_authority", "stage_lease"):
                with self.subTest(field=field):
                    with self.assertRaises(ValidationError):
                        create_durable_journal(root, _document(root, **{field: "forbidden"}))
            self.assertFalse((root / ".cso-state").exists())
            self.assertFalse((root / "safe-skill").exists())

    def test_update_requires_closed_transition_and_immutable_identity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._skills_root(Path(temporary))
            create_durable_journal(root, _document(root))

            with self.assertRaises(ValidationError):
                advance_durable_journal(root, _document(root, phase="PUBLISHED"))

            changed = self._prepared(root)
            changed["plan_digest"] = _digest("0")
            with self.assertRaises(IntegrityError):
                advance_durable_journal(root, changed)

            advance_durable_journal(root, self._prepared(root))

    def test_leftover_temporary_file_blocks_advance_without_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._skills_root(Path(temporary))
            create_durable_journal(root, _document(root))
            transaction = root / ".cso-state" / "transactions" / "0123456789abcdef0123456789abcdef"
            leftover = transaction / TEMPORARY_JOURNAL_NAME
            leftover.write_bytes(b"leftover")
            leftover.chmod(0o600)
            before = leftover.read_bytes()

            with self.assertRaises(IntegrityError):
                advance_durable_journal(root, self._prepared(root))

            self.assertEqual(leftover.read_bytes(), before)

    def test_lock_contention_prevents_journal_write(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._skills_root(Path(temporary))
            with MutationLockSet.for_skills(root):
                with self.assertRaises(OperationError):
                    create_durable_journal(root, _document(root))
            transactions = root / ".cso-state" / "transactions"
            self.assertFalse(transactions.exists())

    def test_journal_operations_do_not_use_network(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self._skills_root(Path(temporary))
            with patch.object(socket, "socket", side_effect=AssertionError("network used")):
                create_durable_journal(root, _document(root))
                scan_durable_journals(root)


class WindowsFailClosedContractTests(unittest.TestCase):
    def test_unsupported_platform_creates_no_state_or_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "skills"
            root.mkdir(mode=0o700)
            document = _document(root)
            with patch("skill_orchestrator.durable_journal._posix_supported", return_value=False):
                with self.assertRaises(SecurityError):
                    create_durable_journal(root, document)
                result = scan_durable_journals(root)
            self.assertEqual(result.status, "unsupported")
            self.assertFalse((root / ".cso-state").exists())
            self.assertFalse((root / ".cso-staging").exists())
            self.assertFalse((root / "safe-skill").exists())


if __name__ == "__main__":
    unittest.main()

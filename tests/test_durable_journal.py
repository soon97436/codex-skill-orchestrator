"""Public-contract tests for the 4C2 durable journal and recovery foundation."""

from __future__ import annotations

import os
import json
import socket
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skill_orchestrator.durable_journal import (
    JOURNAL_NAME,
    TEMPORARY_JOURNAL_NAME,
    advance_durable_journal,
    create_durable_journal,
    scan_durable_journals,
)
from skill_orchestrator.errors import IntegrityError, OperationError, SecurityError, ValidationError
from skill_orchestrator.mutation_lock import MutationLockSet
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


@unittest.skipUnless(os.name == "posix", "durable journal is POSIX-only")
class DurableJournalTests(unittest.TestCase):
    def _skills_root(self, base: Path) -> Path:
        root = base / "skills"
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

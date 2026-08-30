import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from skill_orchestrator.errors import SecurityError, ValidationError
from skill_orchestrator.transaction_journal import (
    CLEANUP_STATUSES,
    MAX_MANIFEST_FILES,
    PHASES,
    TERMINAL_PHASES,
    journal_digest,
    manifest_digest,
    normalize_exact_manifest,
    is_terminal_phase,
    validate_phase_transition,
    validate_transaction_id,
    validate_journal_document,
)


def _digest(fill="a"):
    return fill * 64


def _journal(**overrides):
    document = {
        "schema_version": 1,
        "transaction_id": "0123456789abcdef0123456789abcdef",
        "operation": "candidate-install",
        "phase": "PREPARING",
        "target_key": "example-skill",
        "skills_root_identity": {
            "kind": "posix-dev-ino",
            "device": 1,
            "inode": 2,
        },
        "plan_digest": _digest("a"),
        "source_identity_digest": _digest("b"),
        "provenance_trust_digest": _digest("c"),
        "capability_policy_digest": _digest("d"),
        "admission_digest": _digest("e"),
        "new_manifest": [],
        "new_manifest_digest": _digest("f"),
        "previous_target": None,
        "stage_binding": None,
        "quarantine_binding": None,
        "installed_state_before_digest": None,
        "installed_state_after_digest": None,
        "cleanup_status": "none",
        "reason_ids": [],
    }
    document.update(overrides)
    document["new_manifest_digest"] = manifest_digest(document["new_manifest"])
    return document


def _manifest(fill="a"):
    return [
        {"path": "b/second.txt", "sha256": fill * 64, "size": 2},
        {"path": "first.txt", "sha256": fill * 64, "size": 1},
    ]


def _managed_target():
    manifest = _manifest("9")
    return {
        "classification": "managed-current",
        "manifest": manifest,
        "manifest_digest": manifest_digest(manifest),
    }


def _string_values(value):
    if isinstance(value, dict):
        for key, nested in value.items():
            yield from _string_values(key)
            yield from _string_values(nested)
    elif isinstance(value, (list, tuple)):
        for nested in value:
            yield from _string_values(nested)
    elif isinstance(value, str):
        yield value


class TransactionJournalTests(unittest.TestCase):
    def test_valid_absent_preparing_document_is_normalized_and_digestible(self):
        document = _journal()
        normalized = validate_journal_document(document)

        self.assertEqual(normalized, document)
        self.assertIsNot(normalized, document)
        self.assertEqual(len(journal_digest(document)), hashlib.sha256().digest_size * 2)

    def test_transaction_id_is_exact_lowercase_hex_and_not_generated(self):
        self.assertEqual(
            validate_transaction_id("0123456789abcdef0123456789abcdef"),
            "0123456789abcdef0123456789abcdef",
        )
        for value in (
            "0123456789ABCDEF0123456789ABCDEF",
            "01234567-89ab-cdef-0123-456789abcdef",
            "20260830T000000Z-0123456789ab",
            " " * 32,
            "",
            "a" * 31,
            "a" * 33,
        ):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    validate_transaction_id(value)

    def test_manifest_is_exact_sorted_duplicate_safe_and_detached(self):
        entries = _manifest()
        normalized = normalize_exact_manifest(entries)
        self.assertEqual([item["path"] for item in normalized], ["b/second.txt", "first.txt"])
        self.assertEqual(list(normalized[0]), ["path", "sha256", "size"])
        entries[0]["size"] = 99
        entries.append({"path": "later.txt", "sha256": "a" * 64, "size": 3})
        self.assertEqual(normalized[0]["size"], 2)
        self.assertEqual(len(normalized), 2)

        invalid_entries = (
            [{"path": "a.txt", "sha256": "a" * 64, "size": 0}, {"path": "a.txt", "sha256": "b" * 64, "size": 1}],
            [{"path": "a.txt", "sha256": "A" * 64, "size": 0}],
            [{"path": "a.txt", "sha256": "a" * 63, "size": 0}],
            [{"path": "a.txt", "sha256": "a" * 64, "size": -1}],
            [{"path": "a.txt", "sha256": "a" * 64, "size": True}],
            [{"path": "a\\b.txt", "sha256": "a" * 64, "size": 0}],
            [{"path": "/a.txt", "sha256": "a" * 64, "size": 0}],
            [{"path": "a/../b.txt", "sha256": "a" * 64, "size": 0}],
            [{"path": "a//b.txt", "sha256": "a" * 64, "size": 0}],
            [{"path": "a:b.txt", "sha256": "a" * 64, "size": 0}],
        )
        for value in invalid_entries:
            with self.subTest(value=value):
                with self.assertRaises((ValidationError, SecurityError)):
                    normalize_exact_manifest(value)

    def test_manifest_digest_is_domain_separated_and_order_independent(self):
        entries = _manifest()
        reversed_entries = list(reversed(entries))
        self.assertEqual(manifest_digest(entries), manifest_digest(reversed_entries))
        normalized = normalize_exact_manifest(entries)
        expected = hashlib.sha256(
            b"cso-candidate-manifest-v1\0"
            + json.dumps(
                normalized,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(manifest_digest(entries), expected)
        self.assertNotEqual(manifest_digest(entries), hashlib.sha256(json.dumps(normalized).encode()).hexdigest())

    def test_manifest_limit_is_explicit(self):
        entries = [
            {"path": "file-%04d.txt" % index, "sha256": "a" * 64, "size": index}
            for index in range(MAX_MANIFEST_FILES + 1)
        ]
        with self.assertRaises(ValidationError):
            normalize_exact_manifest(entries)

    def test_valid_phase_documents_cover_prepared_quarantine_committed_and_recovery(self):
        prepared = _journal(
            phase="PREPARED",
            stage_binding={"relative_name": "stage-1", "manifest_digest": _digest("f")},
        )
        quarantine = _journal(
            phase="QUARANTINED",
            previous_target=_managed_target(),
            stage_binding={"relative_name": "stage-1", "manifest_digest": _digest("f")},
            quarantine_binding={"relative_name": "quarantine-1", "manifest_digest": _digest("9")},
        )
        committed = _journal(
            phase="COMMITTED",
            stage_binding={"relative_name": "stage-1", "manifest_digest": _digest("f")},
            installed_state_after_digest=_digest("8"),
        )
        recovery = _journal(
            phase="RECOVERY_REQUIRED",
            cleanup_status="recovery-required",
            reason_ids=["phase5e.transaction.rollback-failed"],
        )
        for document in (prepared, quarantine, committed, recovery):
            with self.subTest(phase=document["phase"]):
                self.assertEqual(validate_journal_document(document), document)

    def test_phase_vocabulary_and_explicit_transitions_are_closed(self):
        self.assertEqual(len(PHASES), 13)
        self.assertEqual(
            set(TERMINAL_PHASES),
            {"COMMITTED", "ROLLED_BACK", "ABORTED", "RECOVERY_REQUIRED"},
        )
        for phase in TERMINAL_PHASES:
            self.assertTrue(is_terminal_phase(phase))
            with self.assertRaises(ValidationError):
                validate_phase_transition(phase, "PREPARING")
        self.assertTrue(validate_phase_transition("PREPARING", "PREPARED"))
        self.assertTrue(validate_phase_transition("PREPARED", "PUBLISH_INTENT"))
        self.assertTrue(validate_phase_transition("ROLLING_BACK", "ROLLED_BACK"))
        with self.assertRaises(ValidationError):
            validate_phase_transition("PREPARED", "PUBLISHED")
        with self.assertRaises(ValidationError):
            validate_phase_transition("not-a-phase", "PREPARED")
        self.assertFalse(is_terminal_phase("preparing"))

    def test_unknown_keys_bad_digests_and_unsafe_bindings_are_rejected(self):
        unknown = _journal(authorization="never-persist")
        with self.assertRaises(ValidationError):
            validate_journal_document(unknown)

        mismatch = _journal()
        mismatch["new_manifest_digest"] = _digest("0")
        with self.assertRaises(ValidationError):
            validate_journal_document(mismatch)

        for field in ("plan_digest", "source_identity_digest", "provenance_trust_digest", "capability_policy_digest", "admission_digest"):
            malformed = _journal()
            malformed[field] = "not-a-digest"
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    validate_journal_document(malformed)

        for value in ("../stage", "/tmp/stage", "stage/name", "stage\\name", "CON", "stage."):
            malformed = _journal(stage_binding={"relative_name": value, "manifest_digest": _digest("f")})
            with self.subTest(value=value):
                with self.assertRaises(SecurityError):
                    validate_journal_document(malformed)

        actual_stage_name = ".example-skill.cso-stage-0123456789abcdef0123456789abcdef"
        normalized = validate_journal_document(
            _journal(
                phase="PREPARED",
                stage_binding={"relative_name": actual_stage_name, "manifest_digest": _digest("f")},
            )
        )
        self.assertEqual(normalized["stage_binding"]["relative_name"], actual_stage_name)

    def test_phase_field_invariants_are_fail_closed(self):
        cases = (
            _journal(phase="PREPARING", quarantine_binding={"relative_name": "q", "manifest_digest": _digest("a")}),
            _journal(phase="PREPARING", installed_state_after_digest=_digest("a")),
            _journal(phase="PREPARED"),
            _journal(phase="PREPARED", stage_binding={"relative_name": "stage", "manifest_digest": _digest("a")}, installed_state_after_digest=_digest("b")),
            _journal(phase="QUARANTINED", stage_binding={"relative_name": "stage", "manifest_digest": _digest("a")}),
            _journal(phase="PUBLISHED"),
            _journal(phase="VERIFIED", installed_state_after_digest=_digest("a")),
            _journal(phase="STATE_COMMITTING"),
            _journal(phase="COMMITTED", cleanup_status="cleanup-required", installed_state_after_digest=_digest("a")),
            _journal(phase="ROLLED_BACK", installed_state_after_digest=_digest("a")),
            _journal(phase="ABORTED", cleanup_status="recovery-required"),
            _journal(phase="RECOVERY_REQUIRED", cleanup_status="recovery-required", reason_ids=[]),
        )
        for document in cases:
            with self.subTest(phase=document["phase"]):
                with self.assertRaises(ValidationError):
                    validate_journal_document(document)

    def test_reason_ids_are_lowercase_unique_sorted_and_bounded(self):
        document = _journal(reason_ids=["z.reason", "a.reason"])
        normalized = validate_journal_document(document)
        self.assertEqual(normalized["reason_ids"], ["a.reason", "z.reason"])
        for value in (
            ["A.reason"],
            ["reason/path"],
            ["reason with spaces"],
            ["reason", "reason"],
            ["reason" for _ in range(33)],
        ):
            malformed = _journal(reason_ids=value)
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    validate_journal_document(malformed)

    def test_normalization_and_digest_are_deterministic_and_alias_safe(self):
        first = _journal(reason_ids=["z.reason", "a.reason"])
        second = copy.deepcopy(first)
        second["reason_ids"] = list(reversed(second["reason_ids"]))
        second["skills_root_identity"] = {
            "inode": 2,
            "device": 1,
            "kind": "posix-dev-ino",
        }
        self.assertEqual(validate_journal_document(first), validate_journal_document(second))
        self.assertEqual(journal_digest(first), journal_digest(second))
        normalized = validate_journal_document(first)
        first["reason_ids"].append("later.reason")
        first["skills_root_identity"]["inode"] = 99
        self.assertEqual(normalized["reason_ids"], ["a.reason", "z.reason"])
        self.assertEqual(normalized["skills_root_identity"]["inode"], 2)

    def test_pure_journal_calls_have_zero_filesystem_side_effects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
            document = _journal()
            validate_journal_document(document)
            journal_digest(document)
            after = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
            self.assertEqual(before, after)
            self.assertFalse((root / ".cso-state").exists())
            self.assertFalse((root / "transactions").exists())
            self.assertFalse((root / "installed-state.json").exists())
            self.assertFalse((root / "journal.json").exists())

    def test_normalized_journal_has_no_machine_or_absolute_path_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            normalized = validate_journal_document(_journal())
            forbidden = (temporary, str(Path.home()), str(Path(__file__).resolve().parents[1]))
            for value in _string_values(normalized):
                for path in forbidden:
                    self.assertNotIn(path, value)


if __name__ == "__main__":
    unittest.main()

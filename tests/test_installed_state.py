import hashlib
import copy
import json
import tempfile
import unittest
from pathlib import Path

from skill_orchestrator.errors import SecurityError, ValidationError
from skill_orchestrator.installed_state import (
    SOURCE_TYPES,
    installed_state_digest,
    validate_installed_state_document,
)
from skill_orchestrator.transaction_journal import manifest_digest


def _digest(fill="a"):
    return fill * 64


def _manifest():
    return [{"path": "SKILL.md", "sha256": "f" * 64, "size": 12}]


def _state(**overrides):
    manifest = _manifest()
    document = {
        "schema_version": 1,
        "skill_id": "example-skill",
        "skill_version": "1.2.3",
        "registry_entry_digest": _digest("a"),
        "source_type": "bundled",
        "source_identity_digest": _digest("b"),
        "target_key": "example-skill",
        "skills_root_identity": {
            "kind": "posix-dev-ino",
            "device": 1,
            "inode": 2,
        },
        "operation": "candidate-install",
        "transaction_id": "0123456789abcdef0123456789abcdef",
        "declared_manifest": manifest,
        "declared_manifest_digest": manifest_digest(manifest),
        "installed_manifest": manifest,
        "installed_manifest_digest": manifest_digest(manifest),
        "provenance_trust_digest": _digest("c"),
        "capability_policy_digest": _digest("d"),
        "admission_plan_digest": _digest("e"),
        "cso_version": "0.1.0",
    }
    document.update(overrides)
    return document


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


class InstalledStateTests(unittest.TestCase):
    def test_valid_minimal_v1_state_is_normalized_and_digestible(self):
        document = _state()
        normalized = validate_installed_state_document(document)

        self.assertEqual(normalized, document)
        self.assertIsNot(normalized, document)
        self.assertEqual(len(installed_state_digest(document)), hashlib.sha256().digest_size * 2)

    def test_source_type_vocabulary_is_closed(self):
        self.assertEqual(SOURCE_TYPES, ("bundled", "git"))
        for source_type in ("remote", "path", "", None):
            document = _state(source_type=source_type)
            with self.subTest(source_type=source_type):
                with self.assertRaises(ValidationError):
                    validate_installed_state_document(document)

    def test_skill_identity_and_version_are_validated(self):
        for field, values in (
            ("skill_id", ("Bad-ID", "../skill", "", "skill/name")),
            ("skill_version", ("1.2", "v1.2.3", "1.2.3.4", "")),
        ):
            for value in values:
                document = _state(**{field: value})
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValidationError):
                        validate_installed_state_document(document)

    def test_all_digest_fields_require_lowercase_sha256(self):
        fields = (
            "registry_entry_digest",
            "source_identity_digest",
            "provenance_trust_digest",
            "capability_policy_digest",
            "admission_plan_digest",
        )
        for field in fields:
            for value in ("a" * 63, "A" * 64, "not-a-digest", True):
                document = _state(**{field: value})
                with self.subTest(field=field, value=value):
                    with self.assertRaises(ValidationError):
                        validate_installed_state_document(document)

    def test_transaction_root_and_target_identity_are_fail_closed(self):
        for value in ("A" * 32, "a" * 31, "a" * 33, "not-a-transaction"):
            with self.subTest(transaction_id=value):
                with self.assertRaises(ValidationError):
                    validate_installed_state_document(_state(transaction_id=value))

        for value in ("../target", "/tmp/target", "target/name", "target\\name", "CON", "target."):
            with self.subTest(target_key=value):
                with self.assertRaises(SecurityError):
                    validate_installed_state_document(_state(target_key=value))

        for identity in (
            {"kind": "windows", "device": 1, "inode": 2},
            {"kind": "posix-dev-ino", "device": True, "inode": 2},
            {"kind": "posix-dev-ino", "device": -1, "inode": 2},
            {"kind": "posix-dev-ino", "device": 1, "inode": False},
            {"kind": "posix-dev-ino", "device": 1, "inode": 0},
            {"kind": "posix-dev-ino", "device": 1, "inode": 2, "path": "/tmp"},
        ):
            with self.subTest(identity=identity):
                with self.assertRaises(ValidationError):
                    validate_installed_state_document(_state(skills_root_identity=identity))

    def test_manifest_digests_and_exact_content_equality_are_required(self):
        altered = _manifest()
        altered[0]["size"] += 1
        document = _state(installed_manifest=altered)
        with self.assertRaises(ValidationError):
            validate_installed_state_document(document)

        document = _state(declared_manifest_digest=_digest("0"))
        with self.assertRaises(ValidationError):
            validate_installed_state_document(document)

        document = _state(installed_manifest_digest=_digest("0"))
        with self.assertRaises(ValidationError):
            validate_installed_state_document(document)

        document = _state(declared_manifest=[])
        document["declared_manifest_digest"] = manifest_digest([])
        with self.assertRaises(ValidationError):
            validate_installed_state_document(document)

        document = _state(installed_manifest=[])
        document["installed_manifest_digest"] = manifest_digest([])
        with self.assertRaises(ValidationError):
            validate_installed_state_document(document)

    def test_unknown_authority_and_raw_path_fields_are_rejected(self):
        forbidden = (
            "authorization",
            "authorized",
            "auth_token",
            "approval",
            "user",
            "username",
            "home",
            "hostname",
            "cwd",
            "source_path",
            "repository_path",
            "command",
            "environment",
            "exception",
            "traceback",
        )
        for field in forbidden:
            document = _state(**{field: "must-not-persist"})
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    validate_installed_state_document(document)

        document = _state(cso_version="/Users/example/project")
        with self.assertRaises(ValidationError):
            validate_installed_state_document(document)
        document = _state(declared_manifest=[{"path": "/tmp/SKILL.md", "sha256": "f" * 64, "size": 12}])
        with self.assertRaises(SecurityError):
            validate_installed_state_document(document)

    def test_digest_is_domain_separated_deterministic_and_alias_safe(self):
        first = _state()
        second = copy.deepcopy(first)
        second["skills_root_identity"] = {
            "inode": 2,
            "device": 1,
            "kind": "posix-dev-ino",
        }
        second["declared_manifest"] = list(reversed(second["declared_manifest"]))
        second["installed_manifest"] = list(reversed(second["installed_manifest"]))
        self.assertEqual(validate_installed_state_document(first), validate_installed_state_document(second))
        self.assertEqual(installed_state_digest(first), installed_state_digest(second))
        normalized = validate_installed_state_document(first)
        first["skills_root_identity"]["inode"] = 99
        first["declared_manifest"][0]["size"] = 999
        self.assertEqual(normalized["skills_root_identity"]["inode"], 2)
        self.assertEqual(normalized["declared_manifest"][0]["size"], 12)

        expected = hashlib.sha256(
            b"cso-installed-state-v1\0"
            + json.dumps(
                normalized,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        self.assertEqual(installed_state_digest(second), expected)

    def test_pure_installed_state_calls_have_zero_filesystem_side_effects(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
            document = _state()
            validate_installed_state_document(document)
            installed_state_digest(document)
            after = sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))
            self.assertEqual(before, after)
            self.assertFalse((root / ".cso-state").exists())
            self.assertFalse((root / "transactions").exists())
            self.assertFalse((root / "installed-state.json").exists())
            self.assertFalse((root / "journal.json").exists())

    def test_normalized_state_has_no_machine_or_absolute_path_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            normalized = validate_installed_state_document(_state())
            forbidden = (temporary, str(Path.home()), str(Path(__file__).resolve().parents[1]))
            for value in _string_values(normalized):
                for path in forbidden:
                    self.assertNotIn(path, value)


if __name__ == "__main__":
    unittest.main()

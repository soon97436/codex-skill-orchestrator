import copy
import json
import unittest
from pathlib import Path

from skill_orchestrator.errors import ValidationError
from skill_orchestrator.registry_trust_policy import (
    resolve_trust_policy,
    validate_trust_profile_document,
)
from skill_orchestrator.validation import validate_registry_trust_snapshot


ROOT = Path(__file__).resolve().parents[1]


OPERATIONAL_POLICY = {
    "schema_version": 1,
    "allowed_source_types": ["bundled"],
    "allowed_remote_hosts": [],
    "allowed_spdx_licenses": ["MIT", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC"],
    "require_checksums": True,
    "require_immutable_revision_for_remote": True,
    "deny_network_in_phase_1": True,
}


def profile_document(
    *,
    source_types=None,
    provenance_classes=None,
    licenses=None,
):
    return {
        "schema_version": 1,
        "default_profile": "first-party-bundled",
        "profiles": [
            {
                "id": "first-party-bundled",
                "source_policy": {
                    "allowed_source_types": ["bundled"] if source_types is None else source_types,
                },
                "provenance_policy": {
                    "allowed_classes": ["first-party"]
                    if provenance_classes is None
                    else provenance_classes,
                },
                "license_policy": {
                    "allowed_spdx_licenses": ["MIT"] if licenses is None else licenses,
                },
            }
        ],
    }


class RegistryTrustPolicyTests(unittest.TestCase):
    def test_valid_default_first_party_profile(self) -> None:
        document = json.loads(
            (ROOT / "security" / "trust_profiles.json").read_text(encoding="utf-8")
        )

        validated = validate_trust_profile_document(document, OPERATIONAL_POLICY)

        self.assertEqual(validated["default_profile"], "first-party-bundled")
        self.assertEqual(len(validated["profiles"]), 1)

    def test_strict_top_level_keys(self) -> None:
        document = profile_document()
        document["unexpected"] = True

        with self.assertRaises(ValidationError):
            validate_trust_profile_document(document, OPERATIONAL_POLICY)

    def test_strict_profile_keys(self) -> None:
        document = profile_document()
        document["profiles"][0]["unexpected"] = True

        with self.assertRaises(ValidationError):
            validate_trust_profile_document(document, OPERATIONAL_POLICY)

    def test_duplicate_profile_ids_are_rejected(self) -> None:
        document = profile_document()
        document["profiles"].append(copy.deepcopy(document["profiles"][0]))

        with self.assertRaises(ValidationError):
            validate_trust_profile_document(document, OPERATIONAL_POLICY)

    def test_missing_default_profile_is_rejected(self) -> None:
        document = profile_document()
        del document["default_profile"]

        with self.assertRaises(ValidationError):
            validate_trust_profile_document(document, OPERATIONAL_POLICY)

    def test_unknown_default_profile_is_rejected(self) -> None:
        document = profile_document()
        document["default_profile"] = "missing-profile"

        with self.assertRaises(ValidationError):
            validate_trust_profile_document(document, OPERATIONAL_POLICY)

    def test_invalid_provenance_class_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            validate_trust_profile_document(
                profile_document(provenance_classes=["community"]),
                OPERATIONAL_POLICY,
            )

    def test_duplicate_provenance_classes_are_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            validate_trust_profile_document(
                profile_document(provenance_classes=["first-party", "first-party"]),
                OPERATIONAL_POLICY,
            )

    def test_source_profile_cannot_exceed_operational_floor(self) -> None:
        with self.assertRaises(ValidationError):
            validate_trust_profile_document(
                profile_document(source_types=["bundled", "git"]),
                OPERATIONAL_POLICY,
            )

    def test_license_profile_cannot_exceed_operational_floor(self) -> None:
        with self.assertRaises(ValidationError):
            validate_trust_profile_document(
                profile_document(licenses=["MIT", "GPL-3.0-only"]),
                OPERATIONAL_POLICY,
            )

    def test_git_cannot_be_enabled_under_bundled_floor(self) -> None:
        with self.assertRaises(ValidationError):
            validate_trust_profile_document(
                profile_document(source_types=["git"]),
                OPERATIONAL_POLICY,
            )

    def test_profile_resolution_is_exact_and_deterministic(self) -> None:
        document = profile_document()
        first = resolve_trust_policy(document, OPERATIONAL_POLICY)
        second = resolve_trust_policy(copy.deepcopy(document), copy.deepcopy(OPERATIONAL_POLICY))

        self.assertEqual(first, second)
        self.assertEqual(first["profile_id"], "first-party-bundled")
        self.assertEqual(first["allowed_source_types"], ["bundled"])
        self.assertEqual(first["allowed_provenance_classes"], ["first-party"])

    def test_unknown_requested_profile_fails(self) -> None:
        with self.assertRaises(ValidationError):
            resolve_trust_policy(profile_document(), OPERATIONAL_POLICY, profile_id="missing")

    def test_resolved_policy_inherits_checksum_requirement(self) -> None:
        policy = resolve_trust_policy(profile_document(), OPERATIONAL_POLICY)

        self.assertTrue(policy["require_checksums"])

    def test_resolved_policy_inherits_immutable_revision_requirement(self) -> None:
        policy = resolve_trust_policy(profile_document(), OPERATIONAL_POLICY)

        self.assertTrue(policy["require_immutable_revision_for_remote"])

    def test_trust_snapshot_contains_validated_profiles(self) -> None:
        snapshot = validate_registry_trust_snapshot(ROOT)

        self.assertEqual(snapshot["schema_version"], 1)
        self.assertIn("registry", snapshot)
        self.assertIn("operational_policy", snapshot)
        self.assertIn("trust_profiles", snapshot)
        self.assertEqual(snapshot["trust_profiles"]["default_profile"], "first-party-bundled")


if __name__ == "__main__":
    unittest.main()

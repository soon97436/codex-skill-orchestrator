import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skill_orchestrator import registry_trust_adapter
from skill_orchestrator.errors import IntegrityError, SecurityError, ValidationError
from skill_orchestrator.registry_trust import TRUST_DIMENSIONS
from skill_orchestrator.validation import (
    validate_registry,
    validate_registry_snapshot,
    validate_registry_trust_snapshot,
)


ROOT = Path(__file__).resolve().parents[1]


def copy_registry_fixture(destination: Path) -> None:
    for directory in ("registry", "security", "router"):
        shutil.copytree(ROOT / directory, destination / directory)
    shutil.copy2(ROOT / "LICENSE", destination / "LICENSE")


class RegistryTrustAdapterTests(unittest.TestCase):
    def test_first_party_project_is_admissible(self) -> None:
        result = registry_trust_adapter.evaluate_project_registry_trust(ROOT)

        self.assertEqual(result["schema_version"], 1)
        self.assertFalse(result["truncated"])
        self.assertEqual(len(result["skills"]), 1)
        self.assertEqual(result["trust_profile_id"], "first-party-bundled")
        self.assertEqual(result["skills"][0]["skill_id"], "codex-skill-orchestrator")
        self.assertEqual(result["skills"][0]["status"], "admissible")

    def test_phase5a_dimensions_remain_in_fixed_order(self) -> None:
        result = registry_trust_adapter.evaluate_project_registry_trust(ROOT)

        self.assertEqual(
            [decision["dimension"] for decision in result["skills"][0]["decisions"]],
            list(TRUST_DIMENSIONS),
        )

    def test_repeated_calls_are_deterministic(self) -> None:
        self.assertEqual(
            registry_trust_adapter.evaluate_project_registry_trust(ROOT),
            registry_trust_adapter.evaluate_project_registry_trust(ROOT),
        )

    def test_output_contains_metadata_only(self) -> None:
        result = registry_trust_adapter.evaluate_project_registry_trust(ROOT)
        serialized = json.dumps(result, ensure_ascii=False, sort_keys=True).lower()

        for forbidden in (
            str(ROOT).lower(),
            "https://",
            "sha256",
            "timestamp",
            "hostname",
            "username",
            "environment",
            "secret",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_validate_registry_return_shape_remains_compatible(self) -> None:
        registry = validate_registry(ROOT)
        snapshot = validate_registry_snapshot(ROOT)

        self.assertEqual(registry, snapshot["registry"])
        self.assertEqual(set(registry), {"codex-skill-orchestrator"})
        self.assertNotIn("policy", registry)

    def test_snapshot_contains_validated_registry_and_policy(self) -> None:
        snapshot = validate_registry_snapshot(ROOT)

        self.assertEqual(snapshot["schema_version"], 1)
        self.assertIn("registry", snapshot)
        self.assertIn("policy", snapshot)
        self.assertEqual(snapshot["policy"]["schema_version"], 1)
        self.assertEqual(snapshot["policy"]["allowed_source_types"], ["bundled"])
        self.assertTrue(snapshot["policy"]["require_checksums"])
        self.assertTrue(snapshot["policy"]["require_immutable_revision_for_remote"])

    def test_trust_snapshot_contains_validated_profile(self) -> None:
        snapshot = validate_registry_trust_snapshot(ROOT)
        self.assertEqual(snapshot["trust_profiles"]["default_profile"], "first-party-bundled")

    def test_tampered_bundled_file_still_raises_integrity_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-registry-") as temporary:
            fixture = Path(temporary)
            copy_registry_fixture(fixture)
            skill = fixture / "router" / "codex-skill-orchestrator" / "SKILL.md"
            skill.write_text(skill.read_text(encoding="utf-8") + "\nmodified\n", encoding="utf-8")

            with self.assertRaises(IntegrityError):
                registry_trust_adapter.evaluate_project_registry_trust(fixture)

    def test_tampered_registry_hash_still_raises_integrity_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-registry-") as temporary:
            fixture = Path(temporary)
            copy_registry_fixture(fixture)
            path = fixture / "registry" / "skills.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            document["skills"][0]["files"][0]["sha256"] = "0" * 64
            path.write_text(json.dumps(document), encoding="utf-8")

            with self.assertRaises(IntegrityError):
                registry_trust_adapter.evaluate_project_registry_trust(fixture)

    def test_checksum_index_mismatch_still_raises_integrity_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-registry-") as temporary:
            fixture = Path(temporary)
            copy_registry_fixture(fixture)
            path = fixture / "security" / "checksums.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            document["bundles"]["codex-skill-orchestrator@0.1.0"]["SKILL.md"] = "0" * 64
            path.write_text(json.dumps(document), encoding="utf-8")

            with self.assertRaises(IntegrityError):
                registry_trust_adapter.evaluate_project_registry_trust(fixture)

    def test_network_source_fails_closed_with_existing_security_error(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-registry-") as temporary:
            fixture = Path(temporary)
            copy_registry_fixture(fixture)
            path = fixture / "registry" / "skills.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            document["skills"][0]["source"].update(
                {
                    "type": "git",
                    "repository": "https://example.invalid/project.git",
                    "revision": "0" * 40,
                }
            )
            document["skills"][0]["provenance"]["third_party"] = True
            path.write_text(json.dumps(document), encoding="utf-8")

            with self.assertRaises(SecurityError):
                registry_trust_adapter.evaluate_project_registry_trust(fixture)

    def test_malformed_registry_is_not_translated_to_unknown(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-registry-") as temporary:
            fixture = Path(temporary)
            copy_registry_fixture(fixture)
            path = fixture / "registry" / "skills.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            document["skills"][0]["id"] = ""
            path.write_text(json.dumps(document), encoding="utf-8")

            with self.assertRaises(ValidationError):
                registry_trust_adapter.evaluate_project_registry_trust(fixture)

    def test_adapter_does_not_mutate_snapshot_structures(self) -> None:
        snapshot = validate_registry_trust_snapshot(ROOT)
        before = copy.deepcopy(snapshot)

        with patch.object(
            registry_trust_adapter,
            "validate_registry_trust_snapshot",
            return_value=snapshot,
        ):
            registry_trust_adapter.evaluate_project_registry_trust(ROOT)

        self.assertEqual(snapshot, before)

    def test_capability_dimension_remains_not_applicable(self) -> None:
        result = registry_trust_adapter.evaluate_project_registry_trust(ROOT)
        decision = result["skills"][0]["decisions"][-1]

        self.assertEqual(decision["dimension"], "capability_policy")
        self.assertEqual(decision["status"], "not-applicable")
        self.assertIn(
            "trust.limit.capability-enforcement-not-implemented",
            result["skills"][0]["limitations"],
        )

    def test_adapter_does_not_coerce_unsupported_policy_values(self) -> None:
        snapshot = validate_registry_trust_snapshot(ROOT)
        snapshot["operational_policy"]["allowed_source_types"] = ["archive"]

        with patch.object(
            registry_trust_adapter,
            "validate_registry_trust_snapshot",
            return_value=snapshot,
        ):
            with self.assertRaises(ValidationError):
                registry_trust_adapter.evaluate_project_registry_trust(ROOT)

    def test_explicit_default_profile_selection_is_deterministic(self) -> None:
        first = registry_trust_adapter.evaluate_project_registry_trust(
            ROOT, profile_id="first-party-bundled"
        )
        second = registry_trust_adapter.evaluate_project_registry_trust(
            ROOT, profile_id="first-party-bundled"
        )
        self.assertEqual(first, second)

    def test_unknown_profile_fails_without_fallback(self) -> None:
        with self.assertRaises(ValidationError):
            registry_trust_adapter.evaluate_project_registry_trust(ROOT, profile_id="missing")

    def test_adapter_uses_snapshot_without_independent_file_reads(self) -> None:
        snapshot = validate_registry_trust_snapshot(ROOT)
        with patch.object(
            registry_trust_adapter,
            "validate_registry_trust_snapshot",
            return_value=snapshot,
        ):
            result = registry_trust_adapter.evaluate_project_registry_trust(Path("/does/not/exist"))
        self.assertEqual(result["trust_profile_id"], "first-party-bundled")

    def test_profile_disallows_validated_third_party_class(self) -> None:
        snapshot = copy.deepcopy(validate_registry_trust_snapshot(ROOT))
        entry = snapshot["registry"]["codex-skill-orchestrator"]
        entry["provenance"]["third_party"] = True
        with patch.object(
            registry_trust_adapter,
            "validate_registry_trust_snapshot",
            return_value=snapshot,
        ):
            result = registry_trust_adapter.evaluate_project_registry_trust(ROOT)
        provenance = result["skills"][0]["decisions"][3]
        self.assertEqual(provenance["status"], "fail")
        self.assertEqual(provenance["reason_ids"], ["trust.provenance.class-disallowed"])
        self.assertNotIn("trust.provenance.incomplete", result["skills"][0]["reasons"])


if __name__ == "__main__":
    unittest.main()

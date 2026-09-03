"""Public contracts for pure candidate-publication outcome metadata."""

from __future__ import annotations

import ast
import copy
import json
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path

import skill_orchestrator.publication_outcome as publication_outcome
from skill_orchestrator.publication_outcome import (
    DURABILITY_OUTCOMES,
    JOURNAL_DISPOSITIONS,
    LEASE_DISPOSITIONS,
    NAMESPACE_OUTCOMES,
    PublicationOutcome,
    REASON_IDS,
    RETRY_SAFETIES,
    normalize_publication_outcome,
)


def _outcome(**overrides):
    values = {
        "namespace_outcome": "not-attempted",
        "durability_outcome": "not-applicable",
        "retry_safety": "may-retry-after-revalidation",
        "lease_disposition": "live",
        "journal_disposition": "no-transition-required",
        "recovery_required": False,
        "reason_ids": ("publication.validation-failed",),
    }
    values.update(overrides)
    return PublicationOutcome(**values)


class PublicationOutcomeTests(unittest.TestCase):
    def test_value_is_immutable_and_equality_is_deterministic(self):
        first = _outcome(reason_ids=("publication.validation-failed",))
        second = _outcome(reason_ids=("publication.validation-failed",))
        self.assertEqual(first, second)
        with self.assertRaises(FrozenInstanceError):
            first.namespace_outcome = "published"

    def test_copy_and_deepcopy_preserve_immutable_value_data(self):
        outcome = _outcome()
        self.assertEqual(copy.copy(outcome), outcome)
        self.assertEqual(copy.deepcopy(outcome), outcome)

    def test_canonical_dict_is_deterministic_json_safe_and_detached(self):
        outcome = _outcome(
            reason_ids=("publication.destination-exists", "publication.validation-failed")
        )
        expected = {
            "namespace_outcome": "not-attempted",
            "durability_outcome": "not-applicable",
            "retry_safety": "may-retry-after-revalidation",
            "lease_disposition": "live",
            "journal_disposition": "no-transition-required",
            "recovery_required": False,
            "reason_ids": ["publication.validation-failed", "publication.destination-exists"],
        }
        result = outcome.to_dict()
        self.assertEqual(result, expected)
        self.assertEqual(json.loads(json.dumps(result, sort_keys=True)), result)
        result["reason_ids"].append("changed-by-caller")
        self.assertEqual(outcome.reason_ids, tuple(expected["reason_ids"]))

    def test_reason_ids_are_immutable_deduplicated_bounded_and_closed(self):
        outcome = _outcome(
            reason_ids=("publication.validation-failed", "publication.validation-failed")
        )
        self.assertEqual(outcome.reason_ids, ("publication.validation-failed",))
        self.assertIsInstance(outcome.reason_ids, tuple)
        with self.assertRaises(ValueError):
            _outcome(reason_ids=("/private/secret",))
        with self.assertRaises(ValueError):
            _outcome(reason_ids=("publication.validation-failed",) * (len(REASON_IDS) + 1))

    def test_closed_vocabularies_reject_unknown_values(self):
        for field in (
            "namespace_outcome",
            "durability_outcome",
            "retry_safety",
            "lease_disposition",
            "journal_disposition",
        ):
            with self.subTest(field=field), self.assertRaises(ValueError):
                _outcome(**{field: "unknown-value"})

    def test_validation_failure_profile(self):
        outcome = _outcome()
        self.assertEqual(outcome.journal_disposition, "no-transition-required")

    def test_destination_exists_profile(self):
        outcome = _outcome(
            namespace_outcome="definitely-not-published",
            journal_disposition="rollback-required",
            reason_ids=("publication.destination-exists",),
        )
        self.assertEqual(outcome.lease_disposition, "live")
        self.assertFalse(outcome.recovery_required)

    def test_source_identity_lost_profile(self):
        outcome = _outcome(
            retry_safety="must-not-retry",
            lease_disposition="tainted",
            journal_disposition="recovery-required",
            recovery_required=True,
            reason_ids=("publication.source-identity-mismatch",),
        )
        self.assertEqual(outcome.namespace_outcome, "not-attempted")

    def test_documented_no_mutation_profile(self):
        outcome = _outcome(
            namespace_outcome="definitely-not-published",
            journal_disposition="rollback-required",
            reason_ids=("publication.known-no-mutation-failure",),
        )
        self.assertEqual(outcome.retry_safety, "may-retry-after-revalidation")

    def test_published_durable_profile(self):
        outcome = _outcome(
            namespace_outcome="published",
            durability_outcome="confirmed",
            retry_safety="must-not-retry",
            lease_disposition="consumed",
            journal_disposition="mark-published",
            reason_ids=("publication.native-success",),
        )
        self.assertFalse(outcome.recovery_required)

    def test_published_uncertain_profile(self):
        outcome = _outcome(
            namespace_outcome="published",
            durability_outcome="uncertain",
            retry_safety="must-not-retry",
            lease_disposition="tainted",
            journal_disposition="recovery-required",
            recovery_required=True,
            reason_ids=("publication.parent-sync-failed",),
        )
        self.assertTrue(outcome.recovery_required)

    def test_indeterminate_native_outcome_profile(self):
        outcome = _outcome(
            namespace_outcome="indeterminate",
            durability_outcome="unknown",
            retry_safety="must-not-retry",
            lease_disposition="tainted",
            journal_disposition="recovery-required",
            recovery_required=True,
            reason_ids=("publication.native-outcome-indeterminate",),
        )
        self.assertEqual(outcome.durability_outcome, "unknown")

    def test_verifier_failure_profile(self):
        outcome = _outcome(
            namespace_outcome="published",
            durability_outcome="confirmed",
            retry_safety="must-not-retry",
            lease_disposition="consumed",
            journal_disposition="recovery-required",
            recovery_required=True,
            reason_ids=("publication.verification-mismatch",),
        )
        self.assertTrue(outcome.recovery_required)

    def test_verified_exact_profile(self):
        outcome = _outcome(
            namespace_outcome="published",
            durability_outcome="confirmed",
            retry_safety="must-not-retry",
            lease_disposition="consumed",
            journal_disposition="mark-verified",
            reason_ids=("publication.verification-exact",),
        )
        self.assertEqual(outcome.journal_disposition, "mark-verified")

    def test_impossible_combinations_are_rejected(self):
        cases = (
            {"namespace_outcome": "published"},
            {"namespace_outcome": "published", "durability_outcome": "confirmed", "retry_safety": "may-retry-after-revalidation", "lease_disposition": "consumed", "journal_disposition": "mark-published"},
            {"namespace_outcome": "indeterminate", "durability_outcome": "unknown", "retry_safety": "must-not-retry", "lease_disposition": "live", "journal_disposition": "recovery-required", "recovery_required": True},
            {"namespace_outcome": "indeterminate", "durability_outcome": "unknown", "retry_safety": "must-not-retry", "lease_disposition": "tainted", "journal_disposition": "recovery-required", "recovery_required": False},
            {"namespace_outcome": "published", "durability_outcome": "uncertain", "retry_safety": "must-not-retry", "lease_disposition": "tainted", "journal_disposition": "recovery-required", "recovery_required": False},
            {"namespace_outcome": "definitely-not-published", "lease_disposition": "consumed", "journal_disposition": "rollback-required"},
            {"durability_outcome": "confirmed"},
            {"journal_disposition": "mark-published"},
            {"journal_disposition": "mark-verified"},
            {"journal_disposition": "recovery-required"},
        )
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                _outcome(**values)

    def test_normalizer_returns_detached_canonical_mapping(self):
        result = normalize_publication_outcome(
            namespace_outcome="definitely-not-published",
            durability_outcome="not-applicable",
            retry_safety="may-retry-after-revalidation",
            lease_disposition="live",
            journal_disposition="rollback-required",
            recovery_required=False,
            reason_ids=("publication.destination-exists",),
        )
        self.assertEqual(result["reason_ids"], ["publication.destination-exists"])

    def test_metadata_construction_remains_portable_on_windows(self):
        self.assertEqual(_outcome().namespace_outcome, "not-attempted")

    def test_module_has_no_filesystem_native_or_integration_imports(self):
        source = Path(publication_outcome.__file__).read_text(encoding="utf-8")
        imports = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
        forbidden = (
            "os", "pathlib", "ctypes", "subprocess", "mutation_lock", "transactional_fs",
            "transactional_replace", "durable_journal", "transaction_journal",
            "durable_target_observation", "durable_target_verification", "installation_authorization",
            "execution_handoff", "engine", "cli",
        )
        for name in forbidden:
            with self.subTest(name=name):
                self.assertFalse(any(name in imported for imported in imports))

    def test_closed_vocabularies_cover_the_contract(self):
        self.assertEqual(NAMESPACE_OUTCOMES, ("not-attempted", "definitely-not-published", "published", "indeterminate"))
        self.assertEqual(DURABILITY_OUTCOMES, ("not-applicable", "confirmed", "uncertain", "unknown"))
        self.assertEqual(RETRY_SAFETIES, ("may-retry-after-revalidation", "must-not-retry"))
        self.assertEqual(LEASE_DISPOSITIONS, ("live", "consumed", "tainted"))
        self.assertEqual(JOURNAL_DISPOSITIONS[-2:], ("recovery-required", "mark-verified"))


class DocumentationContractTests(unittest.TestCase):
    def test_documentation_preserves_no_authority_and_cooperative_writer_boundary(self):
        document = " ".join(
            (Path(__file__).parents[1] / "docs" / "PHASE5E_PUBLICATION_OUTCOME_CONTRACT.md")
            .read_text(encoding="utf-8")
            .lower()
            .split()
        )
        for phrase in (
            "no filesystem authority",
            "no publication authority",
            "cooperating cso writers only",
            "does not solve hostile same-user source-leaf substitution",
            "target-absence linearization point",
            "no errno mapping",
        ):
            with self.subTest(phrase=phrase):
                self.assertIn(phrase, document)


if __name__ == "__main__":
    unittest.main()

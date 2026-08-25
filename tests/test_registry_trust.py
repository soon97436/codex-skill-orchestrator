import copy
import unittest

from skill_orchestrator.registry_trust import (
    ADMISSION_STATUSES,
    DECISION_STATUSES,
    TRUST_DIMENSIONS,
    TRUST_REASON_IDS,
    evaluate_registry_trust,
)


BASE_ENTRY = {
    "id": "example-skill",
    "source": {"type": "bundled", "revision": None},
    "license": {"spdx": "MIT", "redistribution": True},
}
BASE_POLICY = {
    "allowed_source_types": ["bundled"],
    "allowed_spdx_licenses": ["MIT"],
    "require_checksums": True,
    "require_immutable_revision_for_remote": True,
}
PROFILE_POLICY = {
    **BASE_POLICY,
    "allowed_provenance_classes": ["first-party"],
}
BASE_EVIDENCE = {
    "registry_valid": True,
    "source_revision_immutable": None,
    "provenance_complete": True,
    "integrity_verified": True,
}


def evaluate(entry=None, policy=None, evidence=None):
    return evaluate_registry_trust(
        copy.deepcopy(BASE_ENTRY if entry is None else entry),
        policy=copy.deepcopy(BASE_POLICY if policy is None else policy),
        evidence=copy.deepcopy(BASE_EVIDENCE if evidence is None else evidence),
    )


def evaluate_profile(entry=None, policy=None, evidence=None):
    entry_value = copy.deepcopy(BASE_ENTRY if entry is None else entry)
    policy_value = copy.deepcopy(PROFILE_POLICY if policy is None else policy)
    evidence_value = copy.deepcopy(BASE_EVIDENCE if evidence is None else evidence)
    return evaluate_registry_trust(
        entry_value,
        policy=policy_value,
        evidence=evidence_value,
    )


class RegistryTrustTests(unittest.TestCase):
    def test_fully_satisfied_bundled_metadata_is_admissible(self):
        result = evaluate()
        self.assertEqual(result["status"], "admissible")
        self.assertEqual(result["skill_id"], "example-skill")

    def test_disallowed_git_source_is_rejected(self):
        entry = copy.deepcopy(BASE_ENTRY)
        entry["source"] = {"type": "git", "revision": "a" * 40}
        result = evaluate(entry)
        self.assertEqual(result["decisions"][1]["status"], "fail")
        self.assertEqual(result["status"], "rejected")

    def test_registry_false_is_rejected(self):
        result = evaluate(evidence={**BASE_EVIDENCE, "registry_valid": False})
        self.assertEqual(result["decisions"][0]["status"], "fail")
        self.assertEqual(result["status"], "rejected")

    def test_registry_unknown_is_unknown_without_other_failure(self):
        result = evaluate(evidence={**BASE_EVIDENCE, "registry_valid": None})
        self.assertEqual(result["decisions"][0]["status"], "unknown")
        self.assertEqual(result["status"], "unknown")

    def test_incomplete_provenance_is_rejected(self):
        result = evaluate(evidence={**BASE_EVIDENCE, "provenance_complete": False})
        self.assertEqual(result["status"], "rejected")

    def test_unknown_provenance_is_unknown_without_other_failure(self):
        result = evaluate(evidence={**BASE_EVIDENCE, "provenance_complete": None})
        self.assertEqual(result["status"], "unknown")

    def test_disallowed_license_is_rejected(self):
        entry = copy.deepcopy(BASE_ENTRY)
        entry["license"]["spdx"] = "GPL-3.0-only"
        self.assertEqual(evaluate(entry)["status"], "rejected")

    def test_redistribution_false_is_rejected(self):
        entry = copy.deepcopy(BASE_ENTRY)
        entry["license"]["redistribution"] = False
        self.assertEqual(evaluate(entry)["status"], "rejected")

    def test_required_integrity_verified_is_pass(self):
        result = evaluate()
        self.assertEqual(result["decisions"][5]["status"], "pass")

    def test_required_integrity_failed_is_rejected(self):
        result = evaluate(evidence={**BASE_EVIDENCE, "integrity_verified": False})
        self.assertEqual(result["status"], "rejected")

    def test_required_integrity_missing_is_unknown(self):
        result = evaluate(evidence={**BASE_EVIDENCE, "integrity_verified": None})
        self.assertEqual(result["decisions"][5]["status"], "unknown")
        self.assertEqual(result["status"], "unknown")

    def test_checksums_not_required_is_not_applicable(self):
        policy = {**BASE_POLICY, "require_checksums": False}
        result = evaluate(policy=policy)
        self.assertEqual(result["decisions"][5]["status"], "not-applicable")
        self.assertEqual(result["status"], "admissible")

    def test_bundled_source_identity_is_not_applicable(self):
        result = evaluate()
        self.assertEqual(result["decisions"][2]["status"], "not-applicable")

    def test_allowed_git_source_with_immutable_evidence_passes(self):
        entry = copy.deepcopy(BASE_ENTRY)
        entry["source"] = {"type": "git", "revision": "a" * 40}
        policy = {**BASE_POLICY, "allowed_source_types": ["bundled", "git"]}
        evidence = {**BASE_EVIDENCE, "source_revision_immutable": True}
        result = evaluate(entry, policy, evidence)
        self.assertEqual(result["decisions"][1]["status"], "pass")
        self.assertEqual(result["decisions"][2]["status"], "pass")
        self.assertEqual(result["status"], "admissible")

    def test_allowed_git_source_with_non_immutable_evidence_is_rejected(self):
        entry = copy.deepcopy(BASE_ENTRY)
        entry["source"] = {"type": "git", "revision": "a" * 40}
        policy = {**BASE_POLICY, "allowed_source_types": ["git"]}
        evidence = {**BASE_EVIDENCE, "source_revision_immutable": False}
        self.assertEqual(evaluate(entry, policy, evidence)["status"], "rejected")

    def test_allowed_git_source_with_missing_identity_is_unknown(self):
        entry = copy.deepcopy(BASE_ENTRY)
        entry["source"] = {"type": "git", "revision": "a" * 40}
        policy = {**BASE_POLICY, "allowed_source_types": ["git"]}
        evidence = {**BASE_EVIDENCE, "source_revision_immutable": None}
        result = evaluate(entry, policy, evidence)
        self.assertEqual(result["decisions"][2]["status"], "unknown")
        self.assertEqual(result["status"], "unknown")

    def test_failure_outranks_unknown(self):
        evidence = {
            **BASE_EVIDENCE,
            "registry_valid": False,
            "provenance_complete": None,
        }
        result = evaluate(evidence=evidence)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reasons"][-1], "trust.admission.rejected")

    def test_capability_policy_is_explicitly_not_applicable(self):
        result = evaluate()
        decision = result["decisions"][-1]
        self.assertEqual(decision["dimension"], "capability_policy")
        self.assertEqual(decision["status"], "not-applicable")
        self.assertIn("trust.limit.capability-enforcement-not-implemented", result["limitations"])

    def test_decision_order_is_fixed(self):
        result = evaluate()
        self.assertEqual(
            [decision["dimension"] for decision in result["decisions"]],
            list(TRUST_DIMENSIONS),
        )

    def test_flattened_reason_order_follows_decisions(self):
        result = evaluate()
        expected = [
            reason_id
            for decision in result["decisions"]
            for reason_id in decision["reason_ids"]
        ] + ["trust.admission.admissible"]
        self.assertEqual(result["reasons"], expected)

    def test_limitations_order_is_fixed(self):
        bundled = evaluate()
        self.assertEqual(
            bundled["limitations"],
            ["trust.limit.capability-enforcement-not-implemented"],
        )
        git_entry = copy.deepcopy(BASE_ENTRY)
        git_entry["source"] = {"type": "git", "revision": "a" * 40}
        git_policy = {**BASE_POLICY, "allowed_source_types": ["git"]}
        git_result = evaluate(git_entry, git_policy, {**BASE_EVIDENCE, "source_revision_immutable": True})
        self.assertEqual(
            git_result["limitations"],
            [
                "trust.limit.remote-fetch-disabled",
                "trust.limit.capability-enforcement-not-implemented",
            ],
        )

    def test_repeated_identical_calls_are_equal(self):
        self.assertEqual(evaluate(), evaluate())

    def test_input_objects_are_not_mutated(self):
        entry = copy.deepcopy(BASE_ENTRY)
        policy = copy.deepcopy(BASE_POLICY)
        evidence = copy.deepcopy(BASE_EVIDENCE)
        before = (copy.deepcopy(entry), copy.deepcopy(policy), copy.deepcopy(evidence))
        evaluate_registry_trust(entry, policy=policy, evidence=evidence)
        self.assertEqual((entry, policy, evidence), before)

    def test_output_has_no_machine_or_content_metadata(self):
        result = evaluate()
        serialized = repr(result).lower()
        for forbidden in ("timestamp", "hostname", "username", "/users/", "environment", "path"):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn("example-skill", result["reasons"])

    def test_wrong_top_level_type_raises_type_error(self):
        with self.assertRaises(TypeError):
            evaluate_registry_trust([], policy=BASE_POLICY, evidence=BASE_EVIDENCE)

    def test_missing_normalized_entry_field_raises_value_error(self):
        entry = copy.deepcopy(BASE_ENTRY)
        del entry["license"]
        with self.assertRaises(ValueError):
            evaluate(entry)

    def test_non_boolean_evidence_raises_value_error(self):
        evidence = {**BASE_EVIDENCE, "registry_valid": "true"}
        with self.assertRaises(ValueError):
            evaluate(evidence=evidence)

    def test_unsupported_source_type_raises_value_error(self):
        entry = copy.deepcopy(BASE_ENTRY)
        entry["source"] = {"type": "archive", "revision": None}
        with self.assertRaises(ValueError):
            evaluate(entry)

    def test_unsupported_policy_source_type_raises_value_error(self):
        policy = {**BASE_POLICY, "allowed_source_types": ["archive"]}
        with self.assertRaises(ValueError):
            evaluate(policy=policy)

    def test_reason_ids_are_closed_and_statuses_are_bounded(self):
        result = evaluate()
        self.assertTrue(set(result["reasons"]).issubset(set(TRUST_REASON_IDS)))
        self.assertIn(result["status"], ADMISSION_STATUSES)
        self.assertTrue(
            all(decision["status"] in DECISION_STATUSES for decision in result["decisions"])
        )

    def test_legacy_result_has_no_profile_provenance_effect(self):
        result = evaluate()
        self.assertEqual(result["decisions"][3]["reason_ids"], ["trust.provenance.complete"])
        self.assertNotIn("trust.provenance.class-disallowed", result["reasons"])

    def test_profile_aware_first_party_complete_provenance_passes(self):
        entry = {**BASE_ENTRY, "provenance": {"class": "first-party"}}
        result = evaluate_profile(entry)
        self.assertEqual(result["decisions"][3]["status"], "pass")
        self.assertEqual(result["decisions"][3]["reason_ids"], ["trust.provenance.complete"])
        self.assertEqual(result["status"], "admissible")

    def test_profile_aware_third_party_disallowed_is_not_incomplete(self):
        entry = {**BASE_ENTRY, "provenance": {"class": "third-party"}}
        result = evaluate_profile(entry)
        provenance = result["decisions"][3]
        self.assertEqual(provenance["status"], "fail")
        self.assertEqual(provenance["reason_ids"], ["trust.provenance.class-disallowed"])
        self.assertNotIn("trust.provenance.incomplete", result["reasons"])
        self.assertEqual(result["status"], "rejected")

    def test_profile_aware_allowed_third_party_passes(self):
        entry = {**BASE_ENTRY, "provenance": {"class": "third-party"}}
        policy = {**PROFILE_POLICY, "allowed_provenance_classes": ["first-party", "third-party"]}
        result = evaluate_profile(entry, policy=policy)
        self.assertEqual(result["decisions"][3]["status"], "pass")

    def test_profile_aware_incomplete_provenance_keeps_existing_reason(self):
        entry = {**BASE_ENTRY, "provenance": {"class": "third-party"}}
        result = evaluate_profile(
            entry=entry,
            evidence={**BASE_EVIDENCE, "provenance_complete": False},
        )
        self.assertEqual(
            result["decisions"][3]["reason_ids"],
            ["trust.provenance.incomplete"],
        )

    def test_profile_aware_unknown_provenance_keeps_existing_reason(self):
        entry = {**BASE_ENTRY, "provenance": {"class": "third-party"}}
        result = evaluate_profile(
            entry=entry,
            evidence={**BASE_EVIDENCE, "provenance_complete": None},
        )
        self.assertEqual(
            result["decisions"][3]["reason_ids"],
            ["trust.provenance.unknown"],
        )

    def test_profile_fields_must_be_present_on_both_sides(self):
        entry = {**BASE_ENTRY, "provenance": {"class": "first-party"}}
        with self.assertRaises(ValueError):
            evaluate(entry=entry)
        with self.assertRaises(ValueError):
            evaluate_profile(entry=BASE_ENTRY, policy=PROFILE_POLICY)

    def test_profile_provenance_class_is_closed(self):
        entry = {**BASE_ENTRY, "provenance": {"class": "community"}}
        with self.assertRaises(ValueError):
            evaluate_profile(entry=entry)


if __name__ == "__main__":
    unittest.main()

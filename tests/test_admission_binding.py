import copy
import hashlib
import json
import re
import unittest

from skill_orchestrator.admission_binding import (
    BINDING_STATUSES,
    LIMITATION_IDS,
    TARGET_CLASSES,
    VERIFICATION_STATUSES,
    create_admission_binding,
    verify_admission_binding,
)
from skill_orchestrator.admission_pipeline import evaluate_admission_pipeline
from skill_orchestrator.capability_policy import evaluate_capability_policy
from skill_orchestrator.installation_authorization import (
    evaluate_installation_authorization,
)
from skill_orchestrator.recommendation_admission import (
    evaluate_recommendation_admission,
)
from skill_orchestrator.registry_trust import evaluate_registry_trust


CAPABILITIES = {
    "schema_version": 1,
    "filesystem": {"read": ["project"], "write": []},
    "network": {"mode": "none"},
    "process": {"mode": "none", "commands": []},
}

REGISTRY_ENTRY = {
    "id": "synthetic-skill",
    "name": "Synthetic Skill",
    "description": "A synthetic validated registry candidate.",
    "version": "1.2.3",
    "source": {
        "type": "bundled",
        "path": "router/synthetic-skill",
        "repository": None,
        "revision": None,
    },
    "license": {
        "spdx": "MIT",
        "license_file": "LICENSE",
        "source_url": None,
        "redistribution": True,
    },
    "provenance": {
        "publisher": "Synthetic Publisher",
        "maintainer": "Synthetic Maintainer",
        "third_party": False,
    },
    "files": [
        {
            "path": "SKILL.md",
            "sha256": "a" * 64,
        }
    ],
    "capabilities": copy.deepcopy(CAPABILITIES),
}

TRUST_ENTRY = {
    "id": "synthetic-skill",
    "source": {"type": "bundled", "revision": None},
    "license": {"spdx": "MIT", "redistribution": True},
    "provenance": {"class": "first-party"},
}

TRUST_POLICY = {
    "profile_id": "first-party-bundled",
    "allowed_source_types": ["bundled"],
    "allowed_spdx_licenses": ["MIT"],
    "allowed_provenance_classes": ["first-party"],
    "require_checksums": True,
    "require_immutable_revision_for_remote": True,
}

TRUST_EVIDENCE = {
    "registry_valid": True,
    "source_revision_immutable": None,
    "provenance_complete": True,
    "integrity_verified": True,
}

CAPABILITY_POLICY = {
    "schema_version": 1,
    "profile_id": "local-safe",
    "operational_floor": copy.deepcopy(CAPABILITIES),
    "capabilities": copy.deepcopy(CAPABILITIES),
}

CAPABILITY_DECLARATION = copy.deepcopy(CAPABILITIES)
REQUESTED_CAPABILITIES = copy.deepcopy(CAPABILITIES)


def _upstream_decisions(*, trust_status=None, capability_status=None, operation="install"):
    trust = evaluate_registry_trust(
        TRUST_ENTRY,
        policy=TRUST_POLICY,
        evidence=TRUST_EVIDENCE,
    )
    if trust_status is not None and trust_status != trust["status"]:
        if trust_status == "rejected":
            evidence = dict(TRUST_EVIDENCE, registry_valid=False)
        elif trust_status == "unknown":
            evidence = dict(TRUST_EVIDENCE, provenance_complete=None)
        else:
            raise ValueError("unsupported fixture trust status")
        trust = evaluate_registry_trust(
            TRUST_ENTRY,
            policy=TRUST_POLICY,
            evidence=evidence,
        )

    capability = evaluate_capability_policy(
        CAPABILITY_DECLARATION,
        policy=CAPABILITY_POLICY,
        requested_capabilities=REQUESTED_CAPABILITIES,
        trust_status=trust["status"],
    )
    if capability_status is not None and capability_status != capability["status"]:
        if capability_status == "not-requested":
            request = {
                "schema_version": 1,
                "filesystem": {"read": [], "write": []},
                "network": {"mode": "none"},
                "process": {"mode": "none", "commands": []},
            }
        elif capability_status == "unknown":
            request = copy.deepcopy(REQUESTED_CAPABILITIES)
            declaration = None
        elif capability_status == "rejected":
            request = copy.deepcopy(REQUESTED_CAPABILITIES)
            request["filesystem"]["read"] = ["unrestricted"]
            declaration = CAPABILITY_DECLARATION
        else:
            raise ValueError("unsupported fixture capability status")
        capability = evaluate_capability_policy(
            None if capability_status == "unknown" else CAPABILITY_DECLARATION,
            policy=CAPABILITY_POLICY,
            requested_capabilities=request,
            trust_status=trust["status"],
        )

    recommendation = evaluate_recommendation_admission(
        registry_membership=True,
        trust_status=trust["status"],
        capability_decision=capability,
    )
    installation = evaluate_installation_authorization(
        operation=operation,
        operator_authorization="granted",
        recommendation_decision=recommendation,
    )
    return trust, capability, recommendation, installation


def _case(**overrides):
    trust, capability, recommendation, installation = _upstream_decisions(
        trust_status=overrides.pop("trust_status", None),
        capability_status=overrides.pop("capability_status", None),
        operation=overrides.pop("operation", "install"),
    )
    values = {
        "registry_schema_version": 1,
        "registry_entry": copy.deepcopy(REGISTRY_ENTRY),
        "trust_profile_schema_version": 1,
        "trust_policy": copy.deepcopy(TRUST_POLICY),
        "trust_evidence": copy.deepcopy(TRUST_EVIDENCE),
        "capability_policy": copy.deepcopy(CAPABILITY_POLICY),
        "capability_declaration": copy.deepcopy(CAPABILITY_DECLARATION),
        "requested_capabilities": copy.deepcopy(REQUESTED_CAPABILITIES),
        "trust_decision": trust,
        "capability_decision": capability,
        "recommendation_decision": recommendation,
        "installation_decision": installation,
        "target_class": "registry-skill-user-scope",
    }
    values.update(overrides)
    return values


def _create(values=None):
    return create_admission_binding(**(values or _case()))


class AdmissionBindingTests(unittest.TestCase):
    def test_public_status_vocabulary_is_closed(self):
        self.assertEqual(BINDING_STATUSES, ("bound", "rejected", "unknown", "invalid"))
        self.assertEqual(VERIFICATION_STATUSES, ("current", "stale", "invalid"))
        self.assertEqual(TARGET_CLASSES, ("registry-skill-user-scope",))

    def test_positive_bundled_binding_is_bound_and_metadata_only(self):
        result = _create()
        self.assertEqual(result["status"], "bound")
        self.assertEqual(result["assessment_scope"], "phase5e-evidence-binding")
        self.assertEqual(result["execution_status"], "not-performed")
        self.assertEqual(result["target_class"], "registry-skill-user-scope")
        self.assertEqual(result["operation"], "install")
        self.assertEqual(result["subject"]["kind"], "registry-candidate")
        self.assertEqual(result["subject"]["skill_id"], "synthetic-skill")
        self.assertEqual(result["subject"]["source_type"], "bundled")
        self.assertIsNone(result["subject"]["source_revision"])
        self.assertRegex(result["evidence_digest"], r"^[0-9a-f]{64}$")
        self.assertRegex(result["binding_digest"], r"^[0-9a-f]{64}$")
        serialized = json.dumps(result, sort_keys=True)
        for secret in ("router/synthetic-skill", "SKILL.md", "a" * 64, "Synthetic Publisher"):
            self.assertNotIn(secret, serialized)

    def test_positive_synthetic_git_binding_is_bound_without_network(self):
        values = _case()
        values["registry_entry"]["source"] = {
            "type": "git",
            "path": "router/synthetic-skill",
            "repository": "https://example.invalid/synthetic-skill.git",
            "revision": "b" * 40,
        }
        values["registry_entry"]["provenance"]["third_party"] = True
        values["trust_policy"] = dict(
            values["trust_policy"],
            allowed_source_types=["git"],
            allowed_provenance_classes=["third-party"],
        )
        trust_entry = dict(TRUST_ENTRY)
        trust_entry["source"] = {"type": "git", "revision": "b" * 40}
        trust_entry["provenance"] = {"class": "third-party"}
        values["trust_decision"] = evaluate_registry_trust(
            trust_entry,
            policy=values["trust_policy"],
            evidence=dict(TRUST_EVIDENCE, source_revision_immutable=True),
        )
        values["trust_evidence"] = dict(TRUST_EVIDENCE, source_revision_immutable=True)
        values["recommendation_decision"] = evaluate_recommendation_admission(
            registry_membership=True,
            trust_status="admissible",
            capability_decision=values["capability_decision"],
        )
        values["installation_decision"] = evaluate_installation_authorization(
            operation="install",
            operator_authorization="granted",
            recommendation_decision=values["recommendation_decision"],
        )
        self.assertEqual(_create(values)["status"], "bound")

    def test_pipeline_statuses_map_without_executable_binding(self):
        cases = (
            ("rejected", "rejected"),
            ("unknown", "unknown"),
        )
        for trust_status, expected in cases:
            with self.subTest(trust_status=trust_status):
                self.assertEqual(_create(_case(trust_status=trust_status))["status"], expected)

        invalid = _case()
        invalid["target_class"] = "cso-app"
        self.assertEqual(_create(invalid)["status"], "invalid")

    def test_capability_not_requested_is_bound_with_limitation(self):
        result = _create(_case(capability_status="not-requested"))
        self.assertEqual(result["status"], "bound")
        self.assertIn("phase5e.binding.limit.runtime-capability-not-requested", result["limitations"])

    def test_subject_and_profile_cross_checks_fail_closed(self):
        values = _case()
        values["registry_entry"]["id"] = "other-skill"
        result = _create(values)
        self.assertEqual(result["status"], "invalid")
        self.assertIn("phase5e.binding.subject-mismatch", result["reason_ids"])

        values = _case()
        values["capability_policy"]["profile_id"] = "other-profile"
        result = _create(values)
        self.assertEqual(result["status"], "invalid")
        self.assertIn("phase5e.binding.policy-mismatch", result["reason_ids"])

    def test_registry_entry_contract_is_strict(self):
        mutations = (
            ("name", 7),
            ("version", "not-semver"),
            ("source", {"type": "bundled"}),
            ("license", {"spdx": "MIT"}),
            ("provenance", {"publisher": "x"}),
            ("files", [{"path": "SKILL.md", "sha256": "bad"}]),
            ("capabilities", {"schema_version": 1}),
        )
        for key, value in mutations:
            with self.subTest(key=key):
                values = _case()
                values["registry_entry"][key] = value
                self.assertEqual(_create(values)["status"], "invalid")

        values = _case()
        values["registry_entry"]["files"].append(copy.deepcopy(values["registry_entry"]["files"][0]))
        self.assertEqual(_create(values)["status"], "invalid")

    def test_policy_evidence_declaration_request_and_target_are_strict(self):
        mutations = (
            ("trust_policy", {"profile_id": "x"}),
            ("trust_evidence", {"registry_valid": "yes"}),
            ("capability_policy", {"profile_id": "local-safe"}),
            ("capability_declaration", {"network": {"mode": "unknown"}}),
            ("requested_capabilities", {"network": {"mode": "unknown"}}),
            ("target_class", "/" + "Users/private"),
        )
        for key, value in mutations:
            with self.subTest(key=key):
                values = _case()
                values[key] = value
                self.assertEqual(_create(values)["status"], "invalid")

        values = _case()
        values["trust_policy"]["allowed_source_types"] = ["bundled", "bundled"]
        self.assertEqual(_create(values)["status"], "invalid")

        values = _case()
        values["trust_profile_schema_version"] = 2
        self.assertEqual(_create(values)["status"], "invalid")

        values = _case()
        values["registry_schema_version"] = 2
        self.assertEqual(_create(values)["status"], "invalid")

        values = _case()
        values["capability_declaration"]["network"]["mode"] = "unknown"
        self.assertEqual(_create(values)["status"], "invalid")

        values = _case()
        values["requested_capabilities"]["process"]["mode"] = "unknown"
        self.assertEqual(_create(values)["status"], "invalid")

    def test_operation_and_decision_contracts_fail_closed(self):
        values = _case()
        values["installation_decision"]["operation"] = "remove"
        self.assertEqual(_create(values)["status"], "invalid")

        values = _case()
        values["capability_decision"]["decisions"] = []
        self.assertEqual(_create(values)["status"], "invalid")

        values = _case()
        values["recommendation_decision"]["status"] = "candidate"
        self.assertEqual(_create(values)["status"], "invalid")

        values = _case()
        values["installation_decision"]["operator_authorization"] = "fresh"
        self.assertEqual(_create(values)["status"], "invalid")

    def test_unsupported_json_values_and_bounds_fail_closed(self):
        for value in (3.14, b"bytes", {"set"}, ("tuple",)):
            with self.subTest(value=type(value).__name__):
                values = _case()
                values["trust_evidence"]["extra"] = value
                self.assertEqual(_create(values)["status"], "invalid")

        values = _case()
        values["registry_entry"]["description"] = "x" * 5000
        self.assertEqual(_create(values)["status"], "invalid")

        values = _case()
        nested = {}
        current = nested
        for _ in range(20):
            current["nested"] = {}
            current = current["nested"]
        values["trust_evidence"] = nested
        self.assertEqual(_create(values)["status"], "invalid")

        values = _case()
        values["registry_entry"]["files"] = [
            {"path": "file-%d.md" % index, "sha256": "a" * 64}
            for index in range(65)
        ]
        self.assertEqual(_create(values)["status"], "invalid")

    def test_mapping_and_set_like_order_do_not_change_evidence_or_binding_digest(self):
        first_values = _case()
        first = _create(first_values)
        second_values = copy.deepcopy(first_values)
        second_values["registry_entry"]["files"] = list(reversed(second_values["registry_entry"]["files"]))
        second_values["trust_policy"]["allowed_source_types"] = list(
            reversed(second_values["trust_policy"]["allowed_source_types"])
        )
        second_values["registry_entry"] = {
            key: second_values["registry_entry"][key]
            for key in reversed(tuple(second_values["registry_entry"]))
        }
        second = _create(second_values)
        self.assertEqual(first["evidence_digest"], second["evidence_digest"])
        self.assertEqual(first["binding_digest"], second["binding_digest"])

    def test_operation_changes_binding_digest(self):
        install = _create()
        activate = _create(_case(operation="activate"))
        self.assertEqual(install["status"], "bound")
        self.assertEqual(activate["status"], "bound")
        self.assertNotEqual(install["evidence_digest"], activate["evidence_digest"])
        self.assertNotEqual(install["binding_digest"], activate["binding_digest"])

    def test_domain_separated_digests_are_distinct_and_canonical(self):
        result = _create()
        self.assertNotEqual(result["evidence_digest"], result["binding_digest"])
        self.assertEqual(result["evidence_digest"].lower(), result["evidence_digest"])
        self.assertEqual(result["binding_digest"].lower(), result["binding_digest"])
        self.assertEqual(len(result["evidence_digest"]), 64)
        self.assertEqual(len(result["binding_digest"]), 64)

    def test_current_exact_match_verifies_current(self):
        values = _case()
        binding = _create(values)
        verification = verify_admission_binding(binding, **values)
        self.assertEqual(verification["status"], "current")
        self.assertEqual(verification["assessment_scope"], "phase5e-evidence-binding-verification")
        self.assertEqual(verification["execution_status"], "not-performed")

    def test_stale_identity_manifest_policy_and_evidence_changes_never_verify_current(self):
        original_values = _case()
        binding = _create(original_values)
        mutations = (
            ("registry_entry", lambda v: v["registry_entry"].update({"version": "2.0.0"})),
            ("registry_entry", lambda v: v["registry_entry"]["files"][0].update({"sha256": "b" * 64})),
            ("trust_policy", lambda v: v["trust_policy"].update({"profile_id": "other-profile"})),
            ("trust_evidence", lambda v: v["trust_evidence"].update({"provenance_complete": None})),
            ("capability_policy", lambda v: v["capability_policy"]["capabilities"]["filesystem"].update({"read": []})),
            ("requested_capabilities", lambda v: v["requested_capabilities"]["filesystem"].update({"read": []})),
            ("trust_decision", lambda v: v["trust_decision"].update({"skill_id": "other-skill"})),
        )
        for key, mutate in mutations:
            with self.subTest(key=key):
                values = copy.deepcopy(original_values)
                mutate(values)
                verification = verify_admission_binding(binding, **values)
                self.assertIn(verification["status"], {"stale", "invalid"})
                self.assertNotEqual(verification["status"], "current")

    def test_stale_decisions_and_pipeline_state_never_verify_current(self):
        original_values = _case()
        binding = _create(original_values)
        values = copy.deepcopy(original_values)
        values["recommendation_decision"]["registry_membership"] = False
        self.assertNotEqual(
            verify_admission_binding(binding, **values)["status"], "current"
        )

        values = copy.deepcopy(original_values)
        values["installation_decision"]["operation"] = "activate"
        self.assertNotEqual(
            verify_admission_binding(binding, **values)["status"], "current"
        )

        values = copy.deepcopy(original_values)
        values["target_class"] = "cso-app"
        self.assertNotEqual(
            verify_admission_binding(binding, **values)["status"], "current"
        )

    def test_each_material_evidence_change_is_stale_or_invalid(self):
        original = _case()
        binding = _create(original)
        mutations = (
            ("id", lambda v: v["registry_entry"].update({"id": "other-skill"})),
            ("source-type", lambda v: v["registry_entry"]["source"].update({"type": "git", "repository": "https://example.invalid/repo", "revision": "b" * 40})),
            ("source-revision", lambda v: v["registry_entry"]["source"].update({"revision": "c" * 40})),
            ("license", lambda v: v["registry_entry"]["license"].update({"spdx": "Apache-2.0"})),
            ("provenance", lambda v: v["registry_entry"]["provenance"].update({"maintainer": "Changed"})),
            ("trust-schema", lambda v: v.update({"trust_profile_schema_version": 2})),
            ("declaration", lambda v: v["capability_declaration"]["filesystem"].update({"read": []})),
            ("request", lambda v: v["requested_capabilities"]["filesystem"].update({"read": []})),
            ("trust-decision", lambda v: v["trust_decision"].update({"skill_id": "other-skill"})),
            ("recommendation-decision", lambda v: v["recommendation_decision"].update({"registry_membership": False})),
            ("installation-decision", lambda v: v["installation_decision"].update({"operation": "activate"})),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                values = copy.deepcopy(original)
                mutate(values)
                result = verify_admission_binding(binding, **values)
                self.assertIn(result["status"], {"stale", "invalid"})
                self.assertNotEqual(result["status"], "current")

    def test_invalid_current_pipeline_is_not_reported_current(self):
        binding = _create()
        values = _case()
        values["recommendation_decision"]["status"] = "invalid"
        values["recommendation_decision"]["capability_status"] = "invalid"
        values["installation_decision"]["status"] = "authorized"
        verification = verify_admission_binding(binding, **values)
        self.assertEqual(verification["status"], "invalid")

    def test_stale_verification_does_not_refresh_stored_binding(self):
        values = _case()
        binding = _create(values)
        changed = copy.deepcopy(values)
        changed["registry_entry"]["version"] = "2.0.0"
        self.assertEqual(verify_admission_binding(binding, **changed)["status"], "stale")
        self.assertEqual(binding["status"], "bound")
        self.assertEqual(verify_admission_binding(binding, **values)["status"], "current")

    def test_pipeline_rejected_and_unknown_are_stale_against_bound_binding(self):
        binding = _create()
        for overrides in ("rejected", "unknown"):
            values = _case(trust_status=overrides)
            verification = verify_admission_binding(binding, **values)
            self.assertEqual(verification["status"], "stale")

    def test_malformed_stored_binding_is_invalid(self):
        binding = _create()
        for key in ("binding_digest", "subject", "limitations", "truncated"):
            with self.subTest(key=key):
                malformed = copy.deepcopy(binding)
                if key == "binding_digest":
                    malformed[key] = "not-a-digest"
                elif key == "subject":
                    malformed[key] = {"skill_id": "synthetic-skill"}
                elif key == "limitations":
                    malformed[key] = ["raw secret"]
                else:
                    malformed[key] = True
                self.assertEqual(verify_admission_binding(malformed, **_case())["status"], "invalid")

        requested = _create()
        with_extra_limitation = copy.deepcopy(requested)
        with_extra_limitation["limitations"].append(
            "phase5e.binding.limit.runtime-capability-not-requested"
        )
        self.assertEqual(
            verify_admission_binding(with_extra_limitation, **_case())["status"],
            "stale",
        )

        not_requested_values = _case(capability_status="not-requested")
        not_requested = _create(not_requested_values)
        without_extra_limitation = copy.deepcopy(not_requested)
        without_extra_limitation["limitations"].remove(
            "phase5e.binding.limit.runtime-capability-not-requested"
        )
        self.assertEqual(
            verify_admission_binding(without_extra_limitation, **not_requested_values)["status"],
            "stale",
        )

    def test_verification_does_not_echo_expected_actual_or_sensitive_values(self):
        values = _case()
        binding = _create(values)
        changed = copy.deepcopy(values)
        changed["registry_entry"]["source"]["path"] = "private/secret.txt"
        changed["registry_entry"]["description"] = "https://secret.invalid/repo"
        changed["registry_entry"]["files"][0]["sha256"] = "c" * 64
        verification = verify_admission_binding(binding, **changed)
        serialized = json.dumps(verification, sort_keys=True)
        self.assertEqual(verification["status"], "stale")
        for secret in (binding["binding_digest"], binding["evidence_digest"], "private/secret.txt", "https://secret.invalid/repo", "c" * 64):
            self.assertNotIn(secret, serialized)

    def test_successful_binding_has_all_required_fixed_limitations(self):
        result = _create()
        for limitation in (
            "phase5e.binding.limit.execution-not-performed",
            "phase5e.binding.limit.operator-freshness-not-verified",
            "phase5e.binding.limit.runtime-capability-enforcement-not-implemented",
            "phase5e.binding.limit.not-an-execution-token",
            "phase5e.binding.limit.remote-fetch-disabled",
        ):
            self.assertIn(limitation, result["limitations"])
        self.assertTrue(set(result["limitations"]).issubset(set(LIMITATION_IDS)))

    def test_no_external_effect_imports_in_binding_module(self):
        import inspect
        import skill_orchestrator.admission_binding as module

        source = inspect.getsource(module)
        for forbidden in (
            "pathlib",
            "os",
            "subprocess",
            "socket",
            "urllib",
            "requests",
            "tempfile",
            "datetime",
            "random",
            "uuid",
        ):
            self.assertNotIn("import " + forbidden, source)


if __name__ == "__main__":
    unittest.main()

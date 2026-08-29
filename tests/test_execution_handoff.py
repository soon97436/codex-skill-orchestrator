import copy
import inspect
import json
import unittest

from skill_orchestrator.admission_binding import create_admission_binding
from skill_orchestrator.admission_pipeline import evaluate_admission_pipeline
from skill_orchestrator.capability_policy import evaluate_capability_policy
from skill_orchestrator.installation_authorization import (
    evaluate_installation_authorization,
)
from skill_orchestrator.recommendation_admission import (
    evaluate_recommendation_admission,
)
from skill_orchestrator.registry_trust import evaluate_registry_trust
from skill_orchestrator.execution_handoff import (
    LIMITATION_IDS,
    REASON_IDS,
    evaluate_execution_handoff,
)


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
    "files": [{"path": "SKILL.md", "sha256": "a" * 64}],
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


def _upstream_decisions(*, capability_status=None, trust_status=None, operation="install"):
    trust = evaluate_registry_trust(
        TRUST_ENTRY,
        policy=TRUST_POLICY,
        evidence=TRUST_EVIDENCE,
    )
    if trust_status == "rejected":
        trust = evaluate_registry_trust(
            TRUST_ENTRY,
            policy=TRUST_POLICY,
            evidence=dict(TRUST_EVIDENCE, registry_valid=False),
        )
    elif trust_status == "unknown":
        trust = evaluate_registry_trust(
            TRUST_ENTRY,
            policy=TRUST_POLICY,
            evidence=dict(TRUST_EVIDENCE, provenance_complete=None),
        )

    declaration = copy.deepcopy(CAPABILITIES)
    request = copy.deepcopy(CAPABILITIES)
    if capability_status == "not-requested":
        request = {
            "schema_version": 1,
            "filesystem": {"read": [], "write": []},
            "network": {"mode": "none"},
            "process": {"mode": "none", "commands": []},
        }
    elif capability_status == "unknown":
        declaration = None
    elif capability_status == "rejected":
        request["filesystem"]["read"] = ["unrestricted"]

    capability = evaluate_capability_policy(
        declaration,
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
    return trust, capability, recommendation, installation, declaration, request


def _case(**overrides):
    trust, capability, recommendation, installation, declaration, request = (
        _upstream_decisions(
            capability_status=overrides.pop("capability_status", None),
            trust_status=overrides.pop("trust_status", None),
            operation=overrides.pop("binding_operation", "install"),
        )
    )
    values = {
        "stored_binding": None,
        "registry_schema_version": 1,
        "registry_entry": copy.deepcopy(REGISTRY_ENTRY),
        "trust_profile_schema_version": 1,
        "trust_policy": copy.deepcopy(TRUST_POLICY),
        "trust_evidence": copy.deepcopy(TRUST_EVIDENCE),
        "capability_policy": copy.deepcopy(CAPABILITY_POLICY),
        "capability_declaration": copy.deepcopy(declaration),
        "requested_capabilities": copy.deepcopy(request),
        "trust_decision": trust,
        "capability_decision": capability,
        "recommendation_decision": recommendation,
        "installation_decision": installation,
        "operation": "install",
        "target_class": "registry-skill-user-scope",
        "fresh_operator_authorization": "granted",
        "_binding_operation": overrides.pop("_binding_operation", "install"),
    }
    binding_operation = values.pop("_binding_operation")
    if binding_operation != "install":
        trust, capability, recommendation, installation, declaration, request = (
            _upstream_decisions(operation=binding_operation)
        )
        values.update(
            trust_decision=trust,
            capability_decision=capability,
            recommendation_decision=recommendation,
            installation_decision=installation,
            capability_declaration=copy.deepcopy(declaration),
            requested_capabilities=copy.deepcopy(request),
        )
    values.update(overrides)
    values["stored_binding"] = create_admission_binding(
        registry_schema_version=values["registry_schema_version"],
        registry_entry=values["registry_entry"],
        trust_profile_schema_version=values["trust_profile_schema_version"],
        trust_policy=values["trust_policy"],
        trust_evidence=values["trust_evidence"],
        capability_policy=values["capability_policy"],
        capability_declaration=values["capability_declaration"],
        requested_capabilities=values["requested_capabilities"],
        trust_decision=values["trust_decision"],
        capability_decision=values["capability_decision"],
        recommendation_decision=values["recommendation_decision"],
        installation_decision=values["installation_decision"],
        target_class=values["target_class"],
    )
    return values


def _evaluate(values=None, **overrides):
    values = copy.deepcopy(values or _case())
    values.update(overrides)
    return evaluate_execution_handoff(**values)


class ExecutionHandoffTests(unittest.TestCase):
    def test_exact_current_install_with_fresh_grant_is_ready(self):
        result = _evaluate()
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["operation"], "install")
        self.assertEqual(result["target_class"], "registry-skill-user-scope")
        self.assertEqual(result["execution_status"], "not-performed")

    def test_result_shape_is_fixed_and_metadata_only(self):
        result = _evaluate()
        self.assertEqual(
            list(result),
            [
                "schema_version",
                "status",
                "assessment_scope",
                "operation",
                "target_class",
                "execution_status",
                "reason_ids",
                "limitations",
                "truncated",
            ],
        )
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["assessment_scope"], "phase5e-execution-handoff")
        self.assertFalse(result["truncated"])
        serialized = json.dumps(result, sort_keys=True)
        for secret in (
            "synthetic-skill",
            "router/synthetic-skill",
            "SKILL.md",
            "a" * 64,
            "Synthetic Publisher",
        ):
            self.assertNotIn(secret, serialized)

    def test_status_and_layer_like_values_are_closed(self):
        result = _evaluate()
        self.assertIn(result["status"], {"ready", "rejected", "unknown", "invalid"})
        self.assertNotIn("authorized", result["status"])

    def test_malformed_stored_binding_is_invalid(self):
        result = _evaluate(stored_binding={"status": "bound"})
        self.assertEqual(result["status"], "invalid")
        self.assertIn("phase5e.handoff.binding.invalid", result["reason_ids"])

    def test_malformed_current_evidence_is_invalid(self):
        values = _case()
        values["registry_entry"]["source"] = {"type": "bundled"}
        result = _evaluate(values)
        self.assertEqual(result["status"], "invalid")

    def test_stale_current_evidence_is_rejected(self):
        values = _case()
        values["registry_entry"]["version"] = "2.0.0"
        result = _evaluate(values)
        self.assertEqual(result["status"], "rejected")
        self.assertIn("phase5e.handoff.binding.stale", result["reason_ids"])

    def test_pairing_substitution_never_becomes_ready(self):
        original = _case()
        substituted = _case()
        substituted["registry_entry"]["version"] = "2.0.0"
        result = _evaluate(
            dict(substituted, stored_binding=original["stored_binding"])
        )
        self.assertIn(result["status"], {"rejected", "invalid"})
        self.assertNotEqual(result["status"], "ready")

    def test_material_evidence_mutations_never_become_ready(self):
        original = _case()
        mutations = (
            ("registry_entry", lambda v: v["registry_entry"].update({"name": "Changed"})),
            ("manifest", lambda v: v["registry_entry"]["files"][0].update({"sha256": "b" * 64})),
            ("trust_policy", lambda v: v["trust_policy"].update({"profile_id": "other"})),
            ("capability_policy", lambda v: v["capability_policy"]["capabilities"]["filesystem"].update({"read": []})),
            ("request", lambda v: v["requested_capabilities"]["filesystem"].update({"read": []})),
            ("decision", lambda v: v["recommendation_decision"].update({"registry_membership": False})),
        )
        for label, mutate in mutations:
            with self.subTest(label=label):
                values = copy.deepcopy(original)
                mutate(values)
                result = _evaluate(values)
                self.assertNotEqual(result["status"], "ready")

    def test_fresh_authorization_denied_is_rejected(self):
        result = _evaluate(fresh_operator_authorization="denied")
        self.assertEqual(result["status"], "rejected")
        self.assertIn("phase5e.handoff.authorization.denied", result["reason_ids"])

    def test_fresh_authorization_not_provided_is_unknown(self):
        result = _evaluate(fresh_operator_authorization="not-provided")
        self.assertEqual(result["status"], "unknown")
        self.assertIn("phase5e.handoff.authorization.required", result["reason_ids"])

    def test_malformed_fresh_authorization_is_invalid_and_not_echoed(self):
        secret = "operator-secret"
        result = _evaluate(fresh_operator_authorization=secret)
        self.assertEqual(result["status"], "invalid")
        self.assertNotIn(secret, json.dumps(result, sort_keys=True))

    def test_stored_grant_does_not_satisfy_fresh_authorization(self):
        result = _evaluate(fresh_operator_authorization="not-provided")
        self.assertNotEqual(result["status"], "ready")

    def test_ready_always_discloses_freshness_limitation(self):
        result = _evaluate()
        self.assertIn(
            "phase5e.handoff.limit.fresh-authorization-not-independently-verified",
            result["limitations"],
        )

    def test_activate_exact_match_is_reserved_and_rejected(self):
        values = _case(_binding_operation="activate")
        values["operation"] = "activate"
        result = _evaluate(values)
        self.assertEqual(result["status"], "rejected")
        self.assertIn(
            "phase5e.handoff.operation.activation-reserved", result["reason_ids"]
        )

    def test_install_binding_reused_for_activate_is_rejected(self):
        result = _evaluate(operation="activate")
        self.assertEqual(result["status"], "rejected")
        self.assertIn("phase5e.handoff.operation.mismatch", result["reason_ids"])

    def test_activate_binding_reused_for_install_is_rejected(self):
        values = _case(_binding_operation="activate")
        result = _evaluate(values, operation="install")
        self.assertEqual(result["status"], "rejected")
        self.assertIn("phase5e.handoff.operation.mismatch", result["reason_ids"])

    def test_malformed_operation_is_invalid_and_not_echoed(self):
        secret = "/" + "Users/private/secret"
        result = _evaluate(operation=secret)
        self.assertEqual(result["status"], "invalid")
        self.assertNotIn(secret, json.dumps(result, sort_keys=True))

    def test_target_exact_match_is_required(self):
        result = _evaluate()
        self.assertEqual(result["target_class"], "registry-skill-user-scope")

    def test_known_target_substitution_is_rejected(self):
        for target in ("cso-app", "router-profile", "workspace"):
            with self.subTest(target=target):
                result = _evaluate(_case(), target_class=target)
                self.assertEqual(result["status"], "rejected")
                self.assertIn("phase5e.handoff.target.mismatch", result["reason_ids"])

    def test_arbitrary_or_path_target_is_invalid_without_echo(self):
        for target in ("arbitrary-target", "/" + "Users/private/secret"):
            with self.subTest(target=target):
                result = _evaluate(target_class=target)
                self.assertEqual(result["status"], "invalid")
                self.assertNotIn(target, json.dumps(result, sort_keys=True))

    def test_capability_not_requested_install_can_be_ready(self):
        result = _evaluate(_case(capability_status="not-requested"))
        self.assertEqual(result["status"], "ready")
        self.assertIn(
            "phase5e.handoff.limit.runtime-capability-not-requested",
            result["limitations"],
        )

    def test_ready_is_not_execution_or_authorization_token(self):
        result = _evaluate()
        self.assertEqual(result["execution_status"], "not-performed")
        serialized = json.dumps(result, sort_keys=True)
        for forbidden in ("credential", "permit", "executed", "installed"):
            self.assertNotIn(forbidden, serialized)

    def test_repeated_identical_runs_are_equal(self):
        values = _case()
        self.assertEqual(_evaluate(values), _evaluate(values))

    def test_reason_and_limitation_ids_are_allowlisted(self):
        for values in (
            _case(),
            _case(fresh_operator_authorization="denied"),
            _case(fresh_operator_authorization="not-provided"),
            _case(operation="activate"),
        ):
            result = _evaluate(values)
            self.assertTrue(set(result["reason_ids"]).issubset(set(REASON_IDS)))
            self.assertTrue(set(result["limitations"]).issubset(set(LIMITATION_IDS)))
        result = _evaluate(_case(), target_class="cso-app")
        self.assertTrue(set(result["reason_ids"]).issubset(set(REASON_IDS)))
        self.assertTrue(set(result["limitations"]).issubset(set(LIMITATION_IDS)))

    def test_no_detached_binding_verification_input_is_accepted(self):
        with self.assertRaises(TypeError):
            _evaluate(binding_verification={"status": "current"})

    def test_public_interface_is_small_and_keyword_only(self):
        parameters = inspect.signature(evaluate_execution_handoff).parameters
        self.assertNotIn("binding_verification", parameters)
        self.assertTrue(all(parameter.kind == inspect.Parameter.KEYWORD_ONLY for parameter in parameters.values()))

    def test_no_external_effect_imports_in_handoff_module(self):
        import skill_orchestrator.execution_handoff as module

        source = inspect.getsource(module)
        for forbidden in (
            "import os",
            "import pathlib",
            "import subprocess",
            "import socket",
            "import urllib",
            "import requests",
            "import tempfile",
            "import time",
            "import datetime",
            "import random",
            "import secrets",
            "import uuid",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()

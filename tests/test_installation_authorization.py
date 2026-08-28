import json
import os
import socket
import subprocess
import unittest
import urllib.request
from unittest.mock import patch

from skill_orchestrator.installation_authorization import (
    LIMITATION_IDS,
    REASON_IDS,
    evaluate_installation_authorization,
)


FAMILIES = (
    "filesystem-read",
    "filesystem-write",
    "network",
    "process",
)


def recommendation_result(
    status="recommendable",
    *,
    registry_membership=True,
    trust_status="admissible",
    capability_status="admissible",
):
    if capability_status == "admissible":
        decision_statuses = ("allowed", "not-requested", "not-requested", "not-requested")
        decision_reason = "capability.policy.allowed"
    elif capability_status == "not-requested":
        decision_statuses = ("not-requested",) * 4
        decision_reason = "capability.admission.not-requested"
    elif capability_status == "rejected":
        decision_statuses = ("denied", "not-requested", "not-requested", "not-requested")
        decision_reason = "capability.admission.rejected"
    elif capability_status == "unknown":
        decision_statuses = ("unknown", "not-requested", "not-requested", "not-requested")
        decision_reason = "capability.admission.unknown"
    else:
        decision_statuses = ("unknown",) * 4
        decision_reason = "recommendation.admission.invalid"

    limitations = [
        "recommendation.limit.installation-not-authorized",
        "recommendation.limit.runtime-capability-not-authorized",
    ]
    if status == "recommendable" and capability_status == "not-requested":
        limitations.insert(0, "recommendation.limit.capability-authorization-not-granted")

    return {
        "schema_version": 1,
        "status": status,
        "assessment_scope": "recommendation-admission-only",
        "registry_membership": registry_membership,
        "trust_status": trust_status,
        "capability_status": capability_status,
        "decisions": [
            {
                "capability": family,
                "status": decision_status,
                "reason_ids": [decision_reason],
            }
            for family, decision_status in zip(FAMILIES, decision_statuses)
        ],
        "reasons": ["recommendation.admission.recommendable"] if status == "recommendable" else [],
        "limitations": limitations,
        "truncated": False,
    }


def evaluate(
    *,
    operation="install",
    operator_authorization="granted",
    recommendation=None,
):
    if recommendation is None:
        recommendation = recommendation_result()
    return evaluate_installation_authorization(
        operation=operation,
        operator_authorization=operator_authorization,
        recommendation_decision=recommendation,
    )


class InstallationAuthorizationTests(unittest.TestCase):
    def test_install_authorization_is_metadata_only(self):
        result = evaluate()

        self.assertEqual(result["status"], "authorized")
        self.assertEqual(result["operation"], "install")
        self.assertEqual(result["operator_authorization"], "granted")
        self.assertEqual(result["recommendation_status"], "recommendable")
        self.assertEqual(result["capability_status"], "admissible")

    def test_activate_authorization_is_distinct_and_unexecuted(self):
        result = evaluate(operation="activate")

        self.assertEqual(result["status"], "authorized")
        self.assertEqual(result["operation"], "activate")
        self.assertIn("installation.limit.activation-not-performed", result["limitations"])

    def test_invalid_operation_fails_closed(self):
        result = evaluate(operation="rollback")

        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["operation"], "invalid")
        self.assertIn("installation.authorization.operation-invalid", result["reason_ids"])

    def test_granted_operator_authorizes_recommendable_install(self):
        result = evaluate(operator_authorization="granted")

        self.assertEqual(result["status"], "authorized")
        self.assertIn("installation.authorization.operator-granted", result["reason_ids"])

    def test_denied_operator_rejects_recommendable_request(self):
        result = evaluate(operator_authorization="denied")

        self.assertEqual(result["status"], "rejected")
        self.assertIn("installation.authorization.operator-denied", result["reason_ids"])

    def test_not_provided_operator_is_unknown(self):
        result = evaluate(operator_authorization="not-provided")

        self.assertEqual(result["status"], "unknown")
        self.assertIn("installation.authorization.operator-required", result["reason_ids"])

    def test_invalid_operator_fails_closed(self):
        result = evaluate(operator_authorization="maybe")

        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["operator_authorization"], "invalid")
        self.assertIn("installation.authorization.operator-invalid", result["reason_ids"])

    def test_malformed_recommendation_is_invalid(self):
        result = evaluate(recommendation={})

        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["recommendation_status"], "invalid")

    def test_missing_recommendation_field_is_invalid(self):
        decision = recommendation_result()
        del decision["capability_status"]

        result = evaluate(recommendation=decision)

        self.assertEqual(result["status"], "invalid")
        self.assertIn("installation.authorization.recommendation-invalid", result["reason_ids"])

    def test_unsupported_recommendation_status_is_invalid(self):
        decision = recommendation_result()
        decision["status"] = "candidate"

        result = evaluate(recommendation=decision)

        self.assertEqual(result["status"], "invalid")

    def test_recommendation_schema_mismatch_is_invalid(self):
        decision = recommendation_result()
        decision["schema_version"] = 2

        result = evaluate(recommendation=decision)

        self.assertEqual(result["status"], "invalid")

    def test_recommendation_scope_mismatch_is_invalid(self):
        decision = recommendation_result()
        decision["assessment_scope"] = "capability-policy-only"

        result = evaluate(recommendation=decision)

        self.assertEqual(result["status"], "invalid")

    def test_recommendation_truncated_is_invalid(self):
        decision = recommendation_result()
        decision["truncated"] = True

        result = evaluate(recommendation=decision)

        self.assertEqual(result["status"], "invalid")

    def test_non_boolean_registry_membership_is_invalid(self):
        decision = recommendation_result(registry_membership=1)

        result = evaluate(recommendation=decision)

        self.assertEqual(result["status"], "invalid")

    def test_malformed_capability_status_is_invalid(self):
        decision = recommendation_result()
        decision["capability_status"] = "maybe"

        result = evaluate(recommendation=decision)

        self.assertEqual(result["status"], "invalid")

    def test_malformed_trust_status_is_invalid(self):
        decision = recommendation_result(trust_status="maybe")

        result = evaluate(recommendation=decision)

        self.assertEqual(result["status"], "invalid")

    def test_rejected_recommendation_remains_rejected(self):
        result = evaluate(recommendation=recommendation_result("rejected"))

        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["recommendation_status"], "rejected")

    def test_unknown_recommendation_remains_unknown(self):
        result = evaluate(recommendation=recommendation_result("unknown", capability_status="unknown"))

        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["recommendation_status"], "unknown")

    def test_invalid_recommendation_remains_invalid(self):
        result = evaluate(recommendation=recommendation_result("invalid", capability_status="invalid"))

        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["recommendation_status"], "invalid")

    def test_recommendable_with_denied_operator_cannot_authorize(self):
        result = evaluate(operator_authorization="denied")

        self.assertNotEqual(result["status"], "authorized")
        self.assertEqual(result["status"], "rejected")

    def test_recommendable_with_missing_operator_cannot_authorize(self):
        result = evaluate(operator_authorization="not-provided")

        self.assertNotEqual(result["status"], "authorized")
        self.assertEqual(result["status"], "unknown")

    def test_recommendable_requires_registry_membership(self):
        decision = recommendation_result(registry_membership=False)
        decision["status"] = "recommendable"

        result = evaluate(recommendation=decision)

        self.assertEqual(result["status"], "invalid")

    def test_recommendable_requires_admissible_trust(self):
        decision = recommendation_result(trust_status="unknown")
        decision["status"] = "recommendable"

        result = evaluate(recommendation=decision)

        self.assertEqual(result["status"], "invalid")

    def test_recommendable_requires_admissible_or_not_requested_capability(self):
        decision = recommendation_result(capability_status="rejected")
        decision["status"] = "recommendable"

        result = evaluate(recommendation=decision)

        self.assertEqual(result["status"], "invalid")

    def test_admissible_capability_can_authorize(self):
        result = evaluate(recommendation=recommendation_result(capability_status="admissible"))

        self.assertEqual(result["status"], "authorized")

    def test_not_requested_capability_can_authorize_without_runtime_grant(self):
        result = evaluate(recommendation=recommendation_result(capability_status="not-requested"))

        self.assertEqual(result["status"], "authorized")
        self.assertIn("installation.limit.skill-capability-not-requested", result["limitations"])
        self.assertIn("installation.limit.runtime-capability-not-authorized", result["limitations"])

    def test_rejected_capability_cannot_be_hidden_inside_recommendable(self):
        decision = recommendation_result(capability_status="rejected")
        decision["status"] = "recommendable"

        result = evaluate(recommendation=decision)

        self.assertEqual(result["status"], "invalid")

    def test_unknown_capability_cannot_be_hidden_inside_recommendable(self):
        decision = recommendation_result(capability_status="unknown")
        decision["status"] = "recommendable"

        result = evaluate(recommendation=decision)

        self.assertEqual(result["status"], "invalid")

    def test_rejected_trust_cannot_be_hidden_inside_recommendable(self):
        decision = recommendation_result(trust_status="rejected")
        decision["status"] = "recommendable"

        result = evaluate(recommendation=decision)

        self.assertEqual(result["status"], "invalid")

    def test_registry_absence_cannot_be_hidden_inside_recommendable(self):
        decision = recommendation_result(registry_membership=False)
        decision["status"] = "recommendable"

        result = evaluate(recommendation=decision)

        self.assertEqual(result["status"], "invalid")

    def test_install_authorization_never_claims_installed_or_executed(self):
        result = evaluate()
        serialized = json.dumps(result, sort_keys=True)

        self.assertNotIn('"installed"', serialized)
        self.assertNotIn('"executed"', serialized)
        self.assertNotIn('"running"', serialized)

    def test_activate_authorization_never_claims_activated(self):
        result = evaluate(operation="activate")
        serialized = json.dumps(result, sort_keys=True)

        self.assertNotIn('"activated"', serialized)

    def test_fixed_limitations_do_not_claim_os_permission(self):
        result = evaluate()

        self.assertIn("installation.limit.os-permission-not-granted", result["limitations"])
        self.assertIn("installation.limit.destination-validation-not-performed", result["limitations"])

    def test_result_keys_and_vocabularies_are_closed(self):
        result = evaluate()

        self.assertEqual(
            set(result),
            {
                "schema_version",
                "status",
                "assessment_scope",
                "operation",
                "operator_authorization",
                "recommendation_status",
                "capability_status",
                "reason_ids",
                "limitations",
                "truncated",
            },
        )
        self.assertIn(result["status"], {"authorized", "rejected", "unknown", "invalid"})

    def test_mapping_insertion_order_does_not_change_result(self):
        decision = recommendation_result()
        reordered = {key: decision[key] for key in reversed(tuple(decision))}

        first = evaluate(recommendation=decision)
        second = evaluate(recommendation=reordered)

        self.assertEqual(first, second)

    def test_repeated_runs_are_equal(self):
        results = [evaluate() for _ in range(3)]

        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])

    def test_reason_order_is_deterministic(self):
        result = evaluate(operator_authorization="denied")

        positions = [REASON_IDS.index(reason_id) for reason_id in result["reason_ids"]]
        self.assertEqual(positions, sorted(positions))

    def test_reason_ids_and_limitations_are_allowlisted(self):
        result = evaluate(operation="activate")

        self.assertTrue(set(result["reason_ids"]).issubset(set(REASON_IDS)))
        self.assertTrue(set(result["limitations"]).issubset(set(LIMITATION_IDS)))

    def test_privacy_does_not_echo_untrusted_recommendation_fields(self):
        decision = recommendation_result()
        decision["candidate_id"] = "PRIVATE_CANDIDATE"
        decision["profile_id"] = "/" + "Users/private-user/.ssh/key"
        decision["reasons"] = ["https://private.example/secret"]

        result = evaluate(recommendation=decision)
        serialized = json.dumps(result, sort_keys=True)

        for marker in (
            "PRIVATE_CANDIDATE",
            "private-user",
            "https://private.example",
            "/" + "Users/",
            "hostname",
            "username",
            "timestamp",
            "environment",
        ):
            self.assertNotIn(marker, serialized)

    def test_no_external_effects_are_used(self):
        with patch("builtins.open", side_effect=AssertionError("filesystem")), patch(
            "subprocess.run", side_effect=AssertionError("subprocess")
        ), patch("subprocess.Popen", side_effect=AssertionError("subprocess")), patch(
            "socket.socket", side_effect=AssertionError("network")
        ), patch(
            "urllib.request.urlopen", side_effect=AssertionError("network")
        ), patch("os.getenv", side_effect=AssertionError("environment")):
            result = evaluate()

        self.assertEqual(result["status"], "authorized")


if __name__ == "__main__":
    unittest.main()

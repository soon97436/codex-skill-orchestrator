import copy
import json
import os
import socket
import subprocess
import unittest
import urllib.request
from unittest.mock import patch

from skill_orchestrator.recommendation_admission import (
    ADMISSION_REASON_IDS,
    LIMITATION_IDS,
    evaluate_recommendation_admission,
)


FAMILIES = (
    "filesystem-read",
    "filesystem-write",
    "network",
    "process",
)


def capability_result(status="admissible", trust_status="admissible"):
    if status == "admissible":
        decision_statuses = ("allowed", "not-requested", "not-requested", "not-requested")
        reason = "capability.policy.allowed"
    elif status == "not-requested":
        decision_statuses = ("not-requested",) * 4
        reason = "capability.admission.not-requested"
    elif status == "rejected":
        decision_statuses = ("denied", "not-requested", "not-requested", "not-requested")
        reason = "capability.admission.rejected"
    elif status == "unknown":
        decision_statuses = ("unknown", "not-requested", "not-requested", "not-requested")
        reason = "capability.admission.unknown"
    else:
        decision_statuses = ("unknown",) * 4
        reason = "capability.admission.rejected"
    return {
        "schema_version": 1,
        "status": status,
        "assessment_scope": "capability-policy-only",
        "trust_status": trust_status,
        "profile_id": "local-safe",
        "decisions": [
            {
                "capability": family,
                "status": decision_status,
                "reason_ids": [reason],
            }
            for family, decision_status in zip(FAMILIES, decision_statuses)
        ],
        "reasons": [reason],
        "limitations": ["capability.limit.enforcement-not-implemented"],
        "truncated": False,
    }


def evaluate(*, registry_membership=True, trust_status="admissible", capability_status="admissible"):
    return evaluate_recommendation_admission(
        registry_membership=registry_membership,
        trust_status=trust_status,
        capability_decision=capability_result(capability_status, trust_status),
    )


class RecommendationAdmissionTests(unittest.TestCase):
    def test_registry_absent_is_rejected(self):
        result = evaluate(registry_membership=False)

        self.assertEqual(result["status"], "rejected")
        self.assertIn("recommendation.admission.registry-absent", result["reasons"])

    def test_registry_membership_must_be_boolean(self):
        result = evaluate(registry_membership=1)

        self.assertEqual(result["status"], "invalid")
        self.assertIn("recommendation.admission.invalid", result["reasons"])

    def test_rejected_trust_is_rejected(self):
        result = evaluate(trust_status="rejected", capability_status="rejected")

        self.assertEqual(result["status"], "rejected")
        self.assertIn("recommendation.admission.trust-rejected", result["reasons"])

    def test_unknown_trust_is_unknown(self):
        result = evaluate(trust_status="unknown", capability_status="unknown")

        self.assertEqual(result["status"], "unknown")
        self.assertIn("recommendation.admission.trust-unknown", result["reasons"])

    def test_not_evaluated_trust_is_unknown(self):
        result = evaluate(trust_status="not-evaluated", capability_status="unknown")

        self.assertEqual(result["status"], "unknown")
        self.assertIn("recommendation.admission.trust-required", result["reasons"])

    def test_admissible_capability_is_recommendable(self):
        result = evaluate()

        self.assertEqual(result["status"], "recommendable")
        self.assertIn("recommendation.admission.recommendable", result["reasons"])

    def test_not_requested_capability_is_recommendable_but_not_authorized(self):
        result = evaluate(capability_status="not-requested")

        self.assertEqual(result["status"], "recommendable")
        self.assertEqual(
            result["limitations"],
            [
                "recommendation.limit.capability-authorization-not-granted",
                "recommendation.limit.installation-not-authorized",
                "recommendation.limit.runtime-capability-not-authorized",
            ],
        )

    def test_rejected_capability_is_rejected(self):
        result = evaluate(capability_status="rejected")

        self.assertEqual(result["status"], "rejected")
        self.assertIn("recommendation.admission.capability-rejected", result["reasons"])

    def test_unknown_capability_is_unknown(self):
        result = evaluate(capability_status="unknown")

        self.assertEqual(result["status"], "unknown")
        self.assertIn("recommendation.admission.capability-unknown", result["reasons"])

    def test_invalid_capability_is_invalid(self):
        result = evaluate(capability_status="invalid")

        self.assertEqual(result["status"], "invalid")
        self.assertIn("recommendation.admission.capability-invalid", result["reasons"])

    def test_malformed_capability_result_is_invalid(self):
        result = evaluate_recommendation_admission(
            registry_membership=True,
            trust_status="admissible",
            capability_decision={},
        )

        self.assertEqual(result["status"], "invalid")

    def test_schema_mismatch_is_invalid(self):
        decision = capability_result()
        decision["schema_version"] = 2

        result = evaluate_recommendation_admission(
            registry_membership=True,
            trust_status="admissible",
            capability_decision=decision,
        )

        self.assertEqual(result["status"], "invalid")
        self.assertIn("recommendation.admission.capability-invalid", result["reasons"])

    def test_assessment_scope_mismatch_is_invalid(self):
        decision = capability_result()
        decision["assessment_scope"] = "trust-only"

        result = evaluate_recommendation_admission(
            registry_membership=True,
            trust_status="admissible",
            capability_decision=decision,
        )

        self.assertEqual(result["status"], "invalid")

    def test_trust_status_mismatch_is_invalid(self):
        decision = capability_result("unknown", trust_status="unknown")

        result = evaluate_recommendation_admission(
            registry_membership=True,
            trust_status="admissible",
            capability_decision=decision,
        )

        self.assertEqual(result["status"], "invalid")
        self.assertIn("recommendation.admission.trust-mismatch", result["reasons"])

    def test_unknown_trust_vocabulary_is_invalid(self):
        result = evaluate_recommendation_admission(
            registry_membership=True,
            trust_status="maybe",
            capability_decision=capability_result(trust_status="maybe"),
        )

        self.assertEqual(result["status"], "invalid")

    def test_decision_order_is_canonical(self):
        result = evaluate()

        self.assertEqual(
            [item["capability"] for item in result["decisions"]],
            list(FAMILIES),
        )

    def test_mapping_insertion_order_does_not_change_result(self):
        decision = capability_result()
        reordered = {key: decision[key] for key in reversed(tuple(decision))}

        first = evaluate_recommendation_admission(
            registry_membership=True,
            trust_status="admissible",
            capability_decision=decision,
        )
        second = evaluate_recommendation_admission(
            registry_membership=True,
            trust_status="admissible",
            capability_decision=reordered,
        )

        self.assertEqual(first, second)

    def test_repeated_runs_are_equal(self):
        results = [evaluate() for _ in range(3)]

        self.assertEqual(results[0], results[1])
        self.assertEqual(results[1], results[2])

    def test_reason_ids_are_allowlisted(self):
        result = evaluate(capability_status="rejected")

        self.assertTrue(set(result["reasons"]).issubset(set(ADMISSION_REASON_IDS)))

    def test_result_is_metadata_only(self):
        decision = capability_result()
        decision["profile_id"] = "TOP_SECRET_COMMAND"
        decision["decisions"][0]["reason_ids"] = ["/" + "Users/private-user/.ssh/key"]
        decision["reasons"] = ["https://private.example/secret"]

        result = evaluate_recommendation_admission(
            registry_membership=True,
            trust_status="admissible",
            capability_decision=decision,
        )
        serialized = json.dumps(result, sort_keys=True)

        self.assertNotIn("TOP_SECRET_COMMAND", serialized)
        self.assertNotIn("private-user", serialized)
        self.assertNotIn("https://", serialized)
        self.assertNotIn("/" + "Users/", serialized)
        self.assertNotIn("hostname", serialized)
        self.assertNotIn("username", serialized)
        self.assertNotIn("timestamp", serialized)
        self.assertNotIn("environment", serialized)

    def test_invalid_capability_payload_does_not_echo_sensitive_values(self):
        secret = "PRIVATE_TOKEN_VALUE"
        decision = {"secret": secret}

        result = evaluate_recommendation_admission(
            registry_membership=True,
            trust_status="admissible",
            capability_decision=decision,
        )

        self.assertEqual(result["status"], "invalid")
        self.assertNotIn(secret, repr(result))

    def test_no_external_effects_are_used(self):
        with patch("builtins.open", side_effect=AssertionError("filesystem")), patch(
            "subprocess.run", side_effect=AssertionError("subprocess")
        ), patch("subprocess.Popen", side_effect=AssertionError("subprocess")), patch(
            "socket.socket", side_effect=AssertionError("network")
        ), patch(
            "urllib.request.urlopen", side_effect=AssertionError("network")
        ), patch("os.getenv", side_effect=AssertionError("environment")):
            result = evaluate()

        self.assertEqual(result["status"], "recommendable")

    def test_extra_capability_fields_fail_closed(self):
        decision = capability_result()
        decision["candidate_id"] = "do-not-echo"

        result = evaluate_recommendation_admission(
            registry_membership=True,
            trust_status="admissible",
            capability_decision=decision,
        )

        self.assertEqual(result["status"], "invalid")

    def test_capability_truncation_is_invalid(self):
        decision = capability_result()
        decision["truncated"] = True

        result = evaluate_recommendation_admission(
            registry_membership=True,
            trust_status="admissible",
            capability_decision=decision,
        )

        self.assertEqual(result["status"], "invalid")

    def test_unknown_capability_decision_status_is_invalid(self):
        decision = capability_result()
        decision["status"] = "maybe"

        result = evaluate_recommendation_admission(
            registry_membership=True,
            trust_status="admissible",
            capability_decision=decision,
        )

        self.assertEqual(result["status"], "invalid")

    def test_limitations_are_fixed_and_bounded(self):
        result = evaluate()

        self.assertEqual(result["limitations"], list(LIMITATION_IDS))


if __name__ == "__main__":
    unittest.main()

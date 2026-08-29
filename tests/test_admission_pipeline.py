import copy
import json
import unittest
from unittest.mock import patch

from skill_orchestrator.admission_pipeline import evaluate_admission_pipeline


TRUST_DIMENSIONS = (
    "registry",
    "source_policy",
    "source_identity",
    "provenance",
    "license",
    "integrity",
    "capability_policy",
)
CAPABILITY_FAMILIES = (
    "filesystem-read",
    "filesystem-write",
    "network",
    "process",
)


def trust_result(status="admissible"):
    if status == "admissible":
        decision_statuses = ("pass", "pass", "not-applicable", "pass", "pass", "pass", "not-applicable")
        decision_reasons = (
            "trust.registry.valid",
            "trust.source.allowlisted",
            "trust.source.identity-not-applicable",
            "trust.provenance.complete",
            "trust.license.approved",
            "trust.integrity.verified",
            "trust.capability.not-evaluated",
        )
        final_reason = "trust.admission.admissible"
    elif status == "rejected":
        decision_statuses = ("fail", "pass", "not-applicable", "pass", "pass", "pass", "not-applicable")
        decision_reasons = (
            "trust.registry.invalid",
            "trust.source.allowlisted",
            "trust.source.identity-not-applicable",
            "trust.provenance.complete",
            "trust.license.approved",
            "trust.integrity.verified",
            "trust.capability.not-evaluated",
        )
        final_reason = "trust.admission.rejected"
    elif status == "unknown":
        decision_statuses = ("unknown", "pass", "not-applicable", "pass", "pass", "pass", "not-applicable")
        decision_reasons = (
            "trust.registry.unknown",
            "trust.source.allowlisted",
            "trust.source.identity-not-applicable",
            "trust.provenance.complete",
            "trust.license.approved",
            "trust.integrity.verified",
            "trust.capability.not-evaluated",
        )
        final_reason = "trust.admission.unknown"
    else:
        raise ValueError("unsupported fixture status")
    return {
        "schema_version": 1,
        "status": status,
        "skill_id": "fixture-skill",
        "decisions": [
            {
                "dimension": dimension,
                "status": decision_status,
                "reason_ids": [reason_id],
            }
            for dimension, decision_status, reason_id in zip(
                TRUST_DIMENSIONS, decision_statuses, decision_reasons
            )
        ],
        "reasons": list(decision_reasons) + [final_reason],
        "limitations": ["trust.limit.capability-enforcement-not-implemented"],
        "truncated": False,
    }


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
    elif status == "invalid":
        decision_statuses = ("unknown",) * 4
        reason = "capability.admission.rejected"
    else:
        raise ValueError("unsupported fixture status")
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
            for family, decision_status in zip(CAPABILITY_FAMILIES, decision_statuses)
        ],
        "reasons": [reason],
        "limitations": ["capability.limit.enforcement-not-implemented"],
        "truncated": False,
    }


def recommendation_result(
    status="recommendable",
    *,
    trust_status="admissible",
    capability_status="admissible",
    registry_membership=True,
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
        decision_reason = "recommendation.admission.capability-invalid"

    limitations = [
        "recommendation.limit.installation-not-authorized",
        "recommendation.limit.runtime-capability-not-authorized",
    ]
    if status == "recommendable" and capability_status == "not-requested":
        limitations.insert(0, "recommendation.limit.capability-authorization-not-granted")
    reasons = []
    if status == "recommendable":
        reasons = ["recommendation.admission.recommendable"]
    elif status == "rejected":
        reasons = ["recommendation.admission.capability-rejected"]
    elif status == "unknown":
        reasons = ["recommendation.admission.capability-unknown"]
    elif status == "invalid":
        reasons = ["recommendation.admission.capability-invalid"]
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
            for family, decision_status in zip(CAPABILITY_FAMILIES, decision_statuses)
        ],
        "reasons": reasons,
        "limitations": limitations,
        "truncated": False,
    }


def installation_result(
    status="authorized",
    *,
    operator_authorization="granted",
    recommendation_status="recommendable",
    capability_status="admissible",
    operation="install",
):
    if status == "authorized":
        reasons = [
            "installation.authorization.operator-granted",
            "installation.authorization.authorized",
        ]
    elif status == "rejected":
        reasons = [
            "installation.authorization.operator-denied",
            "installation.authorization.rejected",
        ]
    elif status == "unknown":
        reasons = [
            "installation.authorization.operator-required",
            "installation.authorization.unknown",
        ]
    else:
        reasons = [
            "installation.authorization.recommendation-invalid",
            "installation.authorization.invalid",
        ]
    limitations = [
        "installation.limit.execution-not-performed",
        "installation.limit.destination-validation-not-performed",
        "installation.limit.os-permission-not-granted",
        "installation.limit.runtime-capability-not-authorized",
    ]
    if capability_status == "not-requested":
        limitations.append("installation.limit.skill-capability-not-requested")
    if operation == "activate":
        limitations.append("installation.limit.activation-not-performed")
    return {
        "schema_version": 1,
        "status": status,
        "assessment_scope": "installation-authorization-only",
        "operation": operation,
        "operator_authorization": operator_authorization,
        "recommendation_status": recommendation_status,
        "capability_status": capability_status,
        "reason_ids": reasons,
        "limitations": limitations,
        "truncated": False,
    }


def pipeline(
    *,
    trust=None,
    capability=None,
    recommendation=None,
    installation=None,
):
    return evaluate_admission_pipeline(
        trust_decision=trust if trust is not None else trust_result(),
        capability_decision=capability if capability is not None else capability_result(),
        recommendation_decision=(
            recommendation if recommendation is not None else recommendation_result()
        ),
        installation_decision=(
            installation if installation is not None else installation_result()
        ),
    )


class AdmissionPipelineTests(unittest.TestCase):
    def test_positive_pipeline_is_admissible(self):
        result = pipeline()
        self.assertEqual(result["overall_status"], "admissible")
        self.assertEqual(result["execution_status"], "not-performed")

    def test_capability_not_requested_can_be_admissible_without_runtime_grant(self):
        result = pipeline(
            capability=capability_result("not-requested"),
            recommendation=recommendation_result(
                capability_status="not-requested"
            ),
            installation=installation_result(capability_status="not-requested"),
        )
        self.assertEqual(result["overall_status"], "admissible")
        self.assertIn(
            "phase5e.limit.runtime-capability-not-requested",
            result["limitations"],
        )
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn("granted", serialized)
        self.assertNotIn("runtime-authorized", serialized)

    def test_non_mapping_stage_inputs_are_invalid(self):
        for field in ("trust", "capability", "recommendation", "installation"):
            with self.subTest(field=field):
                result = pipeline(**{field: "not-an-object"})
                self.assertEqual(result["overall_status"], "invalid")

    def test_missing_keys_are_invalid(self):
        cases = (
            ("trust", "schema_version"),
            ("capability", "profile_id"),
            ("recommendation", "status"),
            ("installation", "operator_authorization"),
        )
        for field, key in cases:
            with self.subTest(field=field, key=key):
                value = {
                    "trust": trust_result(),
                    "capability": capability_result(),
                    "recommendation": recommendation_result(),
                    "installation": installation_result(),
                }[field]
                del value[key]
                self.assertEqual(pipeline(**{field: value})["overall_status"], "invalid")

    def test_extra_keys_are_invalid(self):
        cases = (
            ("trust", "unexpected"),
            ("capability", "unexpected"),
            ("recommendation", "unexpected"),
            ("installation", "unexpected"),
        )
        for field, key in cases:
            with self.subTest(field=field):
                value = {
                    "trust": trust_result(),
                    "capability": capability_result(),
                    "recommendation": recommendation_result(),
                    "installation": installation_result(),
                }[field]
                value[key] = "opaque"
                self.assertEqual(pipeline(**{field: value})["overall_status"], "invalid")

    def test_schema_scope_status_and_truncated_contracts_are_strict(self):
        mutations = (
            ("trust", "schema_version", 2),
            ("capability", "assessment_scope", "other"),
            ("recommendation", "status", "candidate"),
            ("installation", "truncated", True),
        )
        for field, key, value in mutations:
            with self.subTest(field=field, key=key):
                payload = {
                    "trust": trust_result(),
                    "capability": capability_result(),
                    "recommendation": recommendation_result(),
                    "installation": installation_result(),
                }[field]
                payload[key] = value
                self.assertEqual(pipeline(**{field: payload})["overall_status"], "invalid")

    def test_malformed_decisions_reasons_and_limitations_are_invalid(self):
        mutations = (
            ("trust", "decisions", []),
            ("capability", "decisions", []),
            ("recommendation", "reasons", ["not-allowed"]),
            ("installation", "limitations", ["not-allowed"]),
        )
        for field, key, value in mutations:
            with self.subTest(field=field, key=key):
                payload = {
                    "trust": trust_result(),
                    "capability": capability_result(),
                    "recommendation": recommendation_result(),
                    "installation": installation_result(),
                }[field]
                payload[key] = value
                self.assertEqual(pipeline(**{field: payload})["overall_status"], "invalid")

    def test_cross_stage_trust_mismatch_is_invalid(self):
        capability = capability_result("rejected", "rejected")
        recommendation = recommendation_result(
            "rejected", trust_status="admissible", capability_status="rejected"
        )
        installation = installation_result(
            "rejected", recommendation_status="rejected", capability_status="rejected"
        )
        result = pipeline(
            capability=capability,
            recommendation=recommendation,
            installation=installation,
        )
        self.assertEqual(result["overall_status"], "invalid")
        self.assertIn("phase5e.evidence.mismatch", result["reason_ids"])

    def test_cross_stage_recommendation_mismatches_are_invalid(self):
        recommendation = recommendation_result()
        recommendation["trust_status"] = "unknown"
        result = pipeline(recommendation=recommendation)
        self.assertEqual(result["overall_status"], "invalid")

        recommendation = recommendation_result()
        recommendation["capability_status"] = "unknown"
        result = pipeline(recommendation=recommendation)
        self.assertEqual(result["overall_status"], "invalid")

    def test_cross_stage_installation_mismatches_are_invalid(self):
        installation = installation_result()
        installation["recommendation_status"] = "unknown"
        self.assertEqual(pipeline(installation=installation)["overall_status"], "invalid")

        installation = installation_result()
        installation["capability_status"] = "unknown"
        self.assertEqual(pipeline(installation=installation)["overall_status"], "invalid")

    def test_rejected_upstream_state_is_rejected(self):
        cases = (
            {
                "trust": trust_result("rejected"),
                "capability": capability_result("rejected", "rejected"),
                "recommendation": recommendation_result(
                    "rejected", trust_status="rejected", capability_status="rejected"
                ),
                "installation": installation_result(
                    "rejected", recommendation_status="rejected", capability_status="rejected"
                ),
            },
            {
                "capability": capability_result("rejected"),
                "recommendation": recommendation_result(
                    "rejected", capability_status="rejected"
                ),
                "installation": installation_result(
                    "rejected", recommendation_status="rejected", capability_status="rejected"
                ),
            },
            {
                "capability": capability_result("rejected"),
                "recommendation": recommendation_result(
                    "rejected", capability_status="rejected"
                ),
                "installation": installation_result(
                    "rejected", recommendation_status="rejected", capability_status="rejected"
                ),
            },
            {"installation": installation_result("rejected", operator_authorization="denied")},
        )
        for overrides in cases:
            with self.subTest(overrides=tuple(overrides)):
                self.assertEqual(pipeline(**overrides)["overall_status"], "rejected")

    def test_unknown_upstream_state_is_unknown(self):
        trust = trust_result("unknown")
        capability = capability_result("unknown", "unknown")
        recommendation = recommendation_result("unknown", trust_status="unknown", capability_status="unknown")
        installation = installation_result("unknown", recommendation_status="unknown", capability_status="unknown")
        self.assertEqual(
            pipeline(
                trust=trust,
                capability=capability,
                recommendation=recommendation,
                installation=installation,
            )["overall_status"],
            "unknown",
        )

    def test_invalid_upstream_state_is_invalid(self):
        for field in ("capability", "recommendation", "installation"):
            with self.subTest(field=field):
                value = {
                    "capability": capability_result("invalid"),
                    "recommendation": recommendation_result("invalid", capability_status="invalid"),
                    "installation": installation_result("invalid", recommendation_status="invalid", capability_status="invalid"),
                }[field]
                self.assertEqual(pipeline(**{field: value})["overall_status"], "invalid")

    def test_rejected_trust_cannot_be_laundered_by_positive_downstream(self):
        trust = trust_result("rejected")
        capability = capability_result("admissible", "rejected")
        recommendation = recommendation_result(trust_status="rejected")
        installation = installation_result()
        installation["recommendation_status"] = "recommendable"
        self.assertEqual(
            pipeline(
                trust=trust,
                capability=capability,
                recommendation=recommendation,
                installation=installation,
            )["overall_status"],
            "invalid",
        )

    def test_unknown_trust_cannot_be_laundered_by_positive_downstream(self):
        trust = trust_result("unknown")
        capability = capability_result("admissible", "unknown")
        recommendation = recommendation_result(trust_status="unknown")
        installation = installation_result()
        self.assertEqual(
            pipeline(
                trust=trust,
                capability=capability,
                recommendation=recommendation,
                installation=installation,
            )["overall_status"],
            "invalid",
        )

    def test_rejected_capability_cannot_be_laundered_by_recommendable(self):
        capability = capability_result("rejected")
        recommendation = recommendation_result(capability_status="rejected")
        recommendation["status"] = "recommendable"
        installation = installation_result(capability_status="rejected")
        installation["recommendation_status"] = "recommendable"
        installation["capability_status"] = "rejected"
        self.assertEqual(
            pipeline(capability=capability, recommendation=recommendation, installation=installation)[
                "overall_status"
            ],
            "invalid",
        )

    def test_unknown_capability_cannot_be_laundered_by_authorized_install(self):
        capability = capability_result("unknown")
        recommendation = recommendation_result("unknown", capability_status="unknown")
        installation = installation_result(
            capability_status="unknown", recommendation_status="unknown"
        )
        installation["status"] = "authorized"
        installation["operator_authorization"] = "granted"
        self.assertEqual(
            pipeline(capability=capability, recommendation=recommendation, installation=installation)[
                "overall_status"
            ],
            "invalid",
        )

    def test_rejected_or_unknown_recommendation_cannot_be_authorized(self):
        for recommendation_status in ("rejected", "unknown", "invalid"):
            with self.subTest(recommendation_status=recommendation_status):
                capability_status = {
                    "rejected": "rejected",
                    "unknown": "unknown",
                    "invalid": "invalid",
                }[recommendation_status]
                recommendation = recommendation_result(
                    recommendation_status,
                    capability_status=capability_status,
                )
                installation = installation_result(
                    "authorized",
                    recommendation_status=recommendation_status,
                    capability_status=capability_status,
                )
                self.assertEqual(
                    pipeline(recommendation=recommendation, installation=installation)[
                        "overall_status"
                    ],
                    "invalid",
                )

    def test_authorized_install_requires_granted_operator(self):
        for operator_authorization in ("not-provided", "denied"):
            with self.subTest(operator_authorization=operator_authorization):
                installation = installation_result(
                    "authorized", operator_authorization=operator_authorization
                )
                self.assertEqual(
                    pipeline(installation=installation)["overall_status"], "invalid"
                )

    def test_authorized_install_requires_recommendable_and_safe_capability_state(self):
        installation = installation_result(
            "authorized", recommendation_status="rejected", capability_status="admissible"
        )
        self.assertEqual(pipeline(installation=installation)["overall_status"], "invalid")

        installation = installation_result(
            "authorized", recommendation_status="recommendable", capability_status="rejected"
        )
        recommendation = recommendation_result("recommendable", capability_status="rejected")
        self.assertEqual(
            pipeline(installation=installation, recommendation=recommendation)["overall_status"],
            "invalid",
        )

    def test_stage_order_is_immutable(self):
        result = pipeline()
        self.assertEqual(
            [stage["stage"] for stage in result["stages"]],
            [
                "registry-trust",
                "capability-policy",
                "recommendation-admission",
                "installation-authorization",
            ],
        )

    def test_mapping_insertion_order_does_not_change_result(self):
        values = {
            "trust_decision": trust_result(),
            "capability_decision": capability_result(),
            "recommendation_decision": recommendation_result(),
            "installation_decision": installation_result(),
        }
        reordered = {
            key: {inner_key: values[key][inner_key] for inner_key in reversed(tuple(values[key]))}
            for key in reversed(tuple(values))
        }
        first = evaluate_admission_pipeline(**values)
        second = evaluate_admission_pipeline(**reordered)
        self.assertEqual(first, second)

    def test_repeated_runs_are_equal(self):
        self.assertEqual(pipeline(), pipeline())

    def test_reason_and_limitation_order_is_fixed(self):
        result = pipeline(
            capability=capability_result("not-requested"),
            recommendation=recommendation_result(capability_status="not-requested"),
            installation=installation_result(capability_status="not-requested"),
        )
        self.assertEqual(
            result["limitations"],
            [
                "phase5e.limit.execution-not-performed",
                "phase5e.limit.evidence-binding-not-implemented",
                "phase5e.limit.operator-freshness-not-verified",
                "phase5e.limit.runtime-capability-enforcement-not-implemented",
                "phase5e.limit.runtime-capability-not-requested",
            ],
        )
        self.assertEqual(result["reason_ids"], sorted(result["reason_ids"], key=result["reason_ids"].index))

    def test_semantically_equivalent_mappings_have_byte_equivalent_json(self):
        first = pipeline()
        values = {
            "trust_decision": trust_result(),
            "capability_decision": capability_result(),
            "recommendation_decision": recommendation_result(),
            "installation_decision": installation_result(),
        }
        second = evaluate_admission_pipeline(
            **{
                key: {inner_key: values[key][inner_key] for inner_key in reversed(tuple(values[key]))}
                for key in reversed(tuple(values))
            }
        )
        encode = lambda value: json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8") + b"\n"
        self.assertEqual(encode(first), encode(second))

    def test_result_is_metadata_only(self):
        sensitive = "TOP_SECRET " + "/" + "Users/private-user\nhttps://private.example token=abc"
        values = {
            "trust_decision": trust_result(),
            "capability_decision": capability_result(),
            "recommendation_decision": recommendation_result(),
            "installation_decision": installation_result(),
        }
        values["trust_decision"]["skill_id"] = sensitive
        values["capability_decision"]["profile_id"] = sensitive
        values["recommendation_decision"]["reasons"] = ["recommendation.admission.recommendable"]
        values["installation_decision"]["reason_ids"] = [
            "installation.authorization.operator-granted",
            "installation.authorization.authorized",
        ]
        result = evaluate_admission_pipeline(**values)
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn("TOP_SECRET", serialized)
        self.assertNotIn("/Users/", serialized)
        self.assertNotIn("https://", serialized)
        self.assertNotIn("private-user", serialized)
        self.assertNotIn("token=", serialized)

    def test_no_external_effects_or_upstream_evaluator_calls(self):
        with patch("subprocess.run", side_effect=AssertionError("subprocess used")), patch(
            "urllib.request.urlopen", side_effect=AssertionError("network used")
        ), patch("pathlib.Path.open", side_effect=AssertionError("filesystem used")):
            result = pipeline()
        self.assertEqual(result["overall_status"], "admissible")


if __name__ == "__main__":
    unittest.main()

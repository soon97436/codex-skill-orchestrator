"""Pure deterministic installation and activation authorization decisions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, List, Optional

from .capability_policy import (
    CAPABILITY_FAMILIES,
    CAPABILITY_REASON_IDS,
    DECISION_STATUSES,
    OVERALL_STATUSES,
    TRUST_STATUSES,
)
from .recommendation_admission import ADMISSION_REASON_IDS


OPERATIONS = ("install", "activate")
OPERATOR_AUTHORIZATION_STATES = ("granted", "denied", "not-provided")
RESULT_STATUSES = ("authorized", "rejected", "unknown", "invalid")

REASON_IDS = (
    "installation.authorization.operation-invalid",
    "installation.authorization.operator-invalid",
    "installation.authorization.recommendation-invalid",
    "installation.authorization.recommendation-rejected",
    "installation.authorization.recommendation-unknown",
    "installation.authorization.operator-required",
    "installation.authorization.operator-denied",
    "installation.authorization.operator-granted",
    "installation.authorization.authorized",
    "installation.authorization.rejected",
    "installation.authorization.unknown",
    "installation.authorization.invalid",
)

LIMITATION_IDS = (
    "installation.limit.execution-not-performed",
    "installation.limit.destination-validation-not-performed",
    "installation.limit.os-permission-not-granted",
    "installation.limit.runtime-capability-not-authorized",
    "installation.limit.activation-not-performed",
    "installation.limit.skill-capability-not-requested",
)

_ASSESSMENT_SCOPE = "installation-authorization-only"
_RECOMMENDATION_SCOPE = "recommendation-admission-only"
_RECOMMENDATION_KEYS = {
    "schema_version",
    "status",
    "assessment_scope",
    "registry_membership",
    "trust_status",
    "capability_status",
    "decisions",
    "reasons",
    "limitations",
    "truncated",
}
_DECISION_KEYS = {"capability", "status", "reason_ids"}
_RECOMMENDATION_LIMITATIONS = {
    "recommendation.limit.capability-authorization-not-granted",
    "recommendation.limit.installation-not-authorized",
    "recommendation.limit.runtime-capability-not-authorized",
}
_DECISION_REASON_IDS = set(CAPABILITY_REASON_IDS) | set(ADMISSION_REASON_IDS)
_BASE_LIMITATIONS = (
    "installation.limit.execution-not-performed",
    "installation.limit.destination-validation-not-performed",
    "installation.limit.os-permission-not-granted",
    "installation.limit.runtime-capability-not-authorized",
)

_REASON_ORDER = {reason_id: index for index, reason_id in enumerate(REASON_IDS)}


def _valid_string(value: Any, allowed: tuple) -> bool:
    return type(value) is str and value in allowed


def _valid_id_list(value: Any, allowed: set) -> bool:
    try:
        return (
            type(value) is list
            and len(value) <= 64
            and len(set(value)) == len(value)
            and all(type(item) is str and item in allowed for item in value)
        )
    except (TypeError, ValueError):
        return False


def _valid_recommendation_limitations(value: Any, status: str, capability_status: str) -> bool:
    if type(value) is not list or len(value) > 3:
        return False
    expected = [
        "recommendation.limit.installation-not-authorized",
        "recommendation.limit.runtime-capability-not-authorized",
    ]
    if status == "recommendable" and capability_status == "not-requested":
        expected.insert(0, "recommendation.limit.capability-authorization-not-granted")
    return value == expected and set(value).issubset(_RECOMMENDATION_LIMITATIONS)


def _validate_recommendation(value: Any) -> Optional[Dict[str, Any]]:
    """Validate and reduce an Increment 2 result to safe structural facts."""

    if not isinstance(value, Mapping):
        return None
    try:
        if set(value) != _RECOMMENDATION_KEYS:
            return None
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            return None
        if value["assessment_scope"] != _RECOMMENDATION_SCOPE:
            return None
        if type(value["registry_membership"]) is not bool:
            return None
        trust_status = value["trust_status"]
        if trust_status not in TRUST_STATUSES:
            return None
        capability_status = value["capability_status"]
        if capability_status not in OVERALL_STATUSES:
            return None
        status = value["status"]
        if status not in {"recommendable", "rejected", "unknown", "invalid"}:
            return None
        if type(value["truncated"]) is not bool or value["truncated"]:
            return None
        if not _valid_id_list(value["reasons"], set(ADMISSION_REASON_IDS)):
            return None
        if not _valid_recommendation_limitations(
            value["limitations"], status, capability_status
        ):
            return None

        decisions = value["decisions"]
        if type(decisions) is not list or len(decisions) != len(CAPABILITY_FAMILIES):
            return None
        safe_decisions: List[Dict[str, Any]] = []
        decision_statuses: List[str] = []
        for expected_family, decision in zip(CAPABILITY_FAMILIES, decisions):
            if not isinstance(decision, Mapping) or set(decision) != _DECISION_KEYS:
                return None
            if decision["capability"] != expected_family:
                return None
            decision_status = decision["status"]
            if decision_status not in DECISION_STATUSES:
                return None
            if not _valid_id_list(decision["reason_ids"], _DECISION_REASON_IDS):
                return None
            decision_statuses.append(decision_status)
            safe_decisions.append(
                {
                    "capability": expected_family,
                    "status": decision_status,
                    "reason_ids": list(decision["reason_ids"]),
                }
            )

        if status == "recommendable":
            if value["registry_membership"] is not True:
                return None
            if trust_status != "admissible":
                return None
            if capability_status not in {"admissible", "not-requested"}:
                return None
            if capability_status == "not-requested":
                if any(decision_status != "not-requested" for decision_status in decision_statuses):
                    return None
            elif (
                "allowed" not in decision_statuses
                or any(status_value in {"denied", "unknown"} for status_value in decision_statuses)
            ):
                return None
        return {
            "status": status,
            "capability_status": capability_status,
            "decisions": safe_decisions,
        }
    except (KeyError, TypeError, ValueError):
        return None


def _safe_operator(value: Any) -> str:
    return value if _valid_string(value, OPERATOR_AUTHORIZATION_STATES) else "invalid"


def _safe_recommendation_status(value: Optional[Dict[str, Any]]) -> str:
    return value["status"] if value is not None else "invalid"


def _safe_capability_status(value: Optional[Dict[str, Any]]) -> str:
    return value["capability_status"] if value is not None else "invalid"


def _result(
    *,
    status: str,
    operation: str,
    operator_authorization: str,
    recommendation_status: str,
    capability_status: str,
    reasons: List[str],
    capability_not_requested: bool = False,
) -> Dict[str, Any]:
    limitations = list(_BASE_LIMITATIONS)
    if capability_not_requested:
        limitations.append("installation.limit.skill-capability-not-requested")
    if operation == "activate":
        limitations.append("installation.limit.activation-not-performed")
    ordered_reasons = sorted(
        set(reasons),
        key=lambda reason_id: _REASON_ORDER.get(reason_id, len(_REASON_ORDER)),
    )
    return {
        "schema_version": 1,
        "status": status,
        "assessment_scope": _ASSESSMENT_SCOPE,
        "operation": operation,
        "operator_authorization": operator_authorization,
        "recommendation_status": recommendation_status,
        "capability_status": capability_status,
        "reason_ids": ordered_reasons,
        "limitations": limitations,
        "truncated": False,
    }


def evaluate_installation_authorization(
    *,
    operation: str,
    operator_authorization: str,
    recommendation_decision: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return a bounded authorization decision without executing anything."""

    operation_valid = _valid_string(operation, OPERATIONS)
    operator_valid = _valid_string(operator_authorization, OPERATOR_AUTHORIZATION_STATES)
    validated = _validate_recommendation(recommendation_decision)
    safe_operation = operation if operation_valid else "invalid"
    safe_operator = operator_authorization if operator_valid else "invalid"
    recommendation_status = _safe_recommendation_status(validated)
    capability_status = _safe_capability_status(validated)

    reasons: List[str] = []
    if not operation_valid:
        reasons.append("installation.authorization.operation-invalid")
    if not operator_valid:
        reasons.append("installation.authorization.operator-invalid")
    if validated is None:
        reasons.append("installation.authorization.recommendation-invalid")

    if reasons:
        reasons.append("installation.authorization.invalid")
        return _result(
            status="invalid",
            operation=safe_operation,
            operator_authorization=safe_operator,
            recommendation_status=recommendation_status,
            capability_status=capability_status,
            reasons=reasons,
        )

    assert validated is not None
    recommendation_status = validated["status"]
    capability_status = validated["capability_status"]
    if recommendation_status == "invalid":
        reasons.append("installation.authorization.recommendation-invalid")
        reasons.append("installation.authorization.invalid")
        return _result(
            status="invalid",
            operation=safe_operation,
            operator_authorization=safe_operator,
            recommendation_status=recommendation_status,
            capability_status=capability_status,
            reasons=reasons,
        )
    if recommendation_status == "rejected":
        reasons.append("installation.authorization.recommendation-rejected")
        reasons.append("installation.authorization.rejected")
        return _result(
            status="rejected",
            operation=safe_operation,
            operator_authorization=safe_operator,
            recommendation_status=recommendation_status,
            capability_status=capability_status,
            reasons=reasons,
        )
    if recommendation_status == "unknown":
        reasons.append("installation.authorization.recommendation-unknown")
        reasons.append("installation.authorization.unknown")
        return _result(
            status="unknown",
            operation=safe_operation,
            operator_authorization=safe_operator,
            recommendation_status=recommendation_status,
            capability_status=capability_status,
            reasons=reasons,
        )

    if safe_operator == "denied":
        reasons.extend(
            [
                "installation.authorization.operator-denied",
                "installation.authorization.rejected",
            ]
        )
        return _result(
            status="rejected",
            operation=safe_operation,
            operator_authorization=safe_operator,
            recommendation_status=recommendation_status,
            capability_status=capability_status,
            reasons=reasons,
        )
    if safe_operator == "not-provided":
        reasons.extend(
            [
                "installation.authorization.operator-required",
                "installation.authorization.unknown",
            ]
        )
        return _result(
            status="unknown",
            operation=safe_operation,
            operator_authorization=safe_operator,
            recommendation_status=recommendation_status,
            capability_status=capability_status,
            reasons=reasons,
        )

    reasons.extend(
        [
            "installation.authorization.operator-granted",
            "installation.authorization.authorized",
        ]
    )
    return _result(
        status="authorized",
        operation=safe_operation,
        operator_authorization=safe_operator,
        recommendation_status=recommendation_status,
        capability_status=capability_status,
        reasons=reasons,
        capability_not_requested=capability_status == "not-requested",
    )


__all__ = [
    "LIMITATION_IDS",
    "OPERATIONS",
    "OPERATOR_AUTHORIZATION_STATES",
    "REASON_IDS",
    "RESULT_STATUSES",
    "evaluate_installation_authorization",
]

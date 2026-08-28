"""Pure deterministic recommendation-admission decisions."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Dict, List, Optional, Tuple

from .capability_policy import (
    CAPABILITY_FAMILIES,
    CAPABILITY_REASON_IDS,
    DECISION_STATUSES,
    OVERALL_STATUSES,
    TRUST_STATUSES,
)


ADMISSION_REASON_IDS = (
    "recommendation.admission.registry-absent",
    "recommendation.admission.registry-invalid",
    "recommendation.admission.trust-required",
    "recommendation.admission.trust-rejected",
    "recommendation.admission.trust-unknown",
    "recommendation.admission.trust-mismatch",
    "recommendation.admission.capability-invalid",
    "recommendation.admission.capability-rejected",
    "recommendation.admission.capability-unknown",
    "recommendation.admission.capability-not-requested",
    "recommendation.admission.recommendable",
    "recommendation.admission.invalid",
)

LIMITATION_IDS = (
    "recommendation.limit.installation-not-authorized",
    "recommendation.limit.runtime-capability-not-authorized",
)

_CAPABILITY_NOT_GRANTED = "recommendation.limit.capability-authorization-not-granted"
_CAPABILITY_RESULT_KEYS = {
    "schema_version",
    "status",
    "assessment_scope",
    "trust_status",
    "profile_id",
    "decisions",
    "reasons",
    "limitations",
    "truncated",
}
_DECISION_KEYS = {"capability", "status", "reason_ids"}
_CAPABILITY_LIMITATION_IDS = {"capability.limit.enforcement-not-implemented"}
_PROFILE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", flags=re.ASCII)

_REASON_ORDER = {
    reason_id: index
    for index, reason_id in enumerate(
        (
            "recommendation.admission.registry-invalid",
            "recommendation.admission.registry-absent",
            "recommendation.admission.trust-mismatch",
            "recommendation.admission.capability-invalid",
            "recommendation.admission.trust-rejected",
            "recommendation.admission.trust-required",
            "recommendation.admission.trust-unknown",
            "recommendation.admission.capability-rejected",
            "recommendation.admission.capability-unknown",
            "recommendation.admission.capability-not-requested",
            "recommendation.admission.recommendable",
            "recommendation.admission.invalid",
        )
    )
}


def _result(
    *,
    status: str,
    registry_membership: bool,
    trust_status: str,
    capability_status: str,
    reasons: List[str],
    decisions: Optional[List[Dict[str, Any]]] = None,
    not_requested: bool = False,
) -> Dict[str, Any]:
    limitation_ids = list(LIMITATION_IDS)
    if not_requested:
        limitation_ids.insert(0, _CAPABILITY_NOT_GRANTED)
    ordered_reasons = sorted(
        set(reasons),
        key=lambda reason_id: _REASON_ORDER.get(reason_id, len(_REASON_ORDER)),
    )
    if decisions is None:
        decisions = [
            {
                "capability": capability,
                "status": "unknown",
                "reason_ids": ["recommendation.admission.invalid"],
            }
            for capability in CAPABILITY_FAMILIES
        ]
    return {
        "schema_version": 1,
        "status": status,
        "assessment_scope": "recommendation-admission-only",
        "registry_membership": registry_membership,
        "trust_status": trust_status,
        "capability_status": capability_status,
        "decisions": decisions,
        "reasons": ordered_reasons,
        "limitations": limitation_ids,
        "truncated": False,
    }


def _invalid_result(
    *,
    registry_membership: bool,
    trust_status: str,
    capability_status: str,
    reason_ids: Optional[List[str]] = None,
    decisions: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    reasons = list(reason_ids or [])
    reasons.append("recommendation.admission.invalid")
    return _result(
        status="invalid",
        registry_membership=registry_membership,
        trust_status=trust_status,
        capability_status=capability_status,
        reasons=reasons,
        decisions=decisions,
    )


def _safe_trust_status(value: Any) -> str:
    return value if value in TRUST_STATUSES else "unknown"


def _safe_capability_status(value: Any) -> str:
    return value if value in OVERALL_STATUSES else "invalid"


def _is_safe_profile_id(value: Any) -> bool:
    return (
        type(value) is str
        and bool(value)
        and len(value) <= 64
        and _PROFILE_ID_RE.fullmatch(value) is not None
    )


def _valid_reason_list(value: Any) -> bool:
    return (
        type(value) is list
        and len(value) <= 64
        and len(set(value)) == len(value)
        and all(type(reason_id) is str and reason_id in CAPABILITY_REASON_IDS for reason_id in value)
    )


def _valid_limitation_list(value: Any) -> bool:
    return (
        type(value) is list
        and len(value) <= 64
        and len(set(value)) == len(value)
        and all(
            type(limitation) is str and limitation in _CAPABILITY_LIMITATION_IDS
            for limitation in value
        )
    )


def _validate_capability_decision(value: Any) -> Optional[Dict[str, Any]]:
    """Validate only the normalized, content-free capability result shape."""

    if not isinstance(value, Mapping):
        return None
    try:
        if set(value) != _CAPABILITY_RESULT_KEYS:
            return None
        if type(value["schema_version"]) is not int or value["schema_version"] != 1:
            return None
        if value["assessment_scope"] != "capability-policy-only":
            return None
        if value["trust_status"] not in TRUST_STATUSES:
            return None
        if not _is_safe_profile_id(value["profile_id"]):
            return None
        status = value["status"]
        if status not in OVERALL_STATUSES:
            return None
        if type(value["truncated"]) is not bool or value["truncated"]:
            return None
        if not _valid_limitation_list(value["limitations"]):
            return None
        if not _valid_reason_list(value["reasons"]):
            return None

        decisions = value["decisions"]
        if type(decisions) is not list or len(decisions) != len(CAPABILITY_FAMILIES):
            return None
        decision_statuses: List[str] = []
        for expected_family, decision in zip(CAPABILITY_FAMILIES, decisions):
            if not isinstance(decision, Mapping) or set(decision) != _DECISION_KEYS:
                return None
            if decision["capability"] != expected_family:
                return None
            decision_status = decision["status"]
            if decision_status not in DECISION_STATUSES:
                return None
            if not _valid_reason_list(decision["reason_ids"]):
                return None
            decision_statuses.append(decision_status)

        if status == "not-requested":
            if any(decision_status != "not-requested" for decision_status in decision_statuses):
                return None
        elif status == "admissible":
            if value["trust_status"] != "admissible":
                return None
            if "allowed" not in decision_statuses:
                return None
            if any(decision_status in {"denied", "unknown"} for decision_status in decision_statuses):
                return None
        elif status == "unknown":
            if "unknown" not in decision_statuses or "allowed" in decision_statuses:
                return None
        elif status == "invalid":
            if "allowed" in decision_statuses:
                return None
        elif status == "rejected":
            if "denied" not in decision_statuses or "allowed" in decision_statuses:
                return None
        return {
            "status": status,
            "trust_status": value["trust_status"],
            "decisions": [
                {
                    "capability": decision["capability"],
                    "status": decision["status"],
                    "reason_ids": list(decision["reason_ids"]),
                }
                for decision in decisions
            ],
        }
    except (KeyError, TypeError, ValueError):
        return None


def evaluate_recommendation_admission(
    *,
    registry_membership: bool,
    trust_status: str,
    capability_decision: Mapping[str, Any],
) -> Dict[str, Any]:
    """Return a bounded recommendation-admission decision.

    This seam consumes normalized upstream results only. It never loads the
    registry, grants capability permissions, installs a skill, or executes it.
    """

    if type(registry_membership) is not bool:
        return _invalid_result(
            registry_membership=False,
            trust_status=_safe_trust_status(trust_status),
            capability_status="invalid",
            reason_ids=["recommendation.admission.registry-invalid"],
        )
    if trust_status not in TRUST_STATUSES:
        return _invalid_result(
            registry_membership=registry_membership,
            trust_status="unknown",
            capability_status="invalid",
        )

    validated = _validate_capability_decision(capability_decision)
    if validated is None:
        return _invalid_result(
            registry_membership=registry_membership,
            trust_status=trust_status,
            capability_status="invalid",
            reason_ids=["recommendation.admission.capability-invalid"],
        )

    capability_status = validated["status"]
    if validated["trust_status"] != trust_status:
        return _invalid_result(
            registry_membership=registry_membership,
            trust_status=trust_status,
            capability_status=capability_status,
            reason_ids=["recommendation.admission.trust-mismatch"],
        )

    if not registry_membership:
        return _result(
            status="rejected",
            registry_membership=False,
            trust_status=trust_status,
            capability_status=capability_status,
            reasons=["recommendation.admission.registry-absent"],
            decisions=validated["decisions"],
        )

    reasons: List[str] = []
    if trust_status == "rejected":
        reasons.append("recommendation.admission.trust-rejected")
    elif trust_status == "not-evaluated":
        reasons.append("recommendation.admission.trust-required")
    elif trust_status == "unknown":
        reasons.append("recommendation.admission.trust-unknown")

    if capability_status == "invalid":
        reasons.append("recommendation.admission.capability-invalid")
        return _result(
            status="invalid",
            registry_membership=True,
            trust_status=trust_status,
            capability_status=capability_status,
            reasons=reasons,
            decisions=validated["decisions"],
        )
    if capability_status == "rejected":
        reasons.append("recommendation.admission.capability-rejected")
    elif capability_status == "unknown":
        reasons.append("recommendation.admission.capability-unknown")
    elif capability_status == "not-requested":
        reasons.append("recommendation.admission.capability-not-requested")

    if trust_status == "rejected" or capability_status == "rejected":
        status = "rejected"
    elif trust_status != "admissible" or capability_status == "unknown":
        status = "unknown"
    else:
        status = "recommendable"
        reasons.append("recommendation.admission.recommendable")

    return _result(
        status=status,
        registry_membership=True,
        trust_status=trust_status,
        capability_status=capability_status,
        reasons=reasons,
        decisions=validated["decisions"],
        not_requested=capability_status == "not-requested" and status == "recommendable",
    )


__all__ = [
    "ADMISSION_REASON_IDS",
    "LIMITATION_IDS",
    "evaluate_recommendation_admission",
]

"""Pure deterministic composition of normalized Phase 5 admission decisions."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Dict, Iterable, List, Optional, Sequence

from .capability_policy import (
    CAPABILITY_FAMILIES,
    CAPABILITY_REASON_IDS,
    DECISION_STATUSES as CAPABILITY_DECISION_STATUSES,
    OVERALL_STATUSES as CAPABILITY_STATUSES,
    TRUST_STATUSES,
)
from .installation_authorization import (
    OPERATIONS,
    OPERATOR_AUTHORIZATION_STATES,
    REASON_IDS as INSTALLATION_REASON_IDS,
    RESULT_STATUSES as INSTALLATION_STATUSES,
)
from .recommendation_admission import (
    ADMISSION_REASON_IDS,
)
from .registry_trust import (
    ADMISSION_STATUSES as TRUST_STATUSES_FROM_RESULT,
    DECISION_STATUSES as TRUST_DECISION_STATUSES,
    TRUST_DIMENSIONS,
    TRUST_REASON_IDS,
)


STAGE_ORDER = (
    "registry-trust",
    "capability-policy",
    "recommendation-admission",
    "installation-authorization",
)
ASSESSMENT_SCOPE = "phase5e-admission-pipeline"
PIPELINE_STATUSES = ("admissible", "rejected", "unknown", "invalid")
RECOMMENDATION_STATUSES = ("recommendable", "rejected", "unknown", "invalid")

REASON_IDS = (
    "phase5e.input.invalid",
    "phase5e.evidence.mismatch",
    "phase5e.upstream.rejected",
    "phase5e.upstream.unknown",
    "phase5e.pipeline.invalid",
    "phase5e.pipeline.rejected",
    "phase5e.pipeline.unknown",
    "phase5e.pipeline.admissible",
    "phase5e.execution.not-performed",
)

LIMITATION_IDS = (
    "phase5e.limit.execution-not-performed",
    "phase5e.limit.evidence-binding-not-implemented",
    "phase5e.limit.operator-freshness-not-verified",
    "phase5e.limit.runtime-capability-enforcement-not-implemented",
    "phase5e.limit.runtime-capability-not-requested",
)

_BASE_LIMITATIONS = LIMITATION_IDS[:4]
_PROFILE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", flags=re.ASCII)
_SKILL_ID_RE = _PROFILE_ID_RE
_REASON_ORDER = {reason_id: index for index, reason_id in enumerate(REASON_IDS)}
_TRUST_RESULT_KEYS = {
    "schema_version",
    "status",
    "skill_id",
    "decisions",
    "reasons",
    "limitations",
    "truncated",
}
_TRUST_DECISION_KEYS = {"dimension", "status", "reason_ids"}
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
_CAPABILITY_DECISION_KEYS = {"capability", "status", "reason_ids"}
_RECOMMENDATION_RESULT_KEYS = {
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
_INSTALLATION_RESULT_KEYS = {
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
}


def _exact_keys(value: Mapping[str, Any], expected: set) -> bool:
    try:
        return set(value) == expected
    except Exception:
        return False


def _valid_reason_list(
    value: Any,
    allowed: Iterable[str],
    *,
    maximum: int = 64,
    minimum: int = 1,
) -> bool:
    if type(value) is not list or len(value) < minimum or len(value) > maximum:
        return False
    if any(type(item) is not str for item in value):
        return False
    if len(set(value)) != len(value):
        return False
    allowed_set = set(allowed)
    return all(item in allowed_set for item in value)


def _valid_id_list(
    value: Any,
    allowed: Iterable[str],
    *,
    maximum: int = 64,
    minimum: int = 1,
) -> bool:
    return _valid_reason_list(value, allowed, maximum=maximum, minimum=minimum)


def _valid_identifier(
    value: Any, pattern: re.Pattern, *, maximum: Optional[int] = None
) -> bool:
    return (
        type(value) is str
        and bool(value)
        and (maximum is None or len(value) <= maximum)
        and pattern.fullmatch(value) is not None
    )


def _valid_schema_and_truncation(value: Mapping[str, Any]) -> bool:
    return (
        type(value.get("schema_version")) is int
        and value.get("schema_version") == 1
        and type(value.get("truncated")) is bool
        and value.get("truncated") is False
    )


def _validate_trust(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, Mapping) or not _exact_keys(value, _TRUST_RESULT_KEYS):
        return None
    try:
        if not _valid_schema_and_truncation(value):
            return None
        status = value["status"]
        if status not in TRUST_STATUSES_FROM_RESULT:
            return None
        if not _valid_identifier(value["skill_id"], _SKILL_ID_RE):
            return None
        decisions = value["decisions"]
        if type(decisions) is not list or len(decisions) != len(TRUST_DIMENSIONS):
            return None
        for expected_dimension, decision in zip(TRUST_DIMENSIONS, decisions):
            if not isinstance(decision, Mapping) or not _exact_keys(
                decision, _TRUST_DECISION_KEYS
            ):
                return None
            if decision["dimension"] != expected_dimension:
                return None
            if decision["status"] not in TRUST_DECISION_STATUSES:
                return None
            if not _valid_id_list(decision["reason_ids"], TRUST_REASON_IDS):
                return None
        if not _valid_id_list(value["reasons"], TRUST_REASON_IDS):
            return None
        expected_admission_reason = {
            "admissible": "trust.admission.admissible",
            "rejected": "trust.admission.rejected",
            "unknown": "trust.admission.unknown",
        }[status]
        if expected_admission_reason not in value["reasons"]:
            return None
        limitations = value["limitations"]
        if limitations not in (
            ["trust.limit.capability-enforcement-not-implemented"],
            [
                "trust.limit.remote-fetch-disabled",
                "trust.limit.capability-enforcement-not-implemented",
            ],
        ):
            return None
        return {"status": status}
    except Exception:
        return None


def _validate_capability(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, Mapping) or not _exact_keys(value, _CAPABILITY_RESULT_KEYS):
        return None
    try:
        if not _valid_schema_and_truncation(value):
            return None
        if value["assessment_scope"] != "capability-policy-only":
            return None
        trust_status = value["trust_status"]
        if trust_status not in TRUST_STATUSES:
            return None
        if not _valid_identifier(value["profile_id"], _PROFILE_ID_RE, maximum=64):
            return None
        status = value["status"]
        if status not in CAPABILITY_STATUSES:
            return None
        if value["limitations"] != ["capability.limit.enforcement-not-implemented"]:
            return None
        if not _valid_id_list(value["reasons"], CAPABILITY_REASON_IDS):
            return None
        decisions = value["decisions"]
        if type(decisions) is not list or len(decisions) != len(CAPABILITY_FAMILIES):
            return None
        decision_statuses = []
        for expected_family, decision in zip(CAPABILITY_FAMILIES, decisions):
            if not isinstance(decision, Mapping) or not _exact_keys(
                decision, _CAPABILITY_DECISION_KEYS
            ):
                return None
            if decision["capability"] != expected_family:
                return None
            decision_status = decision["status"]
            if decision_status not in CAPABILITY_DECISION_STATUSES:
                return None
            if not _valid_id_list(decision["reason_ids"], CAPABILITY_REASON_IDS):
                return None
            decision_statuses.append(decision_status)

        if status == "not-requested":
            if any(item != "not-requested" for item in decision_statuses):
                return None
        elif status == "admissible":
            if trust_status != "admissible" or "allowed" not in decision_statuses:
                return None
            if any(item in {"denied", "unknown"} for item in decision_statuses):
                return None
        elif status == "rejected":
            if "denied" not in decision_statuses or "allowed" in decision_statuses:
                return None
        elif status == "unknown":
            if "unknown" not in decision_statuses or "allowed" in decision_statuses:
                return None
        elif "allowed" in decision_statuses:
            return None
        return {"status": status, "trust_status": trust_status}
    except Exception:
        return None


def _recommendation_limitations(status: str, capability_status: str) -> List[str]:
    expected = [
        "recommendation.limit.installation-not-authorized",
        "recommendation.limit.runtime-capability-not-authorized",
    ]
    if status == "recommendable" and capability_status == "not-requested":
        expected.insert(0, "recommendation.limit.capability-authorization-not-granted")
    return expected


def _validate_recommendation(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, Mapping) or not _exact_keys(
        value, _RECOMMENDATION_RESULT_KEYS
    ):
        return None
    try:
        if not _valid_schema_and_truncation(value):
            return None
        if value["assessment_scope"] != "recommendation-admission-only":
            return None
        if type(value["registry_membership"]) is not bool:
            return None
        trust_status = value["trust_status"]
        capability_status = value["capability_status"]
        status = value["status"]
        if trust_status not in TRUST_STATUSES:
            return None
        if capability_status not in CAPABILITY_STATUSES:
            return None
        if status not in RECOMMENDATION_STATUSES:
            return None
        if value["limitations"] != _recommendation_limitations(status, capability_status):
            return None
        if not _valid_id_list(value["reasons"], ADMISSION_REASON_IDS):
            return None
        decisions = value["decisions"]
        if type(decisions) is not list or len(decisions) != len(CAPABILITY_FAMILIES):
            return None
        decision_statuses = []
        allowed_decision_reasons = set(CAPABILITY_REASON_IDS) | set(ADMISSION_REASON_IDS)
        for expected_family, decision in zip(CAPABILITY_FAMILIES, decisions):
            if not isinstance(decision, Mapping) or not _exact_keys(
                decision, {"capability", "status", "reason_ids"}
            ):
                return None
            if decision["capability"] != expected_family:
                return None
            decision_status = decision["status"]
            if decision_status not in CAPABILITY_DECISION_STATUSES:
                return None
            if not _valid_id_list(decision["reason_ids"], allowed_decision_reasons):
                return None
            decision_statuses.append(decision_status)

        if status == "recommendable":
            if not value["registry_membership"]:
                return None
            if trust_status != "admissible":
                return None
            if capability_status not in {"admissible", "not-requested"}:
                return None
            if capability_status == "not-requested":
                if any(item != "not-requested" for item in decision_statuses):
                    return None
            elif "allowed" not in decision_statuses or any(
                item in {"denied", "unknown"} for item in decision_statuses
            ):
                return None
        elif status == "unknown":
            if trust_status == "admissible" and capability_status not in {
                "unknown",
                "not-requested",
            }:
                return None
        elif status == "invalid":
            if capability_status != "invalid":
                return None
        return {
            "status": status,
            "trust_status": trust_status,
            "capability_status": capability_status,
        }
    except Exception:
        return None


def _installation_limitations(operation: str, capability_status: str) -> List[str]:
    expected = [
        "installation.limit.execution-not-performed",
        "installation.limit.destination-validation-not-performed",
        "installation.limit.os-permission-not-granted",
        "installation.limit.runtime-capability-not-authorized",
    ]
    if capability_status == "not-requested":
        expected.append("installation.limit.skill-capability-not-requested")
    if operation == "activate":
        expected.append("installation.limit.activation-not-performed")
    return expected


def _validate_installation(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, Mapping) or not _exact_keys(
        value, _INSTALLATION_RESULT_KEYS
    ):
        return None
    try:
        if not _valid_schema_and_truncation(value):
            return None
        if value["assessment_scope"] != "installation-authorization-only":
            return None
        operation = value["operation"]
        operator_authorization = value["operator_authorization"]
        recommendation_status = value["recommendation_status"]
        capability_status = value["capability_status"]
        status = value["status"]
        if operation not in OPERATIONS:
            return None
        if operator_authorization not in OPERATOR_AUTHORIZATION_STATES:
            return None
        if recommendation_status not in RECOMMENDATION_STATUSES:
            return None
        if capability_status not in CAPABILITY_STATUSES:
            return None
        if status not in INSTALLATION_STATUSES:
            return None
        if value["limitations"] != _installation_limitations(operation, capability_status):
            return None
        if not _valid_id_list(value["reason_ids"], INSTALLATION_REASON_IDS):
            return None

        if status == "authorized":
            if (
                operator_authorization != "granted"
                or recommendation_status != "recommendable"
                or capability_status not in {"admissible", "not-requested"}
            ):
                return None
        elif status == "rejected":
            if operator_authorization != "denied" and recommendation_status != "rejected":
                return None
        elif status == "unknown":
            if operator_authorization != "not-provided" and recommendation_status != "unknown":
                return None
        return {
            "status": status,
            "operator_authorization": operator_authorization,
            "recommendation_status": recommendation_status,
            "capability_status": capability_status,
        }
    except Exception:
        return None


def _stage(stage: str, status: str, *, invalid: bool = False) -> Dict[str, Any]:
    if invalid:
        reason_ids = ["phase5e.input.invalid"]
    elif status == "rejected":
        reason_ids = ["phase5e.upstream.rejected"]
    elif status == "unknown":
        reason_ids = ["phase5e.upstream.unknown"]
    else:
        reason_ids = []
    return {"stage": stage, "status": status, "reason_ids": reason_ids}


def _safe_status(value: Optional[Dict[str, Any]], key: str, fallback: str) -> str:
    if value is None:
        return fallback
    return value[key]


def _ordered_reasons(reason_ids: Sequence[str]) -> List[str]:
    unique = []
    for reason_id in reason_ids:
        if reason_id in REASON_IDS and reason_id not in unique:
            unique.append(reason_id)
    return sorted(unique, key=_REASON_ORDER.__getitem__)


def _result(
    *,
    overall_status: str,
    stages: List[Dict[str, Any]],
    reasons: Sequence[str],
    capability_not_requested: bool,
) -> Dict[str, Any]:
    limitations = list(_BASE_LIMITATIONS)
    if capability_not_requested:
        limitations.append("phase5e.limit.runtime-capability-not-requested")
    status_reason = {
        "admissible": "phase5e.pipeline.admissible",
        "rejected": "phase5e.pipeline.rejected",
        "unknown": "phase5e.pipeline.unknown",
        "invalid": "phase5e.pipeline.invalid",
    }[overall_status]
    ordered_reasons = _ordered_reasons(list(reasons) + [status_reason, "phase5e.execution.not-performed"])
    return {
        "schema_version": 1,
        "overall_status": overall_status,
        "assessment_scope": ASSESSMENT_SCOPE,
        "stages": stages,
        "execution_status": "not-performed",
        "reason_ids": ordered_reasons,
        "limitations": limitations,
        "truncated": False,
    }


def evaluate_admission_pipeline(
    *,
    trust_decision: Mapping[str, Any],
    capability_decision: Mapping[str, Any],
    recommendation_decision: Mapping[str, Any],
    installation_decision: Mapping[str, Any],
) -> Dict[str, Any]:
    """Validate and compose normalized Phase 5 decisions.

    This facade intentionally does not call upstream evaluators or load any
    data.  It only validates caller-supplied metadata and reports the
    deterministic policy/admission state; no execution is performed.
    """

    trust = _validate_trust(trust_decision)
    capability = _validate_capability(capability_decision)
    recommendation = _validate_recommendation(recommendation_decision)
    installation = _validate_installation(installation_decision)

    stages = [
        _stage(
            "registry-trust",
            _safe_status(trust, "status", "invalid"),
            invalid=trust is None,
        ),
        _stage(
            "capability-policy",
            _safe_status(capability, "status", "invalid"),
            invalid=capability is None,
        ),
        _stage(
            "recommendation-admission",
            _safe_status(recommendation, "status", "invalid"),
            invalid=recommendation is None,
        ),
        _stage(
            "installation-authorization",
            _safe_status(installation, "status", "invalid"),
            invalid=installation is None,
        ),
    ]

    invalid_input = any(item is None for item in (trust, capability, recommendation, installation))
    mismatch = False
    if not invalid_input:
        assert trust is not None
        assert capability is not None
        assert recommendation is not None
        assert installation is not None
        trust_status = trust["status"]
        capability_status = capability["status"]
        recommendation_status = recommendation["status"]
        if capability["trust_status"] != trust_status:
            mismatch = True
        if recommendation["trust_status"] != trust_status:
            mismatch = True
        if recommendation["capability_status"] != capability_status:
            mismatch = True
        if installation["recommendation_status"] != recommendation_status:
            mismatch = True
        if installation["capability_status"] != recommendation["capability_status"]:
            mismatch = True

        if trust_status != "admissible" and (
            recommendation_status == "recommendable" or installation["status"] == "authorized"
        ):
            mismatch = True
        if capability_status in {"rejected", "unknown", "invalid"} and recommendation_status == "recommendable":
            mismatch = True
        if recommendation_status in {"rejected", "unknown", "invalid"} and installation["status"] == "authorized":
            mismatch = True
        if installation["status"] == "authorized" and (
            installation["operator_authorization"] != "granted"
            or recommendation_status != "recommendable"
            or trust_status != "admissible"
            or capability_status not in {"admissible", "not-requested"}
        ):
            mismatch = True

    if invalid_input:
        overall_status = "invalid"
    elif mismatch or any(stage["status"] == "invalid" for stage in stages):
        overall_status = "invalid"
    elif any(stage["status"] == "rejected" for stage in stages):
        overall_status = "rejected"
    elif any(stage["status"] == "unknown" for stage in stages):
        overall_status = "unknown"
    elif (
        trust is not None
        and capability is not None
        and recommendation is not None
        and installation is not None
        and trust["status"] == "admissible"
        and capability["status"] in {"admissible", "not-requested"}
        and recommendation["status"] == "recommendable"
        and installation["status"] == "authorized"
        and installation["operator_authorization"] == "granted"
    ):
        overall_status = "admissible"
    else:
        overall_status = "invalid"

    reasons: List[str] = []
    if invalid_input or any(stage["status"] == "invalid" for stage in stages):
        reasons.append("phase5e.input.invalid")
    if mismatch:
        reasons.append("phase5e.evidence.mismatch")
    if not invalid_input and not mismatch:
        if any(stage["status"] == "rejected" for stage in stages):
            reasons.append("phase5e.upstream.rejected")
        elif any(stage["status"] == "unknown" for stage in stages):
            reasons.append("phase5e.upstream.unknown")
    capability_not_requested = (
        capability is not None and capability.get("status") == "not-requested"
    )
    return _result(
        overall_status=overall_status,
        stages=stages,
        reasons=reasons,
        capability_not_requested=capability_not_requested,
    )


__all__ = [
    "ASSESSMENT_SCOPE",
    "LIMITATION_IDS",
    "PIPELINE_STATUSES",
    "REASON_IDS",
    "STAGE_ORDER",
    "evaluate_admission_pipeline",
]

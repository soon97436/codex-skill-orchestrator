"""Pure deterministic handoff eligibility for a future execution adapter.

This module is deliberately limited to evaluating one invocation.  It does
not install, activate, resolve a destination, or perform any execution.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, Iterable, List, Optional

from .admission_binding import (
    TARGET_CLASSES,
    VERIFICATION_STATUSES,
    verify_admission_binding,
)


ASSESSMENT_SCOPE = "phase5e-execution-handoff"
HANDOFF_STATUSES = ("ready", "rejected", "unknown", "invalid")
STATUSES = HANDOFF_STATUSES
OPERATIONS = ("install", "activate")
FRESH_AUTHORIZATION_STATES = ("granted", "denied", "not-provided")
TARGET_CLASS = TARGET_CLASSES[0]

# These are recognized structural target labels from the management plane.  A
# handoff supports only TARGET_CLASS; the other labels are safe, fixed
# substitutions and therefore produce rejection rather than an invalid
# arbitrary value.  They are never interpreted as paths.
_RECOGNIZED_TARGET_CLASSES = (
    TARGET_CLASS,
    "cso-app",
    "router-profile",
    "workspace",
)

REASON_IDS = (
    "phase5e.handoff.input.invalid",
    "phase5e.handoff.binding.invalid",
    "phase5e.handoff.binding.stale",
    "phase5e.handoff.authorization.invalid",
    "phase5e.handoff.authorization.denied",
    "phase5e.handoff.authorization.required",
    "phase5e.handoff.operation.invalid",
    "phase5e.handoff.operation.mismatch",
    "phase5e.handoff.operation.activation-reserved",
    "phase5e.handoff.target.invalid",
    "phase5e.handoff.target.mismatch",
    "phase5e.handoff.ready",
    "phase5e.handoff.rejected",
    "phase5e.handoff.unknown",
    "phase5e.handoff.invalid",
)

LIMITATION_IDS = (
    "phase5e.handoff.limit.execution-not-performed",
    "phase5e.handoff.limit.fresh-authorization-not-independently-verified",
    "phase5e.handoff.limit.destination-validation-not-performed",
    "phase5e.handoff.limit.os-permission-not-granted",
    "phase5e.handoff.limit.runtime-capability-enforcement-not-implemented",
    "phase5e.handoff.limit.not-an-execution-token",
    "phase5e.handoff.limit.remote-fetch-disabled",
    "phase5e.handoff.limit.runtime-capability-not-requested",
    "phase5e.handoff.limit.activation-reserved",
)

_BASE_LIMITATIONS = list(LIMITATION_IDS[:7])
_REASON_ORDER = {reason_id: index for index, reason_id in enumerate(REASON_IDS)}


def _ordered_ids(values: Iterable[str]) -> List[str]:
    """Return unique known IDs in one stable order."""

    unique = {value for value in values if value in _REASON_ORDER}
    return sorted(unique, key=_REASON_ORDER.__getitem__)


def _safe_operation(value: Any) -> str:
    return value if type(value) is str and value in OPERATIONS else "invalid"


def _safe_target(value: Any) -> str:
    return (
        value
        if type(value) is str and value in _RECOGNIZED_TARGET_CLASSES
        else "invalid"
    )


def _limitations(
    stored_binding: Any,
    *,
    activation_reserved: bool = False,
) -> List[str]:
    result = list(_BASE_LIMITATIONS)
    if isinstance(stored_binding, Mapping):
        try:
            if "phase5e.binding.limit.runtime-capability-not-requested" in stored_binding.get(
                "limitations", []
            ):
                result.append("phase5e.handoff.limit.runtime-capability-not-requested")
        except Exception:
            pass
    if activation_reserved:
        result.append("phase5e.handoff.limit.activation-reserved")
    return result


def _result(
    *,
    status: str,
    operation: Any,
    target_class: Any,
    reasons: Iterable[str],
    stored_binding: Any,
    activation_reserved: bool = False,
) -> Dict[str, Any]:
    if status not in HANDOFF_STATUSES:
        status = "invalid"
    return {
        "schema_version": 1,
        "status": status,
        "assessment_scope": ASSESSMENT_SCOPE,
        "operation": _safe_operation(operation),
        "target_class": _safe_target(target_class),
        "execution_status": "not-performed",
        "reason_ids": _ordered_ids(reasons),
        "limitations": _limitations(
            stored_binding,
            activation_reserved=activation_reserved,
        ),
        "truncated": False,
    }


def _stored_fields(stored_binding: Any) -> Optional[Dict[str, str]]:
    if not isinstance(stored_binding, Mapping):
        return None
    try:
        operation = stored_binding["operation"]
        target_class = stored_binding["target_class"]
    except Exception:
        return None
    if type(operation) is not str or operation not in OPERATIONS:
        return None
    if type(target_class) is not str or target_class not in TARGET_CLASSES:
        return None
    return {"operation": operation, "target_class": target_class}


def _verify_current(
    *,
    stored_binding: Any,
    registry_schema_version: int,
    registry_entry: Mapping[str, Any],
    trust_profile_schema_version: int,
    trust_policy: Mapping[str, Any],
    trust_evidence: Mapping[str, Any],
    capability_policy: Mapping[str, Any],
    capability_declaration: Optional[Mapping[str, Any]],
    requested_capabilities: Mapping[str, Any],
    trust_decision: Mapping[str, Any],
    capability_decision: Mapping[str, Any],
    recommendation_decision: Mapping[str, Any],
    installation_decision: Mapping[str, Any],
    target_class: str,
) -> str:
    """Recompute verification for the exact stored binding in this call."""

    try:
        verification = verify_admission_binding(
            stored_binding,
            registry_schema_version=registry_schema_version,
            registry_entry=registry_entry,
            trust_profile_schema_version=trust_profile_schema_version,
            trust_policy=trust_policy,
            trust_evidence=trust_evidence,
            capability_policy=capability_policy,
            capability_declaration=capability_declaration,
            requested_capabilities=requested_capabilities,
            trust_decision=trust_decision,
            capability_decision=capability_decision,
            recommendation_decision=recommendation_decision,
            installation_decision=installation_decision,
            target_class=target_class,
        )
    except Exception:
        return "invalid"
    if not isinstance(verification, Mapping):
        return "invalid"
    try:
        status = verification["status"]
    except (KeyError, TypeError, AttributeError):
        return "invalid"
    return status if status in VERIFICATION_STATUSES else "invalid"


def evaluate_execution_handoff(
    *,
    stored_binding: Mapping[str, Any],
    registry_schema_version: int,
    registry_entry: Mapping[str, Any],
    trust_profile_schema_version: int,
    trust_policy: Mapping[str, Any],
    trust_evidence: Mapping[str, Any],
    capability_policy: Mapping[str, Any],
    capability_declaration: Optional[Mapping[str, Any]],
    requested_capabilities: Mapping[str, Any],
    trust_decision: Mapping[str, Any],
    capability_decision: Mapping[str, Any],
    recommendation_decision: Mapping[str, Any],
    installation_decision: Mapping[str, Any],
    operation: str,
    target_class: str,
    fresh_operator_authorization: str,
) -> Dict[str, Any]:
    """Return one invocation-local, metadata-only execution handoff result.

    The function internally verifies ``stored_binding`` against every current
    deterministic evidence input.  A ``ready`` result only permits a future
    install adapter to receive the same invocation; it is not execution,
    installation, a credential, or a reusable authorization token.
    """

    stored = _stored_fields(stored_binding)
    safe_operation = _safe_operation(operation)
    safe_target = _safe_target(target_class)

    verification_status = _verify_current(
        stored_binding=stored_binding,
        registry_schema_version=registry_schema_version,
        registry_entry=registry_entry,
        trust_profile_schema_version=trust_profile_schema_version,
        trust_policy=trust_policy,
        trust_evidence=trust_evidence,
        capability_policy=capability_policy,
        capability_declaration=capability_declaration,
        requested_capabilities=requested_capabilities,
        trust_decision=trust_decision,
        capability_decision=capability_decision,
        recommendation_decision=recommendation_decision,
        installation_decision=installation_decision,
        target_class=target_class,
    )

    # ``verify_admission_binding`` validates only the supported target class,
    # so a recognized-but-substituted management-plane label can surface as
    # ``invalid`` there.  Preserve the more useful fail-closed substitution
    # diagnostic without ever allowing it to proceed.
    if (
        stored is not None
        and safe_target in _RECOGNIZED_TARGET_CLASSES
        and safe_target != stored["target_class"]
    ):
        return _result(
            status="rejected",
            operation=safe_operation,
            target_class=safe_target,
            reasons=("phase5e.handoff.target.mismatch", "phase5e.handoff.rejected"),
            stored_binding=stored_binding,
        )

    if stored is not None and safe_target == "invalid":
        return _result(
            status="invalid",
            operation=safe_operation,
            target_class=safe_target,
            reasons=("phase5e.handoff.target.invalid", "phase5e.handoff.invalid"),
            stored_binding=stored_binding,
        )

    if verification_status == "invalid" or stored is None:
        return _result(
            status="invalid",
            operation=safe_operation,
            target_class=safe_target,
            reasons=("phase5e.handoff.binding.invalid", "phase5e.handoff.invalid"),
            stored_binding=stored_binding,
        )

    if verification_status == "stale":
        return _result(
            status="rejected",
            operation=safe_operation,
            target_class=safe_target,
            reasons=("phase5e.handoff.binding.stale", "phase5e.handoff.rejected"),
            stored_binding=stored_binding,
        )

    if type(fresh_operator_authorization) is not str or fresh_operator_authorization not in FRESH_AUTHORIZATION_STATES:
        return _result(
            status="invalid",
            operation=safe_operation,
            target_class=safe_target,
            reasons=("phase5e.handoff.authorization.invalid", "phase5e.handoff.invalid"),
            stored_binding=stored_binding,
        )
    if fresh_operator_authorization == "denied":
        return _result(
            status="rejected",
            operation=safe_operation,
            target_class=safe_target,
            reasons=("phase5e.handoff.authorization.denied", "phase5e.handoff.rejected"),
            stored_binding=stored_binding,
        )
    if fresh_operator_authorization == "not-provided":
        return _result(
            status="unknown",
            operation=safe_operation,
            target_class=safe_target,
            reasons=("phase5e.handoff.authorization.required", "phase5e.handoff.unknown"),
            stored_binding=stored_binding,
        )

    if safe_operation == "invalid":
        return _result(
            status="invalid",
            operation=safe_operation,
            target_class=safe_target,
            reasons=("phase5e.handoff.operation.invalid", "phase5e.handoff.invalid"),
            stored_binding=stored_binding,
        )
    if safe_operation != stored["operation"]:
        return _result(
            status="rejected",
            operation=safe_operation,
            target_class=safe_target,
            reasons=("phase5e.handoff.operation.mismatch", "phase5e.handoff.rejected"),
            stored_binding=stored_binding,
        )

    if safe_target == "invalid":
        return _result(
            status="invalid",
            operation=safe_operation,
            target_class=safe_target,
            reasons=("phase5e.handoff.target.invalid", "phase5e.handoff.invalid"),
            stored_binding=stored_binding,
        )
    if safe_target != stored["target_class"]:
        return _result(
            status="rejected",
            operation=safe_operation,
            target_class=safe_target,
            reasons=("phase5e.handoff.target.mismatch", "phase5e.handoff.rejected"),
            stored_binding=stored_binding,
        )

    if safe_operation == "activate":
        return _result(
            status="rejected",
            operation=safe_operation,
            target_class=safe_target,
            reasons=(
                "phase5e.handoff.operation.activation-reserved",
                "phase5e.handoff.rejected",
            ),
            stored_binding=stored_binding,
            activation_reserved=True,
        )

    return _result(
        status="ready",
        operation=safe_operation,
        target_class=safe_target,
        reasons=("phase5e.handoff.ready",),
        stored_binding=stored_binding,
    )


__all__ = [
    "ASSESSMENT_SCOPE",
    "FRESH_AUTHORIZATION_STATES",
    "HANDOFF_STATUSES",
    "LIMITATION_IDS",
    "OPERATIONS",
    "REASON_IDS",
    "STATUSES",
    "TARGET_CLASS",
    "evaluate_execution_handoff",
]

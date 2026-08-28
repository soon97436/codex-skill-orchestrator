"""Pure deterministic capability-policy validation and admission decisions."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any, Dict, List, Optional

from .errors import ValidationError


CAPABILITY_FAMILIES = (
    "filesystem-read",
    "filesystem-write",
    "network",
    "process",
)

CAPABILITY_REASON_IDS = (
    "capability.request.empty",
    "capability.request.invalid",
    "capability.request.unknown",
    "capability.request.duplicate",
    "capability.declaration.missing",
    "capability.declaration.invalid",
    "capability.policy.allowed",
    "capability.policy.disallowed",
    "capability.policy.exceeds-floor",
    "capability.trust.required",
    "capability.trust.rejected",
    "capability.trust.unknown",
    "capability.admission.not-requested",
    "capability.admission.admissible",
    "capability.admission.rejected",
    "capability.admission.unknown",
    "capability.limit.enforcement-not-implemented",
)

TRUST_STATUSES = ("admissible", "rejected", "unknown", "not-evaluated")
OVERALL_STATUSES = ("admissible", "rejected", "unknown", "invalid", "not-requested")
DECISION_STATUSES = ("allowed", "denied", "unknown", "not-requested")

_POLICY_KEYS = {"schema_version", "operational_floor", "default_profile", "profiles"}
_RESOLVED_POLICY_KEYS = {"schema_version", "profile_id", "operational_floor", "capabilities"}
_PROFILE_KEYS = {"id", "capabilities"}
_CAPABILITY_KEYS = {"schema_version", "filesystem", "network", "process"}
_FILESYSTEM_KEYS = {"read", "write"}
_NETWORK_KEYS = {"mode"}
_PROCESS_KEYS = {"mode", "commands"}

_PROFILE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", flags=re.ASCII)
_COMMAND_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$", flags=re.ASCII)

_SCOPE_RANK = {"project": 0, "workspace": 1, "unrestricted": 2}
_NETWORK_RANK = {"none": 0, "localhost": 1, "restricted": 2, "unrestricted": 3}
_PROCESS_RANK = {"none": 0, "commands": 1, "arbitrary": 2}
_SCOPES = tuple(_SCOPE_RANK)
_NETWORK_MODES = tuple(_NETWORK_RANK)
_PROCESS_MODES = tuple(_PROCESS_RANK)


class _InputIssue(Exception):
    """Internal content-free validation signal for caller-supplied mappings."""

    def __init__(self, reason_id: str) -> None:
        super().__init__(reason_id)
        self.reason_id = reason_id


def _policy_failure(message: str) -> None:
    raise ValidationError(message)


def _input_failure(reason_id: str) -> None:
    raise _InputIssue(reason_id)


def _exact_keys(mapping: Mapping[str, Any], expected: set, *, fail: Callable[[str], None],
                invalid_failure: str, unknown_failure: str) -> None:
    actual = set(mapping)
    if actual - expected:
        fail(unknown_failure)
    if expected - actual:
        fail(invalid_failure)


def _profile_id(value: Any, *, label: str) -> str:
    if type(value) is not str or not value or len(value) > 64 or not _PROFILE_ID_RE.fullmatch(value):
        raise ValidationError("%s is invalid" % label)
    return value


def _parse_capabilities(
    value: Any,
    *,
    fail: Callable[[str], None],
    invalid_failure: str,
    unknown_failure: str,
    duplicate_failure: str,
) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        fail(invalid_failure)
    _exact_keys(
        value,
        _CAPABILITY_KEYS,
        fail=fail,
        invalid_failure=invalid_failure,
        unknown_failure=unknown_failure,
    )
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        fail(invalid_failure)

    filesystem = value["filesystem"]
    if not isinstance(filesystem, Mapping):
        fail(invalid_failure)
    _exact_keys(
        filesystem,
        _FILESYSTEM_KEYS,
        fail=fail,
        invalid_failure=invalid_failure,
        unknown_failure=unknown_failure,
    )

    normalized_filesystem: Dict[str, List[str]] = {}
    for access in ("read", "write"):
        scopes = filesystem[access]
        if type(scopes) is not list or len(scopes) > 64:
            fail(invalid_failure)
        if any(type(scope) is not str or scope not in _SCOPE_RANK for scope in scopes):
            fail(invalid_failure)
        if len(set(scopes)) != len(scopes):
            fail(duplicate_failure)
        normalized_filesystem[access] = sorted(scopes, key=_SCOPE_RANK.__getitem__)

    network = value["network"]
    if not isinstance(network, Mapping):
        fail(invalid_failure)
    _exact_keys(
        network,
        _NETWORK_KEYS,
        fail=fail,
        invalid_failure=invalid_failure,
        unknown_failure=unknown_failure,
    )
    network_mode = network["mode"]
    if type(network_mode) is not str or network_mode not in _NETWORK_MODES:
        fail(invalid_failure)

    process = value["process"]
    if not isinstance(process, Mapping):
        fail(invalid_failure)
    _exact_keys(
        process,
        _PROCESS_KEYS,
        fail=fail,
        invalid_failure=invalid_failure,
        unknown_failure=unknown_failure,
    )
    process_mode = process["mode"]
    commands = process["commands"]
    if type(process_mode) is not str or process_mode not in _PROCESS_MODES:
        fail(invalid_failure)
    if type(commands) is not list or len(commands) > 64:
        fail(invalid_failure)
    if any(type(command) is not str or not _COMMAND_RE.fullmatch(command) for command in commands):
        fail(invalid_failure)
    if len(set(commands)) != len(commands):
        fail(duplicate_failure)
    if process_mode == "commands" and not commands:
        fail(invalid_failure)
    if process_mode != "commands" and commands:
        fail(invalid_failure)

    return {
        "schema_version": 1,
        "filesystem": normalized_filesystem,
        "network": {"mode": network_mode},
        "process": {
            "mode": process_mode,
            "commands": sorted(commands),
        },
    }


def _scope_is_covered(requested: List[str], available: List[str]) -> bool:
    return all(
        any(_SCOPE_RANK[allowed] >= _SCOPE_RANK[need] for allowed in available)
        for need in requested
    )


def _profile_scopes_within_floor(profile: List[str], floor: List[str]) -> bool:
    return _scope_is_covered(profile, floor)


def _validate_profile_monotonicity(floor: Mapping[str, Any], profile: Mapping[str, Any]) -> None:
    for access in ("read", "write"):
        if not _profile_scopes_within_floor(
            profile["filesystem"][access], floor["filesystem"][access]
        ):
            raise ValidationError("capability profile weakens the operational floor")

    if _NETWORK_RANK[profile["network"]["mode"]] > _NETWORK_RANK[floor["network"]["mode"]]:
        raise ValidationError("capability profile weakens the operational floor")

    profile_mode = profile["process"]["mode"]
    floor_mode = floor["process"]["mode"]
    if _PROCESS_RANK[profile_mode] > _PROCESS_RANK[floor_mode]:
        raise ValidationError("capability profile weakens the operational floor")
    if profile_mode == "commands" and floor_mode == "commands":
        if not set(profile["process"]["commands"]).issubset(
            set(floor["process"]["commands"])
        ):
            raise ValidationError("capability profile weakens the operational floor")


def validate_capability_policy_document(document: Mapping[str, Any]) -> Dict[str, Any]:
    """Validate and canonicalize a supplied capability-profile document."""

    if not isinstance(document, Mapping):
        raise ValidationError("capability policy must be an object")
    _exact_keys(
        document,
        _POLICY_KEYS,
        fail=_policy_failure,
        invalid_failure="capability policy has missing keys",
        unknown_failure="capability policy has unknown keys",
    )
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise ValidationError("capability policy has unsupported schema_version")

    floor = _parse_capabilities(
        document["operational_floor"],
        fail=_policy_failure,
        invalid_failure="capability policy floor is invalid",
        unknown_failure="capability policy floor has unknown keys",
        duplicate_failure="capability policy floor contains duplicates",
    )
    default_profile = _profile_id(document["default_profile"], label="default profile")
    profiles = document["profiles"]
    if type(profiles) is not list or not profiles or len(profiles) > 64:
        raise ValidationError("capability policy profiles must be a bounded non-empty array")

    normalized_profiles: List[Dict[str, Any]] = []
    seen_ids = set()
    for profile in profiles:
        if not isinstance(profile, Mapping):
            raise ValidationError("capability profile must be an object")
        _exact_keys(
            profile,
            _PROFILE_KEYS,
            fail=_policy_failure,
            invalid_failure="capability profile has missing keys",
            unknown_failure="capability profile has unknown keys",
        )
        profile_id = _profile_id(profile["id"], label="capability profile id")
        if profile_id in seen_ids:
            raise ValidationError("duplicate capability profile id")
        seen_ids.add(profile_id)
        profile_capabilities = _parse_capabilities(
            profile["capabilities"],
            fail=_policy_failure,
            invalid_failure="capability profile capabilities are invalid",
            unknown_failure="capability profile capabilities have unknown keys",
            duplicate_failure="capability profile capabilities contain duplicates",
        )
        _validate_profile_monotonicity(floor, profile_capabilities)
        normalized_profiles.append(
            {"id": profile_id, "capabilities": profile_capabilities}
        )

    if default_profile not in seen_ids:
        raise ValidationError("default capability profile does not resolve")

    normalized_profiles.sort(key=lambda item: item["id"])
    return {
        "schema_version": 1,
        "operational_floor": floor,
        "default_profile": default_profile,
        "profiles": normalized_profiles,
    }


def resolve_capability_policy(
    document: Mapping[str, Any], profile_id: Optional[str] = None
) -> Dict[str, Any]:
    """Resolve exactly one profile; unknown IDs never fall back."""

    validated = validate_capability_policy_document(document)
    selected = validated["default_profile"] if profile_id is None else profile_id
    selected = _profile_id(selected, label="requested capability profile")
    matches = [profile for profile in validated["profiles"] if profile["id"] == selected]
    if len(matches) != 1:
        raise ValidationError("unknown capability profile")
    return {
        "schema_version": 1,
        "profile_id": selected,
        "operational_floor": validated["operational_floor"],
        "capabilities": matches[0]["capabilities"],
    }


def _normalize_resolved_policy(policy: Mapping[str, Any]) -> Dict[str, Any]:
    if not isinstance(policy, Mapping):
        raise ValidationError("capability policy must be an object")
    if "profiles" in policy or "default_profile" in policy:
        return resolve_capability_policy(policy)
    _exact_keys(
        policy,
        _RESOLVED_POLICY_KEYS,
        fail=_policy_failure,
        invalid_failure="resolved capability policy has missing keys",
        unknown_failure="resolved capability policy has unknown keys",
    )
    if type(policy["schema_version"]) is not int or policy["schema_version"] != 1:
        raise ValidationError("resolved capability policy has unsupported schema_version")
    selected = _profile_id(policy["profile_id"], label="resolved capability profile")
    floor = _parse_capabilities(
        policy["operational_floor"],
        fail=_policy_failure,
        invalid_failure="resolved capability floor is invalid",
        unknown_failure="resolved capability floor has unknown keys",
        duplicate_failure="resolved capability floor contains duplicates",
    )
    capabilities = _parse_capabilities(
        policy["capabilities"],
        fail=_policy_failure,
        invalid_failure="resolved capability profile is invalid",
        unknown_failure="resolved capability profile has unknown keys",
        duplicate_failure="resolved capability profile contains duplicates",
    )
    _validate_profile_monotonicity(floor, capabilities)
    return {
        "schema_version": 1,
        "profile_id": selected,
        "operational_floor": floor,
        "capabilities": capabilities,
    }


def _decision(capability: str, status: str, reason_id: str) -> Dict[str, Any]:
    if capability not in CAPABILITY_FAMILIES:
        raise ValueError("unsupported capability family")
    if status not in DECISION_STATUSES:
        raise ValueError("unsupported capability decision status")
    if reason_id not in CAPABILITY_REASON_IDS:
        raise ValueError("unsupported capability reason")
    return {
        "capability": capability,
        "status": status,
        "reason_ids": [reason_id],
    }


def _not_requested_decision(capability: str) -> Dict[str, Any]:
    return _decision(capability, "not-requested", "capability.admission.not-requested")


def _empty_decisions() -> List[Dict[str, Any]]:
    return [_not_requested_decision(capability) for capability in CAPABILITY_FAMILIES]


def _request_is_empty(request: Mapping[str, Any]) -> bool:
    return (
        not request["filesystem"]["read"]
        and not request["filesystem"]["write"]
        and request["network"]["mode"] == "none"
        and request["process"]["mode"] == "none"
    )


def _build_result(
    *,
    status: str,
    trust_status: str,
    profile_id: str,
    decisions: List[Dict[str, Any]],
    reason_ids: List[str],
) -> Dict[str, Any]:
    if status not in OVERALL_STATUSES:
        raise ValueError("unsupported capability admission status")
    reasons: List[str] = []
    for reason_id in reason_ids + [
        reason_id
        for decision in decisions
        for reason_id in decision["reason_ids"]
    ]:
        if reason_id not in CAPABILITY_REASON_IDS:
            raise ValueError("unsupported capability reason")
        if reason_id not in reasons:
            reasons.append(reason_id)

    if status == "admissible":
        final_reason = "capability.admission.admissible"
    elif status == "unknown":
        final_reason = "capability.admission.unknown"
    elif status in {"rejected", "invalid"}:
        final_reason = "capability.admission.rejected"
    else:
        final_reason = "capability.admission.not-requested"
    if final_reason not in reasons:
        reasons.append(final_reason)

    return {
        "schema_version": 1,
        "status": status,
        "assessment_scope": "capability-policy-only",
        "trust_status": trust_status,
        "profile_id": profile_id,
        "decisions": decisions,
        "reasons": reasons,
        "limitations": ["capability.limit.enforcement-not-implemented"],
        "truncated": False,
    }


def _invalid_result(
    *, trust_status: str, profile_id: str, reason_id: str
) -> Dict[str, Any]:
    decisions = [
        _decision(capability, "unknown", reason_id)
        for capability in CAPABILITY_FAMILIES
    ]
    return _build_result(
        status="invalid",
        trust_status=trust_status,
        profile_id=profile_id,
        decisions=decisions,
        reason_ids=[reason_id],
    )


def _trust_gated_result(
    request: Mapping[str, Any],
    *,
    trust_status: str,
    profile_id: str,
) -> Dict[str, Any]:
    if trust_status == "rejected":
        status = "rejected"
        decision_status = "denied"
        reason_id = "capability.trust.rejected"
    else:
        status = "unknown"
        decision_status = "unknown"
        reason_id = (
            "capability.trust.required"
            if trust_status == "not-evaluated"
            else "capability.trust.unknown"
        )
    decisions = []
    for capability in CAPABILITY_FAMILIES:
        active = {
            "filesystem-read": bool(request["filesystem"]["read"]),
            "filesystem-write": bool(request["filesystem"]["write"]),
            "network": request["network"]["mode"] != "none",
            "process": request["process"]["mode"] != "none",
        }[capability]
        decisions.append(
            _decision(capability, decision_status, reason_id)
            if active
            else _not_requested_decision(capability)
        )
    return _build_result(
        status=status,
        trust_status=trust_status,
        profile_id=profile_id,
        decisions=decisions,
        reason_ids=[reason_id],
    )


def _evaluate_family(
    family: str,
    declaration: Mapping[str, Any],
    request: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> Dict[str, Any]:
    if family == "filesystem-read":
        requested = request["filesystem"]["read"]
        if not requested:
            return _not_requested_decision(family)
        if not _scope_is_covered(requested, declaration["filesystem"]["read"]):
            return _decision(family, "denied", "capability.policy.disallowed")
        if not _scope_is_covered(requested, policy["filesystem"]["read"]):
            return _decision(family, "denied", "capability.policy.exceeds-floor")
        return _decision(family, "allowed", "capability.policy.allowed")

    if family == "filesystem-write":
        requested = request["filesystem"]["write"]
        if not requested:
            return _not_requested_decision(family)
        if not _scope_is_covered(requested, declaration["filesystem"]["write"]):
            return _decision(family, "denied", "capability.policy.disallowed")
        if not _scope_is_covered(requested, policy["filesystem"]["write"]):
            return _decision(family, "denied", "capability.policy.exceeds-floor")
        return _decision(family, "allowed", "capability.policy.allowed")

    if family == "network":
        requested_mode = request["network"]["mode"]
        if requested_mode == "none":
            return _not_requested_decision(family)
        if _NETWORK_RANK[declaration["network"]["mode"]] < _NETWORK_RANK[requested_mode]:
            return _decision(family, "denied", "capability.policy.disallowed")
        if _NETWORK_RANK[policy["network"]["mode"]] < _NETWORK_RANK[requested_mode]:
            return _decision(family, "denied", "capability.policy.exceeds-floor")
        return _decision(family, "allowed", "capability.policy.allowed")

    requested_mode = request["process"]["mode"]
    requested_commands = set(request["process"]["commands"])
    if requested_mode == "none":
        return _not_requested_decision(family)
    declaration_mode = declaration["process"]["mode"]
    policy_mode = policy["process"]["mode"]
    if _PROCESS_RANK[declaration_mode] < _PROCESS_RANK[requested_mode]:
        return _decision(family, "denied", "capability.policy.disallowed")
    if requested_mode == "commands" and declaration_mode == "commands":
        if not requested_commands.issubset(set(declaration["process"]["commands"])):
            return _decision(family, "denied", "capability.policy.disallowed")
    if requested_mode == "arbitrary" and declaration_mode != "arbitrary":
        return _decision(family, "denied", "capability.policy.disallowed")
    if _PROCESS_RANK[policy_mode] < _PROCESS_RANK[requested_mode]:
        return _decision(family, "denied", "capability.policy.exceeds-floor")
    if requested_mode == "commands" and policy_mode == "commands":
        if not requested_commands.issubset(set(policy["process"]["commands"])):
            return _decision(family, "denied", "capability.policy.exceeds-floor")
    if requested_mode == "arbitrary" and policy_mode != "arbitrary":
        return _decision(family, "denied", "capability.policy.exceeds-floor")
    return _decision(family, "allowed", "capability.policy.allowed")


def evaluate_capability_policy(
    capability_declaration: Optional[Mapping[str, Any]],
    *,
    policy: Mapping[str, Any],
    requested_capabilities: Mapping[str, Any],
    trust_status: str,
) -> Dict[str, Any]:
    """Return a deterministic, metadata-only capability admission decision."""

    if trust_status not in TRUST_STATUSES:
        raise ValueError("unsupported trust status")
    resolved_policy = _normalize_resolved_policy(policy)
    profile_id = resolved_policy["profile_id"]

    if not isinstance(requested_capabilities, Mapping):
        return _invalid_result(
            trust_status=trust_status,
            profile_id=profile_id,
            reason_id="capability.request.invalid",
        )
    if not requested_capabilities:
        return _build_result(
            status="not-requested",
            trust_status=trust_status,
            profile_id=profile_id,
            decisions=_empty_decisions(),
            reason_ids=["capability.request.empty"],
        )

    try:
        request = _parse_capabilities(
            requested_capabilities,
            fail=_input_failure,
            invalid_failure="capability.request.invalid",
            unknown_failure="capability.request.unknown",
            duplicate_failure="capability.request.duplicate",
        )
    except _InputIssue as issue:
        return _invalid_result(
            trust_status=trust_status,
            profile_id=profile_id,
            reason_id=issue.reason_id,
        )

    if _request_is_empty(request):
        return _build_result(
            status="not-requested",
            trust_status=trust_status,
            profile_id=profile_id,
            decisions=_empty_decisions(),
            reason_ids=["capability.request.empty"],
        )

    if trust_status != "admissible":
        return _trust_gated_result(
            request,
            trust_status=trust_status,
            profile_id=profile_id,
        )

    if capability_declaration is None:
        decisions = []
        for family in CAPABILITY_FAMILIES:
            active = {
                "filesystem-read": bool(request["filesystem"]["read"]),
                "filesystem-write": bool(request["filesystem"]["write"]),
                "network": request["network"]["mode"] != "none",
                "process": request["process"]["mode"] != "none",
            }[family]
            decisions.append(
                _decision(family, "unknown", "capability.declaration.missing")
                if active
                else _not_requested_decision(family)
            )
        return _build_result(
            status="unknown",
            trust_status=trust_status,
            profile_id=profile_id,
            decisions=decisions,
            reason_ids=["capability.declaration.missing"],
        )

    try:
        declaration = _parse_capabilities(
            capability_declaration,
            fail=_input_failure,
            invalid_failure="capability.declaration.invalid",
            unknown_failure="capability.declaration.invalid",
            duplicate_failure="capability.declaration.invalid",
        )
    except _InputIssue:
        return _invalid_result(
            trust_status=trust_status,
            profile_id=profile_id,
            reason_id="capability.declaration.invalid",
        )

    decisions = [
        _evaluate_family(
            family,
            declaration,
            request,
            resolved_policy["capabilities"],
        )
        for family in CAPABILITY_FAMILIES
    ]
    decision_statuses = {decision["status"] for decision in decisions}
    if "denied" in decision_statuses:
        status = "rejected"
    elif "unknown" in decision_statuses:
        status = "unknown"
    else:
        status = "admissible"
    return _build_result(
        status=status,
        trust_status=trust_status,
        profile_id=profile_id,
        decisions=decisions,
        reason_ids=[],
    )

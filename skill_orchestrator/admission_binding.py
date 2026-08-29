"""Pure deterministic binding and verification of Phase 5 admission evidence."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .admission_pipeline import (
    PIPELINE_STATUSES,
    REASON_IDS as PIPELINE_REASON_IDS,
    STAGE_ORDER,
    evaluate_admission_pipeline,
)
from .capability_policy import (
    CAPABILITY_FAMILIES,
    DECISION_STATUSES as CAPABILITY_DECISION_STATUSES,
    OVERALL_STATUSES as CAPABILITY_STATUSES,
    TRUST_STATUSES as CAPABILITY_TRUST_STATUSES,
)
from .installation_authorization import (
    OPERATIONS,
    OPERATOR_AUTHORIZATION_STATES,
    RESULT_STATUSES as INSTALLATION_STATUSES,
)
from .recommendation_admission import ADMISSION_REASON_IDS
from .registry_trust import (
    ADMISSION_STATUSES as TRUST_STATUSES,
    DECISION_STATUSES as TRUST_DECISION_STATUSES,
    TRUST_DIMENSIONS,
    TRUST_REASON_IDS,
)


ASSESSMENT_SCOPE = "phase5e-evidence-binding"
VERIFICATION_SCOPE = "phase5e-evidence-binding-verification"
BINDING_STATUSES = ("bound", "rejected", "unknown", "invalid")
VERIFICATION_STATUSES = ("current", "stale", "invalid")
TARGET_CLASSES = ("registry-skill-user-scope",)
SOURCE_TYPES = ("bundled", "git")

REASON_IDS = (
    "phase5e.binding.input.invalid",
    "phase5e.binding.subject.invalid",
    "phase5e.binding.subject-mismatch",
    "phase5e.binding.policy-mismatch",
    "phase5e.binding.pipeline.invalid",
    "phase5e.binding.pipeline.rejected",
    "phase5e.binding.pipeline.unknown",
    "phase5e.binding.bound",
    "phase5e.binding.verify.invalid",
    "phase5e.binding.verify.current",
    "phase5e.binding.verify.stale",
)

LIMITATION_IDS = (
    "phase5e.binding.limit.execution-not-performed",
    "phase5e.binding.limit.operator-freshness-not-verified",
    "phase5e.binding.limit.runtime-capability-enforcement-not-implemented",
    "phase5e.binding.limit.not-an-execution-token",
    "phase5e.binding.limit.remote-fetch-disabled",
    "phase5e.binding.limit.runtime-capability-not-requested",
)

MAX_BINDING_DEPTH = 12
MAX_BINDING_ITEMS = 2048
MAX_BINDING_LIST_ITEMS = 128
MAX_BINDING_STRING_BYTES = 4096
MAX_BINDING_INTEGER_BITS = 256

_BASE_LIMITATIONS = list(LIMITATION_IDS[:5])
_IDENTIFIER_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", flags=re.ASCII)
_SEMVER_RE = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$", flags=re.ASCII
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", flags=re.ASCII)
_COMMAND_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$", flags=re.ASCII)
_CAPABILITY_SCOPES = {"project": 0, "workspace": 1, "unrestricted": 2}
_NETWORK_MODES = {"none": 0, "localhost": 1, "restricted": 2, "unrestricted": 3}
_PROCESS_MODES = {"none": 0, "commands": 1, "arbitrary": 2}
_REGISTRY_KEYS = {
    "id",
    "name",
    "description",
    "version",
    "source",
    "license",
    "provenance",
    "files",
}
_REGISTRY_OPTIONAL_KEYS = {"capabilities"}
_CAPABILITY_KEYS = {"schema_version", "filesystem", "network", "process"}
_TRUST_POLICY_KEYS = {
    "profile_id",
    "allowed_source_types",
    "allowed_spdx_licenses",
    "allowed_provenance_classes",
    "require_checksums",
    "require_immutable_revision_for_remote",
}
_CAPABILITY_POLICY_KEYS = {
    "schema_version",
    "profile_id",
    "operational_floor",
    "capabilities",
}
_TRUST_EVIDENCE_KEYS = {
    "registry_valid",
    "source_revision_immutable",
    "provenance_complete",
    "integrity_verified",
}
_TRUST_DECISION_KEYS = {"schema_version", "status", "skill_id", "decisions", "reasons", "limitations", "truncated"}
_CAPABILITY_DECISION_KEYS = {
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
_RECOMMENDATION_DECISION_KEYS = {
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
_INSTALLATION_DECISION_KEYS = {
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
_PIPELINE_KEYS = {
    "schema_version",
    "overall_status",
    "assessment_scope",
    "stages",
    "execution_status",
    "reason_ids",
    "limitations",
    "truncated",
}
_PIPELINE_STAGE_STATUSES = set(PIPELINE_STATUSES) | {
    "not-requested",
    "recommendable",
    "authorized",
}
_BINDING_KEYS = {
    "schema_version",
    "status",
    "assessment_scope",
    "subject",
    "operation",
    "target_class",
    "evidence_digest",
    "binding_digest",
    "execution_status",
    "reason_ids",
    "limitations",
    "truncated",
}
_SUBJECT_KEYS = {"kind", "skill_id", "skill_version", "source_type", "source_revision"}
_VERIFICATION_BINDING_KEYS = _BINDING_KEYS
_REASON_ORDER = {reason_id: index for index, reason_id in enumerate(REASON_IDS)}


class _BindingIssue(Exception):
    """Internal content-free input validation signal."""

    def __init__(self, reason_id: str) -> None:
        super().__init__(reason_id)
        self.reason_id = reason_id


def _exact_keys(value: Mapping[str, Any], expected: set) -> bool:
    try:
        return set(value) == expected
    except Exception:
        return False


def _canonical_value(value: Any, *, depth: int = 0, budget: Optional[List[int]] = None) -> Any:
    """Convert JSON-safe values to plain values with deterministic bounds."""

    if budget is None:
        budget = [0]
    if depth > MAX_BINDING_DEPTH:
        raise _BindingIssue("phase5e.binding.input.invalid")
    budget[0] += 1
    if budget[0] > MAX_BINDING_ITEMS:
        raise _BindingIssue("phase5e.binding.input.invalid")

    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if value.bit_length() > MAX_BINDING_INTEGER_BITS:
            raise _BindingIssue("phase5e.binding.input.invalid")
        return value
    if type(value) is str:
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise _BindingIssue("phase5e.binding.input.invalid") from exc
        if len(encoded) > MAX_BINDING_STRING_BYTES:
            raise _BindingIssue("phase5e.binding.input.invalid")
        return value
    if type(value) is list:
        if len(value) > MAX_BINDING_LIST_ITEMS:
            raise _BindingIssue("phase5e.binding.input.invalid")
        return [
            _canonical_value(item, depth=depth + 1, budget=budget) for item in value
        ]
    if isinstance(value, Mapping):
        try:
            items = list(value.items())
        except Exception as exc:
            raise _BindingIssue("phase5e.binding.input.invalid") from exc
        if len(items) > MAX_BINDING_LIST_ITEMS:
            raise _BindingIssue("phase5e.binding.input.invalid")
        result: Dict[str, Any] = {}
        for key, item in items:
            if type(key) is not str or key in result:
                raise _BindingIssue("phase5e.binding.input.invalid")
            result[key] = _canonical_value(item, depth=depth + 1, budget=budget)
        return result
    raise _BindingIssue("phase5e.binding.input.invalid")


def _canonical_json(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError) as exc:
        raise _BindingIssue("phase5e.binding.input.invalid") from exc


def _digest(component: str, value: Mapping[str, Any]) -> str:
    prefix = ("CSO-PHASE5E-BINDING-V1:" + component + "\n").encode("ascii")
    return hashlib.sha256(prefix + _canonical_json(value)).hexdigest()


def _nonempty_string(value: Any) -> bool:
    return type(value) is str and bool(value) and "\x00" not in value


def _identifier(value: Any, *, maximum: Optional[int] = None) -> bool:
    return (
        _nonempty_string(value)
        and (maximum is None or len(value) <= maximum)
        and _IDENTIFIER_RE.fullmatch(value) is not None
    )


def _relative_path(value: Any) -> bool:
    if not _nonempty_string(value) or "\\" in value or value.startswith("/"):
        return False
    parts = value.split("/")
    return all(part not in {"", ".", ".."} and ":" not in part for part in parts)


def _unique_sorted_strings(value: Any, allowed: Optional[set] = None) -> List[str]:
    if type(value) is not list or not value or len(value) > 64:
        raise _BindingIssue("phase5e.binding.subject.invalid")
    if any(not _nonempty_string(item) for item in value):
        raise _BindingIssue("phase5e.binding.subject.invalid")
    if len(set(value)) != len(value):
        raise _BindingIssue("phase5e.binding.subject.invalid")
    if allowed is not None and any(item not in allowed for item in value):
        raise _BindingIssue("phase5e.binding.subject.invalid")
    return sorted(value)


def _normalize_capabilities(
    value: Any, *, reason_id: str, allow_unknown_modes: bool = False
) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _BindingIssue(reason_id)
    if not _exact_keys(value, _CAPABILITY_KEYS):
        raise _BindingIssue(reason_id)
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise _BindingIssue(reason_id)

    filesystem = value["filesystem"]
    if not isinstance(filesystem, Mapping) or not _exact_keys(filesystem, {"read", "write"}):
        raise _BindingIssue(reason_id)
    normalized_filesystem: Dict[str, List[str]] = {}
    for access in ("read", "write"):
        scopes = filesystem[access]
        if type(scopes) is not list or len(scopes) > 64:
            raise _BindingIssue(reason_id)
        if any(type(scope) is not str or scope not in _CAPABILITY_SCOPES for scope in scopes):
            raise _BindingIssue(reason_id)
        if len(set(scopes)) != len(scopes):
            raise _BindingIssue(reason_id)
        normalized_filesystem[access] = sorted(scopes, key=_CAPABILITY_SCOPES.__getitem__)

    network = value["network"]
    if not isinstance(network, Mapping) or not _exact_keys(network, {"mode"}):
        raise _BindingIssue(reason_id)
    network_mode = network["mode"]
    allowed_network_modes = set(_NETWORK_MODES)
    if allow_unknown_modes:
        allowed_network_modes.add("unknown")
    if type(network_mode) is not str or network_mode not in allowed_network_modes:
        raise _BindingIssue(reason_id)

    process = value["process"]
    if not isinstance(process, Mapping) or not _exact_keys(process, {"mode", "commands"}):
        raise _BindingIssue(reason_id)
    process_mode = process["mode"]
    commands = process["commands"]
    allowed_process_modes = set(_PROCESS_MODES)
    if allow_unknown_modes:
        allowed_process_modes.add("unknown")
    if type(process_mode) is not str or process_mode not in allowed_process_modes:
        raise _BindingIssue(reason_id)
    if type(commands) is not list or len(commands) > 64:
        raise _BindingIssue(reason_id)
    if any(type(command) is not str or _COMMAND_RE.fullmatch(command) is None for command in commands):
        raise _BindingIssue(reason_id)
    if len(set(commands)) != len(commands):
        raise _BindingIssue(reason_id)
    if (process_mode == "commands") != bool(commands):
        raise _BindingIssue(reason_id)

    return {
        "schema_version": 1,
        "filesystem": normalized_filesystem,
        "network": {"mode": network_mode},
        "process": {"mode": process_mode, "commands": sorted(commands)},
    }


def _scope_covered(requested: Sequence[str], allowed: Sequence[str]) -> bool:
    return all(
        any(_CAPABILITY_SCOPES[item] >= _CAPABILITY_SCOPES[need] for item in allowed)
        for need in requested
    )


def _capability_policy_monotonic(floor: Mapping[str, Any], profile: Mapping[str, Any]) -> bool:
    for access in ("read", "write"):
        if not _scope_covered(profile["filesystem"][access], floor["filesystem"][access]):
            return False
    floor_network = floor["network"]["mode"]
    profile_network = profile["network"]["mode"]
    if floor_network == "unknown" or profile_network == "unknown":
        return False
    if _NETWORK_MODES[profile_network] > _NETWORK_MODES[floor_network]:
        return False
    floor_process = floor["process"]["mode"]
    profile_process = profile["process"]["mode"]
    if floor_process == "unknown" or profile_process == "unknown":
        return False
    if _PROCESS_MODES[profile_process] > _PROCESS_MODES[floor_process]:
        return False
    if profile_process == "commands" and floor_process == "commands":
        return set(profile["process"]["commands"]).issubset(
            set(floor["process"]["commands"])
        )
    return True


def _normalize_registry_entry(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping) or not _exact_keys(
        value, _REGISTRY_KEYS | _REGISTRY_OPTIONAL_KEYS
    ) and not _exact_keys(value, _REGISTRY_KEYS):
        raise _BindingIssue("phase5e.binding.subject.invalid")
    actual = set(value)
    if not _REGISTRY_KEYS.issubset(actual) or actual - _REGISTRY_KEYS - _REGISTRY_OPTIONAL_KEYS:
        raise _BindingIssue("phase5e.binding.subject.invalid")
    if not _identifier(value["id"]):
        raise _BindingIssue("phase5e.binding.subject.invalid")
    if not _nonempty_string(value["name"]) or not _nonempty_string(value["description"]):
        raise _BindingIssue("phase5e.binding.subject.invalid")
    if not _nonempty_string(value["version"]) or _SEMVER_RE.fullmatch(value["version"]) is None:
        raise _BindingIssue("phase5e.binding.subject.invalid")

    source = value["source"]
    if not isinstance(source, Mapping) or not _exact_keys(
        source, {"type", "path", "repository", "revision"}
    ):
        raise _BindingIssue("phase5e.binding.subject.invalid")
    source_type = source["type"]
    if source_type not in SOURCE_TYPES or not _relative_path(source["path"]):
        raise _BindingIssue("phase5e.binding.subject.invalid")
    repository = source["repository"]
    revision = source["revision"]
    if source_type == "bundled":
        if repository is not None or revision is not None:
            raise _BindingIssue("phase5e.binding.subject.invalid")
    else:
        if not _nonempty_string(repository) or not repository.startswith("https://"):
            raise _BindingIssue("phase5e.binding.subject.invalid")
        if not _nonempty_string(revision):
            raise _BindingIssue("phase5e.binding.subject.invalid")

    license_info = value["license"]
    if not isinstance(license_info, Mapping) or not _exact_keys(
        license_info, {"spdx", "license_file", "source_url", "redistribution"}
    ):
        raise _BindingIssue("phase5e.binding.subject.invalid")
    if not _nonempty_string(license_info["spdx"]) or not _relative_path(license_info["license_file"]):
        raise _BindingIssue("phase5e.binding.subject.invalid")
    if license_info["source_url"] is not None and not _nonempty_string(license_info["source_url"]):
        raise _BindingIssue("phase5e.binding.subject.invalid")
    if source_type == "bundled" and license_info["source_url"] is not None:
        raise _BindingIssue("phase5e.binding.subject.invalid")
    if type(license_info["redistribution"]) is not bool:
        raise _BindingIssue("phase5e.binding.subject.invalid")

    provenance = value["provenance"]
    if not isinstance(provenance, Mapping) or not _exact_keys(
        provenance, {"publisher", "maintainer", "third_party"}
    ):
        raise _BindingIssue("phase5e.binding.subject.invalid")
    if not _nonempty_string(provenance["publisher"]) or not _nonempty_string(
        provenance["maintainer"]
    ):
        raise _BindingIssue("phase5e.binding.subject.invalid")
    if type(provenance["third_party"]) is not bool:
        raise _BindingIssue("phase5e.binding.subject.invalid")
    if source_type == "bundled" and provenance["third_party"]:
        raise _BindingIssue("phase5e.binding.subject.invalid")

    files = value["files"]
    if type(files) is not list or not files or len(files) > 64:
        raise _BindingIssue("phase5e.binding.subject.invalid")
    normalized_files: List[Dict[str, str]] = []
    seen_paths = set()
    for item in files:
        if not isinstance(item, Mapping) or not _exact_keys(item, {"path", "sha256"}):
            raise _BindingIssue("phase5e.binding.subject.invalid")
        path = item["path"]
        digest = item["sha256"]
        if not _relative_path(path) or type(digest) is not str or _SHA256_RE.fullmatch(digest) is None:
            raise _BindingIssue("phase5e.binding.subject.invalid")
        if path in seen_paths:
            raise _BindingIssue("phase5e.binding.subject.invalid")
        seen_paths.add(path)
        normalized_files.append({"path": path, "sha256": digest})
    normalized_files.sort(key=lambda item: item["path"])

    result: Dict[str, Any] = {
        "id": value["id"],
        "name": value["name"],
        "description": value["description"],
        "version": value["version"],
        "source": {
            "type": source_type,
            "path": source["path"],
            "repository": repository,
            "revision": revision,
        },
        "license": {
            "spdx": license_info["spdx"],
            "license_file": license_info["license_file"],
            "source_url": license_info["source_url"],
            "redistribution": license_info["redistribution"],
        },
        "provenance": {
            "publisher": provenance["publisher"],
            "maintainer": provenance["maintainer"],
            "third_party": provenance["third_party"],
        },
        "files": normalized_files,
    }
    if "capabilities" in value:
        result["capabilities"] = _normalize_capabilities(
            value["capabilities"],
            reason_id="phase5e.binding.subject.invalid",
            allow_unknown_modes=True,
        )
    return result


def _normalize_trust_policy(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping) or not _exact_keys(value, _TRUST_POLICY_KEYS):
        raise _BindingIssue("phase5e.binding.input.invalid")
    if not _identifier(value["profile_id"], maximum=64):
        raise _BindingIssue("phase5e.binding.input.invalid")
    source_types = _unique_sorted_strings(value["allowed_source_types"])
    if any(item not in SOURCE_TYPES for item in source_types):
        raise _BindingIssue("phase5e.binding.input.invalid")
    licenses = _unique_sorted_strings(value["allowed_spdx_licenses"])
    provenance = _unique_sorted_strings(
        value["allowed_provenance_classes"], {"first-party", "third-party"}
    )
    for key in ("require_checksums", "require_immutable_revision_for_remote"):
        if type(value[key]) is not bool:
            raise _BindingIssue("phase5e.binding.input.invalid")
    return {
        "profile_id": value["profile_id"],
        "allowed_source_types": source_types,
        "allowed_spdx_licenses": licenses,
        "allowed_provenance_classes": provenance,
        "require_checksums": value["require_checksums"],
        "require_immutable_revision_for_remote": value[
            "require_immutable_revision_for_remote"
        ],
    }


def _normalize_trust_evidence(value: Any) -> Dict[str, Optional[bool]]:
    if not isinstance(value, Mapping) or not _exact_keys(value, _TRUST_EVIDENCE_KEYS):
        raise _BindingIssue("phase5e.binding.input.invalid")
    result: Dict[str, Optional[bool]] = {}
    for key in _TRUST_EVIDENCE_KEYS:
        item = value[key]
        if item is not None and type(item) is not bool:
            raise _BindingIssue("phase5e.binding.input.invalid")
        result[key] = item
    return result


def _normalize_capability_policy(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping) or not _exact_keys(value, _CAPABILITY_POLICY_KEYS):
        raise _BindingIssue("phase5e.binding.input.invalid")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise _BindingIssue("phase5e.binding.input.invalid")
    if not _identifier(value["profile_id"], maximum=64):
        raise _BindingIssue("phase5e.binding.input.invalid")
    floor = _normalize_capabilities(
        value["operational_floor"], reason_id="phase5e.binding.input.invalid"
    )
    capabilities = _normalize_capabilities(
        value["capabilities"], reason_id="phase5e.binding.input.invalid"
    )
    if not _capability_policy_monotonic(floor, capabilities):
        raise _BindingIssue("phase5e.binding.policy-mismatch")
    return {
        "schema_version": 1,
        "profile_id": value["profile_id"],
        "operational_floor": floor,
        "capabilities": capabilities,
    }


def _normalize_optional_capabilities(value: Any) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    return _normalize_capabilities(value, reason_id="phase5e.binding.input.invalid")


def _normalize_requested_capabilities(value: Any) -> Dict[str, Any]:
    return _normalize_capabilities(value, reason_id="phase5e.binding.input.invalid")


def _validate_pipeline(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping) or not _exact_keys(value, _PIPELINE_KEYS):
        raise _BindingIssue("phase5e.binding.pipeline.invalid")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise _BindingIssue("phase5e.binding.pipeline.invalid")
    if value["assessment_scope"] != "phase5e-admission-pipeline":
        raise _BindingIssue("phase5e.binding.pipeline.invalid")
    status = value["overall_status"]
    if status not in PIPELINE_STATUSES:
        raise _BindingIssue("phase5e.binding.pipeline.invalid")
    if value["execution_status"] != "not-performed" or value["truncated"] is not False:
        raise _BindingIssue("phase5e.binding.pipeline.invalid")
    stages = value["stages"]
    if type(stages) is not list or len(stages) != len(STAGE_ORDER):
        raise _BindingIssue("phase5e.binding.pipeline.invalid")
    for expected, stage in zip(STAGE_ORDER, stages):
        if not isinstance(stage, Mapping) or not _exact_keys(
            stage, {"stage", "status", "reason_ids"}
        ):
            raise _BindingIssue("phase5e.binding.pipeline.invalid")
        if stage["stage"] != expected or stage["status"] not in _PIPELINE_STAGE_STATUSES:
            raise _BindingIssue("phase5e.binding.pipeline.invalid")
        reasons = stage["reason_ids"]
        if type(reasons) is not list or len(set(reasons)) != len(reasons):
            raise _BindingIssue("phase5e.binding.pipeline.invalid")
        if any(type(reason) is not str or reason not in PIPELINE_REASON_IDS for reason in reasons):
            raise _BindingIssue("phase5e.binding.pipeline.invalid")
    for key in ("reason_ids", "limitations"):
        item = value[key]
        if type(item) is not list or len(item) > 64:
            raise _BindingIssue("phase5e.binding.pipeline.invalid")
        try:
            duplicate = len(set(item)) != len(item)
        except Exception as exc:
            raise _BindingIssue("phase5e.binding.pipeline.invalid") from exc
        if duplicate:
            raise _BindingIssue("phase5e.binding.pipeline.invalid")
        allowed = PIPELINE_REASON_IDS if key == "reason_ids" else {
            "phase5e.limit.execution-not-performed",
            "phase5e.limit.evidence-binding-not-implemented",
            "phase5e.limit.operator-freshness-not-verified",
            "phase5e.limit.runtime-capability-enforcement-not-implemented",
            "phase5e.limit.runtime-capability-not-requested",
        }
        if any(type(item_id) is not str or item_id not in allowed for item_id in item):
            raise _BindingIssue("phase5e.binding.pipeline.invalid")
    return dict(value)


def _validate_upstream_decisions(
    trust_decision: Any,
    capability_decision: Any,
    recommendation_decision: Any,
    installation_decision: Any,
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    values = (trust_decision, capability_decision, recommendation_decision, installation_decision)
    if any(not isinstance(value, Mapping) for value in values):
        raise _BindingIssue("phase5e.binding.pipeline.invalid")
    try:
        normalized = tuple(_canonical_value(value) for value in values)
    except _BindingIssue as issue:
        raise _BindingIssue("phase5e.binding.pipeline.invalid") from issue
    trust, capability, recommendation, installation = normalized
    try:
        pipeline = evaluate_admission_pipeline(
            trust_decision=trust,
            capability_decision=capability,
            recommendation_decision=recommendation,
            installation_decision=installation,
        )
    except Exception as exc:
        raise _BindingIssue("phase5e.binding.pipeline.invalid") from exc
    validated_pipeline = _validate_pipeline(pipeline)
    return trust, capability, recommendation, installation, validated_pipeline


def _normalize_for_digest(value: Any) -> Any:
    """Normalize set-like diagnostic lists without deduplicating them."""

    if isinstance(value, list):
        normalized = [_normalize_for_digest(item) for item in value]
        return normalized
    if isinstance(value, dict):
        result = {key: _normalize_for_digest(item) for key, item in value.items()}
        if "reason_ids" in result and isinstance(result["reason_ids"], list):
            result["reason_ids"] = sorted(result["reason_ids"])
        if "reasons" in result and isinstance(result["reasons"], list):
            result["reasons"] = sorted(result["reasons"])
        if "limitations" in result and isinstance(result["limitations"], list):
            result["limitations"] = sorted(result["limitations"])
        return result
    return value


def _binding_limitations(capability_status: str) -> List[str]:
    result = list(_BASE_LIMITATIONS)
    if capability_status == "not-requested":
        result.append("phase5e.binding.limit.runtime-capability-not-requested")
    return result


def _ordered_reason_ids(reason_ids: Sequence[str]) -> List[str]:
    unique = []
    for reason_id in reason_ids:
        if reason_id in REASON_IDS and reason_id not in unique:
            unique.append(reason_id)
    return sorted(unique, key=_REASON_ORDER.__getitem__)


def _status_result(
    status: str,
    reason_ids: Sequence[str],
    *,
    capability_status: Optional[str] = None,
    verification: bool = False,
) -> Dict[str, Any]:
    if status not in BINDING_STATUSES and not verification:
        raise ValueError("unsupported binding status")
    limitations = _binding_limitations(capability_status or "")
    reasons = _ordered_reason_ids(reason_ids)
    return {
        "schema_version": 1,
        "status": status,
        "assessment_scope": VERIFICATION_SCOPE if verification else ASSESSMENT_SCOPE,
        "execution_status": "not-performed",
        "reason_ids": reasons,
        "limitations": limitations,
        "truncated": False,
    }


def _subject(entry: Mapping[str, Any]) -> Dict[str, Any]:
    source = entry["source"]
    return {
        "kind": "registry-candidate",
        "skill_id": entry["id"],
        "skill_version": entry["version"],
        "source_type": source["type"],
        "source_revision": source["revision"],
    }


def _prepare_inputs(
    *,
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
) -> Dict[str, Any]:
    if type(registry_schema_version) is not int or registry_schema_version != 1:
        raise _BindingIssue("phase5e.binding.subject.invalid")
    if type(trust_profile_schema_version) is not int or trust_profile_schema_version != 1:
        raise _BindingIssue("phase5e.binding.input.invalid")
    if target_class not in TARGET_CLASSES:
        raise _BindingIssue("phase5e.binding.input.invalid")

    # Preflight every caller-supplied value before semantic validation so
    # unsupported JSON values and unbounded containers cannot reach a digest.
    try:
        budget = [0]
        registry_entry = _canonical_value(registry_entry, budget=budget)
        trust_policy = _canonical_value(trust_policy, budget=budget)
        trust_evidence = _canonical_value(trust_evidence, budget=budget)
        capability_policy = _canonical_value(capability_policy, budget=budget)
        capability_declaration = _canonical_value(capability_declaration, budget=budget)
        requested_capabilities = _canonical_value(requested_capabilities, budget=budget)
        trust_decision = _canonical_value(trust_decision, budget=budget)
        capability_decision = _canonical_value(capability_decision, budget=budget)
        recommendation_decision = _canonical_value(recommendation_decision, budget=budget)
        installation_decision = _canonical_value(installation_decision, budget=budget)
        target_class = _canonical_value(target_class, budget=budget)
    except _BindingIssue as issue:
        raise _BindingIssue("phase5e.binding.input.invalid") from issue

    entry = _normalize_registry_entry(registry_entry)
    policy = _normalize_trust_policy(trust_policy)
    evidence = _normalize_trust_evidence(trust_evidence)
    cap_policy = _normalize_capability_policy(capability_policy)
    declaration = _normalize_optional_capabilities(capability_declaration)
    request = _normalize_requested_capabilities(requested_capabilities)
    trust, capability, recommendation, installation, pipeline = _validate_upstream_decisions(
        trust_decision,
        capability_decision,
        recommendation_decision,
        installation_decision,
    )

    if entry["id"] != trust.get("skill_id"):
        raise _BindingIssue("phase5e.binding.subject-mismatch")
    if cap_policy["profile_id"] != capability.get("profile_id"):
        raise _BindingIssue("phase5e.binding.policy-mismatch")
    operation = installation.get("operation")
    if operation not in OPERATIONS:
        raise _BindingIssue("phase5e.binding.input.invalid")

    capability_status = capability.get("status")
    if capability_status not in CAPABILITY_STATUSES:
        raise _BindingIssue("phase5e.binding.pipeline.invalid")

    return {
        "registry_schema_version": registry_schema_version,
        "registry_entry": entry,
        "trust_profile_schema_version": trust_profile_schema_version,
        "trust_policy": policy,
        "trust_evidence": evidence,
        "capability_policy": cap_policy,
        "capability_declaration": declaration,
        "requested_capabilities": request,
        "trust_decision": trust,
        "capability_decision": capability,
        "recommendation_decision": recommendation,
        "installation_decision": installation,
        "pipeline": pipeline,
        "target_class": target_class,
        "operation": operation,
        "capability_status": capability_status,
    }


def _evidence_payload(prepared: Mapping[str, Any]) -> Dict[str, Any]:
    keys = (
        "registry_schema_version",
        "registry_entry",
        "trust_profile_schema_version",
        "trust_policy",
        "trust_evidence",
        "capability_policy",
        "capability_declaration",
        "requested_capabilities",
        "trust_decision",
        "capability_decision",
        "recommendation_decision",
        "installation_decision",
        "pipeline",
    )
    return _normalize_for_digest({key: prepared[key] for key in keys})


def _bound_result(prepared: Mapping[str, Any]) -> Dict[str, Any]:
    evidence_digest = _digest("evidence", _evidence_payload(prepared))
    binding_payload = {
        "schema_version": 1,
        "evidence_digest": evidence_digest,
        "operation": prepared["operation"],
        "target_class": prepared["target_class"],
    }
    binding_digest = _digest("binding", binding_payload)
    return {
        "schema_version": 1,
        "status": "bound",
        "assessment_scope": ASSESSMENT_SCOPE,
        "subject": _subject(prepared["registry_entry"]),
        "operation": prepared["operation"],
        "target_class": prepared["target_class"],
        "evidence_digest": evidence_digest,
        "binding_digest": binding_digest,
        "execution_status": "not-performed",
        "reason_ids": ["phase5e.binding.bound"],
        "limitations": _binding_limitations(prepared["capability_status"]),
        "truncated": False,
    }


def create_admission_binding(
    *,
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
) -> Dict[str, Any]:
    """Create a deterministic evidence binding without performing execution."""

    try:
        prepared = _prepare_inputs(
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
    except _BindingIssue as issue:
        return _status_result("invalid", [issue.reason_id])
    except Exception:
        return _status_result("invalid", ["phase5e.binding.input.invalid"])

    pipeline_status = prepared["pipeline"]["overall_status"]
    if pipeline_status == "invalid":
        return _status_result(
            "invalid",
            ["phase5e.binding.pipeline.invalid"],
            capability_status=prepared["capability_status"],
        )
    if pipeline_status == "rejected":
        return _status_result(
            "rejected",
            ["phase5e.binding.pipeline.rejected"],
            capability_status=prepared["capability_status"],
        )
    if pipeline_status == "unknown":
        return _status_result(
            "unknown",
            ["phase5e.binding.pipeline.unknown"],
            capability_status=prepared["capability_status"],
        )
    if pipeline_status != "admissible":
        return _status_result("invalid", ["phase5e.binding.pipeline.invalid"])
    return _bound_result(prepared)


def _valid_digest(value: Any) -> bool:
    return type(value) is str and _SHA256_RE.fullmatch(value) is not None


def _validate_stored_binding(value: Any) -> Tuple[Dict[str, Any], bool]:
    if not isinstance(value, Mapping) or not _exact_keys(value, _BINDING_KEYS):
        raise _BindingIssue("phase5e.binding.verify.invalid")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise _BindingIssue("phase5e.binding.verify.invalid")
    if value["status"] != "bound" or value["assessment_scope"] != ASSESSMENT_SCOPE:
        raise _BindingIssue("phase5e.binding.verify.invalid")
    if value["execution_status"] != "not-performed" or value["truncated"] is not False:
        raise _BindingIssue("phase5e.binding.verify.invalid")
    if value["operation"] not in OPERATIONS or value["target_class"] not in TARGET_CLASSES:
        raise _BindingIssue("phase5e.binding.verify.invalid")
    if not _valid_digest(value["evidence_digest"]) or not _valid_digest(value["binding_digest"]):
        raise _BindingIssue("phase5e.binding.verify.invalid")
    if value["reason_ids"] != ["phase5e.binding.bound"]:
        raise _BindingIssue("phase5e.binding.verify.invalid")
    limitations = value["limitations"]
    if type(limitations) is not list or len(set(limitations)) != len(limitations):
        raise _BindingIssue("phase5e.binding.verify.invalid")
    expected_base = list(_BASE_LIMITATIONS)
    if limitations not in (
        expected_base,
        expected_base + ["phase5e.binding.limit.runtime-capability-not-requested"],
    ):
        raise _BindingIssue("phase5e.binding.verify.invalid")

    subject = value["subject"]
    if not isinstance(subject, Mapping) or not _exact_keys(subject, _SUBJECT_KEYS):
        raise _BindingIssue("phase5e.binding.verify.invalid")
    if subject["kind"] != "registry-candidate":
        raise _BindingIssue("phase5e.binding.verify.invalid")
    if not _identifier(subject["skill_id"]):
        raise _BindingIssue("phase5e.binding.verify.invalid")
    if not _nonempty_string(subject["skill_version"]) or _SEMVER_RE.fullmatch(subject["skill_version"]) is None:
        raise _BindingIssue("phase5e.binding.verify.invalid")
    if subject["source_type"] not in SOURCE_TYPES:
        raise _BindingIssue("phase5e.binding.verify.invalid")
    if subject["source_type"] == "bundled":
        if subject["source_revision"] is not None:
            raise _BindingIssue("phase5e.binding.verify.invalid")
    elif not _nonempty_string(subject["source_revision"]):
        raise _BindingIssue("phase5e.binding.verify.invalid")
    return dict(value), "phase5e.binding.limit.runtime-capability-not-requested" in limitations


def verify_admission_binding(
    binding: Mapping[str, Any],
    *,
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
) -> Dict[str, Any]:
    """Verify a stored binding against current deterministic evidence."""

    try:
        stored_input = _canonical_value(binding)
        stored, not_requested = _validate_stored_binding(stored_input)
    except _BindingIssue:
        return _status_result(
            "invalid",
            ["phase5e.binding.verify.invalid"],
            verification=True,
        )
    except Exception:
        return _status_result(
            "invalid",
            ["phase5e.binding.verify.invalid"],
            verification=True,
        )

    try:
        prepared = _prepare_inputs(
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
    except _BindingIssue:
        return _status_result(
            "invalid",
            ["phase5e.binding.verify.invalid"],
            verification=True,
            capability_status=(
                "not-requested" if not_requested else None
            ),
        )
    except Exception:
        return _status_result(
            "invalid",
            ["phase5e.binding.verify.invalid"],
            verification=True,
        )

    if prepared["pipeline"]["overall_status"] == "invalid":
        return _status_result(
            "invalid",
            ["phase5e.binding.verify.invalid"],
            capability_status=prepared["capability_status"],
            verification=True,
        )
    if stored["limitations"] != _binding_limitations(prepared["capability_status"]):
        return _status_result(
            "stale",
            ["phase5e.binding.verify.stale"],
            capability_status=prepared["capability_status"],
            verification=True,
        )
    if prepared["pipeline"]["overall_status"] != "admissible":
        return _status_result(
            "stale",
            ["phase5e.binding.verify.stale"],
            capability_status=prepared["capability_status"],
            verification=True,
        )

    current = _bound_result(prepared)
    if (
        current["evidence_digest"] != stored["evidence_digest"]
        or current["binding_digest"] != stored["binding_digest"]
        or current["subject"] != stored["subject"]
        or current["operation"] != stored["operation"]
        or current["target_class"] != stored["target_class"]
    ):
        return _status_result(
            "stale",
            ["phase5e.binding.verify.stale"],
            capability_status=prepared["capability_status"],
            verification=True,
        )
    return _status_result(
        "current",
        ["phase5e.binding.verify.current"],
        capability_status=prepared["capability_status"],
        verification=True,
    )


__all__ = [
    "ASSESSMENT_SCOPE",
    "BINDING_STATUSES",
    "LIMITATION_IDS",
    "REASON_IDS",
    "TARGET_CLASSES",
    "VERIFICATION_SCOPE",
    "VERIFICATION_STATUSES",
    "create_admission_binding",
    "verify_admission_binding",
]

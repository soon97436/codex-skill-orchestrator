"""Pure deterministic planning for a bundled Phase 5E candidate install.

This module deliberately stops at a metadata-only plan. It recomputes the
live execution handoff for every invocation and never reads a source tree,
resolves a destination, stages files, or performs a mutation.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .execution_handoff import evaluate_execution_handoff


ASSESSMENT_SCOPE = "phase5e-candidate-install-plan"
PLAN_STATUSES = ("planned", "rejected", "unknown", "invalid")
SOURCE_TYPES = ("bundled", "git", "invalid")
TARGET_CLASS = "registry-skill-user-scope"

MAX_DECLARED_FILES = 64
MAX_RELATIVE_PATH_UTF8_BYTES = 240
MAX_PATH_DEPTH = 16
MAX_SEGMENT_UTF8_BYTES = 100

REASON_IDS = (
    "phase5e.plan.input.invalid",
    "phase5e.plan.handoff.invalid",
    "phase5e.plan.handoff.rejected",
    "phase5e.plan.handoff.unknown",
    "phase5e.plan.source.invalid",
    "phase5e.plan.source.unsupported",
    "phase5e.plan.source-path.unsafe",
    "phase5e.plan.manifest.invalid",
    "phase5e.plan.manifest.duplicate",
    "phase5e.plan.manifest.collision",
    "phase5e.plan.resource-limit",
    "phase5e.plan.planned",
    "phase5e.plan.rejected",
    "phase5e.plan.unknown",
    "phase5e.plan.invalid",
)

LIMITATION_IDS = (
    "phase5e.plan.limit.execution-not-performed",
    "phase5e.plan.limit.not-execution-authority",
    "phase5e.plan.limit.filesystem-not-inspected",
    "phase5e.plan.limit.source-bytes-not-verified",
    "phase5e.plan.limit.byte-size-not-verified",
    "phase5e.plan.limit.destination-not-resolved",
    "phase5e.plan.limit.disk-space-not-verified",
    "phase5e.plan.limit.runtime-capability-enforcement-not-implemented",
    "phase5e.plan.limit.remote-fetch-disabled",
    "phase5e.plan.limit.runtime-capability-not-requested",
)

_REASON_ORDER = {reason_id: index for index, reason_id in enumerate(REASON_IDS)}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", flags=re.ASCII)
_SOURCE_KEYS = {"type", "path", "repository", "revision"}
_MANIFEST_KEYS = {"path", "sha256"}
_OPERATIONS = ("install", "activate")
_TARGET_CLASSES = (
    TARGET_CLASS,
    "cso-app",
    "router-profile",
    "workspace",
)
_RESERVED_BASENAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *(("com%d" % index) for index in range(1, 10)),
    *(("lpt%d" % index) for index in range(1, 10)),
}
_RESOURCE_LIMITS = {
    "max_declared_files": MAX_DECLARED_FILES,
    "max_relative_path_utf8_bytes": MAX_RELATIVE_PATH_UTF8_BYTES,
    "max_path_depth": MAX_PATH_DEPTH,
    "max_segment_utf8_bytes": MAX_SEGMENT_UTF8_BYTES,
}


def _ordered_reasons(reason_ids: Iterable[str]) -> List[str]:
    unique = {reason_id for reason_id in reason_ids if reason_id in _REASON_ORDER}
    return sorted(unique, key=_REASON_ORDER.__getitem__)


def _safe_operation(value: Any) -> str:
    return value if type(value) is str and value in _OPERATIONS else "invalid"


def _safe_target(value: Any) -> str:
    if type(value) is str and value in _TARGET_CLASSES:
        return value
    return "invalid"


def _safe_source_type(entry: Any) -> str:
    try:
        source = entry.get("source")
    except Exception:
        return "invalid"
    if isinstance(source, Mapping):
        value = source.get("type")
        if type(value) is str and value in SOURCE_TYPES[:2]:
            return value
    return "invalid"


def _safe_file_count(entry: Any) -> int:
    try:
        files = entry.get("files")
        if type(files) is list:
            return len(files)
    except Exception:
        return 0
    return 0


def _limitations(*, capability_not_requested: bool = False) -> List[str]:
    result = list(LIMITATION_IDS[:-1])
    if capability_not_requested:
        result.append(LIMITATION_IDS[-1])
    return result


def _result(
    *,
    status: str,
    operation: Any,
    target_class: Any,
    source_type: Any,
    file_count: int,
    reasons: Iterable[str],
    capability_not_requested: bool = False,
) -> Dict[str, Any]:
    if status not in PLAN_STATUSES:
        status = "invalid"
    safe_file_count = file_count if type(file_count) is int and file_count >= 0 else 0
    return {
        "schema_version": 1,
        "status": status,
        "assessment_scope": ASSESSMENT_SCOPE,
        "operation": _safe_operation(operation),
        "target_class": _safe_target(target_class),
        "source_type": (
            source_type
            if type(source_type) is str and source_type in SOURCE_TYPES
            else "invalid"
        ),
        "file_count": safe_file_count,
        "resource_limits": dict(_RESOURCE_LIMITS),
        "execution_status": "not-performed",
        "reason_ids": _ordered_reasons(reasons),
        "limitations": _limitations(
            capability_not_requested=capability_not_requested
        ),
        "truncated": False,
    }


def _capability_not_requested(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    try:
        if value.get("status") == "not-requested":
            return True
        decisions = value.get("decisions")
        return (
            type(decisions) is list
            and bool(decisions)
            and all(
                isinstance(decision, Mapping)
                and decision.get("status") == "not-requested"
                for decision in decisions
            )
        )
    except Exception:
        return False


def _handoff_status(value: Any) -> str:
    if not isinstance(value, Mapping):
        return "invalid"
    try:
        status = value["status"]
    except Exception:
        return "invalid"
    return (
        status
        if type(status) is str and status in {"ready", "rejected", "unknown", "invalid"}
        else "invalid"
    )


def _portable_path(value: Any) -> bool:
    if type(value) is not str or not value or "\x00" in value:
        return False
    try:
        encoded = value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    if len(encoded) > MAX_RELATIVE_PATH_UTF8_BYTES:
        return False
    if unicodedata.normalize("NFC", value) != value:
        return False
    if value.startswith("/") or "\\" in value or ":" in value:
        return False
    parts = value.split("/")
    if len(parts) > MAX_PATH_DEPTH or any(
        not part or part in {".", ".."} for part in parts
    ):
        return False
    for part in parts:
        try:
            if len(part.encode("utf-8")) > MAX_SEGMENT_UTF8_BYTES:
                return False
        except UnicodeEncodeError:
            return False
        if part.endswith((".", " ")):
            return False
        basename = part.split(".", 1)[0].casefold()
        if basename in _RESERVED_BASENAMES:
            return False
    return True


def _validate_source(entry: Any) -> Tuple[str, Optional[str]]:
    try:
        if not isinstance(entry, Mapping):
            return "invalid", "phase5e.plan.source.invalid"
        source = entry["source"]
        if not isinstance(source, Mapping) or set(source) != _SOURCE_KEYS:
            return "invalid", "phase5e.plan.source.invalid"
        source_type = source["type"]
        if type(source_type) is not str or source_type not in SOURCE_TYPES[:2]:
            return "invalid", "phase5e.plan.source.invalid"
        path = source["path"]
        if not _portable_path(path):
            return source_type, "phase5e.plan.source-path.unsafe"
        repository = source["repository"]
        revision = source["revision"]
        if source_type == "bundled":
            if repository is not None or revision is not None:
                return source_type, "phase5e.plan.source.invalid"
        else:
            if (
                type(repository) is not str
                or not repository
                or not repository.startswith("https://")
                or type(revision) is not str
                or not revision
            ):
                return source_type, "phase5e.plan.source.invalid"
        return source_type, None
    except Exception:
        return "invalid", "phase5e.plan.source.invalid"


def _validate_manifest(entry: Any) -> Tuple[int, Optional[str]]:
    try:
        if not isinstance(entry, Mapping):
            return 0, "phase5e.plan.manifest.invalid"
        files = entry["files"]
        if type(files) is not list or not files:
            return len(files) if type(files) is list else 0, "phase5e.plan.manifest.invalid"
        if len(files) > MAX_DECLARED_FILES:
            return len(files), "phase5e.plan.resource-limit"

        exact_paths = set()
        folded_paths = set()
        normalized_items = []
        for item in files:
            if not isinstance(item, Mapping) or set(item) != _MANIFEST_KEYS:
                return len(files), "phase5e.plan.manifest.invalid"
            path = item["path"]
            digest = item["sha256"]
            if not _portable_path(path):
                return len(files), "phase5e.plan.source-path.unsafe"
            if type(digest) is not str or _SHA256_RE.fullmatch(digest) is None:
                return len(files), "phase5e.plan.manifest.invalid"
            normalized_items.append((path, digest))

        # Canonicalize only the internal validation order. No caller data is
        # returned or rewritten.
        for path, _digest in sorted(normalized_items, key=lambda item: (item[0], item[1])):
            if path in exact_paths:
                return len(files), "phase5e.plan.manifest.duplicate"
            exact_paths.add(path)
            folded = unicodedata.normalize("NFC", path).casefold()
            if folded in folded_paths:
                return len(files), "phase5e.plan.manifest.collision"
            folded_paths.add(folded)
        return len(files), None
    except Exception:
        return 0, "phase5e.plan.manifest.invalid"


def _call_live_handoff(
    *,
    stored_binding: Any,
    registry_schema_version: Any,
    registry_entry: Any,
    trust_profile_schema_version: Any,
    trust_policy: Any,
    trust_evidence: Any,
    capability_policy: Any,
    capability_declaration: Any,
    requested_capabilities: Any,
    trust_decision: Any,
    capability_decision: Any,
    recommendation_decision: Any,
    installation_decision: Any,
    operation: Any,
    target_class: Any,
    fresh_operator_authorization: Any,
) -> Any:
    try:
        return evaluate_execution_handoff(
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
            operation=operation,
            target_class=target_class,
            fresh_operator_authorization=fresh_operator_authorization,
        )
    except Exception:
        return {"status": "invalid"}


def evaluate_candidate_install_plan(
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
    """Return a structural, metadata-only plan for one install invocation."""

    safe_operation = _safe_operation(operation)
    safe_target = _safe_target(target_class)
    source_type = _safe_source_type(registry_entry)
    file_count = _safe_file_count(registry_entry)
    capability_not_requested = _capability_not_requested(capability_decision)

    handoff = _call_live_handoff(
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
        operation=operation,
        target_class=target_class,
        fresh_operator_authorization=fresh_operator_authorization,
    )
    handoff_status = _handoff_status(handoff)
    handoff_reason = {
        "invalid": "phase5e.plan.handoff.invalid",
        "rejected": "phase5e.plan.handoff.rejected",
        "unknown": "phase5e.plan.handoff.unknown",
    }.get(handoff_status)
    if handoff_reason is not None:
        final_reason = {
            "invalid": "phase5e.plan.invalid",
            "rejected": "phase5e.plan.rejected",
            "unknown": "phase5e.plan.unknown",
        }[handoff_status]
        return _result(
            status=handoff_status,
            operation=safe_operation,
            target_class=safe_target,
            source_type=source_type,
            file_count=file_count,
            reasons=(handoff_reason, final_reason),
            capability_not_requested=capability_not_requested,
        )

    if safe_operation != "install" or safe_target != TARGET_CLASS:
        mismatch_is_structural = safe_operation == "invalid" or safe_target == "invalid"
        return _result(
            status="invalid" if mismatch_is_structural else "rejected",
            operation=safe_operation,
            target_class=safe_target,
            source_type=source_type,
            file_count=file_count,
            reasons=(
                "phase5e.plan.input.invalid"
                if mismatch_is_structural
                else "phase5e.plan.rejected",
            ),
            capability_not_requested=capability_not_requested,
        )

    source_type, source_reason = _validate_source(registry_entry)
    if source_reason is not None:
        status = (
            "rejected"
            if source_reason == "phase5e.plan.source-path.unsafe"
            else "invalid"
        )
        return _result(
            status=status,
            operation=safe_operation,
            target_class=safe_target,
            source_type=source_type,
            file_count=file_count,
            reasons=(
                source_reason,
                "phase5e.plan.rejected" if status == "rejected" else "phase5e.plan.invalid",
            ),
            capability_not_requested=capability_not_requested,
        )

    if source_type == "git":
        return _result(
            status="rejected",
            operation=safe_operation,
            target_class=safe_target,
            source_type=source_type,
            file_count=file_count,
            reasons=("phase5e.plan.source.unsupported", "phase5e.plan.rejected"),
            capability_not_requested=capability_not_requested,
        )

    file_count, manifest_reason = _validate_manifest(registry_entry)
    if manifest_reason is not None:
        status = (
            "rejected"
            if manifest_reason in {
                "phase5e.plan.source-path.unsafe",
                "phase5e.plan.manifest.collision",
                "phase5e.plan.resource-limit",
            }
            else "invalid"
        )
        return _result(
            status=status,
            operation=safe_operation,
            target_class=safe_target,
            source_type=source_type,
            file_count=file_count,
            reasons=(
                manifest_reason,
                "phase5e.plan.rejected" if status == "rejected" else "phase5e.plan.invalid",
            ),
            capability_not_requested=capability_not_requested,
        )

    return _result(
        status="planned",
        operation=safe_operation,
        target_class=safe_target,
        source_type=source_type,
        file_count=file_count,
        reasons=("phase5e.plan.planned",),
        capability_not_requested=capability_not_requested,
    )


__all__ = [
    "ASSESSMENT_SCOPE",
    "LIMITATION_IDS",
    "MAX_DECLARED_FILES",
    "MAX_PATH_DEPTH",
    "MAX_RELATIVE_PATH_UTF8_BYTES",
    "MAX_SEGMENT_UTF8_BYTES",
    "PLAN_STATUSES",
    "REASON_IDS",
    "SOURCE_TYPES",
    "TARGET_CLASS",
    "evaluate_candidate_install_plan",
]

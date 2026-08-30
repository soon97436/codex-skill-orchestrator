"""Pure validation and digest helpers for candidate transaction journals.

This module defines durable-data contracts only.  It never reads or writes a
journal, an installed-state file, a staging directory, or a managed target.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .errors import SecurityError, ValidationError
from .validation import IDENTIFIER_RE, SHA256_RE, validate_relative_path


SCHEMA_VERSION = 1
OPERATION = "candidate-install"
MAX_MANIFEST_FILES = 4096
MAX_REASON_IDS = 32

PHASES = (
    "PREPARING",
    "PREPARED",
    "QUARANTINE_INTENT",
    "QUARANTINED",
    "PUBLISH_INTENT",
    "PUBLISHED",
    "VERIFIED",
    "STATE_COMMITTING",
    "COMMITTED",
    "ROLLING_BACK",
    "ROLLED_BACK",
    "ABORTED",
    "RECOVERY_REQUIRED",
)

TERMINAL_PHASES = ("COMMITTED", "ROLLED_BACK", "ABORTED", "RECOVERY_REQUIRED")
CLEANUP_STATUSES = (
    "none",
    "cleanup-required",
    "maintenance-required",
    "recovery-required",
)

JOURNAL_KEYS = (
    "schema_version",
    "transaction_id",
    "operation",
    "phase",
    "target_key",
    "skills_root_identity",
    "plan_digest",
    "source_identity_digest",
    "provenance_trust_digest",
    "capability_policy_digest",
    "admission_digest",
    "new_manifest",
    "new_manifest_digest",
    "previous_target",
    "stage_binding",
    "quarantine_binding",
    "installed_state_before_digest",
    "installed_state_after_digest",
    "cleanup_status",
    "reason_ids",
)

_TRANSACTION_ID_RE = re.compile(r"^[0-9a-f]{32}$", flags=re.ASCII)
_TARGET_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$", flags=re.ASCII)
_BINDING_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$", flags=re.ASCII)
_REASON_ID_RE = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$", flags=re.ASCII)
_RESERVED_BASENAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *("com%d" % index for index in range(1, 10)),
    *("lpt%d" % index for index in range(1, 10)),
}

_TRANSITIONS = {
    "PREPARING": {"PREPARED", "ABORTED", "RECOVERY_REQUIRED"},
    "PREPARED": {"QUARANTINE_INTENT", "PUBLISH_INTENT", "ABORTED", "RECOVERY_REQUIRED"},
    "QUARANTINE_INTENT": {"QUARANTINED", "ROLLING_BACK", "RECOVERY_REQUIRED"},
    "QUARANTINED": {"PUBLISH_INTENT", "ROLLING_BACK", "RECOVERY_REQUIRED"},
    "PUBLISH_INTENT": {"PUBLISHED", "ROLLING_BACK", "RECOVERY_REQUIRED"},
    "PUBLISHED": {"VERIFIED", "ROLLING_BACK", "RECOVERY_REQUIRED"},
    "VERIFIED": {"STATE_COMMITTING", "ROLLING_BACK", "RECOVERY_REQUIRED"},
    "STATE_COMMITTING": {"COMMITTED", "ROLLING_BACK", "RECOVERY_REQUIRED"},
    "ROLLING_BACK": {"ROLLED_BACK", "RECOVERY_REQUIRED"},
}


def _exact_keys(value: Mapping[str, Any], expected: Iterable[str], label: str) -> None:
    expected_set = set(expected)
    actual = set(value)
    missing = expected_set - actual
    extra = actual - expected_set
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing, key=str)))
        if extra:
            details.append("unknown " + ", ".join(sorted(extra, key=str)))
        raise ValidationError("%s: %s" % (label, "; ".join(details)))


def _schema_version(value: Any, label: str) -> int:
    if type(value) is not int or value != SCHEMA_VERSION:
        raise ValidationError("%s must be schema version 1" % label)
    return value


def _sequence(value: Any, label: str) -> Sequence[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ValidationError("%s must be a list or tuple" % label)
    return value


def _sha256(value: Any, label: str) -> str:
    if type(value) is not str or SHA256_RE.fullmatch(value) is None:
        raise ValidationError("%s must be a lowercase SHA-256 digest" % label)
    return value


def _manifest_path(value: Any, label: str) -> str:
    if type(value) is not str or not value or "\x00" in value or "\\" in value:
        raise SecurityError("%s must be a non-empty POSIX relative path" % label)
    if value.startswith("/") or ":" in value:
        raise SecurityError("%s must be a portable relative path" % label)
    parts = value.split("/")
    if any(not part or part in {".", ".."} for part in parts):
        raise SecurityError("%s contains an unsafe path segment" % label)
    validate_relative_path(value, label)
    return value


def _target_key(value: Any, label: str) -> str:
    if type(value) is not str or _TARGET_KEY_RE.fullmatch(value) is None:
        raise SecurityError("%s must be one portable ASCII segment" % label)
    if value in {".", ".."} or ".." in value or value.endswith((".", " ")):
        raise SecurityError("%s contains an unsafe segment" % label)
    basename = value.split(".", 1)[0].casefold()
    if basename in _RESERVED_BASENAMES:
        raise SecurityError("%s uses a reserved device name" % label)
    return value


def _binding_name(value: Any, label: str) -> str:
    if type(value) is not str or _BINDING_NAME_RE.fullmatch(value) is None:
        raise SecurityError("%s must be one portable ASCII segment" % label)
    if value in {".", ".."} or ".." in value or value.endswith((".", " ")):
        raise SecurityError("%s contains an unsafe segment" % label)
    if value.lstrip(".").split(".", 1)[0].casefold() in _RESERVED_BASENAMES:
        raise SecurityError("%s uses a reserved device name" % label)
    return value


def _root_identity(value: Any, label: str = "skills_root_identity") -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError("%s must be an object" % label)
    _exact_keys(value, ("kind", "device", "inode"), label)
    if value["kind"] != "posix-dev-ino":
        raise ValidationError("%s kind is unsupported" % label)
    if type(value["device"]) is not int or value["device"] < 0:
        raise ValidationError("%s device must be a non-negative integer" % label)
    if type(value["inode"]) is not int or value["inode"] <= 0:
        raise ValidationError("%s inode must be a positive integer" % label)
    return {
        "kind": "posix-dev-ino",
        "device": value["device"],
        "inode": value["inode"],
    }


def validate_transaction_id(value: Any) -> str:
    """Validate the operational, non-authoritative transaction identifier."""

    if type(value) is not str or _TRANSACTION_ID_RE.fullmatch(value) is None:
        raise ValidationError("transaction_id must be exactly 32 lowercase hex characters")
    return value


def normalize_exact_manifest(entries: Any) -> List[Dict[str, Any]]:
    """Return a detached, path-sorted V1 manifest without applying byte limits."""

    sequence = _sequence(entries, "manifest")
    if len(sequence) > MAX_MANIFEST_FILES:
        raise ValidationError("manifest exceeds the maximum file count")
    normalized: List[Dict[str, Any]] = []
    seen = set()
    for index, entry in enumerate(sequence):
        label = "manifest entry %d" % index
        if not isinstance(entry, Mapping):
            raise ValidationError("%s must be an object" % label)
        _exact_keys(entry, ("path", "sha256", "size"), label)
        path = _manifest_path(entry["path"], "%s path" % label)
        if path in seen:
            raise ValidationError("manifest contains duplicate paths")
        seen.add(path)
        digest = _sha256(entry["sha256"], "%s sha256" % label)
        size = entry["size"]
        if type(size) is not int or size < 0:
            raise ValidationError("%s size must be a non-negative integer" % label)
        normalized.append({"path": path, "sha256": digest, "size": size})
    normalized.sort(key=lambda item: item["path"])
    return normalized


def _compact_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _domain_digest(domain: bytes, value: Any) -> str:
    return hashlib.sha256(domain + _compact_json(value)).hexdigest()


def manifest_digest(entries: Any) -> str:
    """Digest normalized manifest content with the 4C1B domain separator."""

    return _domain_digest(b"cso-candidate-manifest-v1\0", normalize_exact_manifest(entries))


def _reason_ids(value: Any) -> List[str]:
    sequence = _sequence(value, "reason_ids")
    if len(sequence) > MAX_REASON_IDS:
        raise ValidationError("reason_ids exceeds the maximum count")
    normalized = []
    for index, reason_id in enumerate(sequence):
        if type(reason_id) is not str or _REASON_ID_RE.fullmatch(reason_id) is None:
            raise ValidationError("reason_ids[%d] is not a stable lowercase identifier" % index)
        normalized.append(reason_id)
    if len(set(normalized)) != len(normalized):
        raise ValidationError("reason_ids contains duplicates")
    return sorted(normalized)


def _binding(value: Any, label: str, required: bool = False) -> Optional[Dict[str, str]]:
    if value is None:
        if required:
            raise ValidationError("%s is required" % label)
        return None
    if not isinstance(value, Mapping):
        raise ValidationError("%s must be null or an object" % label)
    _exact_keys(value, ("relative_name", "manifest_digest"), label)
    return {
        "relative_name": _binding_name(value["relative_name"], "%s relative_name" % label),
        "manifest_digest": _sha256(value["manifest_digest"], "%s manifest_digest" % label),
    }


def _previous_target(value: Any) -> Optional[Dict[str, Any]]:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValidationError("previous_target must be null or an object")
    _exact_keys(value, ("classification", "manifest", "manifest_digest"), "previous_target")
    if value["classification"] != "managed-current":
        raise ValidationError("previous_target classification is not replaceable")
    manifest = normalize_exact_manifest(value["manifest"])
    digest = _sha256(value["manifest_digest"], "previous_target manifest_digest")
    if digest != manifest_digest(manifest):
        raise ValidationError("previous_target manifest digest does not match")
    return {
        "classification": "managed-current",
        "manifest": manifest,
        "manifest_digest": digest,
    }


def is_terminal_phase(phase: Any) -> bool:
    return type(phase) is str and phase in TERMINAL_PHASES


def validate_phase_transition(current_phase: Any, next_phase: Any) -> bool:
    """Validate one explicit edge of the closed journal phase machine."""

    if current_phase not in PHASES or next_phase not in PHASES:
        raise ValidationError("unsupported transaction phase")
    if next_phase not in _TRANSITIONS.get(current_phase, set()):
        raise ValidationError("transaction phase transition is not allowed")
    return True


def _phase_invariants(
    phase: str,
    *,
    previous_target: Optional[Dict[str, Any]],
    stage_binding: Optional[Dict[str, str]],
    quarantine_binding: Optional[Dict[str, str]],
    installed_state_after_digest: Optional[str],
    cleanup_status: str,
    reason_ids: List[str],
) -> None:
    if phase == "PREPARING":
        if quarantine_binding is not None or installed_state_after_digest is not None:
            raise ValidationError("PREPARING has impossible fields")
    elif phase == "PREPARED":
        if stage_binding is None or installed_state_after_digest is not None:
            raise ValidationError("PREPARED has impossible fields")
    elif phase in {"QUARANTINE_INTENT", "QUARANTINED"}:
        if previous_target is None or stage_binding is None:
            raise ValidationError("quarantine phase requires managed target and stage")
        if phase == "QUARANTINED" and quarantine_binding is None:
            raise ValidationError("QUARANTINED requires a quarantine binding")
    elif phase in {"PUBLISH_INTENT", "PUBLISHED"}:
        if stage_binding is None:
            raise ValidationError("publish phase requires a stage binding")
    elif phase == "VERIFIED":
        if installed_state_after_digest is not None:
            raise ValidationError("VERIFIED cannot claim committed state")
    elif phase == "STATE_COMMITTING":
        if installed_state_after_digest is None:
            raise ValidationError("STATE_COMMITTING requires the after-state digest")
    elif phase == "COMMITTED":
        if installed_state_after_digest is None:
            raise ValidationError("COMMITTED requires the after-state digest")
        if cleanup_status not in {"none", "maintenance-required"}:
            raise ValidationError("COMMITTED has invalid cleanup status")
    elif phase == "ROLLED_BACK":
        if installed_state_after_digest is not None:
            raise ValidationError("ROLLED_BACK cannot claim committed state")
    elif phase == "ABORTED":
        if installed_state_after_digest is not None or cleanup_status == "recovery-required":
            raise ValidationError("ABORTED has impossible fields")
    elif phase == "RECOVERY_REQUIRED":
        if cleanup_status != "recovery-required" or not reason_ids:
            raise ValidationError("RECOVERY_REQUIRED requires recovery cleanup and a reason")


def validate_journal_document(document: Any) -> Dict[str, Any]:
    """Validate and detach a journal document without granting authority."""

    if not isinstance(document, Mapping):
        raise ValidationError("journal document must be an object")
    _exact_keys(document, JOURNAL_KEYS, "journal document")
    _schema_version(document["schema_version"], "journal schema_version")
    transaction_id = validate_transaction_id(document["transaction_id"])
    if document["operation"] != OPERATION:
        raise ValidationError("journal operation is unsupported")
    phase = document["phase"]
    if type(phase) is not str or phase not in PHASES:
        raise ValidationError("journal phase is unsupported")
    target_key = _target_key(document["target_key"], "target_key")
    root_identity = _root_identity(document["skills_root_identity"])
    plan_digest = _sha256(document["plan_digest"], "plan_digest")
    source_identity_digest = _sha256(
        document["source_identity_digest"], "source_identity_digest"
    )
    provenance_trust_digest = _sha256(
        document["provenance_trust_digest"], "provenance_trust_digest"
    )
    capability_policy_digest = _sha256(
        document["capability_policy_digest"], "capability_policy_digest"
    )
    admission_digest = _sha256(document["admission_digest"], "admission_digest")
    new_manifest = normalize_exact_manifest(document["new_manifest"])
    new_manifest_digest = _sha256(document["new_manifest_digest"], "new_manifest_digest")
    if new_manifest_digest != manifest_digest(new_manifest):
        raise ValidationError("new_manifest_digest does not match new_manifest")
    previous_target = _previous_target(document["previous_target"])
    stage_binding = _binding(document["stage_binding"], "stage_binding")
    quarantine_binding = _binding(document["quarantine_binding"], "quarantine_binding")
    before_digest = document["installed_state_before_digest"]
    if before_digest is not None:
        before_digest = _sha256(before_digest, "installed_state_before_digest")
    after_digest = document["installed_state_after_digest"]
    if after_digest is not None:
        after_digest = _sha256(after_digest, "installed_state_after_digest")
    cleanup_status = document["cleanup_status"]
    if type(cleanup_status) is not str or cleanup_status not in CLEANUP_STATUSES:
        raise ValidationError("cleanup_status is unsupported")
    reason_ids = _reason_ids(document["reason_ids"])
    _phase_invariants(
        phase,
        previous_target=previous_target,
        stage_binding=stage_binding,
        quarantine_binding=quarantine_binding,
        installed_state_after_digest=after_digest,
        cleanup_status=cleanup_status,
        reason_ids=reason_ids,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "transaction_id": transaction_id,
        "operation": OPERATION,
        "phase": phase,
        "target_key": target_key,
        "skills_root_identity": root_identity,
        "plan_digest": plan_digest,
        "source_identity_digest": source_identity_digest,
        "provenance_trust_digest": provenance_trust_digest,
        "capability_policy_digest": capability_policy_digest,
        "admission_digest": admission_digest,
        "new_manifest": new_manifest,
        "new_manifest_digest": new_manifest_digest,
        "previous_target": previous_target,
        "stage_binding": stage_binding,
        "quarantine_binding": quarantine_binding,
        "installed_state_before_digest": before_digest,
        "installed_state_after_digest": after_digest,
        "cleanup_status": cleanup_status,
        "reason_ids": reason_ids,
    }


def journal_digest(document: Any) -> str:
    """Digest validated journal content; the digest is not an authority token."""

    return _domain_digest(b"cso-candidate-journal-v1\0", validate_journal_document(document))

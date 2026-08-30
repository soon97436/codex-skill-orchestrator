"""Pure validation and digest helpers for future installed-state documents.

Installed-state persistence, target inspection, authorization, and recovery are
deliberately outside this module.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Dict, Optional

from .errors import SecurityError, ValidationError
from .transaction_journal import manifest_digest, normalize_exact_manifest
from .validation import IDENTIFIER_RE, SEMVER_RE


SCHEMA_VERSION = 1
OPERATION = "candidate-install"
SOURCE_TYPES = ("bundled", "git")
MAX_CSO_VERSION_BYTES = 64

INSTALLED_STATE_KEYS = (
    "schema_version",
    "skill_id",
    "skill_version",
    "registry_entry_digest",
    "source_type",
    "source_identity_digest",
    "target_key",
    "skills_root_identity",
    "operation",
    "transaction_id",
    "declared_manifest",
    "declared_manifest_digest",
    "installed_manifest",
    "installed_manifest_digest",
    "provenance_trust_digest",
    "capability_policy_digest",
    "admission_plan_digest",
    "cso_version",
)

_TRANSACTION_ID_RE = re.compile(r"^[0-9a-f]{32}$", flags=re.ASCII)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$", flags=re.ASCII)
_TARGET_KEY_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$", flags=re.ASCII)
_CSO_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$", flags=re.ASCII)
_RESERVED_BASENAMES = {
    "con",
    "prn",
    "aux",
    "nul",
    *("com%d" % index for index in range(1, 10)),
    *("lpt%d" % index for index in range(1, 10)),
}


def _exact_keys(value: Mapping[str, Any], expected, label: str) -> None:
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


def _root_identity(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError("skills_root_identity must be an object")
    _exact_keys(value, ("kind", "device", "inode"), "skills_root_identity")
    if value["kind"] != "posix-dev-ino":
        raise ValidationError("skills_root_identity kind is unsupported")
    if type(value["device"]) is not int or value["device"] < 0:
        raise ValidationError("skills_root_identity device must be non-negative")
    if type(value["inode"]) is not int or value["inode"] <= 0:
        raise ValidationError("skills_root_identity inode must be positive")
    return {
        "kind": "posix-dev-ino",
        "device": value["device"],
        "inode": value["inode"],
    }


def _target_key(value: Any) -> str:
    if type(value) is not str or _TARGET_KEY_RE.fullmatch(value) is None:
        raise SecurityError("target_key must be one portable ASCII segment")
    if value in {".", ".."} or ".." in value or value.endswith((".", " ")):
        raise SecurityError("target_key contains an unsafe segment")
    if value.split(".", 1)[0].casefold() in _RESERVED_BASENAMES:
        raise SecurityError("target_key uses a reserved device name")
    return value


def _digest(value: Any, label: str) -> str:
    if type(value) is not str or _SHA256_RE.fullmatch(value) is None:
        raise ValidationError("%s must be a lowercase SHA-256 digest" % label)
    return value


def _transaction_id(value: Any) -> str:
    if type(value) is not str or _TRANSACTION_ID_RE.fullmatch(value) is None:
        raise ValidationError("transaction_id must be exactly 32 lowercase hex characters")
    return value


def _manifest(value: Any, label: str):
    try:
        normalized = normalize_exact_manifest(value)
    except (ValidationError, SecurityError):
        raise
    if not normalized:
        raise ValidationError("%s must not be empty" % label)
    return normalized


def _compact_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def installed_state_digest(document: Any) -> str:
    """Digest validated installed-state content, not an authority token."""

    normalized = validate_installed_state_document(document)
    return hashlib.sha256(
        b"cso-installed-state-v1\0" + _compact_json(normalized)
    ).hexdigest()


def validate_installed_state_document(document: Any) -> Dict[str, Any]:
    """Validate and detach one V1 installed-state document."""

    if not isinstance(document, Mapping):
        raise ValidationError("installed-state document must be an object")
    _exact_keys(document, INSTALLED_STATE_KEYS, "installed-state document")
    if type(document["schema_version"]) is not int or document["schema_version"] != SCHEMA_VERSION:
        raise ValidationError("installed-state schema_version must be 1")
    skill_id = document["skill_id"]
    if type(skill_id) is not str or IDENTIFIER_RE.fullmatch(skill_id) is None:
        raise ValidationError("skill_id is not a normalized identifier")
    skill_version = document["skill_version"]
    if type(skill_version) is not str or SEMVER_RE.fullmatch(skill_version) is None:
        raise ValidationError("skill_version is not a valid semantic version")
    registry_entry_digest = _digest(document["registry_entry_digest"], "registry_entry_digest")
    source_type = document["source_type"]
    if type(source_type) is not str or source_type not in SOURCE_TYPES:
        raise ValidationError("source_type is unsupported")
    source_identity_digest = _digest(
        document["source_identity_digest"], "source_identity_digest"
    )
    target_key = _target_key(document["target_key"])
    root_identity = _root_identity(document["skills_root_identity"])
    if document["operation"] != OPERATION:
        raise ValidationError("operation is unsupported")
    transaction_id = _transaction_id(document["transaction_id"])

    declared_manifest = _manifest(document["declared_manifest"], "declared_manifest")
    declared_manifest_digest = _digest(
        document["declared_manifest_digest"], "declared_manifest_digest"
    )
    if declared_manifest_digest != manifest_digest(declared_manifest):
        raise ValidationError("declared_manifest_digest does not match declared_manifest")

    installed_manifest = _manifest(document["installed_manifest"], "installed_manifest")
    installed_manifest_digest = _digest(
        document["installed_manifest_digest"], "installed_manifest_digest"
    )
    if installed_manifest_digest != manifest_digest(installed_manifest):
        raise ValidationError("installed_manifest_digest does not match installed_manifest")
    if declared_manifest != installed_manifest:
        raise ValidationError("declared and installed manifests differ")

    provenance_trust_digest = _digest(
        document["provenance_trust_digest"], "provenance_trust_digest"
    )
    capability_policy_digest = _digest(
        document["capability_policy_digest"], "capability_policy_digest"
    )
    admission_plan_digest = _digest(
        document["admission_plan_digest"], "admission_plan_digest"
    )
    cso_version: Optional[str] = document["cso_version"]
    if cso_version is not None:
        if (
            type(cso_version) is not str
            or len(cso_version.encode("utf-8")) > MAX_CSO_VERSION_BYTES
            or _CSO_VERSION_RE.fullmatch(cso_version) is None
        ):
            raise ValidationError("cso_version is not a bounded safe string")

    return {
        "schema_version": SCHEMA_VERSION,
        "skill_id": skill_id,
        "skill_version": skill_version,
        "registry_entry_digest": registry_entry_digest,
        "source_type": source_type,
        "source_identity_digest": source_identity_digest,
        "target_key": target_key,
        "skills_root_identity": root_identity,
        "operation": OPERATION,
        "transaction_id": transaction_id,
        "declared_manifest": declared_manifest,
        "declared_manifest_digest": declared_manifest_digest,
        "installed_manifest": installed_manifest,
        "installed_manifest_digest": installed_manifest_digest,
        "provenance_trust_digest": provenance_trust_digest,
        "capability_policy_digest": capability_policy_digest,
        "admission_plan_digest": admission_plan_digest,
        "cso_version": cso_version,
    }

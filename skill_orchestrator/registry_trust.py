"""Pure deterministic trust decisions for normalized registry metadata."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any, Dict, List, Optional, Tuple


TRUST_DIMENSIONS = (
    "registry",
    "source_policy",
    "source_identity",
    "provenance",
    "license",
    "integrity",
    "capability_policy",
)
DECISION_STATUSES = ("pass", "fail", "unknown", "not-applicable")
ADMISSION_STATUSES = ("admissible", "rejected", "unknown")
ADMISSION_REASON_IDS = {
    "admissible": "trust.admission.admissible",
    "rejected": "trust.admission.rejected",
    "unknown": "trust.admission.unknown",
}
SUPPORTED_SOURCE_TYPES = ("bundled", "git")
SUPPORTED_PROVENANCE_CLASSES = ("first-party", "third-party")
EVIDENCE_FIELDS = (
    "registry_valid",
    "source_revision_immutable",
    "provenance_complete",
    "integrity_verified",
)
LIMITATION_IDS = (
    "trust.limit.remote-fetch-disabled",
    "trust.limit.capability-enforcement-not-implemented",
)

TRUST_REASON_IDS = (
    "trust.registry.valid",
    "trust.registry.invalid",
    "trust.registry.unknown",
    "trust.source.allowlisted",
    "trust.source.disallowed",
    "trust.source.identity-not-applicable",
    "trust.source.revision-immutable",
    "trust.source.revision-not-immutable",
    "trust.source.revision-unknown",
    "trust.provenance.complete",
    "trust.provenance.incomplete",
    "trust.provenance.unknown",
    "trust.provenance.class-disallowed",
    "trust.license.approved",
    "trust.license.rejected",
    "trust.integrity.verified",
    "trust.integrity.failed",
    "trust.integrity.unknown",
    "trust.integrity.not-required",
    "trust.capability.not-evaluated",
    "trust.admission.admissible",
    "trust.admission.rejected",
    "trust.admission.unknown",
)

_IDENTIFIER_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", flags=re.ASCII)


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("%s must be an object" % label)
    return value


def _required(mapping: Mapping[str, Any], key: str, label: str) -> Any:
    if key not in mapping:
        raise ValueError("%s is missing %s" % (label, key))
    return mapping[key]


def _require_bool_or_none(value: Any, label: str) -> Optional[bool]:
    if value is not None and type(value) is not bool:
        raise ValueError("%s must be boolean or None" % label)
    return value


def _require_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError("%s must be boolean" % label)
    return value


def _require_strings(value: Any, label: str) -> Tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("%s must be a string sequence" % label)
    result = []
    for item in value:
        if type(item) is not str or not item:
            raise ValueError("%s must contain non-empty strings" % label)
        result.append(item)
    return tuple(result)


def _validate_entry(
    entry: Mapping[str, Any],
) -> Tuple[str, str, Any, str, bool, Optional[str]]:
    skill_id = _required(entry, "id", "entry")
    if type(skill_id) is not str or not _IDENTIFIER_RE.fullmatch(skill_id):
        raise ValueError("entry id is not a normalized identifier")

    source = _require_mapping(_required(entry, "source", "entry"), "entry source")
    source_type = _required(source, "type", "entry source")
    if type(source_type) is not str or source_type not in SUPPORTED_SOURCE_TYPES:
        raise ValueError("entry source type is unsupported")
    revision = _required(source, "revision", "entry source")
    if revision is not None and type(revision) is not str:
        raise ValueError("entry source revision must be string or None")
    if source_type == "git" and (not isinstance(revision, str) or not revision):
        raise ValueError("git source revision must be non-empty")
    if source_type == "bundled" and revision is not None:
        raise ValueError("bundled source revision must be None")

    license_info = _require_mapping(
        _required(entry, "license", "entry"), "entry license"
    )
    spdx = _required(license_info, "spdx", "entry license")
    if type(spdx) is not str or not spdx:
        raise ValueError("entry license SPDX must be a non-empty string")
    redistribution = _require_bool(
        _required(license_info, "redistribution", "entry license"),
        "entry license redistribution",
    )
    provenance_class: Optional[str] = None
    if "provenance" in entry:
        provenance = _require_mapping(entry["provenance"], "entry provenance")
        if set(provenance) != {"class"}:
            raise ValueError("entry provenance must contain only class")
        provenance_class = provenance["class"]
        if provenance_class not in SUPPORTED_PROVENANCE_CLASSES:
            raise ValueError("entry provenance class is unsupported")
    return skill_id, source_type, revision, spdx, redistribution, provenance_class


def _validate_policy(
    policy: Mapping[str, Any],
) -> Tuple[Tuple[str, ...], Tuple[str, ...], bool, bool, Optional[Tuple[str, ...]]]:
    allowed_source_types = _require_strings(
        _required(policy, "allowed_source_types", "policy"),
        "policy allowed_source_types",
    )
    if any(source_type not in SUPPORTED_SOURCE_TYPES for source_type in allowed_source_types):
        raise ValueError("policy allowed_source_types contains an unsupported value")
    allowed_spdx_licenses = _require_strings(
        _required(policy, "allowed_spdx_licenses", "policy"),
        "policy allowed_spdx_licenses",
    )
    require_checksums = _require_bool(
        _required(policy, "require_checksums", "policy"),
        "policy require_checksums",
    )
    require_immutable_revision = _require_bool(
        _required(policy, "require_immutable_revision_for_remote", "policy"),
        "policy require_immutable_revision_for_remote",
    )
    allowed_provenance_classes: Optional[Tuple[str, ...]] = None
    if "allowed_provenance_classes" in policy:
        allowed_provenance_classes = _require_strings(
            policy["allowed_provenance_classes"],
            "policy allowed_provenance_classes",
        )
        if any(
            provenance_class not in SUPPORTED_PROVENANCE_CLASSES
            for provenance_class in allowed_provenance_classes
        ):
            raise ValueError("policy allowed_provenance_classes contains an unsupported value")
        if len(set(allowed_provenance_classes)) != len(allowed_provenance_classes):
            raise ValueError("policy allowed_provenance_classes contains duplicates")
    return (
        allowed_source_types,
        allowed_spdx_licenses,
        require_checksums,
        require_immutable_revision,
        allowed_provenance_classes,
    )


def _validate_evidence(evidence: Mapping[str, Any]) -> Dict[str, Optional[bool]]:
    normalized: Dict[str, Optional[bool]] = {}
    for field in EVIDENCE_FIELDS:
        normalized[field] = _require_bool_or_none(
            evidence.get(field), "evidence %s" % field
        )
    return normalized


def _decision(dimension: str, status: str, reason_id: str) -> Dict[str, Any]:
    if dimension not in TRUST_DIMENSIONS:
        raise ValueError("unsupported trust dimension")
    if status not in DECISION_STATUSES:
        raise ValueError("unsupported decision status")
    if reason_id not in TRUST_REASON_IDS:
        raise ValueError("unsupported trust reason")
    return {
        "dimension": dimension,
        "status": status,
        "reason_ids": [reason_id],
    }


def _admission_status(decisions: Sequence[Mapping[str, Any]]) -> str:
    statuses = [decision["status"] for decision in decisions]
    if "fail" in statuses:
        return "rejected"
    if "unknown" in statuses:
        return "unknown"
    return "admissible"


def _admission_reason(status: str) -> str:
    return ADMISSION_REASON_IDS[status]


def evaluate_registry_trust(
    entry: Mapping[str, Any],
    *,
    policy: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> Dict[str, Any]:
    """Evaluate normalized registry facts without external effects.

    The caller supplies normalized metadata, policy, and evidence.  Missing
    evidence fields are treated as unavailable (``unknown``); malformed
    values raise ``ValueError`` instead of becoming trust decisions.
    """

    if not isinstance(entry, Mapping):
        raise TypeError("entry must be an object")
    if not isinstance(policy, Mapping):
        raise TypeError("policy must be an object")
    if not isinstance(evidence, Mapping):
        raise TypeError("evidence must be an object")

    (
        skill_id,
        source_type,
        _revision,
        spdx,
        redistribution,
        provenance_class,
    ) = _validate_entry(entry)
    (
        allowed_source_types,
        allowed_spdx_licenses,
        require_checksums,
        require_immutable_revision,
        allowed_provenance_classes,
    ) = _validate_policy(policy)
    facts = _validate_evidence(evidence)

    profile_provenance_mode = (provenance_class is not None) or (
        allowed_provenance_classes is not None
    )
    if profile_provenance_mode and (
        provenance_class is None or allowed_provenance_classes is None
    ):
        raise ValueError(
            "profile-aware provenance fields must be supplied together"
        )

    decisions: List[Dict[str, Any]] = []
    registry_valid = facts["registry_valid"]
    if registry_valid is True:
        decisions.append(_decision("registry", "pass", "trust.registry.valid"))
    elif registry_valid is False:
        decisions.append(_decision("registry", "fail", "trust.registry.invalid"))
    else:
        decisions.append(_decision("registry", "unknown", "trust.registry.unknown"))

    if source_type in allowed_source_types:
        decisions.append(
            _decision("source_policy", "pass", "trust.source.allowlisted")
        )
    else:
        decisions.append(
            _decision("source_policy", "fail", "trust.source.disallowed")
        )

    if source_type == "bundled":
        decisions.append(
            _decision(
                "source_identity",
                "not-applicable",
                "trust.source.identity-not-applicable",
            )
        )
    elif require_immutable_revision:
        revision_immutable = facts["source_revision_immutable"]
        if revision_immutable is True:
            decisions.append(
                _decision(
                    "source_identity",
                    "pass",
                    "trust.source.revision-immutable",
                )
            )
        elif revision_immutable is False:
            decisions.append(
                _decision(
                    "source_identity",
                    "fail",
                    "trust.source.revision-not-immutable",
                )
            )
        else:
            decisions.append(
                _decision(
                    "source_identity",
                    "unknown",
                    "trust.source.revision-unknown",
                )
            )
    else:
        decisions.append(
            _decision(
                "source_identity",
                "not-applicable",
                "trust.source.identity-not-applicable",
            )
        )

    provenance_complete = facts["provenance_complete"]
    if provenance_complete is True:
        if profile_provenance_mode and provenance_class not in allowed_provenance_classes:
            decisions.append(
                _decision(
                    "provenance",
                    "fail",
                    "trust.provenance.class-disallowed",
                )
            )
        else:
            decisions.append(
                _decision("provenance", "pass", "trust.provenance.complete")
            )
    elif provenance_complete is False:
        decisions.append(
            _decision("provenance", "fail", "trust.provenance.incomplete")
        )
    else:
        decisions.append(
            _decision("provenance", "unknown", "trust.provenance.unknown")
        )

    if spdx in allowed_spdx_licenses and redistribution is True:
        decisions.append(
            _decision("license", "pass", "trust.license.approved")
        )
    else:
        decisions.append(
            _decision("license", "fail", "trust.license.rejected")
        )

    if not require_checksums:
        decisions.append(
            _decision("integrity", "not-applicable", "trust.integrity.not-required")
        )
    else:
        integrity_verified = facts["integrity_verified"]
        if integrity_verified is True:
            decisions.append(
                _decision("integrity", "pass", "trust.integrity.verified")
            )
        elif integrity_verified is False:
            decisions.append(
                _decision("integrity", "fail", "trust.integrity.failed")
            )
        else:
            decisions.append(
                _decision("integrity", "unknown", "trust.integrity.unknown")
            )

    decisions.append(
        _decision(
            "capability_policy",
            "not-applicable",
            "trust.capability.not-evaluated",
        )
    )

    status = _admission_status(decisions)
    reasons = [
        reason_id
        for decision in decisions
        for reason_id in decision["reason_ids"]
    ]
    reasons.append(_admission_reason(status))
    limitations = []
    if source_type == "git":
        limitations.append("trust.limit.remote-fetch-disabled")
    limitations.append("trust.limit.capability-enforcement-not-implemented")
    return {
        "schema_version": 1,
        "status": status,
        "skill_id": skill_id,
        "decisions": decisions,
        "reasons": reasons,
        "limitations": limitations,
        "truncated": False,
    }

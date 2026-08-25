"""Pure deterministic trust-policy profile validation and resolution."""

from __future__ import annotations

import copy
import re
from typing import Any, Dict, Mapping, Optional, Sequence

from .errors import ValidationError


PROFILE_IDENTIFIER_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", flags=re.ASCII)
SUPPORTED_SOURCE_TYPES = ("bundled", "git")
SUPPORTED_PROVENANCE_CLASSES = ("first-party", "third-party")
TOP_LEVEL_KEYS = {"schema_version", "default_profile", "profiles"}
PROFILE_KEYS = {
    "id",
    "source_policy",
    "provenance_policy",
    "license_policy",
}
PHASE5C_POLICY_KEYS = (
    "allowed_source_types",
    "allowed_spdx_licenses",
    "require_checksums",
    "require_immutable_revision_for_remote",
)


def _expect_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError("%s must be an object" % label)
    return value


def _expect_exact_keys(mapping: Mapping[str, Any], expected: set, label: str) -> None:
    actual = set(mapping)
    missing = expected - actual
    extra = actual - expected
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if extra:
            details.append("unknown " + ", ".join(sorted(extra)))
        raise ValidationError("%s: %s" % (label, "; ".join(details)))


def _expect_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValidationError("%s must be a non-empty string" % label)
    return value


def _expect_unique_string_list(value: Any, label: str) -> Sequence[str]:
    if not isinstance(value, list) or not value:
        raise ValidationError("%s must be a non-empty string array" % label)
    if any(not isinstance(item, str) or not item for item in value):
        raise ValidationError("%s must contain non-empty strings" % label)
    if len(set(value)) != len(value):
        raise ValidationError("%s contains duplicates" % label)
    return value


def _validate_operational_policy(policy: Mapping[str, Any]) -> None:
    _expect_mapping(policy, "operational policy")
    for key in PHASE5C_POLICY_KEYS:
        if key not in policy:
            raise ValidationError("operational policy is missing %s" % key)
    source_types = _expect_unique_string_list(
        policy["allowed_source_types"],
        "operational policy allowed_source_types",
    )
    if any(source_type not in SUPPORTED_SOURCE_TYPES for source_type in source_types):
        raise ValidationError("operational policy contains an unsupported source type")
    _expect_unique_string_list(
        policy["allowed_spdx_licenses"],
        "operational policy allowed_spdx_licenses",
    )
    for key in ("require_checksums", "require_immutable_revision_for_remote"):
        if type(policy[key]) is not bool:
            raise ValidationError("operational policy %s must be boolean" % key)


def _validate_profile(profile: Mapping[str, Any], operational_policy: Mapping[str, Any]) -> str:
    _expect_exact_keys(profile, PROFILE_KEYS, "trust profile")
    profile_id = _expect_string(profile["id"], "trust profile id")
    if not PROFILE_IDENTIFIER_RE.fullmatch(profile_id):
        raise ValidationError("trust profile id is not normalized")

    source_policy = _expect_mapping(profile["source_policy"], "trust profile source_policy")
    _expect_exact_keys(source_policy, {"allowed_source_types"}, "trust profile source_policy")
    source_types = _expect_unique_string_list(
        source_policy["allowed_source_types"],
        "trust profile allowed_source_types",
    )
    if any(source_type not in SUPPORTED_SOURCE_TYPES for source_type in source_types):
        raise ValidationError("trust profile contains an unsupported source type")
    if not set(source_types).issubset(set(operational_policy["allowed_source_types"])):
        raise ValidationError("trust profile weakens the operational source floor")

    provenance_policy = _expect_mapping(
        profile["provenance_policy"],
        "trust profile provenance_policy",
    )
    _expect_exact_keys(
        provenance_policy,
        {"allowed_classes"},
        "trust profile provenance_policy",
    )
    provenance_classes = _expect_unique_string_list(
        provenance_policy["allowed_classes"],
        "trust profile allowed_classes",
    )
    if any(provenance_class not in SUPPORTED_PROVENANCE_CLASSES for provenance_class in provenance_classes):
        raise ValidationError("trust profile contains an unsupported provenance class")

    license_policy = _expect_mapping(profile["license_policy"], "trust profile license_policy")
    _expect_exact_keys(
        license_policy,
        {"allowed_spdx_licenses"},
        "trust profile license_policy",
    )
    licenses = _expect_unique_string_list(
        license_policy["allowed_spdx_licenses"],
        "trust profile allowed_spdx_licenses",
    )
    if not set(licenses).issubset(set(operational_policy["allowed_spdx_licenses"])):
        raise ValidationError("trust profile weakens the operational license floor")
    return profile_id


def validate_trust_profile_document(
    document: Mapping[str, Any],
    operational_policy: Mapping[str, Any],
) -> Dict[str, Any]:
    """Validate a profile document against the operational security floor."""

    _validate_operational_policy(operational_policy)
    profile_document = _expect_mapping(document, "trust profiles")
    _expect_exact_keys(profile_document, TOP_LEVEL_KEYS, "trust profiles")
    if profile_document["schema_version"] != 1:
        raise ValidationError("trust profiles has unsupported schema_version")
    default_profile = _expect_string(profile_document["default_profile"], "default_profile")
    profiles = profile_document["profiles"]
    if not isinstance(profiles, list) or not profiles:
        raise ValidationError("trust profiles profiles must be a non-empty array")

    seen_ids = set()
    for index, profile in enumerate(profiles):
        profile_mapping = _expect_mapping(profile, "trust profile %d" % index)
        profile_id = _validate_profile(profile_mapping, operational_policy)
        if profile_id in seen_ids:
            raise ValidationError("duplicate trust profile id: %s" % profile_id)
        seen_ids.add(profile_id)
    if default_profile not in seen_ids:
        raise ValidationError("default_profile does not resolve to a profile")
    return copy.deepcopy(dict(profile_document))


def resolve_trust_policy(
    profile_document: Mapping[str, Any],
    operational_policy: Mapping[str, Any],
    profile_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve one profile into the normalized Phase 5A policy shape."""

    validated = validate_trust_profile_document(profile_document, operational_policy)
    requested = validated["default_profile"] if profile_id is None else profile_id
    if not isinstance(requested, str) or requested == "":
        raise ValidationError("requested trust profile id is invalid")
    matches = [profile for profile in validated["profiles"] if profile["id"] == requested]
    if len(matches) != 1:
        raise ValidationError("unknown trust profile: %s" % requested)
    profile = matches[0]
    source_types = sorted(profile["source_policy"]["allowed_source_types"])
    licenses = sorted(profile["license_policy"]["allowed_spdx_licenses"])
    provenance_classes = sorted(profile["provenance_policy"]["allowed_classes"])
    return {
        "profile_id": requested,
        "allowed_source_types": source_types,
        "allowed_spdx_licenses": licenses,
        "allowed_provenance_classes": provenance_classes,
        "require_checksums": operational_policy["require_checksums"],
        "require_immutable_revision_for_remote": operational_policy[
            "require_immutable_revision_for_remote"
        ],
    }

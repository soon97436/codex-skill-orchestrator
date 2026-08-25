"""Project validated registry evidence into deterministic trust decisions."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping

from .registry_trust import evaluate_registry_trust
from .validation import validate_registry_snapshot


PHASE5A_POLICY_KEYS = (
    "allowed_source_types",
    "allowed_spdx_licenses",
    "require_checksums",
    "require_immutable_revision_for_remote",
)


def _project_entry(entry: Mapping[str, Any]) -> Dict[str, Any]:
    source = entry["source"]
    license_info = entry["license"]
    return {
        "id": entry["id"],
        "source": {
            "type": source["type"],
            "revision": source["revision"],
        },
        "license": {
            "spdx": license_info["spdx"],
            "redistribution": license_info["redistribution"],
        },
    }


def _project_policy(policy: Mapping[str, Any]) -> Dict[str, Any]:
    return {key: policy[key] for key in PHASE5A_POLICY_KEYS}


def _derive_evidence(
    entry: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> Dict[str, Any]:
    source_type = entry["source"]["type"]
    source_revision_immutable = None
    if source_type == "git" and policy["require_immutable_revision_for_remote"]:
        source_revision_immutable = True
    return {
        "registry_valid": True,
        "source_revision_immutable": source_revision_immutable,
        "provenance_complete": True,
        "integrity_verified": True,
    }


def evaluate_project_registry_trust(project_root: Path) -> Dict[str, Any]:
    """Evaluate all validated project skills without rereading trust inputs.

    Validation errors propagate before this adapter produces any trust result.
    The adapter only projects validated metadata and derives evidence that was
    established by the same validation pass.
    """

    snapshot = validate_registry_snapshot(project_root)
    registry = snapshot["registry"]
    policy = _project_policy(snapshot["policy"])
    skills = []
    for skill_id in sorted(registry):
        entry = registry[skill_id]
        normalized_entry = _project_entry(entry)
        evidence = _derive_evidence(normalized_entry, policy)
        skills.append(
            evaluate_registry_trust(
                normalized_entry,
                policy=policy,
                evidence=evidence,
            )
        )
    return {
        "schema_version": 1,
        "skills": skills,
        "truncated": False,
    }

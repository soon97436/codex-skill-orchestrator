"""Deterministic analysis of declarative skill capability metadata."""

from __future__ import annotations

import unicodedata
from typing import Any, Dict, List, Mapping, Tuple


MAX_SKILLS_EVALUATED = 256
MAX_CAPABILITY_ENTRIES = 256
MAX_DECLARED_COMMANDS = 64
MAX_FINDINGS = 256
MAX_SCOPE_ENTRIES = 64

CAPABILITY_FINDING_IDS = frozenset(
    {
        "capability.declaration.missing",
        "capability.declaration.unknown",
        "capability.network.unrestricted",
        "capability.process.arbitrary",
        "capability.filesystem.unrestricted",
        "capability.analysis.limit",
    }
)


def _skill_sort_key(skill: str) -> Tuple[str, str]:
    return unicodedata.normalize("NFC", skill).casefold(), skill


def _field_ref(skill: str, field: str) -> Dict[str, Any]:
    return {
        "source": "registry.skills",
        "identity": {"skill": skill, "field": field},
    }


def _finding(
    skill: str,
    finding_id: str,
    field: str,
    declared_value: Any,
) -> Dict[str, Any]:
    return {
        "finding_id": finding_id,
        "skill": skill,
        "field_ref": _field_ref(skill, field),
        "declared_value": declared_value,
    }


def _declared_findings(skill: str, capabilities: Mapping[str, Any]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    filesystem = capabilities["filesystem"]
    for access in ("read", "write"):
        if "unrestricted" in filesystem[access]:
            findings.append(
                _finding(
                    skill,
                    "capability.filesystem.unrestricted",
                    f"capabilities.filesystem.{access}",
                    "unrestricted",
                )
            )
    network_mode = capabilities["network"]["mode"]
    if network_mode == "unrestricted":
        findings.append(
            _finding(
                skill,
                "capability.network.unrestricted",
                "capabilities.network.mode",
                network_mode,
            )
        )
    elif network_mode == "unknown":
        findings.append(
            _finding(
                skill,
                "capability.declaration.unknown",
                "capabilities.network.mode",
                network_mode,
            )
        )
    process_mode = capabilities["process"]["mode"]
    if process_mode == "arbitrary":
        findings.append(
            _finding(
                skill,
                "capability.process.arbitrary",
                "capabilities.process.mode",
                process_mode,
            )
        )
    elif process_mode == "unknown":
        findings.append(
            _finding(
                skill,
                "capability.declaration.unknown",
                "capabilities.process.mode",
                process_mode,
            )
        )
    return sorted(
        findings,
        key=lambda item: (
            item["finding_id"],
            item["field_ref"]["identity"]["field"],
        ),
    )


def _canonical_declaration(capabilities: Mapping[str, Any]) -> Dict[str, Any]:
    def stable_strings(values: List[str]) -> List[str]:
        return sorted(
            set(values),
            key=lambda value: (unicodedata.normalize("NFC", value).casefold(), value),
        )

    return {
        "schema_version": capabilities["schema_version"],
        "filesystem": {
            "read": stable_strings(capabilities["filesystem"]["read"]),
            "write": stable_strings(capabilities["filesystem"]["write"]),
        },
        "network": {"mode": capabilities["network"]["mode"]},
        "process": {
            "mode": capabilities["process"]["mode"],
            "commands": stable_strings(capabilities["process"]["commands"]),
        },
    }


def analyze_capabilities(
    registry: Mapping[str, Mapping[str, Any]],
    *,
    max_skills_evaluated: int = MAX_SKILLS_EVALUATED,
    max_capability_entries: int = MAX_CAPABILITY_ENTRIES,
    max_declared_commands: int = MAX_DECLARED_COMMANDS,
    max_findings: int = MAX_FINDINGS,
    max_scope_entries: int = MAX_SCOPE_ENTRIES,
) -> Dict[str, Any]:
    """Return bounded metadata-only capability declarations and findings."""

    limits = (
        max_skills_evaluated,
        max_capability_entries,
        max_declared_commands,
        max_findings,
        max_scope_entries,
    )
    if any(not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 for limit in limits):
        raise ValueError("capability analysis limits must be positive integers")

    skills: List[Dict[str, Any]] = []
    findings: List[Dict[str, Any]] = []
    registry_skills = sorted(registry, key=_skill_sort_key)
    truncated = len(registry_skills) > max_skills_evaluated
    capability_entries = 0
    command_entries = 0
    scope_entries = 0
    for skill in registry_skills[:max_skills_evaluated]:
        declaration = registry[skill].get("capabilities")
        if declaration is not None:
            next_capability_entries = capability_entries + 3
            next_command_entries = command_entries + len(declaration["process"]["commands"])
            next_scope_entries = scope_entries + sum(
                len(declaration["filesystem"][access]) for access in ("read", "write")
            )
            if (
                next_capability_entries > max_capability_entries
                or next_command_entries > max_declared_commands
                or next_scope_entries > max_scope_entries
            ):
                truncated = True
                break
            capability_entries = next_capability_entries
            command_entries = next_command_entries
            scope_entries = next_scope_entries
        if declaration is None:
            skill_findings = [
                _finding(
                    skill,
                    "capability.declaration.missing",
                    "capabilities",
                    "missing",
                )
            ]
            skills.append(
                {
                    "skill": skill,
                    "declaration_status": "missing",
                    "risk_classification": "unknown",
                    "finding_ids": ["capability.declaration.missing"],
                }
            )
        else:
            canonical_declaration = _canonical_declaration(declaration)
            skill_findings = _declared_findings(skill, canonical_declaration)
            finding_ids = sorted({item["finding_id"] for item in skill_findings})
            if any(
                finding_id
                in {
                    "capability.filesystem.unrestricted",
                    "capability.network.unrestricted",
                    "capability.process.arbitrary",
                }
                for finding_id in finding_ids
            ):
                risk = "sensitive-requested"
            elif "capability.declaration.unknown" in finding_ids:
                risk = "unknown"
            else:
                risk = "no-sensitive-request"
            skills.append(
                {
                    "skill": skill,
                    "declaration_status": (
                        "unknown"
                        if "capability.declaration.unknown" in finding_ids
                        else "declared"
                    ),
                    "risk_classification": risk,
                    "capabilities": canonical_declaration,
                    "finding_ids": finding_ids,
                }
            )
        findings.extend(skill_findings)
    if len(findings) > max_findings:
        truncated = True
    if truncated:
        limit_finding = {
            "finding_id": "capability.analysis.limit",
            "field_ref": {
                "source": "registry.skills",
                "identity": {"field": "capabilities"},
            },
            "declared_value": "truncated",
        }
        findings = [
            item
            for item in findings
            if item["finding_id"] != "capability.analysis.limit"
        ][: max_findings - 1]
        findings.append(limit_finding)
    findings.sort(
        key=lambda item: (
            item["finding_id"],
            item.get("skill", ""),
            item["field_ref"]["identity"]["field"],
        )
    )
    return {
        "schema_version": 1,
        "status": "incomplete" if truncated else "complete",
        "policy_mode": "declarative-only",
        "enforcement_status": "not-implemented",
        "registry": {
            "skill_count": len(registry_skills),
            "evaluated_count": len(skills),
        },
        "skills": skills,
        "findings": findings,
        "truncated": truncated,
    }

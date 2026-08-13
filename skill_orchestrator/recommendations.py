"""Explainable, registry-bounded project recommendations."""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .analyzer import analyze_project
from .context import normalize_scope_identity
from .validation import validate_registry


def recommend_profile(analysis: Mapping[str, Any]) -> str:
    project = analysis["project"]
    if project.get("truncated"):
        return "universal"
    if project.get("size") == "small":
        return "small-project"
    if project.get("size") == "large":
        return "large-project"
    return "universal"


def recommendations_complete(analysis: Mapping[str, Any]) -> bool:
    context = analysis.get("context", {})
    return not (
        analysis.get("truncated")
        or analysis.get("project", {}).get("truncated")
        or context.get("truncated")
        or not context.get("conflict_analysis_complete", True)
        or bool(context.get("conflicts"))
    )


def _normalize_skill_identity(skill: str) -> str:
    return unicodedata.normalize("NFC", skill).casefold()


def _reason_sort_key(reason: Mapping[str, str]) -> Tuple[str, str, str, str]:
    return (
        unicodedata.normalize("NFC", reason["type"]).casefold(),
        reason["type"],
        unicodedata.normalize("NFC", reason["evidence"]).casefold(),
        reason["evidence"],
    )


def _candidate_literal_sort_key(candidate: Mapping[str, Any]) -> Tuple[Any, ...]:
    return (
        -candidate["score"],
        _normalize_skill_identity(candidate["skill"]),
        candidate["skill"],
        normalize_scope_identity(candidate["scope"]),
        candidate["scope"],
        tuple(_reason_sort_key(reason) for reason in sorted(candidate["reasons"], key=_reason_sort_key)),
    )


def _finalize_candidates(
    candidates: Sequence[Mapping[str, Any]],
    registry: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    registry_literals: Dict[str, str] = {}
    for skill in sorted(registry, key=lambda value: (_normalize_skill_identity(value), value)):
        registry_literals.setdefault(_normalize_skill_identity(skill), skill)

    grouped: Dict[Tuple[str, Tuple[str, ...]], List[Mapping[str, Any]]] = {}
    for candidate in candidates:
        skill_identity = _normalize_skill_identity(candidate["skill"])
        if skill_identity not in registry_literals:
            continue
        identity = (skill_identity, normalize_scope_identity(candidate["scope"]))
        grouped.setdefault(identity, []).append(candidate)

    finalized: List[Dict[str, Any]] = []
    for identity in sorted(grouped):
        group = grouped[identity]
        winner = min(group, key=_candidate_literal_sort_key)
        unique_reasons = {
            (reason["type"], reason["evidence"])
            for candidate in group
            for reason in candidate["reasons"]
        }
        reasons = [
            {"type": reason_type, "evidence": evidence}
            for reason_type, evidence in sorted(
                unique_reasons,
                key=lambda item: _reason_sort_key({"type": item[0], "evidence": item[1]}),
            )
        ]
        finalized.append(
            {
                "skill": registry_literals[identity[0]],
                "score": max(candidate["score"] for candidate in group),
                "scope": winner["scope"],
                "reasons": reasons,
            }
        )
    return sorted(
        finalized,
        key=lambda item: (
            -item["score"],
            _normalize_skill_identity(item["skill"]),
            item["skill"],
            normalize_scope_identity(item["scope"]),
            item["scope"],
        ),
    )


def recommend_skills(
    analysis: Mapping[str, Any],
    registry: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    technologies = {item["technology"] for item in analysis.get("detected", [])}
    frameworks = {item["framework"] for item in analysis.get("tests", [])}
    candidates: List[Dict[str, Any]] = []

    if technologies:
        reasons = [
            {"type": "repository-technology", "evidence": technology}
            for technology in sorted(technologies)
        ]
        if "github-actions" in technologies:
            reasons.append({"type": "continuous-integration", "evidence": "github-actions"})
        candidates.append({
            "skill": "code-review",
            "score": 90,
            "scope": ".",
            "reasons": reasons,
        })
    if frameworks:
        candidates.append({
            "skill": "tdd",
            "score": 90,
            "scope": ".",
            "reasons": [
                {"type": "test-framework", "evidence": framework}
                for framework in sorted(frameworks)
            ],
        })
    application_technologies = technologies & {
        "dotnet",
        "go",
        "java-gradle",
        "java-maven",
        "nodejs",
        "python",
        "react",
        "rust",
    }
    if application_technologies:
        candidates.append({
            "skill": "diagnosing-bugs",
            "score": 70,
            "scope": ".",
            "reasons": [
                {"type": "application-technology", "evidence": technology}
                for technology in sorted(application_technologies)
            ],
        })

    context = analysis.get("context", {})
    conflicted_paths = {
        path
        for conflict in context.get("conflicts", [])
        for path in conflict.get("paths", [])
    }
    for evidence in context.get("evidence", []):
        if evidence.get("kind") != "agent-instructions":
            continue
        if evidence.get("scope_state") not in {"root", "path-scoped"}:
            continue
        if evidence.get("path") in conflicted_paths:
            continue
        scope = evidence["scope"]
        candidates.append(
            {
                "skill": "writing-for-agents",
                "score": 60,
                "scope": scope,
                "reasons": [
                    {"type": "agent-context", "evidence": evidence["path"]}
                ],
            }
        )
    return _finalize_candidates(candidates, registry)


def analyze_and_recommend(project_root: Path, source_root: Path) -> Dict[str, Any]:
    analysis = analyze_project(project_root)
    registry = validate_registry(source_root)
    return {
        **analysis,
        "recommended_profile": recommend_profile(analysis),
        "recommended_skills": recommend_skills(analysis, registry),
        "recommendations_complete": recommendations_complete(analysis),
    }

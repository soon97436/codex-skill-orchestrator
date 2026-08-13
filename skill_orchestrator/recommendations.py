"""Explainable, registry-bounded project recommendations."""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from .analyzer import analyze_project
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


def recommend_skills(
    analysis: Mapping[str, Any],
    registry: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    technologies = {item["technology"] for item in analysis.get("detected", [])}
    frameworks = {item["framework"] for item in analysis.get("tests", [])}
    candidates: Dict[Tuple[str, str], Dict[str, Any]] = {}

    if technologies:
        reasons = [
            {"type": "repository-technology", "evidence": technology}
            for technology in sorted(technologies)
        ]
        if "github-actions" in technologies:
            reasons.append({"type": "continuous-integration", "evidence": "github-actions"})
        candidates[("code-review", ".")] = {
            "skill": "code-review",
            "score": 90,
            "scope": ".",
            "reasons": reasons,
        }
    if frameworks:
        candidates[("tdd", ".")] = {
            "skill": "tdd",
            "score": 90,
            "scope": ".",
            "reasons": [
                {"type": "test-framework", "evidence": framework}
                for framework in sorted(frameworks)
            ],
        }
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
        candidates[("diagnosing-bugs", ".")] = {
            "skill": "diagnosing-bugs",
            "score": 70,
            "scope": ".",
            "reasons": [
                {"type": "application-technology", "evidence": technology}
                for technology in sorted(application_technologies)
            ],
        }

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
        candidate = candidates.setdefault(
            ("writing-for-agents", scope),
            {
                "skill": "writing-for-agents",
                "score": 60,
                "scope": scope,
                "reasons": [],
            },
        )
        candidate["reasons"].append(
            {"type": "agent-context", "evidence": evidence["path"]}
        )

    allowed = [item for (skill, _scope), item in candidates.items() if skill in registry]
    for item in allowed:
        item["reasons"] = [
            {"type": reason_type, "evidence": evidence}
            for reason_type, evidence in sorted(
                {(reason["type"], reason["evidence"]) for reason in item["reasons"]}
            )
        ]
    return sorted(
        allowed,
        key=lambda item: (
            -item["score"],
            item["skill"],
            unicodedata.normalize("NFC", item["scope"]).casefold(),
            item["scope"],
        ),
    )


def analyze_and_recommend(project_root: Path, source_root: Path) -> Dict[str, Any]:
    analysis = analyze_project(project_root)
    registry = validate_registry(source_root)
    return {
        **analysis,
        "recommended_profile": recommend_profile(analysis),
        "recommended_skills": recommend_skills(analysis, registry),
        "recommendations_complete": recommendations_complete(analysis),
    }

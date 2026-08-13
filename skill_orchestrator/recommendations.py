"""Explainable, registry-bounded project recommendations."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping

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


def recommend_skills(
    analysis: Mapping[str, Any],
    registry: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    technologies = {item["technology"] for item in analysis.get("detected", [])}
    frameworks = {item["framework"] for item in analysis.get("tests", [])}
    candidates: Dict[str, Dict[str, Any]] = {}

    if technologies:
        reasons = ["source project detected"]
        if "github-actions" in technologies:
            reasons.append("continuous integration detected")
        candidates["code-review"] = {"skill": "code-review", "score": 90, "reasons": reasons}
    if frameworks:
        candidates["tdd"] = {
            "skill": "tdd",
            "score": 90,
            "reasons": ["test framework detected: " + ", ".join(sorted(frameworks))],
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
        candidates["diagnosing-bugs"] = {
            "skill": "diagnosing-bugs",
            "score": 70,
            "reasons": ["application technology detected: " + ", ".join(sorted(application_technologies))],
        }

    allowed = [item for skill, item in candidates.items() if skill in registry]
    return sorted(allowed, key=lambda item: (-item["score"], item["skill"]))


def analyze_and_recommend(project_root: Path, source_root: Path) -> Dict[str, Any]:
    analysis = analyze_project(project_root)
    registry = validate_registry(source_root)
    return {
        **analysis,
        "recommended_profile": recommend_profile(analysis),
        "recommended_skills": recommend_skills(analysis, registry),
    }

"""Explainable, registry-bounded project recommendations."""

from __future__ import annotations

import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from .analyzer import analyze_project
from .capabilities import analyze_capabilities
from .context import normalize_scope_identity
from .errors import SecurityError
from .validation import validate_registry, validate_relative_path


SELECTED_REASON_IDS = {
    "application-technology": "recommendation.selected.application-technology",
    "continuous-integration": "recommendation.selected.continuous-integration",
    "repository-technology": "recommendation.selected.repository-technology",
    "test-framework": "recommendation.selected.test-framework",
    "agent-context": "recommendation.selected.agent-context",
}
RULE_SKILLS = {
    "code-review",
    "diagnosing-bugs",
    "tdd",
    "writing-for-agents",
}
MAX_EXCLUDED_ENTRIES = 256
MAX_UNMATCHED_SIGNALS = 256
MAX_LIMITATIONS = 64
MAX_EVIDENCE_REFS = 256
MAX_REGISTRY_SKILLS = 256
RECOMMENDATION_REASON_IDS = frozenset(
    set(SELECTED_REASON_IDS.values())
    | {
        "recommendation.excluded.no-deterministic-rule",
        "recommendation.excluded.required-signal-absent",
        "recommendation.excluded.scope-unknown",
        "recommendation.excluded.context-conflict",
        "recommendation.excluded.untrusted-evidence",
        "recommendation.unmatched.no-registered-skill",
        "recommendation.incomplete.project-traversal",
        "recommendation.incomplete.context-discovery",
        "recommendation.incomplete.conflict-analysis",
        "recommendation.incomplete.context-conflict",
        "recommendation.incomplete.explanation-limit",
    }
)
CONTEXT_CONFLICT_IDS = {
    "context.duplicate-source-registration",
    "context.normalized-path-collision",
}


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


def _build_candidates(analysis: Mapping[str, Any]) -> List[Dict[str, Any]]:
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
        if not _safe_repo_path(evidence.get("path")):
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
    return candidates


def _selected_reason_explanation(reason: Mapping[str, str]) -> Dict[str, Any]:
    reason_type = reason["type"]
    identity_key = "framework" if reason_type == "test-framework" else "technology"
    source = "tests" if reason_type == "test-framework" else "detected"
    if reason_type == "agent-context":
        source = "context.evidence"
        identity_key = "path"
    return {
        "reason_id": SELECTED_REASON_IDS[reason_type],
        "evidence_ref": {
            "source": source,
            "identity": {identity_key: reason["evidence"]},
        },
    }


def _evidence_ref_key(reference: Mapping[str, Any]) -> Tuple[Any, ...]:
    def stable_value(value: Any) -> Any:
        if isinstance(value, list):
            return tuple(stable_value(item) for item in value)
        if isinstance(value, dict):
            return tuple(sorted((key, stable_value(item)) for key, item in value.items()))
        return value

    return (
        reference["source"],
        stable_value(reference["identity"]),
    )


def _analysis_signal_refs(analysis: Mapping[str, Any]) -> List[Dict[str, Any]]:
    references = [
        {"source": "detected", "identity": {"technology": item["technology"]}}
        for item in analysis.get("detected", [])
    ]
    references.extend(
        {"source": "tests", "identity": {"framework": item["framework"]}}
        for item in analysis.get("tests", [])
    )
    references.extend(
        {"source": "context.evidence", "identity": {"path": item["path"]}}
        for item in analysis.get("context", {}).get("evidence", [])
        if _safe_repo_path(item.get("path"))
    )
    unique = {_evidence_ref_key(reference): reference for reference in references}
    return [unique[key] for key in sorted(unique)]


def _safe_repo_path(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        validate_relative_path(value, "explanation evidence path")
    except SecurityError:
        return False
    return True


def _is_agent_context_path(value: str) -> bool:
    return value.rsplit("/", 1)[-1].casefold() in {"agents.md", "claude.md"}


def _conflict_references(analysis: Mapping[str, Any]) -> List[Dict[str, Any]]:
    references: List[Dict[str, Any]] = []
    for conflict in analysis.get("context", {}).get("conflicts", []):
        conflict_id = conflict.get("id")
        paths = conflict.get("paths", [])
        if conflict_id not in CONTEXT_CONFLICT_IDS:
            continue
        if not isinstance(paths, list) or not paths or not all(_safe_repo_path(path) for path in paths):
            continue
        references.append(
            {
                "source": "context.conflicts",
                "identity": {
                    "id": conflict_id,
                    "paths": sorted(set(paths), key=lambda path: (unicodedata.normalize("NFC", path).casefold(), path)),
                },
            }
        )
    unique = {_evidence_ref_key(reference): reference for reference in references}
    return [unique[key] for key in sorted(unique)]


def _apply_explanation_bounds(
    selected: List[Dict[str, Any]],
    excluded: List[Dict[str, Any]],
    unmatched: List[Dict[str, Any]],
    limitations: List[Dict[str, Any]],
    *,
    max_excluded_entries: int,
    max_unmatched_signals: int,
    max_limitations: int,
    max_evidence_refs: int,
    initially_truncated: bool = False,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]], bool]:
    limits = (
        max_excluded_entries,
        max_unmatched_signals,
        max_limitations,
        max_evidence_refs,
    )
    if any(not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 for limit in limits):
        raise ValueError("explanation limits must be positive integers")

    truncated = (
        initially_truncated
        or len(excluded) > max_excluded_entries
        or len(unmatched) > max_unmatched_signals
    )
    excluded = excluded[:max_excluded_entries]
    unmatched = unmatched[:max_unmatched_signals]

    remaining_refs = max_evidence_refs
    for recommendation in selected:
        reasons = recommendation["reasons"]
        kept = reasons[:remaining_refs]
        if len(kept) != len(reasons):
            truncated = True
        recommendation["reasons"] = kept
        remaining_refs -= len(kept)
    for entry in excluded:
        references = entry.get("evidence_refs", [])
        kept = references[:remaining_refs]
        if len(kept) != len(references):
            truncated = True
        if references:
            entry["evidence_refs"] = kept
        remaining_refs -= len(kept)
    kept_unmatched = []
    for entry in unmatched:
        if remaining_refs < 1:
            truncated = True
            continue
        kept_unmatched.append(entry)
        remaining_refs -= 1
    unmatched = kept_unmatched
    for entry in limitations:
        references = entry.get("evidence_refs", [])
        kept = references[:remaining_refs]
        if len(kept) != len(references):
            truncated = True
        if references:
            entry["evidence_refs"] = kept
        remaining_refs -= len(kept)

    if len(limitations) > max_limitations:
        truncated = True
    limitations = limitations[:max_limitations]
    if truncated:
        marker = {"reason_id": "recommendation.incomplete.explanation-limit"}
        limitations = [
            item for item in limitations if item["reason_id"] != marker["reason_id"]
        ]
        if len(limitations) >= max_limitations:
            limitations = limitations[: max_limitations - 1]
        limitations.append(marker)
        limitations.sort(key=lambda item: item["reason_id"])
    return selected, excluded, unmatched, limitations, truncated


def evaluate_recommendations(
    analysis: Mapping[str, Any],
    registry: Mapping[str, Mapping[str, Any]],
    *,
    max_excluded_entries: int = MAX_EXCLUDED_ENTRIES,
    max_unmatched_signals: int = MAX_UNMATCHED_SIGNALS,
    max_limitations: int = MAX_LIMITATIONS,
    max_evidence_refs: int = MAX_EVIDENCE_REFS,
    max_registry_skills: int = MAX_REGISTRY_SKILLS,
) -> Dict[str, Any]:
    if not isinstance(max_registry_skills, int) or isinstance(max_registry_skills, bool) or max_registry_skills < 1:
        raise ValueError("explanation limits must be positive integers")
    candidates = _build_candidates(analysis)
    selected = _finalize_candidates(candidates, registry)
    selected_explanations = [
        {
            "skill": recommendation["skill"],
            "scope": recommendation["scope"],
            "reasons": [
                _selected_reason_explanation(reason)
                for reason in recommendation["reasons"]
            ],
        }
        for recommendation in selected
    ]
    registry_skills = sorted(
        registry,
        key=lambda skill: (_normalize_skill_identity(skill), skill),
    )
    selected_skill_ids = {
        _normalize_skill_identity(recommendation["skill"])
        for recommendation in selected
    }
    context = analysis.get("context", {})
    unsafe_context_evidence = any(
        not _safe_repo_path(item.get("path"))
        for item in context.get("evidence", [])
    )
    agent_paths = {
        item.get("path")
        for item in context.get("evidence", [])
        if item.get("kind") == "agent-instructions"
    }
    unknown_scope_refs = sorted(
        [
            {"source": "context.evidence", "identity": {"path": item["path"]}}
            for item in context.get("evidence", [])
            if item.get("kind") == "agent-instructions"
            and item.get("scope_state") == "unknown"
            and _safe_repo_path(item.get("path"))
        ],
        key=_evidence_ref_key,
    )
    relevant_conflicts = [
        reference
        for reference in _conflict_references(analysis)
        if agent_paths.intersection(reference["identity"]["paths"])
        or any(_is_agent_context_path(path) for path in reference["identity"]["paths"])
    ]
    excluded = []
    for skill in registry_skills:
        skill_identity = _normalize_skill_identity(skill)
        if skill_identity in selected_skill_ids:
            continue
        entry: Dict[str, Any] = {"skill": skill}
        if skill_identity == "writing-for-agents" and relevant_conflicts:
            entry["reason_id"] = "recommendation.excluded.context-conflict"
            entry["evidence_refs"] = relevant_conflicts
        elif skill_identity == "writing-for-agents" and unknown_scope_refs:
            entry["reason_id"] = "recommendation.excluded.scope-unknown"
            entry["evidence_refs"] = unknown_scope_refs
        elif skill_identity == "writing-for-agents" and (
            context.get("truncated") or unsafe_context_evidence
        ):
            entry["reason_id"] = "recommendation.excluded.untrusted-evidence"
        elif skill_identity in RULE_SKILLS:
            entry["reason_id"] = "recommendation.excluded.required-signal-absent"
        else:
            entry["reason_id"] = "recommendation.excluded.no-deterministic-rule"
        excluded.append(entry)
    registered_skill_ids = {_normalize_skill_identity(skill) for skill in registry}
    matched_signal_keys = {
        _evidence_ref_key(_selected_reason_explanation(reason)["evidence_ref"])
        for candidate in candidates
        if _normalize_skill_identity(candidate["skill"]) in registered_skill_ids
        for reason in candidate["reasons"]
    }
    if "writing-for-agents" in registered_skill_ids:
        matched_signal_keys.update(
            _evidence_ref_key(
                {"source": "context.evidence", "identity": {"path": path}}
            )
            for path in agent_paths
            if _safe_repo_path(path) and any(
                path in reference["identity"]["paths"]
                for reference in relevant_conflicts
            )
        )
        matched_signal_keys.update(
            _evidence_ref_key(reference) for reference in unknown_scope_refs
        )
    unmatched_signals = [
        {
            "reason_id": "recommendation.unmatched.no-registered-skill",
            "signal_ref": reference,
        }
        for reference in _analysis_signal_refs(analysis)
        if _evidence_ref_key(reference) not in matched_signal_keys
    ]
    complete = recommendations_complete(analysis) and not unsafe_context_evidence
    limitations = []
    if not context.get("conflict_analysis_complete", True):
        limitations.append(
            {"reason_id": "recommendation.incomplete.conflict-analysis"}
        )
    if context.get("truncated") or unsafe_context_evidence:
        limitations.append(
            {"reason_id": "recommendation.incomplete.context-discovery"}
        )
    conflict_references = _conflict_references(analysis)
    if context.get("conflicts"):
        limitation: Dict[str, Any] = {
            "reason_id": "recommendation.incomplete.context-conflict"
        }
        if conflict_references:
            limitation["evidence_refs"] = conflict_references
        limitations.append(limitation)
    if analysis.get("truncated") or analysis.get("project", {}).get("truncated"):
        limitations.append(
            {"reason_id": "recommendation.incomplete.project-traversal"}
        )
    limitations.sort(key=lambda item: item["reason_id"])
    selected_explanations, excluded, unmatched_signals, limitations, truncated = (
        _apply_explanation_bounds(
            selected_explanations,
            excluded,
            unmatched_signals,
            limitations,
            max_excluded_entries=max_excluded_entries,
            max_unmatched_signals=max_unmatched_signals,
            max_limitations=max_limitations,
            max_evidence_refs=max_evidence_refs,
            initially_truncated=len(registry_skills) > max_registry_skills,
        )
    )
    return {
        "selected": selected,
        "explanations": {
            "schema_version": 1,
            "status": "complete" if complete and not truncated else "incomplete",
            "registry": {
                "skill_count": len(registry_skills),
                "skills": registry_skills[:max_registry_skills],
            },
            "selected": selected_explanations,
            "excluded": excluded,
            "unmatched_signals": unmatched_signals,
            "limitations": limitations,
            "truncated": truncated,
        },
    }


def recommend_skills(
    analysis: Mapping[str, Any],
    registry: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    return evaluate_recommendations(analysis, registry)["selected"]


def analyze_and_recommend(project_root: Path, source_root: Path) -> Dict[str, Any]:
    analysis = analyze_project(project_root)
    registry = validate_registry(source_root)
    evaluation = evaluate_recommendations(analysis, registry)
    return {
        **analysis,
        "recommended_profile": recommend_profile(analysis),
        "recommended_skills": evaluation["selected"],
        "recommendations_complete": recommendations_complete(analysis),
        "recommendation_explanations": evaluation["explanations"],
        "capability_analysis": analyze_capabilities(registry),
    }

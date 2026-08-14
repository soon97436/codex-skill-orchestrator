import json
import tempfile
import unittest
from pathlib import Path

from skill_orchestrator.analyzer import analyze_project
from skill_orchestrator.context import scope_contains
from skill_orchestrator.recommendations import (
    _finalize_candidates,
    RECOMMENDATION_REASON_IDS,
    analyze_and_recommend,
    evaluate_recommendations,
    recommend_profile,
    recommend_skills,
    recommendations_complete,
)
from skill_orchestrator.validation import validate_registry


ROOT = Path(__file__).resolve().parents[1]


class RecommendationTests(unittest.TestCase):
    def test_selected_reason_ids_are_stable(self) -> None:
        analysis = {
            "detected": [
                {"technology": "github-actions", "evidence": [".github/workflows/ci.yml"]},
                {"technology": "python", "evidence": ["pyproject.toml"]},
            ],
            "tests": [{"framework": "pytest", "evidence": ["tests/test_app.py"]}],
            "context": {
                "evidence": [
                    {
                        "path": "services/api/AGENTS.md",
                        "kind": "agent-instructions",
                        "scope": "services/api",
                        "scope_state": "path-scoped",
                    }
                ],
                "scope_overlaps": [],
                "conflicts": [],
                "conflict_analysis_complete": True,
                "truncated": False,
            },
            "project": {"files_analyzed": 2, "size": "small", "truncated": False},
            "truncated": False,
        }

        evaluation = evaluate_recommendations(
            analysis,
            {
                "code-review": {},
                "diagnosing-bugs": {},
                "tdd": {},
                "writing-for-agents": {},
            },
        )

        selected = evaluation["explanations"]["selected"]
        reason_ids = {
            reason["reason_id"]
            for recommendation in selected
            for reason in recommendation["reasons"]
        }
        self.assertEqual(
            reason_ids,
            {
                "recommendation.selected.application-technology",
                "recommendation.selected.agent-context",
                "recommendation.selected.continuous-integration",
                "recommendation.selected.repository-technology",
                "recommendation.selected.test-framework",
            },
        )
        self.assertIn(
            {
                "reason_id": "recommendation.selected.test-framework",
                "evidence_ref": {
                    "source": "tests",
                    "identity": {"framework": "pytest"},
                },
            },
            next(item for item in selected if item["skill"] == "tdd")["reasons"],
        )

    def test_evidence_refs_resolve_by_identity(self) -> None:
        analysis = {
            "detected": [{"technology": "python", "evidence": ["pyproject.toml"]}],
            "tests": [{"framework": "pytest", "evidence": ["tests/test_app.py"]}],
            "context": {
                "evidence": [
                    {
                        "path": "services/api/AGENTS.md",
                        "kind": "agent-instructions",
                        "scope": "services/api",
                        "scope_state": "path-scoped",
                    }
                ],
                "scope_overlaps": [],
                "conflicts": [],
                "conflict_analysis_complete": True,
                "truncated": False,
            },
            "project": {"files_analyzed": 3, "size": "small", "truncated": False},
            "truncated": False,
        }

        selected = evaluate_recommendations(
            analysis,
            {"code-review": {}, "diagnosing-bugs": {}, "tdd": {}, "writing-for-agents": {}},
        )["explanations"]["selected"]

        identities = {
            "detected": [{"technology": item["technology"]} for item in analysis["detected"]],
            "tests": [{"framework": item["framework"]} for item in analysis["tests"]],
            "context.evidence": [
                {"path": item["path"]} for item in analysis["context"]["evidence"]
            ],
        }
        for recommendation in selected:
            for reason in recommendation["reasons"]:
                reference = reason["evidence_ref"]
                self.assertIn(reference["identity"], identities[reference["source"]])

    def test_reason_ids_are_allowlisted(self) -> None:
        analysis = {
            "detected": [{"technology": "python", "evidence": ["pyproject.toml"]}],
            "tests": [{"framework": "pytest", "evidence": ["tests/test_app.py"]}],
            "context": {
                "evidence": [],
                "scope_overlaps": [],
                "conflicts": [],
                "conflict_analysis_complete": False,
                "truncated": True,
            },
            "project": {"files_analyzed": 2, "size": "small", "truncated": False},
            "truncated": False,
        }

        explanation = evaluate_recommendations(
            analysis,
            {"code-review": {}, "codex-skill-orchestrator": {}},
        )["explanations"]

        def reason_ids(value):
            if isinstance(value, dict):
                return ({value["reason_id"]} if "reason_id" in value else set()).union(
                    *(reason_ids(item) for item in value.values())
                )
            if isinstance(value, list):
                return set().union(*(reason_ids(item) for item in value))
            return set()

        self.assertTrue(reason_ids(explanation))
        self.assertLessEqual(reason_ids(explanation), RECOMMENDATION_REASON_IDS)

    def test_explanation_dedup_is_input_order_independent(self) -> None:
        evidence = [
            {
                "path": "Services/AGENTS.md",
                "kind": "agent-instructions",
                "scope": "Services",
                "scope_state": "path-scoped",
            },
            {
                "path": "services/CLAUDE.md",
                "kind": "agent-instructions",
                "scope": "services",
                "scope_state": "path-scoped",
            },
        ]
        detected = [
            {"technology": "python", "evidence": ["pyproject.toml"]},
            {"technology": "docker", "evidence": ["Dockerfile"]},
        ]

        def analysis(reverse: bool):
            return {
                "detected": list(reversed(detected)) if reverse else detected,
                "tests": [],
                "context": {
                    "evidence": list(reversed(evidence)) if reverse else evidence,
                    "scope_overlaps": [],
                    "conflicts": [],
                    "conflict_analysis_complete": True,
                    "truncated": False,
                },
                "project": {"files_analyzed": 4, "size": "small", "truncated": False},
                "truncated": False,
            }

        registry = {"writing-for-agents": {}, "codex-skill-orchestrator": {}}
        first = evaluate_recommendations(analysis(False), registry)["explanations"]
        second = evaluate_recommendations(analysis(True), dict(reversed(list(registry.items()))))[
            "explanations"
        ]

        self.assertEqual(first, second)
        self.assertEqual(len(first["selected"]), 1)
        self.assertEqual(len(first["selected"][0]["reasons"]), 2)

    def test_explanation_output_is_metadata_only_and_paths_are_repo_relative(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-explanation-") as temporary:
            project = Path(temporary)
            secret = "private-context-contents-must-not-appear"
            (project / "AGENTS.md").write_text(secret + "\n", encoding="utf-8")

            result = analyze_and_recommend(project, ROOT)

        explanation = result["recommendation_explanations"]
        serialized = json.dumps(explanation, ensure_ascii=False, sort_keys=True)
        for forbidden in (secret, str(project), "hostname", "username", "timestamp"):
            self.assertNotIn(forbidden, serialized)
        self.assertIn(
            {
                "reason_id": "recommendation.unmatched.no-registered-skill",
                "signal_ref": {
                    "source": "context.evidence",
                    "identity": {"path": "AGENTS.md"},
                },
            },
            explanation["unmatched_signals"],
        )
        for signal in explanation["unmatched_signals"]:
            path = signal["signal_ref"]["identity"].get("path")
            if path is not None:
                self.assertFalse(Path(path).is_absolute())
                self.assertNotIn("\\", path)

    def test_private_path_evidence_fails_closed_without_leakage(self) -> None:
        private_path = "/srv/private/project/AGENTS.md"
        analysis = {
            "detected": [],
            "tests": [],
            "context": {
                "evidence": [
                    {
                        "path": private_path,
                        "kind": "agent-instructions",
                        "scope": ".",
                        "scope_state": "root",
                    }
                ],
                "scope_overlaps": [],
                "conflicts": [],
                "conflict_analysis_complete": True,
                "truncated": False,
            },
            "project": {"files_analyzed": 1, "size": "small", "truncated": False},
            "truncated": False,
        }

        evaluation = evaluate_recommendations(
            analysis,
            {"writing-for-agents": {}},
        )

        serialized = json.dumps(evaluation, sort_keys=True)
        self.assertNotIn(private_path, serialized)
        self.assertEqual(evaluation["selected"], [])
        self.assertEqual(
            evaluation["explanations"]["excluded"],
            [
                {
                    "skill": "writing-for-agents",
                    "reason_id": "recommendation.excluded.untrusted-evidence",
                }
            ],
        )
        self.assertEqual(evaluation["explanations"]["status"], "incomplete")

    def test_empty_registry_explanation(self) -> None:
        analysis = {
            "detected": [],
            "tests": [],
            "context": {
                "evidence": [],
                "scope_overlaps": [],
                "conflicts": [],
                "conflict_analysis_complete": True,
                "truncated": False,
            },
            "project": {"files_analyzed": 0, "size": "small", "truncated": False},
            "truncated": False,
        }

        evaluation = evaluate_recommendations(analysis, {})

        self.assertEqual(evaluation["selected"], [])
        self.assertEqual(
            evaluation["explanations"],
            {
                "schema_version": 1,
                "status": "complete",
                "registry": {"skill_count": 0, "skills": []},
                "selected": [],
                "excluded": [],
                "unmatched_signals": [],
                "limitations": [],
                "truncated": False,
            },
        )

    def test_excluded_skills_are_registry_bounded(self) -> None:
        analysis = {
            "detected": [{"technology": "python", "evidence": ["pyproject.toml"]}],
            "tests": [{"framework": "pytest", "evidence": ["tests/test_app.py"]}],
            "context": {
                "evidence": [],
                "scope_overlaps": [],
                "conflicts": [],
                "conflict_analysis_complete": True,
                "truncated": False,
            },
            "project": {"files_analyzed": 2, "size": "small", "truncated": False},
            "truncated": False,
        }

        evaluation = evaluate_recommendations(
            analysis,
            {"codex-skill-orchestrator": {}},
        )

        self.assertEqual(
            evaluation["explanations"]["excluded"],
            [
                {
                    "skill": "codex-skill-orchestrator",
                    "reason_id": "recommendation.excluded.no-deterministic-rule",
                }
            ],
        )
        serialized = json.dumps(evaluation["explanations"]["excluded"])
        for unregistered in ("code-review", "diagnosing-bugs", "tdd"):
            self.assertNotIn(unregistered, serialized)

    def test_unmatched_signal_contains_no_unregistered_skill_id(self) -> None:
        analysis = {
            "detected": [{"technology": "python", "evidence": ["pyproject.toml"]}],
            "tests": [{"framework": "pytest", "evidence": ["tests/test_app.py"]}],
            "context": {
                "evidence": [],
                "scope_overlaps": [],
                "conflicts": [],
                "conflict_analysis_complete": True,
                "truncated": False,
            },
            "project": {"files_analyzed": 2, "size": "small", "truncated": False},
            "truncated": False,
        }

        evaluation = evaluate_recommendations(analysis, {})

        self.assertEqual(
            evaluation["explanations"]["unmatched_signals"],
            [
                {
                    "reason_id": "recommendation.unmatched.no-registered-skill",
                    "signal_ref": {
                        "source": "detected",
                        "identity": {"technology": "python"},
                    },
                },
                {
                    "reason_id": "recommendation.unmatched.no-registered-skill",
                    "signal_ref": {
                        "source": "tests",
                        "identity": {"framework": "pytest"},
                    },
                },
            ],
        )
        serialized = json.dumps(evaluation["explanations"]["unmatched_signals"])
        for unregistered in ("code-review", "diagnosing-bugs", "tdd"):
            self.assertNotIn(unregistered, serialized)

    def test_conflict_exclusion_references_existing_conflict(self) -> None:
        conflict = {
            "id": "context.duplicate-source-registration",
            "type": "duplicate-source-registration",
            "severity": "warning",
            "paths": ["AGENTS.md"],
            "scope": ".",
            "reason": "one context source is registered more than once",
        }
        analysis = {
            "detected": [],
            "tests": [],
            "context": {
                "evidence": [
                    {
                        "path": "AGENTS.md",
                        "kind": "agent-instructions",
                        "scope": ".",
                        "scope_state": "root",
                    }
                ],
                "scope_overlaps": [],
                "conflicts": [conflict],
                "conflict_analysis_complete": True,
                "truncated": False,
            },
            "project": {"files_analyzed": 1, "size": "small", "truncated": False},
            "truncated": False,
        }

        explanation = evaluate_recommendations(
            analysis,
            {"writing-for-agents": {}},
        )["explanations"]

        conflict_ref = {
            "source": "context.conflicts",
            "identity": {
                "id": "context.duplicate-source-registration",
                "paths": ["AGENTS.md"],
            },
        }
        self.assertEqual(
            explanation["excluded"],
            [
                {
                    "skill": "writing-for-agents",
                    "reason_id": "recommendation.excluded.context-conflict",
                    "evidence_refs": [conflict_ref],
                }
            ],
        )
        self.assertEqual(explanation["unmatched_signals"], [])
        self.assertIn(
            {
                "reason_id": "recommendation.incomplete.context-conflict",
                "evidence_refs": [conflict_ref],
            },
            explanation["limitations"],
        )
        self.assertEqual(explanation["status"], "incomplete")

    def test_conflict_exclusion_does_not_require_trusted_context_evidence(self) -> None:
        analysis = {
            "detected": [],
            "tests": [],
            "context": {
                "evidence": [],
                "scope_overlaps": [],
                "conflicts": [
                    {
                        "id": "context.normalized-path-collision",
                        "type": "normalized-path-collision",
                        "severity": "warning",
                        "paths": ["Services/AGENTS.md", "services/agents.MD"],
                        "scope": "Services",
                        "reason": "multiple context paths share one NFC-casefold identity",
                    }
                ],
                "conflict_analysis_complete": False,
                "truncated": True,
            },
            "project": {"files_analyzed": 2, "size": "small", "truncated": False},
            "truncated": False,
        }

        explanation = evaluate_recommendations(
            analysis,
            {"writing-for-agents": {}},
        )["explanations"]

        self.assertEqual(
            explanation["excluded"][0]["reason_id"],
            "recommendation.excluded.context-conflict",
        )
        self.assertEqual(
            explanation["excluded"][0]["evidence_refs"][0]["identity"]["id"],
            "context.normalized-path-collision",
        )
        self.assertEqual(explanation["selected"], [])

    def test_incomplete_context_adds_limitations(self) -> None:
        analysis = {
            "detected": [],
            "tests": [],
            "context": {
                "evidence": [],
                "scope_overlaps": [],
                "conflicts": [],
                "conflict_analysis_complete": False,
                "truncated": True,
            },
            "project": {"files_analyzed": 50_000, "size": "unknown", "truncated": True},
            "truncated": True,
        }

        explanation = evaluate_recommendations(analysis, {})["explanations"]

        self.assertEqual(
            [item["reason_id"] for item in explanation["limitations"]],
            [
                "recommendation.incomplete.conflict-analysis",
                "recommendation.incomplete.context-discovery",
                "recommendation.incomplete.project-traversal",
            ],
        )
        self.assertEqual(explanation["status"], "incomplete")
        self.assertFalse(explanation["truncated"])

    def test_unknown_scope_exclusion_uses_metadata_reference(self) -> None:
        analysis = {
            "detected": [],
            "tests": [],
            "context": {
                "evidence": [
                    {
                        "path": "rules/AGENTS.md",
                        "kind": "agent-instructions",
                        "scope": "unknown",
                        "scope_state": "unknown",
                    }
                ],
                "scope_overlaps": [],
                "conflicts": [],
                "conflict_analysis_complete": True,
                "truncated": False,
            },
            "project": {"files_analyzed": 1, "size": "small", "truncated": False},
            "truncated": False,
        }

        explanation = evaluate_recommendations(
            analysis,
            {"writing-for-agents": {}},
        )["explanations"]

        self.assertEqual(
            explanation["excluded"],
            [
                {
                    "skill": "writing-for-agents",
                    "reason_id": "recommendation.excluded.scope-unknown",
                    "evidence_refs": [
                        {
                            "source": "context.evidence",
                            "identity": {"path": "rules/AGENTS.md"},
                        }
                    ],
                }
            ],
        )
        self.assertEqual(explanation["unmatched_signals"], [])

    def test_explanation_limit_marks_output_incomplete(self) -> None:
        analysis = {
            "detected": [
                {"technology": "docker", "evidence": ["Dockerfile"]},
                {"technology": "python", "evidence": ["pyproject.toml"]},
            ],
            "tests": [{"framework": "pytest", "evidence": ["tests/test_app.py"]}],
            "context": {
                "evidence": [],
                "scope_overlaps": [],
                "conflicts": [],
                "conflict_analysis_complete": True,
                "truncated": False,
            },
            "project": {"files_analyzed": 3, "size": "small", "truncated": False},
            "truncated": False,
        }

        explanation = evaluate_recommendations(
            analysis,
            {},
            max_unmatched_signals=1,
        )["explanations"]

        self.assertEqual(len(explanation["unmatched_signals"]), 1)
        self.assertTrue(explanation["truncated"])
        self.assertEqual(explanation["status"], "incomplete")
        self.assertIn(
            {"reason_id": "recommendation.incomplete.explanation-limit"},
            explanation["limitations"],
        )

    def test_evidence_ref_limit_preserves_compatible_recommendation(self) -> None:
        analysis = {
            "detected": [
                {"technology": "docker", "evidence": ["Dockerfile"]},
                {"technology": "python", "evidence": ["pyproject.toml"]},
            ],
            "tests": [],
            "context": {
                "evidence": [],
                "scope_overlaps": [],
                "conflicts": [],
                "conflict_analysis_complete": True,
                "truncated": False,
            },
            "project": {"files_analyzed": 2, "size": "small", "truncated": False},
            "truncated": False,
        }

        evaluation = evaluate_recommendations(
            analysis,
            {"code-review": {}},
            max_evidence_refs=1,
        )

        self.assertEqual(
            evaluation["selected"][0]["reasons"],
            [
                {"type": "repository-technology", "evidence": "docker"},
                {"type": "repository-technology", "evidence": "python"},
            ],
        )
        explanation = evaluation["explanations"]
        self.assertEqual(len(explanation["selected"][0]["reasons"]), 1)
        self.assertTrue(explanation["truncated"])
        self.assertEqual(explanation["status"], "incomplete")

    def test_segment_aware_scope_containment(self) -> None:
        self.assertTrue(scope_contains(".", "packages/web/client"))
        self.assertTrue(scope_contains("packages/web", "packages/web/client"))
        self.assertTrue(scope_contains("Packages/Web", "packages/web/Client"))
        self.assertFalse(scope_contains("packages/web", "packages/web-old"))
        self.assertFalse(scope_contains("unknown", "packages/web"))
        self.assertFalse(scope_contains("packages/web", "unknown"))

    def test_casefold_scope_identity_is_deduplicated(self) -> None:
        evidence = [
            {
                "path": "Services/AGENTS.md",
                "kind": "agent-instructions",
                "scope": "Services",
                "scope_state": "path-scoped",
            },
            {
                "path": "services/CLAUDE.md",
                "kind": "agent-instructions",
                "scope": "services",
                "scope_state": "path-scoped",
            },
        ]
        analysis = {
            "detected": [],
            "tests": [],
            "context": {
                "evidence": evidence,
                "scope_overlaps": [],
                "conflicts": [],
                "conflict_analysis_complete": True,
                "truncated": False,
            },
            "project": {"files_analyzed": 2, "size": "small", "truncated": False},
        }

        recommendations = recommend_skills(analysis, {"writing-for-agents": {}})

        self.assertEqual(len(recommendations), 1)
        self.assertEqual(
            recommendations[0]["reasons"],
            [
                {"type": "agent-context", "evidence": "Services/AGENTS.md"},
                {"type": "agent-context", "evidence": "services/CLAUDE.md"},
            ],
        )

    def test_nfc_scope_identity_is_deduplicated(self) -> None:
        evidence = [
            {
                "path": "caf\u00e9/AGENTS.md",
                "kind": "agent-instructions",
                "scope": "caf\u00e9",
                "scope_state": "path-scoped",
            },
            {
                "path": "cafe\u0301/CLAUDE.md",
                "kind": "agent-instructions",
                "scope": "cafe\u0301",
                "scope_state": "path-scoped",
            },
        ]

        def analyze(items):
            return {
                "detected": [],
                "tests": [],
                "context": {
                    "evidence": items,
                    "scope_overlaps": [],
                    "conflicts": [],
                    "conflict_analysis_complete": True,
                    "truncated": False,
                },
                "project": {"files_analyzed": 2, "size": "small", "truncated": False},
            }

        first = recommend_skills(analyze(evidence), {"writing-for-agents": {}})
        second = recommend_skills(
            analyze(list(reversed(evidence))),
            {"writing-for-agents": {}},
        )

        self.assertEqual(first, second)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["scope"], "cafe\u0301")

    def test_casefold_skill_identity_is_deduplicated(self) -> None:
        candidates = [
            {
                "skill": "Writing-For-Agents",
                "score": 60,
                "scope": "Services",
                "reasons": [{"type": "agent-context", "evidence": "Services/AGENTS.md"}],
            },
            {
                "skill": "writing-for-agents",
                "score": 60,
                "scope": "services",
                "reasons": [{"type": "agent-context", "evidence": "services/CLAUDE.md"}],
            },
        ]

        recommendations = _finalize_candidates(candidates, {"writing-for-agents": {}})

        self.assertEqual(len(recommendations), 1)
        self.assertEqual(recommendations[0]["skill"], "writing-for-agents")

    def test_recommendation_literal_scope_selection_is_deterministic(self) -> None:
        candidates = [
            {
                "skill": "writing-for-agents",
                "score": 60,
                "scope": "services",
                "reasons": [{"type": "agent-context", "evidence": "services/CLAUDE.md"}],
            },
            {
                "skill": "writing-for-agents",
                "score": 60,
                "scope": "Services",
                "reasons": [{"type": "agent-context", "evidence": "Services/AGENTS.md"}],
            },
        ]

        recommendations = _finalize_candidates(candidates, {"writing-for-agents": {}})

        self.assertEqual(recommendations[0]["scope"], "Services")

    def test_recommendation_dedup_is_input_order_independent(self) -> None:
        candidates = [
            {
                "skill": "Writing-For-Agents",
                "score": 60,
                "scope": "caf\u00e9",
                "reasons": [{"type": "agent-context", "evidence": "caf\u00e9/AGENTS.md"}],
            },
            {
                "skill": "writing-for-agents",
                "score": 60,
                "scope": "cafe\u0301",
                "reasons": [{"type": "agent-context", "evidence": "cafe\u0301/CLAUDE.md"}],
            },
        ]

        first = _finalize_candidates(candidates, {"writing-for-agents": {}})
        second = _finalize_candidates(
            list(reversed(candidates)),
            {"writing-for-agents": {}},
        )

        self.assertEqual(first, second)

    def test_root_context_recommendation_is_private_and_repo_relative(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-recommend-") as temporary:
            project = Path(temporary)
            secret = "context-content-must-not-appear"
            (project / "AGENTS.md").write_text(secret + "\n", encoding="utf-8")
            analysis = analyze_project(project)

            recommendations = recommend_skills(analysis, {"writing-for-agents": {}})

        self.assertEqual(recommendations[0]["scope"], ".")
        self.assertEqual(
            recommendations[0]["reasons"],
            [{"type": "agent-context", "evidence": "AGENTS.md"}],
        )
        serialized = json.dumps(recommendations, sort_keys=True)
        for forbidden in (secret, str(project), "hostname", "username", "timestamp"):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn("\\", recommendations[0]["reasons"][0]["evidence"])

    def test_incomplete_context_marks_recommendations_incomplete(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-recommend-") as temporary:
            project = Path(temporary)
            (project / "AGENTS.md").write_bytes(b"x" * 256_001)

            result = analyze_and_recommend(project, ROOT)

        self.assertFalse(result["recommendations_complete"])
        self.assertEqual(result["recommended_skills"], [])

    def test_conflicted_context_is_not_used_as_trusted_recommendation_evidence(
        self,
    ) -> None:
        analysis = {
            "detected": [],
            "tests": [],
            "context": {
                "evidence": [
                    {
                        "path": "AGENTS.md",
                        "kind": "agent-instructions",
                        "scope": ".",
                        "scope_state": "root",
                    }
                ],
                "scope_overlaps": [],
                "conflicts": [
                    {
                        "id": "context.duplicate-source-registration",
                        "type": "duplicate-source-registration",
                        "severity": "warning",
                        "paths": ["AGENTS.md"],
                        "scope": ".",
                        "reason": "one context source is registered more than once",
                    }
                ],
                "conflict_analysis_complete": True,
                "truncated": False,
            },
            "project": {"files_analyzed": 1, "size": "small", "truncated": False},
        }

        recommendations = recommend_skills(analysis, {"writing-for-agents": {}})

        self.assertEqual(recommendations, [])
        self.assertFalse(recommendations_complete(analysis))

    def test_normalized_path_collision_remains_untrusted(self) -> None:
        analysis = {
            "detected": [],
            "tests": [],
            "context": {
                "evidence": [
                    {
                        "path": "Services/AGENTS.md",
                        "kind": "agent-instructions",
                        "scope": "Services",
                        "scope_state": "path-scoped",
                    }
                ],
                "scope_overlaps": [],
                "conflicts": [
                    {
                        "id": "context.normalized-path-collision",
                        "type": "normalized-path-collision",
                        "severity": "warning",
                        "paths": ["Services/AGENTS.md", "services/agents.MD"],
                        "scope": "Services",
                        "reason": "multiple context paths share one NFC-casefold identity",
                    }
                ],
                "conflict_analysis_complete": False,
                "truncated": True,
            },
            "project": {"files_analyzed": 2, "size": "small", "truncated": False},
        }

        recommendations = recommend_skills(analysis, {"writing-for-agents": {}})

        self.assertEqual(recommendations, [])
        self.assertFalse(recommendations_complete(analysis))

    def test_path_scoped_context_does_not_escape_scope(self) -> None:
        analysis = {
            "detected": [],
            "tests": [],
            "context": {
                "evidence": [
                    {
                        "path": "services/api/AGENTS.md",
                        "kind": "agent-instructions",
                        "scope": "services/api",
                        "scope_state": "path-scoped",
                    }
                ],
                "scope_overlaps": [],
                "conflicts": [],
                "conflict_analysis_complete": True,
                "truncated": False,
            },
            "project": {"files_analyzed": 1, "size": "small", "truncated": False},
        }

        recommendations = recommend_skills(analysis, {"writing-for-agents": {}})

        self.assertEqual(
            recommendations,
            [
                {
                    "skill": "writing-for-agents",
                    "score": 60,
                    "scope": "services/api",
                    "reasons": [
                        {
                            "type": "agent-context",
                            "evidence": "services/api/AGENTS.md",
                        }
                    ],
                }
            ],
        )

    def test_unknown_context_scope_does_not_create_precise_scope(self) -> None:
        analysis = {
            "detected": [],
            "tests": [],
            "context": {
                "evidence": [
                    {
                        "path": ".cursor/rules/frontend.md",
                        "kind": "cursor-rule",
                        "scope": "unknown",
                        "scope_state": "unknown",
                    }
                ],
                "scope_overlaps": [],
                "conflicts": [],
                "conflict_analysis_complete": True,
                "truncated": False,
            },
            "project": {"files_analyzed": 1, "size": "small", "truncated": False},
        }

        recommendations = recommend_skills(analysis, {"writing-for-agents": {}})

        self.assertEqual(recommendations, [])
        self.assertNotIn("frontend", str(recommendations))

    def test_duplicate_recommendations_are_deduplicated(self) -> None:
        analysis = {
            "detected": [],
            "tests": [],
            "context": {
                "evidence": [
                    {
                        "path": "CLAUDE.md",
                        "kind": "agent-instructions",
                        "scope": ".",
                        "scope_state": "root",
                    },
                    {
                        "path": "AGENTS.md",
                        "kind": "agent-instructions",
                        "scope": ".",
                        "scope_state": "root",
                    },
                    {
                        "path": "AGENTS.md",
                        "kind": "agent-instructions",
                        "scope": ".",
                        "scope_state": "root",
                    },
                ],
                "scope_overlaps": [],
                "conflicts": [],
                "conflict_analysis_complete": True,
                "truncated": False,
            },
            "project": {"files_analyzed": 2, "size": "small", "truncated": False},
        }

        recommendations = recommend_skills(analysis, {"writing-for-agents": {}})

        self.assertEqual(len(recommendations), 1)
        self.assertEqual(
            recommendations[0]["reasons"],
            [
                {"type": "agent-context", "evidence": "AGENTS.md"},
                {"type": "agent-context", "evidence": "CLAUDE.md"},
            ],
        )

    def test_recommendation_order_is_deterministic(self) -> None:
        evidence = [
            {
                "path": "services/Zeta/AGENTS.md",
                "kind": "agent-instructions",
                "scope": "services/Zeta",
                "scope_state": "path-scoped",
            },
            {
                "path": "services/alpha/AGENTS.md",
                "kind": "agent-instructions",
                "scope": "services/alpha",
                "scope_state": "path-scoped",
            },
        ]

        def analysis(items):
            return {
                "detected": [],
                "tests": [],
                "context": {
                    "evidence": items,
                    "scope_overlaps": [],
                    "conflicts": [],
                    "conflict_analysis_complete": True,
                    "truncated": False,
                },
                "project": {"files_analyzed": 2, "size": "small", "truncated": False},
            }

        first = recommend_skills(analysis(evidence), {"writing-for-agents": {}})
        second = recommend_skills(analysis(list(reversed(evidence))), {"writing-for-agents": {}})

        self.assertEqual(first, second)
        self.assertEqual(
            [item["scope"] for item in first],
            ["services/alpha", "services/Zeta"],
        )

    def test_nonexistent_registry_skills_are_never_emitted(self) -> None:
        analysis = {
            "detected": [
                {"technology": "python", "evidence": ["pyproject.toml"]},
                {"technology": "github-actions", "evidence": [".github/workflows/ci.yml"]},
            ],
            "tests": [{"framework": "pytest", "evidence": ["tests/test_app.py"]}],
            "context": {
                "evidence": [
                    {
                        "path": "AGENTS.md",
                        "kind": "agent-instructions",
                        "scope": ".",
                        "scope_state": "root",
                    }
                ],
                "scope_overlaps": [],
                "conflicts": [],
                "conflict_analysis_complete": True,
                "truncated": False,
            },
            "project": {"files_analyzed": 20, "size": "small", "truncated": False},
        }
        registry = validate_registry(ROOT)

        recommendations = recommend_skills(analysis, registry)

        self.assertEqual(recommendations, [])
        self.assertTrue(all(item["skill"] in registry for item in recommendations))

    def test_scoring_is_explainable_deduplicated_and_stably_sorted(self) -> None:
        analysis = {
            "detected": [
                {"technology": "python", "evidence": ["pyproject.toml", "pyproject.toml"]},
                {"technology": "github-actions", "evidence": [".github/workflows/ci.yml"]},
            ],
            "tests": [
                {"framework": "pytest", "evidence": ["tests/test_app.py"]},
                {"framework": "pytest", "evidence": ["tests/test_app.py"]},
            ],
            "project": {"files_analyzed": 20, "size": "small", "truncated": False},
        }
        registry = {
            "code-review": {},
            "diagnosing-bugs": {},
            "tdd": {},
        }

        recommendations = recommend_skills(analysis, registry)

        self.assertEqual(
            [item["skill"] for item in recommendations],
            ["code-review", "tdd", "diagnosing-bugs"],
        )
        self.assertEqual(recommendations[0]["score"], recommendations[1]["score"])
        self.assertTrue(all(item["reasons"] for item in recommendations))
        self.assertTrue(all(item["scope"] == "." for item in recommendations))
        self.assertEqual(
            recommendations[1]["reasons"],
            [{"type": "test-framework", "evidence": "pytest"}],
        )
        self.assertEqual(len({item["skill"] for item in recommendations}), len(recommendations))

    def test_profile_recommendation_is_conservative(self) -> None:
        def analysis(size: str, truncated: bool = False):
            return {"project": {"files_analyzed": 0, "size": size, "truncated": truncated}}

        self.assertEqual(recommend_profile(analysis("small")), "small-project")
        self.assertEqual(recommend_profile(analysis("medium")), "universal")
        self.assertEqual(recommend_profile(analysis("large")), "large-project")
        self.assertEqual(recommend_profile(analysis("large", truncated=True)), "universal")


if __name__ == "__main__":
    unittest.main()

import json
import tempfile
import unittest
from pathlib import Path

from skill_orchestrator.analyzer import analyze_project
from skill_orchestrator.context import scope_contains
from skill_orchestrator.recommendations import (
    _finalize_candidates,
    analyze_and_recommend,
    recommend_profile,
    recommend_skills,
    recommendations_complete,
)
from skill_orchestrator.validation import validate_registry


ROOT = Path(__file__).resolve().parents[1]


class RecommendationTests(unittest.TestCase):
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

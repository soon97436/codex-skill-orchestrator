import unittest
from pathlib import Path

from skill_orchestrator.recommendations import recommend_profile, recommend_skills
from skill_orchestrator.validation import validate_registry


ROOT = Path(__file__).resolve().parents[1]


class RecommendationTests(unittest.TestCase):
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

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from skill_orchestrator.capabilities import analyze_capabilities
from skill_orchestrator.cli import _human_analysis
from skill_orchestrator.recommendations import (
    analyze_and_recommend,
    evaluate_recommendations,
    recommendations_complete,
)


ROOT = Path(__file__).resolve().parents[1]


def run_analyze_bytes(project: Path) -> subprocess.CompletedProcess:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [sys.executable, "-m", "skill_orchestrator", "analyze", "--json"],
        cwd=project,
        env=environment,
        stdin=subprocess.DEVNULL,
        check=False,
        capture_output=True,
        text=False,
    )


class PhaseThreeIntegrationTests(unittest.TestCase):
    def test_phase3_integrated_output_is_deterministic(self) -> None:
        files = {
            "AGENTS.md": "repository instructions\n",
            "package.json": '{"devDependencies":{"vitest":"1.0.0"}}\n',
            "pyproject.toml": "[project]\nname='demo'\n",
            "tests/test_app.py": "def test_demo():\n    assert True\n",
        }

        def analyze(order):
            with tempfile.TemporaryDirectory(prefix="cso-phase3-") as temporary:
                project = Path(temporary)
                for relative in order:
                    path = project / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(files[relative], encoding="utf-8")
                return analyze_and_recommend(project, ROOT)

        first = analyze(files)
        second = analyze(reversed(tuple(files)))

        self.assertEqual(first, second)
        self.assertEqual(
            {
                "recommended_skills",
                "recommendations_complete",
                "recommendation_explanations",
                "capability_analysis",
            },
            {
                key
                for key in first
                if key
                in {
                    "recommended_skills",
                    "recommendations_complete",
                    "recommendation_explanations",
                    "capability_analysis",
                }
            },
        )

    def test_phase3_incomplete_context_propagates_consistently(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-phase3-") as temporary:
            project = Path(temporary)
            (project / "AGENTS.md").write_bytes(b"x" * 256_001)

            result = analyze_and_recommend(project, ROOT)

        self.assertTrue(result["context"]["truncated"])
        self.assertFalse(result["recommendations_complete"])
        self.assertEqual(result["recommendation_explanations"]["status"], "incomplete")
        self.assertIn(
            {"reason_id": "recommendation.incomplete.context-discovery"},
            result["recommendation_explanations"]["limitations"],
        )
        self.assertEqual(result["capability_analysis"]["status"], "complete")
        self.assertFalse(result["capability_analysis"]["truncated"])

    def test_phase3_truncation_statuses_remain_independent(self) -> None:
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
        registry = {"code-review": {}, "tdd": {}}

        recommendation = evaluate_recommendations(
            analysis,
            registry,
            max_evidence_refs=1,
        )
        capability = analyze_capabilities(registry, max_skills_evaluated=1)

        self.assertTrue(recommendations_complete(analysis))
        self.assertTrue(recommendation["selected"])
        self.assertEqual(recommendation["explanations"]["status"], "incomplete")
        self.assertTrue(recommendation["explanations"]["truncated"])
        self.assertIn(
            {"reason_id": "recommendation.incomplete.explanation-limit"},
            recommendation["explanations"]["limitations"],
        )
        self.assertEqual(capability["status"], "incomplete")
        self.assertTrue(capability["truncated"])
        self.assertTrue(
            all(
                skill["declaration_status"] == "missing"
                for skill in capability["skills"]
            )
        )

    def test_phase3_conflict_never_selects_untrusted_context(self) -> None:
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
            "truncated": False,
        }
        registry = {"writing-for-agents": {}}

        result = evaluate_recommendations(analysis, registry)

        self.assertEqual(result["selected"], [])
        self.assertEqual(result["explanations"]["selected"], [])
        self.assertEqual(
            result["explanations"]["excluded"][0]["reason_id"],
            "recommendation.excluded.context-conflict",
        )
        self.assertEqual(result["explanations"]["status"], "incomplete")
        limitation_ids = {
            item["reason_id"] for item in result["explanations"]["limitations"]
        }
        self.assertEqual(
            limitation_ids,
            {
                "recommendation.incomplete.conflict-analysis",
                "recommendation.incomplete.context-conflict",
                "recommendation.incomplete.context-discovery",
            },
        )

    def test_phase3_registry_boundary_is_shared_across_outputs(self) -> None:
        analysis = {
            "detected": [{"technology": "python", "evidence": ["pyproject.toml"]}],
            "tests": [{"framework": "pytest", "evidence": ["tests/test_app.py"]}],
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
                "conflicts": [],
                "conflict_analysis_complete": True,
                "truncated": False,
            },
            "project": {"files_analyzed": 3, "size": "small", "truncated": False},
            "truncated": False,
        }
        registry = {
            "Writing-For-Agents": {},
            "declared-audit-skill": {
                "capabilities": {
                    "schema_version": 1,
                    "filesystem": {"read": ["project"], "write": []},
                    "network": {"mode": "none"},
                    "process": {"mode": "none", "commands": []},
                }
            },
        }

        recommendation = evaluate_recommendations(analysis, registry)
        capability = analyze_capabilities(registry)

        registry_ids = set(registry)
        selected_ids = {item["skill"] for item in recommendation["selected"]}
        excluded_ids = {
            item["skill"] for item in recommendation["explanations"]["excluded"]
        }
        capability_ids = {item["skill"] for item in capability["skills"]}
        self.assertEqual(selected_ids, {"Writing-For-Agents"})
        self.assertEqual(
            set(recommendation["selected"][0]),
            {"skill", "score", "scope", "reasons"},
        )
        self.assertEqual(
            set(recommendation["selected"][0]["reasons"][0]),
            {"type", "evidence"},
        )
        self.assertEqual(excluded_ids, {"declared-audit-skill"})
        self.assertEqual(capability_ids, registry_ids)
        self.assertEqual(capability["policy_mode"], "declarative-only")
        self.assertEqual(capability["enforcement_status"], "not-implemented")
        self.assertLessEqual(selected_ids | excluded_ids | capability_ids, registry_ids)
        self.assertNotIn("code-review", selected_ids | excluded_ids | capability_ids)
        self.assertNotIn("tdd", selected_ids | excluded_ids | capability_ids)

    def test_phase3_integrated_output_is_metadata_only(self) -> None:
        forbidden_values = (
            "phase3-secret-token-value",
            "private prompt excerpt",
            "ENV_SECRET=do-not-emit",
            "C:\\Users\\private-user\\project",
            "\\\\private-host\\share\\project",
        )
        with tempfile.TemporaryDirectory(prefix="cso-phase3-private-") as temporary:
            project = Path(temporary)
            (project / "AGENTS.md").write_text(
                "\n".join(forbidden_values) + "\n",
                encoding="utf-8",
            )

            result = analyze_and_recommend(project, ROOT)

        serialized = json.dumps(result, ensure_ascii=False, sort_keys=True)
        for forbidden in (*forbidden_values, str(project)):
            self.assertNotIn(forbidden, serialized)
        for forbidden_key in (
            "hostname",
            "username",
            "timestamp",
            "environment",
            "machine_metadata",
        ):
            self.assertNotIn(forbidden_key, serialized.casefold())
        for evidence in result["context"]["evidence"]:
            self.assertFalse(Path(evidence["path"]).is_absolute())
            self.assertNotIn("\\", evidence["path"])

    def test_phase3_human_output_remains_concise(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-phase3-") as temporary:
            project = Path(temporary)
            (project / "AGENTS.md").write_text("instructions\n", encoding="utf-8")
            result = analyze_and_recommend(project, ROOT)

        output = _human_analysis(result)

        self.assertLess(len(output.splitlines()), 40)
        self.assertNotIn("capability.declaration.missing", output)
        self.assertNotIn("recommendation.excluded", output)
        self.assertNotIn("recommendation.unmatched", output)
        self.assertNotIn("enforced", output.casefold())
        self.assertNotIn("enforcement", output.casefold())

    def test_phase3_raw_json_is_input_order_independent(self) -> None:
        files = {
            "services/api/AGENTS.md": "instructions\n",
            "package.json": '{"dependencies":{"react":"1.0.0"}}\n',
            "pyproject.toml": "[project]\nname='demo'\n",
        }

        def render(order):
            with tempfile.TemporaryDirectory(prefix="cso-phase3-") as temporary:
                project = Path(temporary)
                for relative in order:
                    path = project / relative
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text(files[relative], encoding="utf-8")
                return run_analyze_bytes(project)

        first = render(files)
        second = render(reversed(tuple(files)))

        self.assertEqual(first.returncode, 0, first.stderr.decode("utf-8"))
        self.assertEqual(second.returncode, 0, second.stderr.decode("utf-8"))
        self.assertEqual(first.stdout, second.stdout)


if __name__ == "__main__":
    unittest.main()

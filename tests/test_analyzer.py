import json
import tempfile
import unittest
from pathlib import Path

from skill_orchestrator.analyzer import MAX_METADATA_BYTES, analyze_project
from skill_orchestrator.errors import SecurityError


class AnalyzerTests(unittest.TestCase):
    def test_empty_repository_is_small_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-analyze-") as temporary:
            root = Path(temporary)

            first = analyze_project(root)
            second = analyze_project(root)

        self.assertEqual(first, second)
        self.assertEqual(first["detected"], [])
        self.assertEqual(first["tests"], [])
        self.assertFalse(first["truncated"])
        self.assertEqual(
            first["project"],
            {"files_analyzed": 0, "size": "small", "truncated": False},
        )

    def test_mixed_stack_has_relative_evidence_and_excludes_dependencies(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-analyze-") as temporary:
            root = Path(temporary)
            (root / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
            (root / "package.json").write_text(
                json.dumps(
                    {
                        "dependencies": {"react": "1"},
                        "devDependencies": {"typescript": "1", "vitest": "1"},
                        "scripts": {"test": "do not inspect this command"},
                    }
                ),
                encoding="utf-8",
            )
            (root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
            workflow = root / ".github" / "workflows"
            workflow.mkdir(parents=True)
            (workflow / "ci.yml").write_text("name: CI\n", encoding="utf-8")
            tests = root / "tests"
            tests.mkdir()
            (tests / "test_demo.py").write_text("pass\n", encoding="utf-8")
            dependency = root / "node_modules" / "ignored"
            dependency.mkdir(parents=True)
            (dependency / "package.json").write_text("{}", encoding="utf-8")

            result = analyze_project(root)

        detected = {item["technology"]: item["evidence"] for item in result["detected"]}
        self.assertEqual(
            sorted(detected),
            ["docker", "github-actions", "nodejs", "python", "react", "typescript"],
        )
        self.assertEqual(detected["react"], ["package.json"])
        self.assertEqual(detected["github-actions"], [".github/workflows/ci.yml"])
        self.assertNotIn("node_modules/ignored/package.json", str(result))
        self.assertEqual(
            [item["framework"] for item in result["tests"]],
            ["pytest", "unittest", "vitest"],
        )
        self.assertTrue(
            all(not Path(evidence).is_absolute() for item in result["detected"] for evidence in item["evidence"])
        )

    def test_unsafe_package_metadata_is_skipped_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-analyze-") as temporary:
            root = Path(temporary)
            (root / "package.json").write_text("{not-json", encoding="utf-8")
            nested = root / "nested"
            nested.mkdir()
            (nested / "package.json").write_bytes(b" " * (MAX_METADATA_BYTES + 1))

            result = analyze_project(root)

        detected = {item["technology"] for item in result["detected"]}
        self.assertEqual(detected, {"nodejs"})
        self.assertEqual(
            result["warnings"],
            [
                "Skipped malformed metadata: package.json",
                "Skipped oversized metadata: nested/package.json",
            ],
        )

    def test_traversal_limit_is_explicit_and_does_not_claim_large(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-analyze-") as temporary:
            root = Path(temporary)
            for index in range(6):
                (root / f"file-{index}.txt").write_text("x", encoding="utf-8")

            result = analyze_project(root, max_entries=3)

        self.assertTrue(result["project"]["truncated"])
        self.assertTrue(result["truncated"])
        self.assertEqual(result["project"]["files_analyzed"], 3)
        self.assertEqual(result["project"]["size"], "unknown")

    def test_link_escape_is_not_followed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-analyze-") as temporary:
            base = Path(temporary)
            root = base / "project"
            root.mkdir()
            outside = base / "outside"
            outside.mkdir()
            (outside / "Cargo.toml").write_text("[package]\n", encoding="utf-8")
            try:
                (root / "escaped").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory links are unavailable")

            result = analyze_project(root)

        self.assertNotIn("rust", {item["technology"] for item in result["detected"]})
        self.assertEqual(result["warnings"], ["Skipped link or reparse point: escaped"])

    def test_project_root_link_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-analyze-") as temporary:
            base = Path(temporary)
            target = base / "target"
            target.mkdir()
            link = base / "project-link"
            try:
                link.symlink_to(target, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory links are unavailable")

            with self.assertRaises(SecurityError):
                analyze_project(link)

    def test_compiled_and_infrastructure_markers_are_detected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-analyze-") as temporary:
            root = Path(temporary)
            for name in (
                "Cargo.toml",
                "go.mod",
                "pom.xml",
                "build.gradle.kts",
                "application.sln",
                "service.csproj",
                "main.tf",
            ):
                (root / name).write_text("metadata\n", encoding="utf-8")

            result = analyze_project(root)

        self.assertEqual(
            [item["technology"] for item in result["detected"]],
            ["dotnet", "go", "java-gradle", "java-maven", "rust", "terraform"],
        )

    def test_large_project_threshold_is_centralized(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-analyze-") as temporary:
            root = Path(temporary)
            for index in range(1_000):
                (root / f"file-{index:04d}.txt").touch()

            result = analyze_project(root)

        self.assertEqual(result["project"]["size"], "large")
        self.assertFalse(result["project"]["truncated"])

    def test_empty_github_workflows_directory_is_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-analyze-") as temporary:
            root = Path(temporary)
            (root / ".github" / "workflows").mkdir(parents=True)

            result = analyze_project(root)

        github_actions = next(item for item in result["detected"] if item["technology"] == "github-actions")
        self.assertEqual(github_actions["evidence"], [".github/workflows/"])


if __name__ == "__main__":
    unittest.main()

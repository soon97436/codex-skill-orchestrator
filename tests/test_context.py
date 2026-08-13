import tempfile
import unittest
from pathlib import Path

from skill_orchestrator.analyzer import analyze_project
from skill_orchestrator.context import MAX_CONTEXT_BYTES, MAX_CONTEXT_FILES


class ContextDiscoveryTests(unittest.TestCase):
    def test_absent_context_files_produce_empty_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            result = analyze_project(Path(temporary))

        self.assertEqual(result["context"], {"evidence": [], "truncated": False})

    def test_root_agents_file_has_repository_scoped_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            root = Path(temporary)
            content = "# Repository instructions\n"
            (root / "AGENTS.md").write_text(content, encoding="utf-8")

            result = analyze_project(root)

        self.assertEqual(
            result["context"],
            {
                "evidence": [
                    {
                        "path": "AGENTS.md",
                        "kind": "agent-instructions",
                        "scope": ".",
                        "size_bytes": len(content.encode("utf-8")),
                    }
                ],
                "truncated": False,
            },
        )

    def test_nested_claude_file_uses_its_directory_as_scope(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            root = Path(temporary)
            nested = root / "services" / "api"
            nested.mkdir(parents=True)
            (nested / "CLAUDE.md").write_text("API guidance\n", encoding="utf-8")

            result = analyze_project(root)

        self.assertEqual(
            result["context"]["evidence"],
            [
                {
                    "path": "services/api/CLAUDE.md",
                    "kind": "agent-instructions",
                    "scope": "services/api",
                    "size_bytes": 13,
                }
            ],
        )

    def test_cursor_and_copilot_context_is_ordered_with_explicit_scope(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            root = Path(temporary)
            cursor_rules = root / ".cursor" / "rules"
            cursor_rules.mkdir(parents=True)
            github = root / ".github"
            github.mkdir()
            (cursor_rules / "z-backend.mdc").write_text("backend\n", encoding="utf-8")
            (cursor_rules / "a-frontend.md").write_text("frontend\n", encoding="utf-8")
            (root / ".cursorrules").write_text("legacy\n", encoding="utf-8")
            (github / "copilot-instructions.md").write_text("copilot\n", encoding="utf-8")

            first = analyze_project(root)
            second = analyze_project(root)

        self.assertEqual(first["context"], second["context"])
        self.assertEqual(
            [(item["path"], item["kind"], item["scope"]) for item in first["context"]["evidence"]],
            [
                (".cursor/rules/a-frontend.md", "cursor-rule", "unknown"),
                (".cursor/rules/z-backend.mdc", "cursor-rule", "unknown"),
                (".cursorrules", "cursor-rules", "."),
                (".github/copilot-instructions.md", "copilot-instructions", "."),
            ],
        )

    def test_oversized_context_file_is_not_read_or_emitted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            root = Path(temporary)
            (root / "AGENTS.md").write_bytes(b"x" * (MAX_CONTEXT_BYTES + 1))

            result = analyze_project(root)

        self.assertEqual(result["context"], {"evidence": [], "truncated": False})
        self.assertIn("Skipped oversized context file: AGENTS.md", result["warnings"])

    def test_context_evidence_count_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            root = Path(temporary)
            for index in range(MAX_CONTEXT_FILES + 1):
                directory = root / f"scope-{index:03d}"
                directory.mkdir()
                (directory / "AGENTS.md").write_text("rules\n", encoding="utf-8")

            result = analyze_project(root)

        self.assertEqual(len(result["context"]["evidence"]), MAX_CONTEXT_FILES)
        self.assertTrue(result["context"]["truncated"])
        self.assertIn(
            f"Context evidence truncated at {MAX_CONTEXT_FILES} files",
            result["warnings"],
        )

    def test_context_symlink_escape_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            base = Path(temporary)
            root = base / "project"
            root.mkdir()
            outside = base / "outside-agents.md"
            outside.write_text("private instructions\n", encoding="utf-8")
            try:
                (root / "AGENTS.md").symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("file links are unavailable")

            result = analyze_project(root)

        self.assertEqual(result["context"], {"evidence": [], "truncated": False})
        self.assertIn("Skipped link or reparse point: AGENTS.md", result["warnings"])
        self.assertNotIn(str(base), str(result))

    def test_bounded_project_traversal_marks_context_incomplete(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            root = Path(temporary)
            (root / "a-first.txt").write_text("first\n", encoding="utf-8")
            (root / "AGENTS.md").write_text("instructions\n", encoding="utf-8")

            result = analyze_project(root, max_entries=1)

        self.assertTrue(result["truncated"])
        self.assertEqual(result["context"]["evidence"], [])
        self.assertTrue(result["context"]["truncated"])
        self.assertIn(
            "Context discovery incomplete because project traversal was truncated",
            result["warnings"],
        )

    def test_known_names_are_case_insensitive_and_paths_are_posix_relative(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            root = Path(temporary)
            nested = root / "Services" / "Web"
            nested.mkdir(parents=True)
            (nested / "agents.MD").write_text("web rules\n", encoding="utf-8")

            result = analyze_project(root)

        evidence = result["context"]["evidence"][0]
        self.assertEqual(evidence["path"], "Services/Web/agents.MD")
        self.assertEqual(evidence["scope"], "Services/Web")
        self.assertNotIn("\\", evidence["path"])
        self.assertFalse(Path(evidence["path"]).is_absolute())

    def test_context_is_not_discovered_in_excluded_runtime_directories(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            root = Path(temporary)
            for name in (".git", ".cso", "node_modules", "venv", "build", "cache"):
                directory = root / name
                directory.mkdir()
                (directory / "AGENTS.md").write_text("ignored\n", encoding="utf-8")

            result = analyze_project(root)

        self.assertEqual(result["context"], {"evidence": [], "truncated": False})


if __name__ == "__main__":
    unittest.main()

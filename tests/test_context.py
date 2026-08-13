import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skill_orchestrator import analyzer
from skill_orchestrator.analyzer import analyze_project
from skill_orchestrator.context import MAX_CONTEXT_FILE_BYTES, MAX_CONTEXT_FILES, discover_context


def expected_context(*, evidence=(), overlaps=(), conflicts=(), incomplete=False):
    return {
        "evidence": list(evidence),
        "scope_overlaps": list(overlaps),
        "conflicts": list(conflicts),
        "conflict_analysis_complete": not incomplete,
        "truncated": incomplete,
    }


class ContextDiscoveryTests(unittest.TestCase):
    def test_scan_entry_limit_bounds_actual_directory_enumeration(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            root = Path(temporary)
            for index in range(20):
                (root / f"file-{index:02d}.txt").touch()
            scanned = 0
            real_scandir = analyzer.os.scandir

            class CountingScandir:
                def __init__(self, path):
                    self._iterator = real_scandir(path)

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self._iterator.close()

                def __iter__(self):
                    return self

                def __next__(self):
                    nonlocal scanned
                    entry = next(self._iterator)
                    scanned += 1
                    return entry

            with mock.patch("skill_orchestrator.analyzer.os.scandir", side_effect=CountingScandir):
                result = analyze_project(root, max_entries=3)

        self.assertLessEqual(scanned, 4)
        self.assertTrue(result["context"]["truncated"])

    def test_reversed_filesystem_enumeration_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            root = Path(temporary)
            (root / "AGENTS.md").write_text("root\n", encoding="utf-8")
            cursor = root / ".cursor" / "rules"
            cursor.mkdir(parents=True)
            (cursor / "z.md").write_text("z\n", encoding="utf-8")
            (cursor / "a.md").write_text("a\n", encoding="utf-8")
            normal = analyze_project(root)
            real_scandir = analyzer.os.scandir

            class ReversedScandir:
                def __init__(self, path):
                    with real_scandir(path) as iterator:
                        self._entries = iter(reversed(list(iterator)))

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return None

                def __iter__(self):
                    return self

                def __next__(self):
                    return next(self._entries)

            with mock.patch("skill_orchestrator.analyzer.os.scandir", side_effect=ReversedScandir):
                reversed_result = analyze_project(root)

        self.assertEqual(normal, reversed_result)

    def test_absent_context_files_produce_empty_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            result = analyze_project(Path(temporary))

        self.assertEqual(result["context"], expected_context())

    def test_root_agents_file_has_repository_scoped_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            root = Path(temporary)
            content = "# Repository instructions\n"
            (root / "AGENTS.md").write_text(content, encoding="utf-8")

            result = analyze_project(root)

        self.assertEqual(
            result["context"],
            expected_context(
                evidence=[
                    {
                        "path": "AGENTS.md",
                        "kind": "agent-instructions",
                        "scope": ".",
                        "scope_state": "root",
                    }
                ]
            ),
        )

    def test_lf_and_crlf_contexts_have_equivalent_semantic_evidence(self) -> None:
        documents = []
        for content in (b"first\nsecond\n", b"first\r\nsecond\r\n"):
            with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
                root = Path(temporary)
                (root / "AGENTS.md").write_bytes(content)
                documents.append(analyze_project(root)["context"])

        self.assertEqual(documents[0], documents[1])

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
                    "scope_state": "path-scoped",
                }
            ],
        )

    def test_scope_overlap_detection_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            root = Path(temporary)
            (root / "AGENTS.md").write_text("root\n", encoding="utf-8")
            services = root / "services"
            api = services / "api"
            api.mkdir(parents=True)
            (services / "CLAUDE.md").write_text("services\n", encoding="utf-8")
            (api / "AGENTS.md").write_text("api\n", encoding="utf-8")

            result = analyze_project(root)

        self.assertEqual(
            result["context"]["scope_overlaps"],
            [
                {
                    "type": "scope-overlap",
                    "paths": ["AGENTS.md", "services/CLAUDE.md"],
                    "scopes": [".", "services"],
                    "relationship": "ancestor-descendant",
                },
                {
                    "type": "scope-overlap",
                    "paths": ["AGENTS.md", "services/api/AGENTS.md"],
                    "scopes": [".", "services/api"],
                    "relationship": "ancestor-descendant",
                },
                {
                    "type": "scope-overlap",
                    "paths": ["services/CLAUDE.md", "services/api/AGENTS.md"],
                    "scopes": ["services", "services/api"],
                    "relationship": "ancestor-descendant",
                },
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
            (cursor_rules / "notes.txt").write_text("notes\n", encoding="utf-8")
            recursive = cursor_rules / "nested"
            recursive.mkdir()
            (recursive / "ignored.md").write_text("nested\n", encoding="utf-8")
            (root / ".cursorrules").write_text("legacy\n", encoding="utf-8")
            (github / "copilot-instructions.md").write_text("copilot\n", encoding="utf-8")

            first = analyze_project(root)
            second = analyze_project(root)

        self.assertEqual(first["context"], second["context"])
        self.assertEqual(
            [
                (item["path"], item["kind"], item["scope"], item["scope_state"])
                for item in first["context"]["evidence"]
            ],
            [
                (".cursor/rules/a-frontend.md", "cursor-rule", "unknown", "unknown"),
                (".cursorrules", "cursor-rules", ".", "root"),
                (".github/copilot-instructions.md", "copilot-instructions", ".", "root"),
            ],
        )
        self.assertEqual(
            first["context"]["scope_overlaps"],
            [
                {
                    "type": "scope-overlap",
                    "paths": [".cursorrules", ".github/copilot-instructions.md"],
                    "scopes": [".", "."],
                    "relationship": "same-scope",
                }
            ],
        )

    def test_oversized_context_file_is_not_read_or_emitted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            root = Path(temporary)
            (root / "AGENTS.md").write_bytes(b"x" * (MAX_CONTEXT_FILE_BYTES + 1))

            result = analyze_project(root)

        self.assertEqual(result["context"], expected_context(incomplete=True))
        self.assertIn("context-oversized: AGENTS.md", result["warnings"])

    def test_malformed_utf8_is_skipped_without_content_leakage(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            root = Path(temporary)
            (root / "AGENTS.md").write_bytes(b"secret-prefix\xffsecret-suffix")

            result = analyze_project(root)

        self.assertEqual(result["context"], expected_context(incomplete=True))
        self.assertIn("context-invalid-utf8: AGENTS.md", result["warnings"])
        self.assertNotIn("secret-prefix", str(result))
        self.assertNotIn("secret-suffix", str(result))

    def test_utf8_bom_empty_and_secret_content_emit_only_metadata(self) -> None:
        payloads = (b"", b"\xef\xbb\xbfvalid\n", b"sensitive-marker-do-not-emit-this-value\n")
        for payload in payloads:
            with self.subTest(payload=payload[:3]):
                with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
                    root = Path(temporary)
                    (root / "AGENTS.md").write_bytes(payload)

                    result = analyze_project(root)

                self.assertEqual(
                    result["context"],
                    expected_context(
                        evidence=[
                            {
                                "path": "AGENTS.md",
                                "kind": "agent-instructions",
                                "scope": ".",
                                "scope_state": "root",
                            }
                        ]
                    ),
                )
                self.assertNotIn("do-not-emit-this-value", str(result))

    def test_context_json_contains_no_content_size_or_machine_identity(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            root = Path(temporary)
            secret = "credential-like-value-must-not-appear"
            (root / "AGENTS.md").write_text(secret + "\n", encoding="utf-8")

            result = analyze_project(root)

        serialized = json.dumps(result, sort_keys=True)
        self.assertEqual(
            set(result["context"]["evidence"][0]),
            {"path", "kind", "scope", "scope_state"},
        )
        for forbidden in (secret, str(root), "size_bytes", "timestamp", "hostname", "username"):
            self.assertNotIn(forbidden, serialized)

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

        self.assertEqual(result["context"], expected_context(incomplete=True))
        self.assertIn("Skipped link or reparse point: AGENTS.md", result["warnings"])
        self.assertIn("context-unsafe-path: AGENTS.md", result["warnings"])
        self.assertNotIn(str(base), str(result))

    def test_symlink_directory_escape_marks_context_incomplete(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            base = Path(temporary)
            root = base / "project"
            root.mkdir()
            outside = base / "outside"
            outside.mkdir()
            (outside / "AGENTS.md").write_text("outside\n", encoding="utf-8")
            try:
                (root / "linked").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory links are unavailable")

            result = analyze_project(root)

        self.assertEqual(result["context"], expected_context(incomplete=True))
        self.assertIn("context-unsafe-path: linked", result["warnings"])
        self.assertNotIn("linked/AGENTS.md", str(result))

    def test_mocked_reparse_context_file_is_not_opened(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            root = Path(temporary)
            (root / "AGENTS.md").write_text("must not be read\n", encoding="utf-8")

            with mock.patch("skill_orchestrator.context.is_reparse_point", return_value=True):
                with mock.patch("skill_orchestrator.context.os.open") as mocked_open:
                    result = analyze_project(root)

        mocked_open.assert_not_called()
        self.assertEqual(result["context"], expected_context(incomplete=True))
        self.assertIn("context-unsafe-file: AGENTS.md", result["warnings"])

    def test_bounded_project_traversal_marks_context_incomplete(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            root = Path(temporary)
            (root / "a-first.txt").write_text("first\n", encoding="utf-8")
            (root / "AGENTS.md").write_text("instructions\n", encoding="utf-8")

            result = analyze_project(root, max_entries=1)

        self.assertTrue(result["truncated"])
        self.assertEqual(result["context"]["evidence"], [])
        self.assertTrue(result["context"]["truncated"])
        self.assertFalse(result["context"]["conflict_analysis_complete"])
        self.assertEqual(result["context"]["conflicts"], [])
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

    def test_case_and_unicode_colliding_context_paths_are_ambiguous(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            root = Path(temporary)
            first = root / "first"
            second = root / "second"
            third = root / "third"
            fourth = root / "fourth"
            for path in (first, second, third, fourth):
                path.write_text("valid\n", encoding="utf-8")
            warnings = []

            registrations = [
                (first, "AGENTS.md"),
                (second, "agents.MD"),
                (third, "caf\u00e9/CLAUDE.md"),
                (fourth, "cafe\u0301/CLAUDE.md"),
            ]
            result = discover_context(registrations, warnings)
            reversed_warnings = []
            reversed_result = discover_context(list(reversed(registrations)), reversed_warnings)

        self.assertEqual(
            result,
            {
                "evidence": [],
                "scope_overlaps": [],
                "conflicts": [
                    {
                        "id": "context.normalized-path-collision",
                        "type": "normalized-path-collision",
                        "severity": "warning",
                        "paths": ["AGENTS.md", "agents.MD"],
                        "scope": ".",
                        "reason": "multiple context paths share one NFC-casefold identity",
                    },
                    {
                        "id": "context.normalized-path-collision",
                        "type": "normalized-path-collision",
                        "severity": "warning",
                        "paths": ["cafe\u0301/CLAUDE.md", "caf\u00e9/CLAUDE.md"],
                        "scope": "cafe\u0301",
                        "reason": "multiple context paths share one NFC-casefold identity",
                    },
                ],
                "conflict_analysis_complete": False,
                "truncated": True,
            },
        )
        self.assertEqual(
            warnings,
            [
                "context-ambiguous-path: AGENTS.md | agents.MD",
                "context-ambiguous-path: cafe\u0301/CLAUDE.md | caf\u00e9/CLAUDE.md",
            ],
        )
        self.assertEqual(result, reversed_result)
        self.assertEqual(warnings, reversed_warnings)
        serialized = json.dumps(result, ensure_ascii=False, sort_keys=True)
        self.assertNotIn(str(root), serialized)
        self.assertNotIn("skill_id", serialized)
        self.assertTrue(
            all(
                not Path(path).is_absolute() and "\\" not in path
                for conflict in result["conflicts"]
                for path in conflict["paths"]
            )
        )

    def test_duplicate_context_registration_is_deduplicated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            path = Path(temporary) / "source"
            path.write_text("must-not-appear\n", encoding="utf-8")
            warnings = []

            result = discover_context(
                [(path, "AGENTS.md"), (path, "AGENTS.md")],
                warnings,
            )

        self.assertEqual(
            result,
            expected_context(
                evidence=[
                    {
                        "path": "AGENTS.md",
                        "kind": "agent-instructions",
                        "scope": ".",
                        "scope_state": "root",
                    }
                ],
                conflicts=[
                    {
                        "id": "context.duplicate-source-registration",
                        "type": "duplicate-source-registration",
                        "severity": "warning",
                        "paths": ["AGENTS.md"],
                        "scope": ".",
                        "reason": "one context source is registered more than once",
                    }
                ],
            ),
        )
        self.assertEqual(warnings, ["context-duplicate-registration: AGENTS.md"])
        self.assertNotIn("must-not-appear", json.dumps(result, sort_keys=True))

    def test_context_is_not_discovered_in_excluded_runtime_directories(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-context-") as temporary:
            root = Path(temporary)
            for name in (".git", ".cso", "node_modules", "venv", "build", "cache"):
                directory = root / name
                directory.mkdir()
                (directory / "AGENTS.md").write_text("ignored\n", encoding="utf-8")

            result = analyze_project(root)

        self.assertEqual(result["context"], expected_context())


if __name__ == "__main__":
    unittest.main()

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from skill_orchestrator.cli import _human_analysis, main


ROOT = Path(__file__).resolve().parents[1]


class FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


class CharacterDeviceWithoutTerminal(io.StringIO):
    def __init__(self, descriptor: int) -> None:
        super().__init__()
        self._descriptor = descriptor

    def isatty(self) -> bool:
        return True

    def fileno(self) -> int:
        return self._descriptor


def run_cso(project: Path, *arguments: str) -> subprocess.CompletedProcess:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(ROOT)
    return subprocess.run(
        [sys.executable, "-m", "skill_orchestrator", *arguments],
        cwd=project,
        env=environment,
        stdin=subprocess.DEVNULL,
        check=False,
        capture_output=True,
        text=True,
    )


def run_cso_bytes(
    project: Path,
    *arguments: str,
    simulate_windows_newlines: bool = False,
) -> subprocess.CompletedProcess:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONPATH"] = str(ROOT)
    if simulate_windows_newlines:
        script = (
            "import io, sys; "
            "sys.stdout = io.TextIOWrapper("
            "sys.stdout.buffer, encoding='utf-8', newline='\\r\\n', write_through=True); "
            "from skill_orchestrator.cli import main; "
            "raise SystemExit(main(sys.argv[1:]))"
        )
        command = [sys.executable, "-c", script, *arguments]
    else:
        command = [sys.executable, "-m", "skill_orchestrator", *arguments]
    return subprocess.run(
        command,
        cwd=project,
        env=environment,
        stdin=subprocess.DEVNULL,
        check=False,
        capture_output=True,
        text=False,
    )


class PhaseTwoCliTests(unittest.TestCase):
    def test_human_output_does_not_claim_exhaustive_absence_when_explanation_incomplete(self) -> None:
        document = {
            "detected": [],
            "tests": [],
            "context": {
                "evidence": [],
                "conflicts": [],
                "conflict_analysis_complete": True,
                "truncated": False,
            },
            "project": {"files_analyzed": 0, "size": "small", "truncated": False},
            "recommended_profile": "small-project",
            "recommended_skills": [],
            "recommendations_complete": True,
            "recommendation_explanations": {
                "status": "incomplete",
                "limitations": [
                    {"reason_id": "recommendation.incomplete.explanation-limit"}
                ],
            },
            "warnings": [],
        }

        output = _human_analysis(document)

        self.assertIn("Recommendation explanation incomplete", output)
        self.assertNotIn("No matching skills are present", output)

    def test_json_stdout_is_utf8_without_bom(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-cli-") as temporary:
            project = Path(temporary)
            context = project / "café"
            context.mkdir()
            (context / "AGENTS.md").write_text("instructions\n", encoding="utf-8")

            result = run_cso_bytes(project, "analyze", "--json")

        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertFalse(result.stdout.startswith(b"\xef\xbb\xbf"))
        decoded = result.stdout.decode("utf-8")
        self.assertIn("café/AGENTS.md", decoded)
        self.assertEqual(json.loads(decoded)["context"]["evidence"][0]["path"], "café/AGENTS.md")

    def test_json_stdout_has_exactly_one_trailing_lf(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-cli-") as temporary:
            result = run_cso_bytes(Path(temporary), "analyze", "--json")

        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertTrue(result.stdout.endswith(b"\n"))
        self.assertFalse(result.stdout.endswith(b"\n\n"))

    def test_json_stdout_is_byte_deterministic(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-cli-") as temporary:
            project = Path(temporary)
            (project / "package.json").write_text("{}", encoding="utf-8")

            first = run_cso_bytes(
                project,
                "analyze",
                "--json",
                simulate_windows_newlines=True,
            )
            second = run_cso_bytes(
                project,
                "analyze",
                "--json",
                simulate_windows_newlines=True,
            )

        self.assertEqual(first.returncode, 0, first.stderr.decode("utf-8"))
        self.assertEqual(second.returncode, 0, second.stderr.decode("utf-8"))
        self.assertEqual(first.stdout, second.stdout)

    def test_json_stdout_uses_canonical_lf(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-cli-") as temporary:
            project = Path(temporary)
            (project / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

            result = run_cso_bytes(
                project,
                "analyze",
                "--json",
                simulate_windows_newlines=True,
            )

        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
        self.assertNotIn(b"\r\n", result.stdout)
        self.assertNotIn(b"\r", result.stdout)
        self.assertIn(b"\n", result.stdout)

    def test_json_stdout_supports_text_only_streams(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main(["profiles", "--json"])

        self.assertEqual(exit_code, 0)
        self.assertTrue(output.getvalue().endswith("\n"))
        self.assertIsInstance(json.loads(output.getvalue()), list)

    def test_analyze_json_is_machine_readable_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-cli-") as temporary:
            project = Path(temporary)
            (project / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

            first = run_cso(project, "analyze", "--json")
            second = run_cso(project, "analyze", "--json")

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(first.stdout, second.stdout)
        document = json.loads(first.stdout)
        self.assertEqual(document["recommended_profile"], "small-project")
        self.assertTrue(document["recommendations_complete"])
        self.assertEqual(
            set(document["recommendation_explanations"]),
            {
                "schema_version",
                "status",
                "registry",
                "selected",
                "excluded",
                "unmatched_signals",
                "limitations",
                "truncated",
            },
        )
        self.assertEqual(document["detected"][0]["technology"], "python")
        self.assertEqual(
            set(document["context"]),
            {
                "evidence",
                "scope_overlaps",
                "conflicts",
                "conflict_analysis_complete",
                "truncated",
            },
        )
        self.assertNotIn(str(project), first.stdout)

    def test_analyze_human_output_explains_context_evidence(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-cli-") as temporary:
            project = Path(temporary)
            (project / "AGENTS.md").write_text("instructions\n", encoding="utf-8")

            result = run_cso(project, "analyze")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Context evidence:", result.stdout)
        self.assertIn("AGENTS.md", result.stdout)
        self.assertIn("agent-instructions", result.stdout)
        self.assertIn("scope: .", result.stdout)
        self.assertIn("scope state: root", result.stdout)
        self.assertNotIn("bytes", result.stdout)
        self.assertNotIn(str(project), result.stdout)

    def test_incomplete_context_never_claims_conflict_analysis_is_complete(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-cli-") as temporary:
            project = Path(temporary)
            (project / "AGENTS.md").write_bytes(b"x" * 256_001)

            result = run_cso(project, "analyze")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Conflict analysis incomplete.", result.stdout)
        self.assertNotIn("No deterministic context conflicts detected.", result.stdout)
        self.assertIn("Recommendation analysis incomplete", result.stdout)
        self.assertNotIn("No matching skills are present", result.stdout)

    def test_init_yes_creates_only_expected_configuration(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-cli-") as temporary:
            project = Path(temporary)
            (project / "package.json").write_text("{}", encoding="utf-8")

            result = run_cso(project, "init", "--yes")

            config_path = project / ".cso" / "config.json"
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(config_path.is_file())
            document = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(document["profile"], "small-project")
            self.assertEqual(document["analysis"]["detected"], ["nodejs"])
            self.assertEqual(document["skills"], [])
            self.assertEqual(
                sorted(path.relative_to(project).as_posix() for path in project.rglob("*")),
                [".cso", ".cso/config.json", "package.json"],
            )

    def test_non_tty_init_fails_safely_without_yes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-cli-") as temporary:
            project = Path(temporary)

            result = run_cso(project, "init")

            self.assertEqual(result.returncode, 1)
            self.assertIn("cso init --yes", result.stderr)
            self.assertNotIn("Traceback", result.stderr)
            self.assertFalse((project / ".cso").exists())

        with tempfile.TemporaryDirectory(prefix="cso-cli-") as temporary:
            project = Path(temporary)
            output = io.StringIO()
            errors = io.StringIO()
            previous_stdin = sys.stdin
            try:
                with open(os.devnull, "r", encoding="utf-8") as null_input:
                    sys.stdin = CharacterDeviceWithoutTerminal(null_input.fileno())
                    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(errors):
                        exit_code = main(["init", "--project-root", str(project)])
            finally:
                sys.stdin = previous_stdin

            self.assertEqual(exit_code, 1)
            self.assertIn("cso init --yes", errors.getvalue())
            self.assertNotIn("Traceback", errors.getvalue())
            self.assertFalse((project / ".cso").exists())

    def test_existing_config_is_protected_and_force_replaces_only_config(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-cli-") as temporary:
            project = Path(temporary)
            first = run_cso(project, "init", "--yes")
            sentinel = project / ".cso" / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            (project / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

            protected = run_cso(project, "init", "--yes")
            forced = run_cso(project, "init", "--yes", "--force")

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(protected.returncode, 1)
            self.assertIn("--force", protected.stderr)
            self.assertEqual(forced.returncode, 0, forced.stderr)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")
            document = json.loads((project / ".cso" / "config.json").read_text(encoding="utf-8"))
            self.assertEqual(document["analysis"]["detected"], ["python"])

    def test_interactive_rejection_is_a_clean_cancellation(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-cli-") as temporary:
            project = Path(temporary)
            output = io.StringIO()
            previous_stdin = sys.stdin
            try:
                sys.stdin = FakeTty("n\n")
                with contextlib.redirect_stdout(output):
                    exit_code = main(["init", "--project-root", str(project)])
            finally:
                sys.stdin = previous_stdin

        self.assertEqual(exit_code, 0)
        self.assertIn("Initialization cancelled", output.getvalue())
        self.assertFalse((project / ".cso").exists())

    def test_doctor_exit_code_reflects_required_health_checks(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-cli-") as temporary:
            project = Path(temporary)
            healthy = run_cso(project, "doctor")
            config_directory = project / ".cso"
            config_directory.mkdir()
            (config_directory / "config.json").write_text("{bad", encoding="utf-8")
            unhealthy = run_cso(project, "doctor")

        self.assertEqual(healthy.returncode, 0, healthy.stderr)
        self.assertIn("Environment healthy", healthy.stdout)
        self.assertEqual(unhealthy.returncode, 1)
        self.assertIn("Configuration", unhealthy.stdout)
        self.assertIn("invalid JSON", unhealthy.stdout)

    def test_phase_two_help_and_usage_exit_codes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-cli-") as temporary:
            project = Path(temporary)
            for arguments in (("--help",), ("analyze", "--help"), ("init", "--help"), ("doctor", "--help")):
                with self.subTest(arguments=arguments):
                    result = run_cso(project, *arguments)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn("usage:", result.stdout)

            invalid = run_cso(project, "analyze", "--not-an-option")

        self.assertEqual(invalid.returncode, 2)


if __name__ == "__main__":
    unittest.main()

import shutil
import tempfile
import unittest
from pathlib import Path

from skill_orchestrator.doctor import run_doctor
from skill_orchestrator.engine import apply_profile


ROOT = Path(__file__).resolve().parents[1]


def copy_source_fixture(destination: Path) -> Path:
    for directory in ("profiles", "registry", "router", "schemas", "security"):
        shutil.copytree(ROOT / directory, destination / directory)
    for filename in (".gitattributes", "LICENSE"):
        shutil.copy2(ROOT / filename, destination / filename)
    return destination


class DoctorTests(unittest.TestCase):
    def test_missing_optional_configuration_is_healthy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-doctor-") as temporary:
            result = run_doctor(ROOT, Path(temporary))

        self.assertEqual(result["status"], "healthy")
        self.assertEqual(
            [check["name"] for check in result["checks"]],
            [
                "python_runtime",
                "registry",
                "registry_schema",
                "profile_schema",
                "config_schema",
                "checksums",
                "canonical_payload_integrity",
                "configuration",
                "platform",
            ],
        )
        self.assertTrue(all(check["status"] == "PASS" for check in result["checks"]))
        configuration = next(check for check in result["checks"] if check["name"] == "configuration")
        self.assertIn("optional", configuration["message"].casefold())

    def test_checksum_corruption_is_reported_with_detail(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-doctor-") as temporary:
            base = Path(temporary)
            source = copy_source_fixture(base / "source")
            project = base / "project"
            project.mkdir()
            skill = source / "router" / "codex-skill-orchestrator" / "SKILL.md"
            skill.write_text(skill.read_text(encoding="utf-8") + "\ntampered\n", encoding="utf-8")

            result = run_doctor(source, project)

        self.assertEqual(result["status"], "unhealthy")
        integrity = next(check for check in result["checks"] if check["name"] == "canonical_payload_integrity")
        self.assertEqual(integrity["status"], "FAIL")
        self.assertIn("checksum mismatch", integrity["message"])

    def test_malformed_registry_and_missing_config_schema_are_reported(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-doctor-") as temporary:
            base = Path(temporary)
            source = copy_source_fixture(base / "source")
            project = base / "project"
            project.mkdir()
            (source / "registry" / "skills.json").write_text("{", encoding="utf-8")
            (source / "schemas" / "cso-config.schema.json").unlink()

            result = run_doctor(source, project)

        failures = {check["name"]: check["message"] for check in result["checks"] if check["status"] == "FAIL"}
        self.assertIn("registry", failures)
        self.assertIn("config_schema", failures)
        self.assertIn("invalid JSON", failures["registry"])
        self.assertIn("cannot read", failures["config_schema"])

    def test_malformed_profile_document_fails_profile_check(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-doctor-") as temporary:
            base = Path(temporary)
            source = copy_source_fixture(base / "source")
            project = base / "project"
            project.mkdir()
            (source / "profiles" / "universal.json").write_text("{}\n", encoding="utf-8")

            result = run_doctor(source, project)

        profile_check = next(check for check in result["checks"] if check["name"] == "profile_schema")
        self.assertEqual(profile_check["status"], "FAIL")
        self.assertIn("profile universal.json", profile_check["message"])

    def test_malformed_present_config_is_unhealthy(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-doctor-") as temporary:
            project = Path(temporary)
            config_directory = project / ".cso"
            config_directory.mkdir()
            (config_directory / "config.json").write_text("{bad", encoding="utf-8")

            result = run_doctor(ROOT, project)

        configuration = next(check for check in result["checks"] if check["name"] == "configuration")
        self.assertEqual(result["status"], "unhealthy")
        self.assertEqual(configuration["status"], "FAIL")
        self.assertIn("invalid JSON", configuration["message"])

    def test_installed_application_can_find_config_schema(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-doctor-") as temporary:
            base = Path(temporary)
            install_root = base / "state"
            skills_dir = base / "skills"
            project = base / "project"
            project.mkdir()
            apply_profile("universal", install_root, skills_dir, source_root=ROOT)

            result = run_doctor(install_root / "app", project)

        config_schema = next(check for check in result["checks"] if check["name"] == "config_schema")
        self.assertEqual(config_schema["status"], "PASS")
        self.assertEqual(result["status"], "healthy")


if __name__ == "__main__":
    unittest.main()

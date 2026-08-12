import json
import shutil
import tempfile
import unittest
from pathlib import Path

from skill_orchestrator.errors import IntegrityError, SecurityError
from skill_orchestrator.validation import validate_registry


ROOT = Path(__file__).resolve().parents[1]


def copy_registry_fixture(destination: Path) -> None:
    for directory in ("registry", "security", "router"):
        shutil.copytree(ROOT / directory, destination / directory)
    shutil.copy2(ROOT / "LICENSE", destination / "LICENSE")


class RegistryTests(unittest.TestCase):
    def test_first_party_registry_is_valid(self) -> None:
        registry = validate_registry(ROOT)
        entry = registry["codex-skill-orchestrator"]
        self.assertFalse(entry["provenance"]["third_party"])
        self.assertEqual(entry["license"]["spdx"], "MIT")
        self.assertEqual(entry["source"]["type"], "bundled")

    def test_tampered_bundled_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-registry-") as temporary:
            fixture = Path(temporary)
            copy_registry_fixture(fixture)
            skill = fixture / "router" / "codex-skill-orchestrator" / "SKILL.md"
            skill.write_text(skill.read_text(encoding="utf-8") + "\nmodified\n", encoding="utf-8")
            with self.assertRaises(IntegrityError):
                validate_registry(fixture)

    def test_network_source_is_rejected_in_phase_one(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-registry-") as temporary:
            fixture = Path(temporary)
            copy_registry_fixture(fixture)
            path = fixture / "registry" / "skills.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            source = document["skills"][0]["source"]
            source.update(
                {
                    "type": "git",
                    "repository": "https://example.invalid/project.git",
                    "revision": "0" * 40,
                }
            )
            document["skills"][0]["provenance"]["third_party"] = True
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(SecurityError):
                validate_registry(fixture)


if __name__ == "__main__":
    unittest.main()

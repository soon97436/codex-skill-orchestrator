import json
import shutil
import subprocess
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
    def test_checksummed_payloads_are_checked_out_as_lf(self) -> None:
        registry = json.loads((ROOT / "registry" / "skills.json").read_text(encoding="utf-8"))
        paths = [
            f"{entry['source']['path'].rstrip('/')}/{file_entry['path']}"
            for entry in registry["skills"]
            for file_entry in entry["files"]
        ]
        result = subprocess.run(
            ["git", "-C", str(ROOT), "check-attr", "eol", "--", *paths],
            check=True,
            capture_output=True,
            text=True,
        )
        attributes = {
            line.split(": ", 2)[0]: line.split(": ", 2)[2]
            for line in result.stdout.splitlines()
        }
        self.assertEqual(attributes, {path: "lf" for path in paths})

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

    def test_tampered_registry_hash_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-registry-") as temporary:
            fixture = Path(temporary)
            copy_registry_fixture(fixture)
            path = fixture / "registry" / "skills.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            document["skills"][0]["files"][0]["sha256"] = "0" * 64
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(IntegrityError):
                validate_registry(fixture)

    def test_registry_and_security_checksum_mismatch_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-registry-") as temporary:
            fixture = Path(temporary)
            copy_registry_fixture(fixture)
            path = fixture / "security" / "checksums.json"
            document = json.loads(path.read_text(encoding="utf-8"))
            bundle = document["bundles"]["codex-skill-orchestrator@0.1.0"]
            bundle["SKILL.md"] = "0" * 64
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(IntegrityError):
                validate_registry(fixture)

    def test_autocrlf_checkout_preserves_canonical_payload_bytes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-eol-") as temporary:
            base = Path(temporary)
            source = base / "source"
            checkout = base / "checkout"
            shutil.copytree(
                ROOT,
                source,
                ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
            )
            commands = (
                ["git", "init", "-q", str(source)],
                ["git", "-C", str(source), "config", "user.name", "EOL Test"],
                ["git", "-C", str(source), "config", "user.email", "eol-test"],
                ["git", "-C", str(source), "add", "-A"],
                ["git", "-C", str(source), "commit", "-qm", "fixture"],
                [
                    "git",
                    "-c",
                    "core.autocrlf=true",
                    "-c",
                    "core.eol=crlf",
                    "clone",
                    "-q",
                    "--no-local",
                    str(source),
                    str(checkout),
                ],
            )
            for command in commands:
                subprocess.run(command, check=True)

            registry = validate_registry(checkout)

            entry = registry["codex-skill-orchestrator"]
            source_root = checkout / entry["source"]["path"]
            for file_entry in entry["files"]:
                with self.subTest(path=file_entry["path"]):
                    self.assertNotIn(b"\r\n", (source_root / file_entry["path"]).read_bytes())

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

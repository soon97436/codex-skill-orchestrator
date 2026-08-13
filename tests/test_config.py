import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skill_orchestrator.config import build_config, validate_config_document, write_config
from skill_orchestrator.errors import OperationError, SecurityError, ValidationError


ROOT = Path(__file__).resolve().parents[1]


class ConfigTests(unittest.TestCase):
    def test_config_is_canonical_lf_and_contains_no_machine_identity(self) -> None:
        analysis = {
            "detected": [
                {"technology": "python", "evidence": ["pyproject.toml"]},
                {"technology": "docker", "evidence": ["Dockerfile"]},
            ]
        }
        document = build_config(
            analysis,
            profile="small-project",
            recommendations=[{"skill": "registered-skill", "score": 90, "reasons": ["reason"]}],
        )

        with tempfile.TemporaryDirectory(prefix="cso-config-") as temporary:
            root = Path(temporary)
            path = write_config(root, document)
            raw = path.read_bytes()

        self.assertNotIn(b"\r\n", raw)
        self.assertTrue(raw.endswith(b"\n"))
        self.assertEqual(raw.decode("utf-8"), json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        self.assertEqual(
            document,
            {
                "analysis": {"detected": ["docker", "python"]},
                "profile": "small-project",
                "skills": ["registered-skill"],
                "version": 1,
            },
        )
        serialized = raw.decode("utf-8").casefold()
        for forbidden in ("timestamp", "hostname", "username", str(Path.home()).casefold()):
            self.assertNotIn(forbidden, serialized)

    def test_schema_and_runtime_validation_fail_closed(self) -> None:
        schema = json.loads((ROOT / "schemas" / "cso-config.schema.json").read_text(encoding="utf-8"))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["required"]), {"version", "profile", "skills", "analysis"})
        document = {
            "version": 1,
            "profile": "small-project",
            "skills": ["registered-skill"],
            "analysis": {"detected": ["python"]},
        }
        validate_config_document(
            document,
            profiles={"small-project"},
            registry_skills={"registered-skill"},
        )

        invalid_documents = [
            dict(document, extra=True),
            dict(document, profile="unknown"),
            dict(document, skills=["not-registered"]),
            dict(document, skills=["registered-skill", "registered-skill"]),
            dict(document, analysis={"detected": ["Python!"]}),
        ]
        for invalid in invalid_documents:
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValidationError):
                    validate_config_document(
                        invalid,
                        profiles={"small-project"},
                        registry_skills={"registered-skill"},
                    )

    def test_existing_config_requires_force_and_preserves_other_files(self) -> None:
        original = {
            "version": 1,
            "profile": "small-project",
            "skills": [],
            "analysis": {"detected": []},
        }
        replacement = dict(original, profile="universal")
        with tempfile.TemporaryDirectory(prefix="cso-config-") as temporary:
            root = Path(temporary)
            path = write_config(root, original)
            sentinel = path.parent / "team-notes.txt"
            sentinel.write_text("preserve", encoding="utf-8")

            with self.assertRaises(OperationError):
                write_config(root, replacement)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)

            write_config(root, replacement, force=True)

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), replacement)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(list(path.parent.glob(".config.json.tmp-*")), [])

    def test_cso_link_or_reparse_point_is_rejected(self) -> None:
        document = {
            "version": 1,
            "profile": "small-project",
            "skills": [],
            "analysis": {"detected": []},
        }
        with tempfile.TemporaryDirectory(prefix="cso-config-") as temporary:
            base = Path(temporary)
            root = base / "project"
            root.mkdir()
            outside = base / "outside"
            outside.mkdir()
            try:
                (root / ".cso").symlink_to(outside, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory links are unavailable")

            with self.assertRaises(SecurityError):
                write_config(root, document)

        self.assertFalse((outside / "config.json").exists())

    def test_windows_style_reparse_point_is_fail_closed(self) -> None:
        document = {
            "version": 1,
            "profile": "small-project",
            "skills": [],
            "analysis": {"detected": []},
        }
        with tempfile.TemporaryDirectory(prefix="cso-config-") as temporary:
            root = Path(temporary)
            (root / ".cso").mkdir()
            with mock.patch(
                "skill_orchestrator.config.is_reparse_point",
                side_effect=lambda path: path.name == ".cso",
            ):
                with self.assertRaises(SecurityError):
                    write_config(root, document)

    def test_failed_atomic_replace_keeps_original_and_cleans_temporary_file(self) -> None:
        original = {
            "version": 1,
            "profile": "small-project",
            "skills": [],
            "analysis": {"detected": []},
        }
        replacement = dict(original, profile="universal")
        with tempfile.TemporaryDirectory(prefix="cso-config-") as temporary:
            root = Path(temporary)
            path = write_config(root, original)

            with mock.patch("skill_orchestrator.config.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OperationError, "cannot write .cso/config.json atomically"):
                    write_config(root, replacement, force=True)

            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)
            self.assertEqual(list(path.parent.glob(".config.json.tmp-*")), [])


if __name__ == "__main__":
    unittest.main()

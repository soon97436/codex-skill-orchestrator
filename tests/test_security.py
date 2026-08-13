import os
import sys
import tempfile
import unittest
from pathlib import Path

from skill_orchestrator.engine import apply_profile
from skill_orchestrator.errors import SecurityError
from skill_orchestrator.validation import safe_join, validate_relative_path


ROOT = Path(__file__).resolve().parents[1]


class SecurityTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "macOS system alias test")
    def test_macos_system_temp_alias_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso mac temp ") as temporary:
            base = Path(temporary)
            result = apply_profile(
                "universal",
                base / "state",
                base / "skills",
                source_root=ROOT,
                dry_run=True,
            )
            self.assertTrue(result["dry_run"])
            self.assertFalse(result["changed"])

    def test_path_traversal_and_drive_paths_are_rejected(self) -> None:
        for unsafe in ("../escape", "/absolute", "folder/../escape", "drive:C"):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(SecurityError):
                    validate_relative_path(unsafe, "fixture")

    def test_safe_join_stays_inside_root(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso path ") as temporary:
            root = Path(temporary)
            self.assertEqual(safe_join(root, "a/b", "fixture"), (root / "a" / "b").resolve())

    def test_destination_inside_source_is_rejected_without_writes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso outside ") as temporary:
            external_skills = Path(temporary) / "skills"
            unsafe_install = ROOT / "unsafe-install-target"
            self.assertFalse(unsafe_install.exists())
            with self.assertRaises(SecurityError):
                apply_profile(
                    "universal",
                    unsafe_install,
                    external_skills,
                    source_root=ROOT,
                    dry_run=True,
                )
            self.assertFalse(unsafe_install.exists())

    def test_symlink_destination_is_rejected_when_supported(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso symlink ") as temporary:
            base = Path(temporary)
            real = base / "real"
            real.mkdir()
            link = base / "skills-link"
            try:
                os.symlink(real, link, target_is_directory=True)
            except (OSError, NotImplementedError):
                self.skipTest("directory symlinks are not available")
            with self.assertRaises(SecurityError):
                apply_profile(
                    "universal",
                    base / "state",
                    link,
                    source_root=ROOT,
                    dry_run=True,
                )


if __name__ == "__main__":
    unittest.main()

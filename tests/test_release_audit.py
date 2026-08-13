import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def copy_release_fixture(destination: Path) -> None:
    shutil.copytree(
        ROOT,
        destination,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
    )
    subprocess.run(["git", "init", "-q", str(destination)], check=True)
    subprocess.run(["git", "-C", str(destination), "config", "user.name", "Release Audit Test"], check=True)
    subprocess.run(
        ["git", "-C", str(destination), "config", "user.email", "release-audit-test"],
        check=True,
    )
    subprocess.run(["git", "-C", str(destination), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(destination), "commit", "-qm", "fixture"], check=True)


def run_release_audit(root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "scripts/release_audit.py", "--json"],
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )


class ReleaseAuditTests(unittest.TestCase):
    def test_valid_repository_passes_release_audit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-release-audit-") as temporary:
            fixture = Path(temporary) / "repo"
            copy_release_fixture(fixture)

            result = run_release_audit(fixture)

            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(json.loads(result.stdout)["status"], "clean")

    def test_broken_checksum_index_fails_release_audit(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-release-audit-") as temporary:
            fixture = Path(temporary) / "repo"
            copy_release_fixture(fixture)
            checksum_path = fixture / "security" / "checksums.json"
            document = json.loads(checksum_path.read_text(encoding="utf-8"))
            bundle = document["bundles"]["codex-skill-orchestrator@0.1.0"]
            bundle["SKILL.md"] = "0" * 64
            checksum_path.write_text(json.dumps(document), encoding="utf-8")

            result = run_release_audit(fixture)

            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertEqual(json.loads(result.stdout)["status"], "findings")


if __name__ == "__main__":
    unittest.main()

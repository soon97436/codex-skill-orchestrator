import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skill_orchestrator import engine
from skill_orchestrator.engine import apply_profile, audit, rollback
from skill_orchestrator.errors import IntegrityError, OperationError
from skill_orchestrator.validation import canonical_json


ROOT = Path(__file__).resolve().parents[1]


def snapshot(root: Path):
    return sorted(path.relative_to(root).as_posix() for path in root.rglob("*"))


def detailed_snapshot(root: Path):
    result = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        stat_result = path.stat()
        content = path.read_bytes() if path.is_file() else None
        result.append((relative, path.is_dir(), stat_result.st_mtime_ns, content))
    return result


class EngineTests(unittest.TestCase):
    def test_python39_write_text_compatibility_preserves_canonical_json(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso python39 write text ") as temporary:
            base = Path(temporary)
            install_root = base / "state"
            skills_dir = base / "skills"

            real_write_text = Path.write_text

            def python39_write_text(self, data, encoding=None, errors=None):
                return real_write_text(self, data, encoding=encoding, errors=errors)

            with mock.patch.object(Path, "write_text", python39_write_text):
                apply_profile("universal", install_root, skills_dir, source_root=ROOT)

            json_paths = [
                skills_dir / "codex-skill-orchestrator" / "references" / "active-profile.json",
                install_root / "state" / "state.json",
            ]
            for path in json_paths:
                actual_bytes = path.read_bytes()
                self.assertNotIn(b"\xef\xbb\xbf", actual_bytes)
                self.assertNotIn(b"\r", actual_bytes)
                self.assertTrue(actual_bytes.endswith(b"\n"))
                parsed = json.loads(actual_bytes.decode("utf-8"))
                self.assertEqual(actual_bytes, canonical_json(parsed).encode("utf-8"))

    def test_dry_run_has_zero_persistent_writes(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso dry run ") as temporary:
            base = Path(temporary)
            before = snapshot(base)
            result = apply_profile(
                "universal",
                base / "state",
                base / "skills",
                source_root=ROOT,
                dry_run=True,
            )
            after = snapshot(base)
            self.assertEqual(before, after)
            self.assertTrue(result["dry_run"])
            self.assertFalse(result["changed"])

    def test_install_activate_audit_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso install ünicode ") as temporary:
            base = Path(temporary)
            install_root = base / "managed state"
            skills_dir = base / "agent skills"
            skills_dir.mkdir()
            sentinel = skills_dir / "user-owned.txt"
            sentinel.write_text("preserve", encoding="utf-8")

            installed = apply_profile(
                "universal",
                install_root,
                skills_dir,
                source_root=ROOT,
            )
            self.assertTrue(installed["changed"])
            self.assertTrue((install_root / "app" / "bin" / "cso.py").is_file())
            self.assertTrue((install_root / "app" / "bin" / "python-discovery.ps1").is_file())
            self.assertTrue((install_root / "app" / "schemas" / "cso-config.schema.json").is_file())
            active_path = skills_dir / "codex-skill-orchestrator" / "references" / "active-profile.json"
            active = json.loads(active_path.read_text(encoding="utf-8"))
            self.assertEqual(active["profile"]["id"], "universal")
            self.assertEqual(audit(install_root, skills_dir, source_root=ROOT)["status"], "clean")

            environment = dict(os.environ)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(install_root / "app" / "bin" / "cso.py"),
                    "route",
                    "--profile",
                    "security",
                    "--task",
                    "audit a vulnerability",
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["profile"], "security")

            installed_launcher = install_root / "app" / "bin" / "cso.py"
            plan = subprocess.run(
                [
                    sys.executable,
                    str(installed_launcher),
                    "plan",
                    "--profile",
                    "universal",
                    "--install-root",
                    str(install_root),
                    "--skills-dir",
                    str(skills_dir),
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(plan.returncode, 0, plan.stderr)

            activated = subprocess.run(
                [
                    sys.executable,
                    str(installed_launcher),
                    "activate",
                    "--profile",
                    "economy",
                    "--install-root",
                    str(install_root),
                    "--skills-dir",
                    str(skills_dir),
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(activated.returncode, 0, activated.stderr)
            self.assertTrue(json.loads(activated.stdout)["changed"])
            active = json.loads(active_path.read_text(encoding="utf-8"))
            self.assertEqual(active["profile"]["id"], "economy")

            installed_audit = subprocess.run(
                [
                    sys.executable,
                    str(installed_launcher),
                    "audit",
                    "--install-root",
                    str(install_root),
                    "--skills-dir",
                    str(skills_dir),
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(installed_audit.returncode, 0, installed_audit.stderr)
            self.assertEqual(json.loads(installed_audit.stdout)["status"], "clean")

            rolled_back = rollback(install_root, skills_dir, source_root=ROOT)
            self.assertEqual(rolled_back["command"], "rollback")
            active = json.loads(active_path.read_text(encoding="utf-8"))
            self.assertEqual(active["profile"]["id"], "universal")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve")
            self.assertEqual(audit(install_root, skills_dir, source_root=ROOT)["status"], "clean")

            manifest_text = "\n".join(
                path.read_text(encoding="utf-8")
                for path in (install_root / "state" / "transactions").glob("*/manifest.json")
            )
            self.assertNotIn(str(base), manifest_text)

    def test_first_install_rollback_removes_only_managed_components(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso rollback ") as temporary:
            base = Path(temporary)
            install_root = base / "state"
            skills_dir = base / "skills"
            skills_dir.mkdir()
            sentinel = skills_dir / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")
            apply_profile("universal", install_root, skills_dir, source_root=ROOT)
            rollback(install_root, skills_dir, source_root=ROOT)
            self.assertFalse((install_root / "app").exists())
            self.assertFalse((skills_dir / "codex-skill-orchestrator").exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_tamper_is_reported_and_blocks_rollback(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso tamper ") as temporary:
            base = Path(temporary)
            install_root = base / "state"
            skills_dir = base / "skills"
            apply_profile("universal", install_root, skills_dir, source_root=ROOT)
            skill = skills_dir / "codex-skill-orchestrator" / "SKILL.md"
            skill.write_text(skill.read_text(encoding="utf-8") + "\nuser change\n", encoding="utf-8")
            result = audit(install_root, skills_dir, source_root=ROOT)
            self.assertEqual(result["status"], "findings")
            self.assertIn("checksum-mismatch", {finding["code"] for finding in result["findings"]})
            with self.assertRaises(IntegrityError):
                rollback(install_root, skills_dir, source_root=ROOT)

    def test_activate_and_rollback_dry_runs_do_not_write(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso dry run installed ") as temporary:
            base = Path(temporary)
            install_root = base / "state"
            skills_dir = base / "skills"
            apply_profile("universal", install_root, skills_dir, source_root=ROOT)
            before_activate = detailed_snapshot(base)
            activated = apply_profile(
                "economy",
                install_root,
                skills_dir,
                source_root=ROOT,
                include_app=False,
                dry_run=True,
            )
            self.assertTrue(activated["dry_run"])
            self.assertEqual(before_activate, detailed_snapshot(base))

            before_rollback = detailed_snapshot(base)
            rolled_back = rollback(install_root, skills_dir, source_root=ROOT, dry_run=True)
            self.assertTrue(rolled_back["dry_run"])
            self.assertEqual(before_rollback, detailed_snapshot(base))

    def test_quarantine_cleanup_failure_keeps_state_consistent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso quarantine ") as temporary:
            base = Path(temporary)
            install_root = base / "state"
            skills_dir = base / "skills"
            apply_profile("universal", install_root, skills_dir, source_root=ROOT)
            real_rmtree = engine.shutil.rmtree

            def fail_quarantine_cleanup(path, *args, **kwargs):
                if ".cso-old-" in Path(path).name:
                    raise PermissionError("simulated antivirus lock")
                return real_rmtree(path, *args, **kwargs)

            with mock.patch("skill_orchestrator.engine.shutil.rmtree", side_effect=fail_quarantine_cleanup):
                result = apply_profile(
                    "economy",
                    install_root,
                    skills_dir,
                    source_root=ROOT,
                    include_app=False,
                )
            self.assertTrue(result["changed"])
            self.assertEqual(audit(install_root, skills_dir, source_root=ROOT)["status"], "clean")
            active = json.loads(
                (skills_dir / "codex-skill-orchestrator" / "references" / "active-profile.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(active["profile"]["id"], "economy")

    def test_partial_rollback_failure_is_compensated(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso rollback compensation ") as temporary:
            base = Path(temporary)
            install_root = base / "state"
            skills_dir = base / "skills"
            apply_profile("universal", install_root, skills_dir, source_root=ROOT)
            original_restore = engine._restore_component
            calls = 0

            def fail_second_restore(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OperationError("simulated second-component failure")
                return original_restore(*args, **kwargs)

            with mock.patch("skill_orchestrator.engine._restore_component", side_effect=fail_second_restore):
                with self.assertRaises(OperationError):
                    rollback(install_root, skills_dir, source_root=ROOT)

            self.assertTrue((install_root / "app").is_dir())
            self.assertTrue((skills_dir / "codex-skill-orchestrator").is_dir())
            self.assertEqual(audit(install_root, skills_dir, source_root=ROOT)["status"], "clean")

    def test_orphaned_lock_file_does_not_block(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso stale lock ") as temporary:
            base = Path(temporary)
            install_root = base / "state"
            skills_dir = base / "skills"
            apply_profile("universal", install_root, skills_dir, source_root=ROOT)
            lock = install_root / "state" / "mutation.lock"
            lock.write_text('{"pid":2147483647,"token":"stale"}', encoding="utf-8")
            result = apply_profile(
                "economy",
                install_root,
                skills_dir,
                source_root=ROOT,
                include_app=False,
            )
            self.assertTrue(result["changed"])
            self.assertTrue(lock.is_file())
            lock_metadata = json.loads(lock.read_text(encoding="ascii"))
            self.assertEqual(lock_metadata["pid"], os.getpid())
            self.assertEqual(audit(install_root, skills_dir, source_root=ROOT)["status"], "clean")

    def test_mutation_lock_is_exclusive_and_reusable(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso lock exclusivity ") as temporary:
            install_root = Path(temporary) / "state"
            with engine.MutationLock(install_root):
                with self.assertRaises(OperationError):
                    with engine.MutationLock(install_root):
                        self.fail("a second mutation lock must not be acquired")
            with engine.MutationLock(install_root):
                pass

    def test_interrupted_transaction_is_reported_and_recovered(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso crash recovery ") as temporary:
            base = Path(temporary)
            install_root = base / "state"
            skills_dir = base / "skills"
            apply_profile("universal", install_root, skills_dir, source_root=ROOT)
            interrupted = apply_profile(
                "economy",
                install_root,
                skills_dir,
                source_root=ROOT,
                include_app=False,
            )
            transaction_id = interrupted["transaction"]
            manifest_path = install_root / "state" / "transactions" / transaction_id / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["status"] = "preparing"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            before_recovery = audit(install_root, skills_dir, source_root=ROOT)
            self.assertIn(
                "interrupted-transaction",
                {finding["code"] for finding in before_recovery["findings"]},
            )

            recovered_then_applied = apply_profile(
                "research",
                install_root,
                skills_dir,
                source_root=ROOT,
                include_app=False,
            )
            self.assertTrue(recovered_then_applied["changed"])
            recovered_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(recovered_manifest["status"], "recovered")
            active_path = skills_dir / "codex-skill-orchestrator" / "references" / "active-profile.json"
            active = json.loads(active_path.read_text(encoding="utf-8"))
            self.assertEqual(active["profile"]["id"], "research")
            self.assertEqual(audit(install_root, skills_dir, source_root=ROOT)["status"], "clean")

    def test_hard_exit_during_rollback_is_resumed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso hard rollback ") as temporary:
            base = Path(temporary)
            install_root = base / "state"
            skills_dir = base / "skills"
            installed = apply_profile("universal", install_root, skills_dir, source_root=ROOT)
            transaction_id = installed["transaction"]
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(ROOT)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            script = "\n".join(
                (
                    "import os",
                    "from pathlib import Path",
                    "from skill_orchestrator import engine",
                    "original = engine._restore_component",
                    "def crash_after_first(*args, **kwargs):",
                    "    original(*args, **kwargs)",
                    "    os._exit(91)",
                    "engine._restore_component = crash_after_first",
                    f"engine.rollback(Path({str(install_root)!r}), Path({str(skills_dir)!r}), source_root=Path({str(ROOT)!r}))",
                )
            )
            crashed = subprocess.run(
                [sys.executable, "-c", script],
                check=False,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(crashed.returncode, 91, crashed.stderr)
            interrupted_audit = audit(install_root, skills_dir, source_root=ROOT)
            self.assertIn(
                "interrupted-rollback",
                {finding["code"] for finding in interrupted_audit["findings"]},
            )

            resumed = rollback(install_root, skills_dir, source_root=ROOT)
            self.assertTrue(resumed["recovered"])
            self.assertEqual(resumed["transaction"], transaction_id)
            self.assertFalse((install_root / "app").exists())
            self.assertFalse((skills_dir / "codex-skill-orchestrator").exists())
            manifest_path = install_root / "state" / "transactions" / transaction_id / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "rolled_back")
            self.assertEqual(manifest["rollback_attempt"]["status"], "recovered")
            self.assertEqual(audit(install_root, skills_dir, source_root=ROOT)["status"], "clean")

    def test_repeated_hard_exit_during_restore_stage_copy_is_resumed(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso partial restore ") as temporary:
            base = Path(temporary)
            install_root = base / "state"
            skills_dir = base / "skills"
            apply_profile("universal", install_root, skills_dir, source_root=ROOT)
            activated = apply_profile(
                "economy",
                install_root,
                skills_dir,
                source_root=ROOT,
                include_app=False,
            )
            transaction_id = activated["transaction"]
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(ROOT)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            script = "\n".join(
                (
                    "import os, shutil",
                    "from pathlib import Path",
                    "from skill_orchestrator import engine",
                    "original = engine._copy_exact_tree",
                    "def crash_mid_restore(source, destination):",
                    "    if '.cso-restore-' not in destination.name:",
                    "        return original(source, destination)",
                    "    destination.mkdir(parents=True, exist_ok=False)",
                    "    first = next(engine._iter_payload_files(source, skip_python_cache=False))",
                    "    relative = first.relative_to(source)",
                    "    output = destination / relative",
                    "    output.parent.mkdir(parents=True, exist_ok=True)",
                    "    shutil.copy2(first, output)",
                    "    os._exit(92)",
                    "engine._copy_exact_tree = crash_mid_restore",
                    f"engine.rollback(Path({str(install_root)!r}), Path({str(skills_dir)!r}), source_root=Path({str(ROOT)!r}))",
                )
            )
            for _ in range(2):
                crashed = subprocess.run(
                    [sys.executable, "-c", script],
                    check=False,
                    capture_output=True,
                    text=True,
                    env=environment,
                )
                self.assertEqual(crashed.returncode, 92, crashed.stderr)
                interrupted = audit(install_root, skills_dir, source_root=ROOT)
                self.assertIn(
                    "interrupted-rollback",
                    {finding["code"] for finding in interrupted["findings"]},
                )

            resumed = rollback(install_root, skills_dir, source_root=ROOT)
            self.assertTrue(resumed["recovered"])
            self.assertEqual(resumed["transaction"], transaction_id)
            active_path = skills_dir / "codex-skill-orchestrator" / "references" / "active-profile.json"
            active = json.loads(active_path.read_text(encoding="utf-8"))
            self.assertEqual(active["profile"]["id"], "universal")
            self.assertEqual(audit(install_root, skills_dir, source_root=ROOT)["status"], "clean")

    def test_reinstall_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso idempotent ") as temporary:
            base = Path(temporary)
            install_root = base / "state"
            skills_dir = base / "skills"
            apply_profile("universal", install_root, skills_dir, source_root=ROOT)
            second = apply_profile("universal", install_root, skills_dir, source_root=ROOT)
            self.assertFalse(second["changed"])
            self.assertIsNone(second["transaction"])


if __name__ == "__main__":
    unittest.main()

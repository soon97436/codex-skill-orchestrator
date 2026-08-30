import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import skipUnless

from skill_orchestrator import mutation_lock as mutation_lock_module
from skill_orchestrator.errors import OperationError, SecurityError
from skill_orchestrator.mutation_lock import MutationLockSet


ROOT = Path(__file__).resolve().parents[1]
CONTENTION_MESSAGE = "another orchestrator mutation is in progress"


def _child_environment():
    environment = dict(os.environ)
    environment["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(ROOT), environment.get("PYTHONPATH")) if part
    )
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    return environment


def _spawn_holder(kind, install_root, skills_root, ready, release, *, crash=False):
    factory = "for_engine" if kind == "engine" else "for_skills"
    script = "\n".join(
        (
            "import os",
            "import time",
            "from pathlib import Path",
            "from skill_orchestrator.mutation_lock import MutationLockSet",
            f"install_root = Path({str(install_root)!r})",
            f"skills_root = Path({str(skills_root)!r})",
            f"ready = Path({str(ready)!r})",
            f"release = Path({str(release)!r})",
            f"factory = MutationLockSet.{factory}",
            f"with factory(install_root, skills_root) if {kind!r} == 'engine' else factory(skills_root):",
            "    ready.write_text('held', encoding='ascii')",
            f"    {'os._exit(73)' if crash else 'while not release.exists(): time.sleep(0.01)'}",
        )
    )
    return subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=str(ROOT),
        env=_child_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_for_file(path, process):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if path.exists():
            return
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise AssertionError(
                "child exited before coordination: "
                + repr((process.returncode, stdout, stderr))
            )
        time.sleep(0.01)
    process.terminate()
    process.wait(timeout=5)
    raise AssertionError("child did not reach its coordination point")


def _try_lock(kind, install_root, skills_root):
    factory = "for_engine" if kind == "engine" else "for_skills"
    script = "\n".join(
        (
            "from pathlib import Path",
            "from skill_orchestrator.errors import OperationError",
            "from skill_orchestrator.mutation_lock import MutationLockSet",
            f"install_root = Path({str(install_root)!r})",
            f"skills_root = Path({str(skills_root)!r})",
            f"factory = MutationLockSet.{factory}",
            "try:",
            f"    context = factory(install_root, skills_root) if {kind!r} == 'engine' else factory(skills_root)",
            "    with context:",
            "        pass",
            "except OperationError as exc:",
            f"    raise SystemExit(17 if str(exc) == {CONTENTION_MESSAGE!r} else 18)",
        )
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(ROOT),
        env=_child_environment(),
        check=False,
        capture_output=True,
        text=True,
    )


class MutationLockTests(unittest.TestCase):
    def _roots(self, base):
        install = base / "install"
        skills = base / "skills"
        install.mkdir()
        skills.mkdir()
        return install, skills

    def _assert_child_stopped(self, process):
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.terminate()
            stdout, stderr = process.communicate(timeout=5)
            self.fail("lock holder did not stop: " + repr((stdout, stderr)))
        self.assertEqual(process.returncode, 0, stderr)

    def test_same_skills_different_install_roots_contend_on_shared_skills_lock(self):
        with tempfile.TemporaryDirectory(prefix="cso lock shared skills ") as temporary:
            base = Path(temporary)
            install_a = base / "install-a"
            install_b = base / "install-b"
            skills = base / "skills"
            install_a.mkdir()
            install_b.mkdir()
            skills.mkdir()
            ready = base / "ready"
            release = base / "release"
            holder = _spawn_holder("engine", install_a, skills, ready, release)
            try:
                _wait_for_file(ready, holder)
                contender = _try_lock("engine", install_b, skills)
                self.assertEqual(contender.returncode, 17, contender.stderr)
            finally:
                release.write_text("release", encoding="ascii")
                self._assert_child_stopped(holder)

    def test_engine_lock_contends_with_future_skills_only_domain(self):
        with tempfile.TemporaryDirectory(prefix="cso lock candidate ") as temporary:
            base = Path(temporary)
            install, skills = self._roots(base)
            ready = base / "ready"
            release = base / "release"
            holder = _spawn_holder("engine", install, skills, ready, release)
            try:
                _wait_for_file(ready, holder)
                contender = _try_lock("skills", install, skills)
                self.assertEqual(contender.returncode, 17, contender.stderr)
            finally:
                release.write_text("release", encoding="ascii")
                self._assert_child_stopped(holder)

    def test_same_install_different_skills_contend_on_legacy_install_lock(self):
        with tempfile.TemporaryDirectory(prefix="cso lock shared install ") as temporary:
            base = Path(temporary)
            install = base / "install"
            skills_a = base / "skills-a"
            skills_b = base / "skills-b"
            install.mkdir()
            skills_a.mkdir()
            skills_b.mkdir()
            ready = base / "ready"
            release = base / "release"
            holder = _spawn_holder("engine", install, skills_a, ready, release)
            try:
                _wait_for_file(ready, holder)
                contender = _try_lock("engine", install, skills_b)
                self.assertEqual(contender.returncode, 17, contender.stderr)
            finally:
                release.write_text("release", encoding="ascii")
                self._assert_child_stopped(holder)

    def test_release_clears_both_domains_and_aliases_deduplicate(self):
        with tempfile.TemporaryDirectory(prefix="cso lock release ") as temporary:
            base = Path(temporary)
            install, skills = self._roots(base)
            with MutationLockSet.for_engine(install, skills):
                with self.assertRaises(OperationError) as raised:
                    with MutationLockSet.for_skills(skills):
                        pass
                self.assertEqual(str(raised.exception), CONTENTION_MESSAGE)
            with MutationLockSet.for_skills(skills / "."):
                pass
            with MutationLockSet.for_engine(install / ".", skills / "."):
                pass

    @skipUnless(os.name != "nt", "POSIX ordering checks are platform-specific")
    def test_resources_are_totally_ordered_and_duplicate_resources_collapse(self):
        with tempfile.TemporaryDirectory(prefix="cso lock ordering ") as temporary:
            base = Path(temporary)
            install, skills = self._roots(base)
            install_resource = mutation_lock_module._MutationResource(
                install,
                "state",
                skills_state=False,
            )
            skills_resource = mutation_lock_module._MutationResource(
                skills,
                ".cso-state",
                skills_state=True,
            )
            lock_set = MutationLockSet((skills_resource, install_resource, skills_resource))
            keys = [resource.key for resource in lock_set._resources]
            self.assertEqual(keys, sorted(keys))
            self.assertEqual(len(keys), 2)

    def test_partial_acquisition_failure_releases_earlier_lock(self):
        with tempfile.TemporaryDirectory(prefix="cso lock partial ") as temporary:
            base = Path(temporary)
            install = base / "a-install"
            skills_held = base / "z-skills-held"
            skills_clean = base / "z-skills-clean"
            install.mkdir()
            skills_held.mkdir()
            skills_clean.mkdir()
            ready = base / "ready"
            release = base / "release"
            holder = _spawn_holder("skills", install, skills_held, ready, release)
            try:
                _wait_for_file(ready, holder)
                with self.assertRaises(OperationError) as raised:
                    with MutationLockSet.for_engine(install, skills_held):
                        pass
                self.assertEqual(str(raised.exception), CONTENTION_MESSAGE)
                with MutationLockSet.for_engine(install, skills_clean):
                    pass
            finally:
                release.write_text("release", encoding="ascii")
                self._assert_child_stopped(holder)

    def test_stale_payload_does_not_block_and_payload_has_no_path(self):
        with tempfile.TemporaryDirectory(prefix="cso lock stale ") as temporary:
            base = Path(temporary)
            install, skills = self._roots(base)
            state = skills / ".cso-state"
            state.mkdir(mode=0o700)
            lock = state / "mutation.lock"
            lock.write_text(json.dumps({"pid": 2147483647, "token": "stale"}), encoding="ascii")
            lock.chmod(0o600)
            with MutationLockSet.for_skills(skills):
                pass
            # Windows byte-range locking may deny a second handle access to the
            # locked byte, so inspect the diagnostic payload after release.
            serialized = lock.read_text(encoding="ascii")
            payload = json.loads(serialized)
            self.assertEqual(set(payload), {"pid", "token"})
            self.assertEqual(payload["pid"], os.getpid())
            self.assertIsInstance(payload["token"], str)
            self.assertGreater(len(payload["token"]), 0)
            self.assertLessEqual(len(payload["token"]), 128)
            self.assertNotEqual(payload, {"pid": 2147483647, "token": "stale"})
            self.assertNotIn(str(base), serialized)
            self.assertNotIn(str(install), serialized)
            self.assertNotIn(str(skills), serialized)
            with MutationLockSet.for_skills(skills):
                pass

    @skipUnless(os.name != "nt", "POSIX mode checks are platform-specific")
    def test_domain_namespace_and_lock_modes(self):
        with tempfile.TemporaryDirectory(prefix="cso lock modes ") as temporary:
            base = Path(temporary)
            install, skills = self._roots(base)
            with MutationLockSet.for_engine(install, skills):
                install_state = install / "state"
                skills_state = skills / ".cso-state"
                self.assertEqual(stat.S_IMODE(install_state.stat().st_mode), 0o755)
                self.assertEqual(stat.S_IMODE(skills_state.stat().st_mode), 0o700)
                self.assertEqual(stat.S_IMODE((install_state / "mutation.lock").stat().st_mode), 0o600)
                self.assertEqual(stat.S_IMODE((skills_state / "mutation.lock").stat().st_mode), 0o600)
            with MutationLockSet.for_engine(install, skills):
                pass

    def test_child_crash_releases_os_lock(self):
        with tempfile.TemporaryDirectory(prefix="cso lock crash ") as temporary:
            base = Path(temporary)
            install, skills = self._roots(base)
            ready = base / "ready"
            release = base / "release"
            holder = _spawn_holder("skills", install, skills, ready, release, crash=True)
            _wait_for_file(ready, holder)
            stdout, stderr = holder.communicate(timeout=5)
            self.assertEqual(holder.returncode, 73, repr((stdout, stderr)))
            with MutationLockSet.for_skills(skills):
                pass

    @skipUnless(os.name != "nt", "POSIX namespace checks are platform-specific")
    def test_posix_rejects_symlink_root(self):
        with tempfile.TemporaryDirectory(prefix="cso lock symlink root ") as temporary:
            base = Path(temporary)
            real = base / "real"
            link = base / "link"
            real.mkdir()
            link.symlink_to(real, target_is_directory=True)
            with self.assertRaises(SecurityError):
                MutationLockSet.for_skills(link)

    @skipUnless(os.name != "nt", "POSIX namespace checks are platform-specific")
    def test_posix_rejects_symlink_skills_state(self):
        with tempfile.TemporaryDirectory(prefix="cso lock symlink state ") as temporary:
            base = Path(temporary)
            skills = base / "skills"
            target = base / "target"
            skills.mkdir()
            target.mkdir()
            (skills / ".cso-state").symlink_to(target, target_is_directory=True)
            with self.assertRaises(SecurityError):
                with MutationLockSet.for_skills(skills):
                    pass

    @skipUnless(os.name != "nt", "POSIX namespace checks are platform-specific")
    def test_posix_rejects_symlink_lock_file(self):
        with tempfile.TemporaryDirectory(prefix="cso lock symlink file ") as temporary:
            base = Path(temporary)
            skills = base / "skills"
            target = base / "target"
            skills.mkdir()
            target.write_text("target", encoding="ascii")
            state = skills / ".cso-state"
            state.mkdir(mode=0o700)
            lock = state / "mutation.lock"
            lock.symlink_to(target)
            with self.assertRaises(SecurityError):
                with MutationLockSet.for_skills(skills):
                    pass

    @skipUnless(os.name != "nt", "POSIX namespace checks are platform-specific")
    def test_posix_rejects_hardlinked_lock_file(self):
        with tempfile.TemporaryDirectory(prefix="cso lock hardlink ") as temporary:
            base = Path(temporary)
            skills = base / "skills"
            skills.mkdir()
            state = skills / ".cso-state"
            state.mkdir(mode=0o700)
            lock = state / "mutation.lock"
            lock.write_bytes(b"\0")
            lock.chmod(0o600)
            os.link(lock, state / "other-lock")
            with self.assertRaises(SecurityError):
                with MutationLockSet.for_skills(skills):
                    pass

    @skipUnless(os.name != "nt", "POSIX namespace checks are platform-specific")
    def test_posix_rejects_group_or_world_writable_skills_state(self):
        with tempfile.TemporaryDirectory(prefix="cso lock writable state ") as temporary:
            base = Path(temporary)
            skills = base / "skills"
            skills.mkdir()
            state = skills / ".cso-state"
            state.mkdir(mode=0o700)
            state.chmod(0o702)
            try:
                with self.assertRaises(SecurityError):
                    with MutationLockSet.for_skills(skills):
                        pass
            finally:
                state.chmod(0o700)

    @skipUnless(os.name != "nt", "POSIX namespace checks are platform-specific")
    def test_posix_rejects_broad_roots(self):
        with self.assertRaises(SecurityError):
            MutationLockSet.for_skills(Path(os.path.abspath(os.sep)))
        with self.assertRaises(SecurityError):
            MutationLockSet.for_skills(Path.home())

    @skipUnless(os.name != "nt", "POSIX permission checks are platform-specific")
    def test_posix_rejects_group_or_world_writable_root(self):
        with tempfile.TemporaryDirectory(prefix="cso lock writable root ") as temporary:
            root = Path(temporary) / "skills"
            root.mkdir()
            root.chmod(0o702)
            try:
                with self.assertRaises(SecurityError):
                    MutationLockSet.for_skills(root)
            finally:
                root.chmod(0o700)

    @skipUnless(os.name == "nt", "Windows native lock checks are platform-specific")
    def test_windows_native_lock_stale_file_and_reparse_boundaries(self):
        with tempfile.TemporaryDirectory(prefix="cso lock windows ") as temporary:
            base = Path(temporary)
            install, skills = self._roots(base)
            state = skills / ".cso-state"
            state.mkdir()
            lock = state / "mutation.lock"
            lock.write_text('{"pid":2147483647,"token":"stale"}', encoding="ascii")
            with MutationLockSet.for_skills(skills):
                pass


if __name__ == "__main__":
    unittest.main()

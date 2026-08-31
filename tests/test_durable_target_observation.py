"""Public contracts for read-only durable final-target observation."""

from __future__ import annotations

import ast
import inspect
import os
import socket
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from skill_orchestrator.durable_target_observation import (
    DurableTargetObservation,
    observe_durable_target,
)
import skill_orchestrator.durable_target_observation as durable_target_observation
from skill_orchestrator.transactional_fs import OwnedStageLease


def _snapshot(root: Path):
    records = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        info = path.lstat()
        records.append((path.relative_to(root).as_posix(), info.st_mode, path.read_bytes() if path.is_file() else None))
    return records


@unittest.skipUnless(os.name == "posix", "durable target observation is POSIX-only")
class DurableTargetObservationTests(unittest.TestCase):
    def _root(self, base: Path) -> Path:
        root = base.resolve() / "skills"
        root.mkdir(mode=0o700)
        return root

    def _identity(self, root: Path):
        info = root.stat()
        return {"kind": "posix-dev-ino", "device": info.st_dev, "inode": info.st_ino}

    def _observe(self, root: Path, key: str = "safe-skill"):
        return observe_durable_target(root, self._identity(root), key)

    def test_absent_target_is_read_only_metadata_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            before = _snapshot(root)
            result = self._observe(root)
            self.assertEqual(result.status, "absent")
            self.assertEqual(result.reason_ids, ("target.absent",))
            self.assertEqual(_snapshot(root), before)
            self.assertFalse((root / ".cso-state").exists())
            self.assertFalse((root / ".cso-staging").exists())

    def test_regular_file_and_directory_are_present(self):
        for kind in ("file", "directory"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as temporary:
                root = self._root(Path(temporary))
                target = root / "safe-skill"
                if kind == "file":
                    target.write_bytes(b"unmanaged")
                else:
                    target.mkdir()
                self.assertEqual(self._observe(root).status, "present")

    def test_symlink_fifo_and_socket_are_unsafe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            outside = root.parent / "outside"
            outside.mkdir()
            (root / "safe-skill").symlink_to(outside, target_is_directory=True)
            self.assertEqual(self._observe(root).status, "unsafe")

        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            target = root / "safe-skill"
            os.mkfifo(target)
            self.assertEqual(self._observe(root).status, "unsafe")

        if hasattr(socket, "AF_UNIX"):
            with tempfile.TemporaryDirectory() as temporary:
                root = self._root(Path(temporary))
                target = root / "safe-skill"
                listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    try:
                        listener.bind(os.fspath(target))
                    except PermissionError:
                        self.skipTest("Unix-domain sockets are unavailable in this environment")
                    self.assertEqual(self._observe(root).status, "unsafe")
                finally:
                    listener.close()

    def test_invalid_keys_and_expected_root_mismatch_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            for key in (".", "..", "a/b", "a\\b", "a:b", "name.", "CON", "a..b"):
                with self.subTest(key=key):
                    self.assertEqual(self._observe(root, key).status, "unsafe")
            identity = self._identity(root)
            identity["inode"] += 1
            self.assertEqual(observe_durable_target(root, identity, "safe-skill").status, "unsafe")

    def test_unsafe_root_fails_closed_without_target_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            root.chmod(0o777)
            try:
                self.assertEqual(self._observe(root).status, "unsafe")
            finally:
                root.chmod(0o700)

    def test_in_call_leaf_drift_is_unstable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            absent = durable_target_observation._LeafSnapshot("absent", None)
            present = durable_target_observation._LeafSnapshot("present", (1, 2, stat.S_IFREG, 1, 0))
            replaced = durable_target_observation._LeafSnapshot("present", (1, 3, stat.S_IFREG, 1, 0))
            for first, second in ((absent, present), (present, absent), (present, replaced)):
                with self.subTest(first=first, second=second), patch(
                    "skill_orchestrator.durable_target_observation._observe_leaf",
                    side_effect=(first, second),
                ):
                    result = self._observe(root)
                    self.assertEqual(result.status, "unstable")
                    self.assertEqual(result.reason_ids, ("target.changed-during-observation",))

    def test_in_call_root_identity_drift_is_unstable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            original_observe = durable_target_observation._observe_leaf
            changed = {"done": False}

            def replace_root(descriptor, target_key):
                result = original_observe(descriptor, target_key)
                if not changed["done"]:
                    changed["done"] = True
                    root.rename(root.with_name("skills-displaced"))
                    root.mkdir(mode=0o700)
                return result

            with patch(
                "skill_orchestrator.durable_target_observation._observe_leaf",
                side_effect=replace_root,
            ):
                result = self._observe(root)
            self.assertEqual(result.status, "unstable")
            self.assertEqual(result.reason_ids, ("skills-root.changed-during-observation",))

    def test_repeated_observations_are_stable_but_never_permission(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            first = self._observe(root)
            self.assertEqual(first, self._observe(root))
            (root / "safe-skill").mkdir()
            self.assertEqual(first.status, "absent")
            self.assertEqual(self._observe(root).status, "present")
            forbidden = ("admitted", "authorized", "publishable", "reserved", "owned", "lease")
            for field in forbidden:
                self.assertFalse(hasattr(first, field))
            self.assertNotIsInstance(first, OwnedStageLease)

    def test_module_has_no_execution_state_or_lock_integration(self):
        source = inspect.getsource(durable_target_observation)
        imported = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.append(node.module or "")
        for forbidden in (
            "durable_journal",
            "durable_stage_reopen",
            "durable_recovery_assessment",
            "installed_state",
            "transactional_replace",
            "engine",
            "cli",
            "mutation_lock",
            "socket",
            "subprocess",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(any(forbidden in module for module in imported))
        self.assertNotIn("MutationLockSet", source)

    def test_missing_descriptor_support_fails_closed_without_opening_root(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            with patch("skill_orchestrator.durable_target_observation._supported", return_value=False), patch(
                "skill_orchestrator.durable_target_observation.os.open",
                side_effect=AssertionError("root opened"),
            ):
                self.assertEqual(self._observe(root).status, "unsupported")


class WindowsFailClosedContractTests(unittest.TestCase):
    def test_windows_returns_before_root_or_target_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "skills"
            root.mkdir(mode=0o700)
            identity = {"kind": "posix-dev-ino", "device": 1, "inode": 2}
            with patch("skill_orchestrator.durable_target_observation.os.name", "nt"), patch(
                "skill_orchestrator.durable_target_observation.os.open",
                side_effect=AssertionError("root opened"),
            ), patch(
                "skill_orchestrator.durable_target_observation.os.stat",
                side_effect=AssertionError("target accessed"),
            ):
                result = observe_durable_target(root, identity, "safe-skill")
            self.assertEqual(result.status, "unsupported")
            self.assertFalse((root / ".cso-state").exists())
            self.assertFalse((root / ".cso-staging").exists())
            self.assertFalse((root / "safe-skill").exists())


if __name__ == "__main__":
    unittest.main()

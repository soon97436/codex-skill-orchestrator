"""Public contracts for read-only durable final-target verification."""

from __future__ import annotations

import ast
import errno
import hashlib
import inspect
import os
import socket
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import skill_orchestrator.durable_target_verification as durable_target_verification
from skill_orchestrator.durable_target_verification import (
    DurableTargetVerification,
    verify_durable_target,
)
from skill_orchestrator.transactional_fs import ExecutionLimits, OwnedStageLease


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _snapshot(root: Path):
    records = []
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        info = path.lstat()
        records.append(
            (
                path.relative_to(root).as_posix(),
                info.st_mode,
                info.st_nlink,
                path.read_bytes() if path.is_file() and not path.is_symlink() else None,
            )
        )
    return records


@unittest.skipUnless(os.name == "posix", "durable target verification is POSIX-only")
class DurableTargetVerificationTests(unittest.TestCase):
    def _root(self, base: Path) -> Path:
        root = base.resolve() / "skills"
        root.mkdir(mode=0o700)
        root.chmod(0o700)
        return root

    def _identity(self, root: Path):
        info = root.stat()
        return {"kind": "posix-dev-ino", "device": info.st_dev, "inode": info.st_ino}

    def _target(self, root: Path, files):
        target = root / "safe-skill"
        target.mkdir(mode=0o700)
        target.chmod(0o700)
        for relative, data in files.items():
            leaf = target / relative
            leaf.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            for directory in (target, *leaf.parents):
                if directory == root.parent:
                    break
                if directory.is_dir():
                    directory.chmod(0o700)
            leaf.write_bytes(data)
            leaf.chmod(0o600)
        return target

    def _manifest(self, files):
        return [
            {"path": path, "sha256": _digest(data), "size": len(data)}
            for path, data in sorted(files.items())
        ]

    def _verify(self, root: Path, manifest, key: str = "safe-skill", **kwargs):
        return verify_durable_target(root, self._identity(root), key, manifest, **kwargs)

    def test_exact_manifest_is_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            files = {"README.md": b"hello", "pkg/main.py": b"print('ok')\n"}
            self._target(root, files)
            result = self._verify(root, self._manifest(files))
            self.assertEqual(result.status, "verified")
            self.assertEqual(result.file_count, 2)
            self.assertEqual(result.total_bytes, sum(len(value) for value in files.values()))
            self.assertEqual(result.reason_ids, ("target.verified",))

    def test_missing_target_is_missing(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            result = self._verify(root, self._manifest({"README.md": b"hello"}))
            self.assertEqual(result.status, "missing")
            self.assertEqual(result.reason_ids, ("target.missing",))

    def test_missing_declared_file_and_extra_regular_file_are_mismatches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            self._target(root, {"README.md": b"hello"})
            manifest = self._manifest({"README.md": b"hello", "pkg/main.py": b"missing"})
            self.assertEqual(self._verify(root, manifest).status, "mismatch")

        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            self._target(root, {"README.md": b"hello", "extra.txt": b"extra"})
            self.assertEqual(self._verify(root, self._manifest({"README.md": b"hello"})).status, "mismatch")

    def test_extra_directory_and_wrong_file_contents_or_size_are_mismatches(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            target = self._target(root, {"README.md": b"hello"})
            extra = target / "unused"
            extra.mkdir(mode=0o700)
            extra.chmod(0o700)
            self.assertEqual(self._verify(root, self._manifest({"README.md": b"hello"})).status, "mismatch")

        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            self._target(root, {"README.md": b"hello"})
            self.assertEqual(self._verify(root, self._manifest({"README.md": b"world"})).status, "mismatch")
            self.assertEqual(
                self._verify(
                    root,
                    [{"path": "README.md", "sha256": _digest(b"hello"), "size": 4}],
                ).status,
                "mismatch",
            )

    def test_symlink_and_special_objects_are_unsafe(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            (root / "safe-skill").write_bytes(b"not-a-directory")
            self.assertEqual(self._verify(root, self._manifest({"README.md": b"hello"})).status, "unsafe")

        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            outside = root.parent / "outside"
            outside.mkdir(mode=0o700)
            (root / "safe-skill").symlink_to(outside, target_is_directory=True)
            self.assertEqual(self._verify(root, self._manifest({"README.md": b"hello"})).status, "unsafe")

        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            target = self._target(root, {"README.md": b"hello"})
            (target / "README.md").unlink()
            (target / "README.md").symlink_to(root.parent / "outside-file")
            self.assertEqual(self._verify(root, self._manifest({"README.md": b"hello"})).status, "unsafe")

        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            target = self._target(root, {"pkg/main.py": b"hello"})
            (target / "pkg").rename(target / "pkg-real")
            (target / "pkg").symlink_to(target / "pkg-real", target_is_directory=True)
            self.assertEqual(self._verify(root, self._manifest({"pkg/main.py": b"hello"})).status, "unsafe")

        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            target = self._target(root, {"README.md": b"hello"})
            os.mkfifo(target / "pipe", 0o600)
            self.assertEqual(self._verify(root, self._manifest({"README.md": b"hello"})).status, "unsafe")

    def test_socket_and_device_are_unsafe_when_fixtures_are_available(self):
        if not hasattr(socket, "AF_UNIX"):
            self.skipTest("Unix-domain sockets are unavailable in this runtime")
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            target = self._target(root, {"README.md": b"hello"})
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                try:
                    listener.bind(os.fspath(target / "socket"))
                except OSError as error:
                    fixture_restrictions = {
                        errno.EACCES,
                        errno.EAFNOSUPPORT,
                        errno.EOPNOTSUPP,
                        errno.ENOSYS,
                        errno.EPERM,
                        errno.EROFS,
                    }
                    if error.errno not in fixture_restrictions:
                        raise
                    self.skipTest("Unix-domain socket creation is unavailable in this environment")
                self.assertEqual(self._verify(root, self._manifest({"README.md": b"hello"})).status, "unsafe")
            finally:
                listener.close()

        if not hasattr(os, "mknod") or not hasattr(os, "makedev"):
            return
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            target = self._target(root, {"README.md": b"hello"})
            try:
                os.mknod(target / "device", stat.S_IFCHR | 0o600, os.makedev(1, 3))
            except OSError:
                return
            self.assertEqual(self._verify(root, self._manifest({"README.md": b"hello"})).status, "unsafe")

    def test_unsafe_hardlink_and_mode_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            target = self._target(root, {"README.md": b"hello"})
            os.link(target / "README.md", target / "alias")
            self.assertEqual(self._verify(root, self._manifest({"README.md": b"hello"})).status, "unsafe")

        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            target = self._target(root, {"README.md": b"hello"})
            (target / "README.md").chmod(0o644)
            self.assertEqual(self._verify(root, self._manifest({"README.md": b"hello"})).status, "unsafe")

    def test_invalid_keys_unsafe_roots_and_root_identity_mismatch_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            manifest = self._manifest({"README.md": b"hello"})
            for key in (".", "..", "a/b", "a\\b", "a:b", "name.", "CON", "a..b"):
                with self.subTest(key=key):
                    self.assertEqual(self._verify(root, manifest, key).status, "unsafe")
            root.chmod(0o777)
            try:
                self.assertEqual(self._verify(root, manifest).status, "unsafe")
            finally:
                root.chmod(0o700)
            wrong = self._identity(root)
            wrong["inode"] += 1
            self.assertEqual(verify_durable_target(root, wrong, "safe-skill", manifest).status, "unsafe")

    def test_root_and_target_replacement_are_unstable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            files = {"README.md": b"hello"}
            self._target(root, files)
            original_walk = durable_target_verification._walk_directory
            changed = {"done": False}

            def replace_root(*args, **kwargs):
                original_walk(*args, **kwargs)
                if not changed["done"]:
                    changed["done"] = True
                    root.rename(root.with_name("skills-displaced"))
                    root.mkdir(mode=0o700)
                    root.chmod(0o700)

            with patch(
                "skill_orchestrator.durable_target_verification._walk_directory",
                side_effect=replace_root,
            ):
                result = self._verify(root, self._manifest(files))
            self.assertEqual(result.status, "unstable")
            self.assertEqual(result.reason_ids, ("skills-root.changed-during-verification",))

        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            files = {"README.md": b"hello"}
            target = self._target(root, files)
            original_walk = durable_target_verification._walk_directory
            changed = {"done": False}

            def replace_target(*args, **kwargs):
                original_walk(*args, **kwargs)
                if not changed["done"]:
                    changed["done"] = True
                    target.rename(target.with_name("safe-skill-displaced"))
                    target.mkdir(mode=0o700)
                    target.chmod(0o700)

            with patch(
                "skill_orchestrator.durable_target_verification._walk_directory",
                side_effect=replace_target,
            ):
                result = self._verify(root, self._manifest(files))
            self.assertEqual(result.status, "unstable")
            self.assertEqual(result.reason_ids, ("target.changed-during-verification",))

    def test_file_mutation_during_verification_is_detected_where_practical(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            files = {"README.md": b"hello"}
            target = self._target(root, files)
            original_read = durable_target_verification._read_regular_file

            def mutate_after_read(*args, **kwargs):
                result = original_read(*args, **kwargs)
                (target / "README.md").write_bytes(b"changed")
                (target / "README.md").chmod(0o600)
                return result

            with patch(
                "skill_orchestrator.durable_target_verification._read_regular_file",
                side_effect=mutate_after_read,
            ):
                result = self._verify(root, self._manifest(files))
            self.assertEqual(result.status, "unstable")

    def test_limits_and_malformed_manifest_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            self._target(root, {"README.md": b"hello"})
            manifest = self._manifest({"README.md": b"hello"})
            self.assertEqual(
                self._verify(
                    root,
                    manifest,
                    limits=ExecutionLimits(max_single_file_bytes=4, max_total_candidate_bytes=10, copy_chunk_bytes=1),
                ).status,
                "unsafe",
            )
            self.assertEqual(
                self._verify(
                    root,
                    manifest,
                    limits=ExecutionLimits(max_single_file_bytes=10, max_total_candidate_bytes=4, copy_chunk_bytes=1),
                ).status,
                "unsafe",
            )
            self.assertEqual(self._verify(root, [{"path": "README.md"}]).status, "unsafe")

        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            self._target(root, {"README.md": b"abc", "extra.txt": b"defg"})
            self.assertEqual(
                self._verify(
                    root,
                    self._manifest({"README.md": b"abc"}),
                    limits=ExecutionLimits(max_single_file_bytes=10, max_total_candidate_bytes=5, copy_chunk_bytes=1),
                ).status,
                "unsafe",
            )

    def test_results_are_detached_and_stale_after_return(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            files = {"README.md": b"hello"}
            target = self._target(root, files)
            first = self._verify(root, self._manifest(files))
            self.assertEqual(first, self._verify(root, self._manifest(files)))
            self.assertIsInstance(first, DurableTargetVerification)
            self.assertNotIsInstance(first, OwnedStageLease)
            for field in ("fd", "lease", "lock", "authorization", "publish", "mutate", "cleanup"):
                with self.subTest(field=field):
                    self.assertFalse(hasattr(first, field))
            (target / "README.md").write_bytes(b"changed")
            (target / "README.md").chmod(0o600)
            self.assertEqual(first.status, "verified")
            self.assertEqual(self._verify(root, self._manifest(files)).status, "mismatch")

    def test_descriptor_traversal_is_read_only_and_has_no_state_stage_or_lock_side_effect(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            files = {"pkg/main.py": b"hello"}
            target = self._target(root, files)
            outside = root.parent / "outside"
            outside.mkdir(mode=0o700)
            (target / "escape").symlink_to(outside, target_is_directory=True)
            before = _snapshot(root)
            self.assertEqual(self._verify(root, self._manifest(files)).status, "unsafe")
            self.assertEqual(_snapshot(root), before)
            self.assertFalse((root / ".cso-state").exists())
            self.assertFalse((root / ".cso-staging").exists())

    def test_module_boundary_has_no_execution_mutation_or_network_integration(self):
        source = inspect.getsource(durable_target_verification)
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
            "urllib",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertFalse(any(forbidden in module for module in imported))
        self.assertNotIn("MutationLockSet", source)
        self.assertNotIn(".cso-state", source)
        self.assertNotIn(".cso-staging", source)
        self.assertNotIn("tree_manifest", source)
        writes = {"mkdir", "write", "unlink", "rmdir", "rename", "replace", "chmod", "fsync"}
        calls = {
            node.func.attr
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        self.assertFalse(calls & writes)

    def test_missing_descriptor_support_fails_closed_before_root_open(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self._root(Path(temporary))
            with patch("skill_orchestrator.durable_target_verification._supported", return_value=False), patch(
                "skill_orchestrator.durable_target_verification.os.open",
                side_effect=AssertionError("root opened"),
            ):
                result = self._verify(root, self._manifest({"README.md": b"hello"}))
            self.assertEqual(result.status, "unsupported")


class WindowsFailClosedContractTests(unittest.TestCase):
    def test_windows_returns_before_root_target_or_manifest_access(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "skills"
            root.mkdir(mode=0o700)
            identity = {"kind": "posix-dev-ino", "device": 1, "inode": 2}
            with patch("skill_orchestrator.durable_target_verification.os.name", "nt"), patch(
                "skill_orchestrator.durable_target_verification.os.open",
                side_effect=AssertionError("root opened"),
            ), patch(
                "skill_orchestrator.durable_target_verification.os.stat",
                side_effect=AssertionError("target accessed"),
            ):
                result = verify_durable_target(root, identity, "safe-skill", [])
            self.assertEqual(result.status, "unsupported")
            self.assertFalse((root / ".cso-state").exists())
            self.assertFalse((root / ".cso-staging").exists())
            self.assertFalse((root / "safe-skill").exists())


if __name__ == "__main__":
    unittest.main()

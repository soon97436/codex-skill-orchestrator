"""Contracts for the private native POSIX no-replace directory-leaf adapter."""

from __future__ import annotations

import ast
import copy
import ctypes
import os
import stat
import sys
import tempfile
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import patch

import skill_orchestrator._posix_no_replace as adapter
from skill_orchestrator._posix_no_replace import (
    MUTATION_CERTAINTIES,
    PLATFORMS,
    REASON_IDS,
    STATUSES,
    NativeNoReplaceResult,
)


ROOT = Path(__file__).resolve().parents[1]


def _directory_flags() -> int:
    return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)


class CommonContractTests(unittest.TestCase):
    def test_leaf_contract_is_ascii_single_component_with_255_byte_limit(self) -> None:
        self.assertEqual(adapter._leaf_bytes(".stage.name", "leaf"), b".stage.name")
        self.assertEqual(len(adapter._leaf_bytes("a" * 255, "leaf")), 255)
        for value in ("", ".", "..", "a/b", "a\x00b", "\u00e9", "a" * 256):
            with self.subTest(value=repr(value)):
                with self.assertRaises(ValueError):
                    adapter._leaf_bytes(value, "leaf")

    def test_descriptor_validator_rejects_bool_non_int_and_negative(self) -> None:
        for value in (True, "3", object()):
            with self.subTest(value=repr(value)):
                with self.assertRaises(TypeError):
                    adapter._descriptor(value, "descriptor")
        with self.assertRaises(ValueError):
            adapter._descriptor(-1, "descriptor")

    def test_result_is_frozen_bounded_and_has_no_endpoint_diagnostics(self) -> None:
        first = NativeNoReplaceResult("linux", "succeeded", True, "succeeded", "native-success")
        second = NativeNoReplaceResult("linux", "succeeded", True, "succeeded", "native-success")
        self.assertEqual(first, second)
        self.assertEqual(copy.copy(first), first)
        with self.assertRaises(FrozenInstanceError):
            first.status = "indeterminate"  # type: ignore[misc]
        self.assertEqual(
            set(first.__dict__),
            {"platform", "status", "attempted", "mutation_certainty", "reason_id"},
        )
        self.assertNotIn("errno", repr(first))
        with self.assertRaises(ValueError):
            NativeNoReplaceResult("linux", "other", True, "succeeded", "native-success")

    def test_vocabularies_are_closed(self) -> None:
        self.assertEqual(STATUSES[0], "succeeded")
        self.assertEqual(MUTATION_CERTAINTIES, ("succeeded", "no-mutation", "indeterminate"))
        self.assertEqual(PLATFORMS, ("darwin", "linux", "unsupported"))
        self.assertIn("native-indeterminate", REASON_IDS)

    def test_unsupported_platform_dispatch_precedes_endpoint_validation(self) -> None:
        with patch.object(adapter.sys, "platform", "win32"):
            result = adapter._move_directory_leaf_no_replace(-1, "", -1, "")
        self.assertEqual(
            (result.platform, result.status, result.attempted, result.mutation_certainty),
            ("unsupported", "unsupported-platform", False, "no-mutation"),
        )


class PosixNativeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="cso no-replace ")
        self.root = Path(self.temporary.name).resolve()
        self.assertNotEqual(self.root, ROOT)
        self.assertFalse(ROOT.is_relative_to(self.root))
        self.assertFalse(self.root.is_relative_to(ROOT))
        self.source_parent = self.root / "source-parent"
        self.destination_parent = self.root / "destination-parent"
        self.source_parent.mkdir()
        self.destination_parent.mkdir()
        self.source_fd = os.open(self.source_parent, _directory_flags())
        self.destination_fd = os.open(self.destination_parent, _directory_flags())

    def tearDown(self) -> None:
        os.close(self.destination_fd)
        os.close(self.source_fd)
        self.temporary.cleanup()

    def _make_source(self, name: str = ".stage.leaf") -> Path:
        source = self.source_parent / name
        source.mkdir()
        (source / "payload").write_bytes(b"stage")
        return source

    def _move(self, source: str = ".stage.leaf", destination: str = "target"):
        return adapter._move_directory_leaf_no_replace(
            self.source_fd, source, self.destination_fd, destination
        )

@unittest.skipUnless(sys.platform in ("darwin", "linux"), "POSIX native adapter tests")
class PosixNativeCommonTests(PosixNativeTestCase):
    def test_structural_misuse_is_rejected_before_filesystem_access(self) -> None:
        for value in (True, "3", object()):
            with self.subTest(value=repr(value)):
                with self.assertRaises(TypeError):
                    adapter._move_directory_leaf_no_replace(
                        value, "source", self.destination_fd, "target"
                    )  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            adapter._move_directory_leaf_no_replace(-1, "source", self.destination_fd, "target")

    def test_parent_must_be_an_open_directory_and_borrowed_descriptors_remain_open(self) -> None:
        regular = self.root / "regular"
        regular.write_bytes(b"x")
        regular_fd = os.open(regular, os.O_RDONLY)
        try:
            result = adapter._move_directory_leaf_no_replace(regular_fd, "source", self.destination_fd, "target")
            self.assertEqual((result.status, result.attempted, result.mutation_certainty), ("invalid-endpoint", False, "no-mutation"))
            os.fstat(regular_fd)
            os.fstat(self.source_fd)
            os.fstat(self.destination_fd)
        finally:
            os.close(regular_fd)

    def test_source_prevalidation_rejects_missing_regular_and_symlink_without_attempt(self) -> None:
        missing = self._move("missing")
        self.assertEqual((missing.status, missing.attempted, missing.mutation_certainty), ("source-missing", False, "no-mutation"))
        (self.source_parent / "regular").write_bytes(b"x")
        regular = self._move("regular")
        self.assertEqual((regular.status, regular.attempted, regular.mutation_certainty), ("source-invalid", False, "no-mutation"))
        self._make_source("source")
        os.symlink("source", "source-link", dir_fd=self.source_fd)
        linked = self._move("source-link")
        self.assertEqual((linked.status, linked.attempted, linked.mutation_certainty), ("source-invalid", False, "no-mutation"))

    def test_same_parent_same_leaf_is_rejected_before_attempt(self) -> None:
        self._make_source("same")
        result = adapter._move_directory_leaf_no_replace(
            self.source_fd, "same", self.source_fd, "same"
        )
        self.assertEqual((result.status, result.attempted, result.mutation_certainty), ("source-invalid", False, "no-mutation"))
        self.assertTrue((self.source_parent / "same").is_dir())

    def test_absent_destination_moves_directory_and_retained_fd_identifies_destination(self) -> None:
        source = self._make_source()
        before = source.stat()
        source_fd = os.open(source, _directory_flags())
        try:
            result = self._move()
            self.assertEqual((result.status, result.attempted, result.mutation_certainty), ("succeeded", True, "succeeded"))
            destination = self.destination_parent / "target"
            self.assertFalse(source.exists())
            self.assertTrue(destination.is_dir())
            after = destination.stat()
            retained = os.fstat(source_fd)
            self.assertEqual((after.st_dev, after.st_ino), (before.st_dev, before.st_ino))
            self.assertEqual((retained.st_dev, retained.st_ino), (before.st_dev, before.st_ino))
        finally:
            os.close(source_fd)

    def _assert_destination_unchanged(self, create_destination) -> None:
        source = self._make_source()
        destination = self.destination_parent / "target"
        create_destination(destination)
        before = os.lstat(destination)
        result = self._move()
        after = os.lstat(destination)
        self.assertTrue(source.is_dir())
        self.assertEqual((before.st_dev, before.st_ino, before.st_mode), (after.st_dev, after.st_ino, after.st_mode))
        self.assertTrue(result.attempted)
        self.assertNotEqual(result.mutation_certainty, "succeeded")

    def test_existing_regular_destination_is_not_overwritten(self) -> None:
        def create(destination: Path) -> None:
            destination.write_bytes(b"existing")

        self._assert_destination_unchanged(create)

    def test_existing_directory_destination_is_not_overwritten(self) -> None:
        self._assert_destination_unchanged(lambda destination: destination.mkdir())

    def test_existing_symlink_destination_is_not_overwritten(self) -> None:
        def create(destination: Path) -> None:
            os.symlink("missing-target", destination)

        self._assert_destination_unchanged(create)

    def test_same_parent_different_leaf_and_different_parent_succeed(self) -> None:
        self._make_source("same-parent")
        same = adapter._move_directory_leaf_no_replace(
            self.source_fd, "same-parent", self.source_fd, "renamed"
        )
        self.assertEqual(same.status, "succeeded")
        self.assertTrue((self.source_parent / "renamed").is_dir())
        self._make_source("different-parent")
        different = self._move("different-parent", "different-target")
        self.assertEqual(different.status, "succeeded")
        self.assertTrue((self.destination_parent / "different-target").is_dir())



@unittest.skipUnless(sys.platform == "linux", "Linux renameat2 contract")
class LinuxNativeTests(PosixNativeTestCase):
    def test_linux_runner_requires_renameat2_symbol_and_conservative_eexist(self) -> None:
        self.assertIsNotNone(adapter._native_function("renameat2"))
        self._make_source()
        (self.destination_parent / "target").write_bytes(b"existing")
        result = self._move()
        self.assertEqual((result.status, result.attempted, result.mutation_certainty), ("destination-exists", True, "indeterminate"))
        self.assertTrue((self.source_parent / ".stage.leaf").is_dir())


@unittest.skipUnless(sys.platform == "darwin", "Darwin renameatx_np contract")
class DarwinNativeTests(PosixNativeTestCase):
    def test_darwin_runner_requires_renameatx_np_symbol_and_safe_eexist(self) -> None:
        self.assertIsNotNone(adapter._native_function("renameatx_np"))
        self._make_source()
        (self.destination_parent / "target").write_bytes(b"existing")
        result = self._move()
        self.assertEqual((result.status, result.attempted, result.mutation_certainty), ("destination-exists", True, "no-mutation"))
        self.assertTrue((self.source_parent / ".stage.leaf").is_dir())


class WindowsFailClosedTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows fail-closed contract")
    def test_windows_returns_before_symbol_or_filesystem_access(self) -> None:
        with patch.object(adapter.ctypes, "CDLL", side_effect=AssertionError("symbol lookup")), patch.object(
            adapter.os, "fstat", side_effect=AssertionError("fstat")
        ), patch.object(adapter.os, "stat", side_effect=AssertionError("stat")), patch.object(
            adapter.os, "rename", side_effect=AssertionError("rename")
        ), patch.object(adapter.os, "replace", side_effect=AssertionError("replace")):
            result = adapter._move_directory_leaf_no_replace(-1, "", -1, "")
        self.assertEqual(
            (result.platform, result.status, result.attempted, result.mutation_certainty),
            ("unsupported", "unsupported-platform", False, "no-mutation"),
        )


class StaticScopeTests(unittest.TestCase):
    def test_module_is_private_and_has_no_production_integration_or_fallback(self) -> None:
        source = Path(adapter.__file__).read_text(encoding="utf-8")
        imports = []
        calls = []
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imports.extend(item.name for item in node.names)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module or "")
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                calls.append((node.func.value.id if isinstance(node.func.value, ast.Name) else "", node.func.attr))
        forbidden_imports = (
            "transactional_fs", "transactional_replace", "mutation_lock", "durable_journal",
            "transaction_journal", "publication_outcome", "durable_target_observation",
            "durable_target_verification", "installation_authorization", "execution_handoff",
            "installed_state", "engine", "cli", "subprocess", "shutil", "pathlib",
        )
        for name in forbidden_imports:
            with self.subTest(name=name):
                self.assertFalse(any(name in imported for imported in imports))
        self.assertFalse(any(owner == "os" and name in {"rename", "replace", "unlink", "mkdir", "rmdir", "makedirs"} for owner, name in calls))
        self.assertNotIn("/proc/self/fd", source)
        self.assertNotIn("SYS_renameat2", source)
        self.assertNotIn("PublicationOutcome", source)


if __name__ == "__main__":
    unittest.main()

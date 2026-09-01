import ast
import hashlib
import json
import os
import stat
import sys
import tempfile
import unittest
from dataclasses import asdict, replace
from pathlib import Path

from skill_orchestrator.transactional_fs import (
    BASE_LIMITATIONS,
    COPY_CHUNK_BYTES,
    MAX_DECLARED_FILES,
    MAX_SINGLE_FILE_BYTES,
    MAX_TOTAL_CANDIDATE_BYTES,
    REASON_IDS,
    STAGE_STATUSES,
    WINDOWS_LIMITATION,
    DeclaredFile,
    ExecutionLimits,
    RealFilesystemAdapter,
    StageRequest,
    StageResult,
    stage_declared_candidate_owned,
    stage_declared_candidate,
)


_TEMP_PARENT = (
    None
    if sys.platform == "win32"
    else Path("/private/tmp")
    if Path("/private/tmp").is_dir()
    else Path(os.path.realpath(tempfile.gettempdir()))
)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _declared(path: str, data: bytes) -> DeclaredFile:
    return DeclaredFile(path, _sha256(data))


def _manifest_digest(records) -> str:
    payload = [
        {"path": path, "sha256": digest, "size": size}
        for path, digest, size in sorted(records)
    ]
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(b"cso-stage-manifest-v1\0" + encoded).hexdigest()


class _Fixture:
    def __init__(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(dir=_TEMP_PARENT)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source"
        self.staging = self.root / "staging"
        self.target = self.root / "target"
        self.source.mkdir()
        self.staging.mkdir()
        self.target.mkdir()

    def close(self) -> None:
        self.temporary.cleanup()

    def write(self, path: str, data: bytes) -> DeclaredFile:
        destination = self.source.joinpath(*path.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return _declared(path, data)

    def request(
        self,
        files,
        *,
        candidate_key: str = "demo-skill",
        limits: ExecutionLimits = ExecutionLimits(),
        source_root=None,
        staging_parent=None,
    ) -> StageRequest:
        return StageRequest(
            source_root=self.source if source_root is None else source_root,
            staging_parent=self.staging if staging_parent is None else staging_parent,
            candidate_key=candidate_key,
            declared_files=tuple(files),
            limits=limits,
        )


class _FaultAdapter(RealFilesystemAdapter):
    def __init__(self, fault: str, *, cleanup_fails: bool = False) -> None:
        super().__init__()
        self.fault = fault
        self.cleanup_fails = cleanup_fails
        self.maximum_read_size = 0
        self._metadata_calls = 0

    def open_roots(self, source_root, staging_parent):
        if self.fault == "ancestor-open":
            raise OSError()
        return super().open_roots(source_root, staging_parent)

    def create_stage(self, roots, candidate_key):
        if self.fault == "stage-mkdir":
            raise OSError()
        stage = super().create_stage(roots, candidate_key)
        if self.fault == "stage-substitution":
            moved = stage.name + ".moved"
            os.rename(
                stage.name,
                moved,
                src_dir_fd=roots.staging_parent_fd,
                dst_dir_fd=roots.staging_parent_fd,
            )
            os.mkdir(stage.name, mode=0o700, dir_fd=roots.staging_parent_fd)
        return stage

    def open_source_file(self, roots, relative_path):
        if self.fault == "source-open":
            raise OSError()
        return super().open_source_file(roots, relative_path)

    def create_stage_file(self, stage, relative_path):
        if self.fault == "file-create":
            raise OSError()
        return super().create_stage_file(stage, relative_path)

    def read(self, opened, size):
        self.maximum_read_size = max(self.maximum_read_size, size)
        if self.fault == "source-read":
            raise OSError()
        return super().read(opened, size)

    def write(self, opened, data):
        if self.fault == "file-write":
            raise OSError()
        if self.fault == "partial-write" and len(data) > 1:
            return super().write(opened, data[: max(1, len(data) // 2)])
        return super().write(opened, data)

    def metadata(self, opened):
        value = super().metadata(opened)
        self._metadata_calls += 1
        if self.fault == "source-changed" and self._metadata_calls == 1:
            return replace(value, modified_ns=value.modified_ns + 1)
        return value

    def verify_stage(self, stage, expected, limits):
        if self.fault == "verify-failure":
            raise OSError()
        if self.fault == "verify-extra":
            descriptor = os.open(
                "extra",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=stage.fd,
            )
            os.write(descriptor, b"extra")
            os.close(descriptor)
        if self.fault == "verify-missing":
            os.unlink(next(iter(expected)), dir_fd=stage.fd)
        if self.fault == "verify-hash":
            path = next(iter(expected))
            descriptor = os.open(path, os.O_WRONLY | os.O_TRUNC, dir_fd=stage.fd)
            os.write(descriptor, b"tampered")
            os.close(descriptor)
        if self.fault == "verify-symlink":
            os.symlink(next(iter(expected)), "extra-link", dir_fd=stage.fd)
        if self.fault == "verify-hardlink":
            os.link(
                next(iter(expected)),
                "extra-hardlink",
                src_dir_fd=stage.fd,
                dst_dir_fd=stage.fd,
            )
        if self.fault == "verify-special":
            os.mkfifo("extra-fifo", dir_fd=stage.fd)
        return super().verify_stage(stage, expected, limits)

    def cleanup_stage(self, stage):
        if self.cleanup_fails:
            raise OSError()
        return super().cleanup_stage(stage)


class _FinalizationAdapter(RealFilesystemAdapter):
    """Records only the narrow stage-finalization seam used by these tests."""

    def __init__(self, *, fail_at=None, cleanup_fails=False) -> None:
        super().__init__()
        self.fail_at = fail_at
        self.cleanup_fails = cleanup_fails
        self.events = []
        self.cleanup_calls = 0
        self.stage_fd = None
        self.staging_parent_fd = None

    def flush(self, opened):
        self.events.append("file")
        return super().flush(opened)

    def finalize_stage(self, stage):
        self.stage_fd = stage.fd
        self.staging_parent_fd = stage.roots.staging_parent_fd
        return super().finalize_stage(stage)

    def _synchronize_directory(self, descriptor):
        if descriptor == self.stage_fd:
            event = "stage-root"
        elif descriptor == self.staging_parent_fd:
            event = "staging-parent"
        else:
            event = "nested"
        self.events.append(event)
        if event == self.fail_at:
            raise OSError("forced %s sync failure" % event)
        return super()._synchronize_directory(descriptor)

    def cleanup_stage(self, stage):
        self.cleanup_calls += 1
        if self.cleanup_fails:
            raise OSError("forced cleanup failure")
        return super().cleanup_stage(stage)


@unittest.skipIf(sys.platform == "win32", "POSIX secure staging tests")
class TransactionalFilesystemTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = _Fixture()

    def tearDown(self) -> None:
        self.fixture.close()

    def test_contract_constants_statuses_and_limitations_are_closed(self) -> None:
        self.assertEqual(MAX_SINGLE_FILE_BYTES, 1_048_576)
        self.assertEqual(MAX_TOTAL_CANDIDATE_BYTES, 8_388_608)
        self.assertEqual(COPY_CHUNK_BYTES, 65_536)
        self.assertEqual(
            STAGE_STATUSES,
            ("staged", "rejected", "invalid", "failed", "cleanup-required"),
        )
        self.assertEqual(len(REASON_IDS), len(set(REASON_IDS)))
        self.assertIn("phase5e.fs.limit.staged-not-installed", BASE_LIMITATIONS)
        for data_type in (DeclaredFile, ExecutionLimits, StageRequest, StageResult):
            self.assertTrue(data_type.__dataclass_params__.frozen)

    def test_malformed_request_limits_and_candidate_key_are_invalid(self) -> None:
        declared = self.fixture.write("SKILL.md", b"ok")
        requests = (
            None,
            replace(self.fixture.request((declared,)), source_root="source"),
            replace(self.fixture.request((declared,)), staging_parent="stage"),
            replace(self.fixture.request((declared,)), source_root=Path("bad\x00root")),
            replace(self.fixture.request((declared,)), candidate_key=""),
            replace(self.fixture.request((declared,)), candidate_key="../escape"),
            replace(self.fixture.request((declared,)), candidate_key="bad:key"),
            replace(self.fixture.request((declared,)), candidate_key="a" * 101),
            replace(self.fixture.request((declared,)), limits=ExecutionLimits(copy_chunk_bytes=0)),
            replace(
                self.fixture.request((declared,)),
                limits=ExecutionLimits(copy_chunk_bytes=COPY_CHUNK_BYTES + 1),
            ),
            replace(
                self.fixture.request((declared,)),
                limits=ExecutionLimits(
                    max_single_file_bytes=MAX_SINGLE_FILE_BYTES + 1,
                    max_total_candidate_bytes=MAX_TOTAL_CANDIDATE_BYTES,
                ),
            ),
            replace(
                self.fixture.request((declared,)),
                limits=ExecutionLimits(
                    max_total_candidate_bytes=MAX_TOTAL_CANDIDATE_BYTES + 1,
                ),
            ),
            replace(
                self.fixture.request((declared,)),
                limits=ExecutionLimits(
                    max_single_file_bytes=2,
                    max_total_candidate_bytes=1,
                    copy_chunk_bytes=1,
                ),
            ),
            replace(self.fixture.request((declared,)), declared_files=[declared]),
        )
        for request in requests:
            with self.subTest(request=request):
                result = stage_declared_candidate(request)
                self.assertEqual(result.status, "invalid")
                self.assertEqual(tuple(self.fixture.staging.iterdir()), ())

    def test_empty_too_many_malformed_hash_duplicate_and_collision_are_invalid(self) -> None:
        good = self.fixture.write("SKILL.md", b"ok")
        cases = (
            (),
            tuple(DeclaredFile("f%d" % index, "a" * 64) for index in range(MAX_DECLARED_FILES + 1)),
            (DeclaredFile("SKILL.md", "A" * 64),),
            (DeclaredFile("../escape", "a" * 64),),
            (DeclaredFile("a\\b", "a" * 64),),
            (good, good),
            (DeclaredFile("Readme.md", "a" * 64), DeclaredFile("README.md", "b" * 64)),
            (DeclaredFile("a", "a" * 64), DeclaredFile("a/b", "b" * 64)),
        )
        for files in cases:
            with self.subTest(files=files):
                result = stage_declared_candidate(self.fixture.request(files))
                self.assertEqual(result.status, "invalid")
                self.assertEqual(tuple(self.fixture.staging.iterdir()), ())

    def test_relative_and_root_like_execution_roots_are_rejected_before_mutation(self) -> None:
        declared = self.fixture.write("SKILL.md", b"ok")
        for source_root, staging_parent in (
            (Path("relative-source"), self.fixture.staging),
            (self.fixture.source, Path("relative-stage")),
            (Path(self.fixture.source.anchor), self.fixture.staging),
            (self.fixture.source, Path(self.fixture.staging.anchor)),
        ):
            with self.subTest(source_root=source_root, staging_parent=staging_parent):
                result = stage_declared_candidate(
                    self.fixture.request(
                        (declared,), source_root=source_root, staging_parent=staging_parent
                    )
                )
                self.assertEqual(result.status, "rejected")
                self.assertEqual(tuple(self.fixture.staging.iterdir()), ())

    def test_exact_one_file_posix_staging(self) -> None:
        payload = b"# Skill\n"
        declared = self.fixture.write("SKILL.md", payload)
        result = stage_declared_candidate(self.fixture.request((declared,)))
        self.assertEqual(result.status, "staged")
        self.assertEqual(result.file_count, 1)
        self.assertEqual(result.total_bytes, len(payload))
        self.assertIsNotNone(result.stage_id)
        self.assertIsNotNone(result.manifest_digest)
        staged_roots = list(self.fixture.staging.iterdir())
        self.assertEqual(len(staged_roots), 1)
        self.assertEqual((staged_roots[0] / "SKILL.md").read_bytes(), payload)
        self.assertEqual(stat.S_IMODE(staged_roots[0].stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE((staged_roots[0] / "SKILL.md").stat().st_mode), 0o600)

    def test_required_stage_sync_follows_file_sync_and_is_bottom_up(self) -> None:
        declared = self.fixture.write("nested/deeper/SKILL.md", b"durable bytes")
        adapter = _FinalizationAdapter()

        result = stage_declared_candidate(self.fixture.request((declared,)), fs=adapter)

        self.assertEqual(result.status, "staged")
        self.assertEqual(
            adapter.events,
            ["file", "nested", "nested", "stage-root", "staging-parent"],
        )
        self.assertEqual(adapter.events[:1], ["file"])
        self.assertEqual(adapter.events[-2:], ["stage-root", "staging-parent"])

    def test_owned_lease_is_not_returned_until_stage_finalization_succeeds(self) -> None:
        declared = self.fixture.write("nested/SKILL.md", b"durable bytes")
        adapter = _FinalizationAdapter()

        outcome = stage_declared_candidate_owned(self.fixture.request((declared,)), fs=adapter)

        self.assertEqual(outcome.result.status, "staged")
        self.assertIsNotNone(outcome.lease)
        self.assertEqual(adapter.events[-2:], ["stage-root", "staging-parent"])
        stage_fd = adapter.stage_fd
        parent_fd = adapter.staging_parent_fd
        self.assertEqual(outcome.lease.cleanup().status, "cleaned")
        for descriptor in (stage_fd, parent_fd):
            with self.subTest(descriptor=descriptor), self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_unowned_finalized_stage_releases_finalization_descriptors(self) -> None:
        declared = self.fixture.write("SKILL.md", b"durable bytes")
        adapter = _FinalizationAdapter()

        result = stage_declared_candidate(self.fixture.request((declared,)), fs=adapter)

        self.assertEqual(result.status, "staged")
        for descriptor in (adapter.stage_fd, adapter.staging_parent_fd):
            with self.subTest(descriptor=descriptor), self.assertRaises(OSError):
                os.fstat(descriptor)

    def test_each_required_stage_sync_failure_cleans_without_returning_a_lease(self) -> None:
        for failure in ("nested", "stage-root", "staging-parent"):
            with self.subTest(failure=failure):
                fixture = _Fixture()
                try:
                    declared = fixture.write("nested/SKILL.md", b"durable bytes")
                    adapter = _FinalizationAdapter(fail_at=failure)
                    sentinel = fixture.target / "must-remain-untouched"
                    sentinel.write_bytes(b"unchanged")

                    outcome = stage_declared_candidate_owned(
                        fixture.request((declared,)), fs=adapter
                    )

                    self.assertEqual(outcome.result.status, "failed")
                    self.assertIsNone(outcome.lease)
                    self.assertEqual(adapter.cleanup_calls, 1)
                    self.assertEqual(tuple(fixture.staging.iterdir()), ())
                    self.assertEqual(sentinel.read_bytes(), b"unchanged")
                    for descriptor in (adapter.stage_fd, adapter.staging_parent_fd):
                        with self.assertRaises(OSError):
                            os.fstat(descriptor)
                finally:
                    fixture.close()

    def test_stage_sync_failure_preserves_existing_cleanup_required_semantics(self) -> None:
        declared = self.fixture.write("SKILL.md", b"durable bytes")
        adapter = _FinalizationAdapter(fail_at="stage-root", cleanup_fails=True)

        outcome = stage_declared_candidate_owned(self.fixture.request((declared,)), fs=adapter)

        self.assertEqual(outcome.result.status, "cleanup-required")
        self.assertIsNone(outcome.lease)
        self.assertEqual(adapter.cleanup_calls, 1)
        self.assertEqual(len(tuple(self.fixture.staging.iterdir())), 1)

    def test_exact_three_file_staging_sorted_manifest_and_undeclared_exclusion(self) -> None:
        values = {"z.txt": b"z", "nested/a.txt": b"aa", "m.txt": b"mmm"}
        declared = tuple(self.fixture.write(path, data) for path, data in values.items())
        self.fixture.write("undeclared.txt", b"do not copy")
        result = stage_declared_candidate(self.fixture.request(reversed(declared)))
        self.assertEqual(result.status, "staged")
        self.assertEqual(result.total_bytes, sum(map(len, values.values())))
        expected_records = tuple(
            (path, _sha256(data), len(data)) for path, data in sorted(values.items())
        )
        self.assertEqual(result.manifest_digest, _manifest_digest(expected_records))
        stage = next(self.fixture.staging.iterdir())
        actual = sorted(
            item.relative_to(stage).as_posix() for item in stage.rglob("*") if item.is_file()
        )
        self.assertEqual(actual, sorted(values))
        self.assertFalse((stage / "undeclared.txt").exists())

    def test_manifest_digest_is_repeatable_and_independent_of_declared_order(self) -> None:
        first = self.fixture.write("a", b"a")
        second = self.fixture.write("b", b"b")
        one = stage_declared_candidate(self.fixture.request((second, first)))
        two = stage_declared_candidate(self.fixture.request((first, second)))
        self.assertEqual(one.status, "staged")
        self.assertEqual(two.status, "staged")
        self.assertEqual(one.manifest_digest, two.manifest_digest)
        self.assertNotEqual(one.stage_id, two.stage_id)

    def test_exact_single_file_bound_is_allowed_and_plus_one_rejected(self) -> None:
        exact_data = b"x" * MAX_SINGLE_FILE_BYTES
        exact = self.fixture.write("exact", exact_data)
        result = stage_declared_candidate(self.fixture.request((exact,)))
        self.assertEqual(result.status, "staged")
        oversized_data = exact_data + b"x"
        oversized = self.fixture.write("oversized", oversized_data)
        result = stage_declared_candidate(self.fixture.request((oversized,)))
        self.assertEqual(result.status, "rejected")
        self.assertIn("phase5e.fs.resource.single-file-limit", result.reason_ids)

    def test_exact_aggregate_bound_is_allowed_and_plus_one_rejected(self) -> None:
        payload = b"x" * MAX_SINGLE_FILE_BYTES
        exact = tuple(self.fixture.write("exact/%d" % index, payload) for index in range(8))
        result = stage_declared_candidate(self.fixture.request(exact))
        self.assertEqual(result.status, "staged")
        self.assertEqual(result.total_bytes, MAX_TOTAL_CANDIDATE_BYTES)
        plus_one = self.fixture.write("exact/extra", b"x")
        result = stage_declared_candidate(self.fixture.request(exact + (plus_one,)))
        self.assertEqual(result.status, "rejected")
        self.assertIn("phase5e.fs.resource.total-limit", result.reason_ids)

    def test_copy_chunk_bound_and_partial_writes(self) -> None:
        payload = b"x" * (COPY_CHUNK_BYTES * 2 + 1)
        declared = self.fixture.write("data", payload)
        adapter = _FaultAdapter("partial-write")
        result = stage_declared_candidate(self.fixture.request((declared,)), fs=adapter)
        self.assertEqual(result.status, "staged")
        self.assertLessEqual(adapter.maximum_read_size, COPY_CHUNK_BYTES)

    def test_hash_mismatch_rejects_and_cleans_stage(self) -> None:
        self.fixture.write("data", b"actual")
        declared = DeclaredFile("data", _sha256(b"declared"))
        result = stage_declared_candidate(self.fixture.request((declared,)))
        self.assertEqual(result.status, "rejected")
        self.assertIn("phase5e.fs.source.hash-mismatch", result.reason_ids)
        self.assertEqual(tuple(self.fixture.staging.iterdir()), ())

    def test_source_metadata_change_fails_closed_and_cleans_stage(self) -> None:
        declared = self.fixture.write("data", b"content")
        result = stage_declared_candidate(
            self.fixture.request((declared,)), fs=_FaultAdapter("source-changed")
        )
        self.assertEqual(result.status, "rejected")
        self.assertIn("phase5e.fs.source.changed", result.reason_ids)
        self.assertEqual(tuple(self.fixture.staging.iterdir()), ())

    def test_second_pass_hash_extra_and_missing_are_detected(self) -> None:
        for fault in (
            "verify-hash",
            "verify-extra",
            "verify-missing",
            "verify-symlink",
            "verify-hardlink",
            "verify-special",
        ):
            with self.subTest(fault=fault):
                fixture = _Fixture()
                try:
                    declared = fixture.write("data", b"content")
                    result = stage_declared_candidate(
                        fixture.request((declared,)), fs=_FaultAdapter(fault)
                    )
                    self.assertNotEqual(result.status, "staged")
                    self.assertEqual(tuple(fixture.staging.iterdir()), ())
                finally:
                    fixture.close()

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unavailable")
    def test_source_symlink_and_symlink_ancestry_are_rejected(self) -> None:
        target = self.fixture.source / "target"
        target.write_bytes(b"data")
        os.symlink("target", self.fixture.source / "link")
        result = stage_declared_candidate(
            self.fixture.request((DeclaredFile("link", _sha256(b"data")),))
        )
        self.assertEqual(result.status, "rejected")
        outside = self.fixture.root / "outside"
        outside.mkdir()
        unsafe_source = self.fixture.root / "unsafe-source"
        os.symlink(outside, unsafe_source)
        result = stage_declared_candidate(
            self.fixture.request((DeclaredFile("x", _sha256(b"x")),), source_root=unsafe_source)
        )
        self.assertEqual(result.status, "rejected")

    @unittest.skipUnless(hasattr(os, "link"), "hardlink unavailable")
    def test_hardlinked_source_is_rejected(self) -> None:
        original = self.fixture.source / "original"
        original.write_bytes(b"data")
        os.link(original, self.fixture.source / "linked")
        result = stage_declared_candidate(
            self.fixture.request((DeclaredFile("linked", _sha256(b"data")),))
        )
        self.assertEqual(result.status, "rejected")
        self.assertEqual(tuple(self.fixture.staging.iterdir()), ())

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO unavailable")
    def test_fifo_source_is_rejected_without_blocking(self) -> None:
        os.mkfifo(self.fixture.source / "pipe")
        result = stage_declared_candidate(
            self.fixture.request((DeclaredFile("pipe", _sha256(b"")),))
        )
        self.assertEqual(result.status, "rejected")
        self.assertEqual(tuple(self.fixture.staging.iterdir()), ())

    def test_source_stage_overlap_is_rejected_before_stage_creation(self) -> None:
        nested = self.fixture.source / "staging"
        nested.mkdir()
        declared = self.fixture.write("data", b"data")
        result = stage_declared_candidate(
            self.fixture.request((declared,), staging_parent=nested)
        )
        self.assertEqual(result.status, "rejected")
        self.assertEqual(tuple(nested.iterdir()), ())

    @unittest.skipUnless(hasattr(os, "symlink"), "symlink unavailable")
    def test_unsafe_stage_ancestry_is_rejected_before_mutation(self) -> None:
        real_stage = self.fixture.root / "real-stage"
        real_stage.mkdir()
        unsafe = self.fixture.root / "unsafe-stage"
        os.symlink(real_stage, unsafe)
        declared = self.fixture.write("data", b"data")
        result = stage_declared_candidate(
            self.fixture.request((declared,), staging_parent=unsafe)
        )
        self.assertEqual(result.status, "rejected")
        self.assertEqual(tuple(real_stage.iterdir()), ())

    def test_windows_real_adapter_rejects_before_any_stage_mutation(self) -> None:
        declared = self.fixture.write("data", b"data")
        result = stage_declared_candidate(
            self.fixture.request((declared,)), fs=RealFilesystemAdapter(platform_name="win32")
        )
        self.assertEqual(result.status, "rejected")
        self.assertIn("phase5e.fs.platform.unsupported", result.reason_ids)
        self.assertIn(WINDOWS_LIMITATION, result.limitations)
        self.assertEqual(tuple(self.fixture.staging.iterdir()), ())

    def test_faults_are_deterministic_and_cleanup_owned_stage(self) -> None:
        after_stage = ("source-open", "source-read", "file-create", "file-write", "verify-failure")
        before_stage = ("ancestor-open", "stage-mkdir")
        for fault in before_stage + after_stage:
            with self.subTest(fault=fault):
                fixture = _Fixture()
                try:
                    declared = fixture.write("data", b"data")
                    first = stage_declared_candidate(fixture.request((declared,)), fs=_FaultAdapter(fault))
                    second = stage_declared_candidate(fixture.request((declared,)), fs=_FaultAdapter(fault))
                    self.assertEqual(first.status, "failed")
                    self.assertEqual(first.status, second.status)
                    self.assertEqual(first.reason_ids, second.reason_ids)
                    self.assertEqual(tuple(fixture.staging.iterdir()), ())
                finally:
                    fixture.close()

    def test_cleanup_failure_is_explicit_cleanup_required(self) -> None:
        declared = self.fixture.write("data", b"data")
        result = stage_declared_candidate(
            self.fixture.request((declared,)), fs=_FaultAdapter("file-write", cleanup_fails=True)
        )
        self.assertEqual(result.status, "cleanup-required")
        self.assertIn("phase5e.fs.cleanup.required", result.reason_ids)
        self.assertEqual(len(tuple(self.fixture.staging.iterdir())), 1)

    def test_stage_substitution_is_detected_and_never_reported_staged(self) -> None:
        declared = self.fixture.write("data", b"data")
        result = stage_declared_candidate(
            self.fixture.request((declared,)), fs=_FaultAdapter("stage-substitution")
        )
        self.assertEqual(result.status, "cleanup-required")
        self.assertNotEqual(result.status, "staged")

    def test_target_is_never_mutated(self) -> None:
        sentinel = self.fixture.target / "sentinel"
        sentinel.write_bytes(b"unchanged")
        declared = self.fixture.write("data", b"data")
        result = stage_declared_candidate(self.fixture.request((declared,)))
        self.assertEqual(result.status, "staged")
        self.assertEqual(sentinel.read_bytes(), b"unchanged")
        self.assertEqual([item.name for item in self.fixture.target.iterdir()], ["sentinel"])

    def test_result_is_metadata_only_and_does_not_disclose_inputs_or_exceptions(self) -> None:
        secret = b"opaque-private-value"
        declared = self.fixture.write("private/data", secret)
        request = self.fixture.request((declared,), candidate_key="private-candidate")
        result = stage_declared_candidate(request)
        serialized = json.dumps(asdict(result), sort_keys=True)
        prohibited = (
            str(self.fixture.source),
            str(self.fixture.staging),
            declared.relative_path,
            declared.sha256,
            "private-candidate",
            secret.decode("ascii"),
            str(Path.home()),
        )
        for value in prohibited:
            self.assertNotIn(value, serialized)
        self.assertEqual(set(result.reason_ids).difference(REASON_IDS), set())

    def test_module_has_no_policy_remote_process_or_execution_imports(self) -> None:
        module = Path(__file__).parents[1] / "skill_orchestrator" / "transactional_fs.py"
        tree = ast.parse(module.read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module)
        forbidden = {
            "subprocess",
            "socket",
            "urllib",
            "requests",
            "candidate_install_plan",
            "execution_handoff",
            "admission",
            "registry_trust",
            "registry_trust_policy",
            "capability_policy",
        }
        self.assertFalse(imports.intersection(forbidden))


class WindowsFailClosedTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows real-adapter contract")
    def test_real_windows_adapter_rejects_before_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "source"
            staging = root / "staging"
            source.mkdir()
            staging.mkdir()
            payload = b"data"
            (source / "data").write_bytes(payload)
            request = StageRequest(
                source_root=source,
                staging_parent=staging,
                candidate_key="demo-skill",
                declared_files=(DeclaredFile("data", _sha256(payload)),),
                limits=ExecutionLimits(),
            )
            result = stage_declared_candidate(request)
            self.assertEqual(result.status, "rejected")
            self.assertIn("phase5e.fs.platform.unsupported", result.reason_ids)
            self.assertIn(WINDOWS_LIMITATION, result.limitations)
            self.assertEqual(tuple(staging.iterdir()), ())


if __name__ == "__main__":
    unittest.main()

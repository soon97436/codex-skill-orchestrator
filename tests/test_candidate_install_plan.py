import copy
import inspect
import json
import unittest
from unittest.mock import patch

try:
    from test_execution_handoff import CAPABILITIES, _case
except ImportError:  # pragma: no cover - package-style discovery fallback
    from tests.test_execution_handoff import CAPABILITIES, _case

from skill_orchestrator.candidate_install_plan import (
    LIMITATION_IDS,
    MAX_DECLARED_FILES,
    MAX_PATH_DEPTH,
    MAX_RELATIVE_PATH_UTF8_BYTES,
    MAX_SEGMENT_UTF8_BYTES,
    PLAN_STATUSES,
    evaluate_candidate_install_plan,
)


def _plan(values=None, **overrides):
    values = copy.deepcopy(values or _case())
    values.update(overrides)
    return evaluate_candidate_install_plan(**values)


def _entry(values=None):
    return copy.deepcopy((values or _case())["registry_entry"])


def _values_for_entry(entry):
    return _case(registry_entry=entry)


def _ready_patch():
    return patch(
        "skill_orchestrator.candidate_install_plan.evaluate_execution_handoff",
        return_value={"status": "ready"},
    )


class CandidateInstallPlanTests(unittest.TestCase):
    def test_public_interface_is_keyword_only_and_does_not_accept_detached_handoff(self):
        parameters = inspect.signature(evaluate_candidate_install_plan).parameters
        self.assertTrue(
            all(parameter.kind == inspect.Parameter.KEYWORD_ONLY for parameter in parameters.values())
        )
        self.assertNotIn("handoff", parameters)
        self.assertNotIn("handoff_result", parameters)
        self.assertNotIn("source_root", parameters)
        self.assertNotIn("destination", parameters)
        self.assertNotIn("transaction_id", parameters)
        with self.assertRaises(TypeError):
            _plan(handoff={"status": "ready"})

    def test_happy_bundled_current_fresh_granted_is_planned(self):
        result = _plan()
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["operation"], "install")
        self.assertEqual(result["target_class"], "registry-skill-user-scope")
        self.assertEqual(result["source_type"], "bundled")
        self.assertEqual(result["file_count"], 1)
        self.assertEqual(result["execution_status"], "not-performed")
        self.assertIn("phase5e.plan.planned", result["reason_ids"])

    def test_result_shape_and_resource_limits_are_exact(self):
        result = _plan()
        self.assertEqual(
            list(result),
            [
                "schema_version",
                "status",
                "assessment_scope",
                "operation",
                "target_class",
                "source_type",
                "file_count",
                "resource_limits",
                "execution_status",
                "reason_ids",
                "limitations",
                "truncated",
            ],
        )
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["assessment_scope"], "phase5e-candidate-install-plan")
        self.assertEqual(
            result["resource_limits"],
            {
                "max_declared_files": 64,
                "max_relative_path_utf8_bytes": 240,
                "max_path_depth": 16,
                "max_segment_utf8_bytes": 100,
            },
        )
        self.assertFalse(result["truncated"])
        self.assertEqual(set(result["limitations"]), set(LIMITATION_IDS[:-1]))

    def test_plan_status_vocabulary_is_closed(self):
        self.assertEqual(PLAN_STATUSES, ("planned", "rejected", "unknown", "invalid"))
        self.assertIn(_plan()["status"], PLAN_STATUSES)

    def test_handoff_rejected_unknown_invalid_map_without_planning(self):
        cases = (
            ("denied", "rejected", "phase5e.plan.handoff.rejected"),
            ("not-provided", "unknown", "phase5e.plan.handoff.unknown"),
            ("bogus", "invalid", "phase5e.plan.handoff.invalid"),
        )
        for authorization, status, reason in cases:
            with self.subTest(authorization=authorization):
                result = _plan(fresh_operator_authorization=authorization)
                self.assertEqual(result["status"], status)
                self.assertIn(reason, result["reason_ids"])
                self.assertNotIn("phase5e.plan.planned", result["reason_ids"])

    def test_stale_binding_is_rejected(self):
        values = _case()
        values["registry_entry"]["version"] = "9.9.9"
        result = _plan(values)
        self.assertEqual(result["status"], "rejected")
        self.assertIn("phase5e.plan.handoff.rejected", result["reason_ids"])

    def test_detached_ready_input_is_not_an_accepted_interface(self):
        with self.assertRaises(TypeError):
            evaluate_candidate_install_plan(
                **_case(),
                handoff_result={"status": "ready"},
            )

    def test_only_install_and_exact_target_are_plannable(self):
        result = _plan(operation="activate")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["operation"], "activate")
        result = _plan(target_class="workspace")
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["target_class"], "workspace")

    def test_git_source_is_rejected_as_unsupported(self):
        entry = _entry()
        entry["source"] = {
            "type": "git",
            "path": "router/synthetic-skill",
            "repository": "https://example.invalid/repo.git",
            "revision": "v1.2.3",
        }
        values = _case(registry_entry=entry)
        with _ready_patch():
            result = _plan(values)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["source_type"], "git")
        self.assertIn("phase5e.plan.source.unsupported", result["reason_ids"])

    def test_bundled_repository_or_revision_contradiction_is_invalid(self):
        values = _case()
        values["registry_entry"]["source"]["repository"] = "https://example.invalid/repo.git"
        with _ready_patch():
            result = _plan(values)
        self.assertEqual(result["status"], "invalid")
        self.assertIn("phase5e.plan.source.invalid", result["reason_ids"])

    def test_malformed_source_is_invalid(self):
        values = _case()
        values["registry_entry"]["source"] = "not-an-object"
        result = _plan(values)
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["source_type"], "invalid")

    def test_declared_manifest_must_be_nonempty_exact_and_hashed(self):
        cases = (
            ([], "phase5e.plan.manifest.invalid"),
            (None, "phase5e.plan.manifest.invalid"),
            ([{"path": "SKILL.md"}], "phase5e.plan.manifest.invalid"),
            ([{"path": "SKILL.md", "sha256": "A" * 64}], "phase5e.plan.manifest.invalid"),
            ([{"path": "SKILL.md", "sha256": "z" * 64}], "phase5e.plan.manifest.invalid"),
            ([{"path": "SKILL.md", "sha256": "a" * 64, "extra": 1}], "phase5e.plan.manifest.invalid"),
        )
        for files, reason in cases:
            with self.subTest(files=files):
                entry = _entry()
                entry["files"] = files
                values = _case(registry_entry=entry)
                with _ready_patch():
                    result = _plan(values)
                self.assertEqual(result["status"], "invalid")
                self.assertIn(reason, result["reason_ids"])

    def test_exact_duplicate_manifest_path_is_invalid(self):
        entry = _entry()
        entry["files"] = [
            {"path": "SKILL.md", "sha256": "a" * 64},
            {"path": "SKILL.md", "sha256": "b" * 64},
        ]
        values = _case(registry_entry=entry)
        with _ready_patch():
            result = _plan(values)
        self.assertEqual(result["status"], "invalid")
        self.assertIn("phase5e.plan.manifest.duplicate", result["reason_ids"])

    def test_casefold_and_unicode_collisions_are_rejected(self):
        for files in (
            [
                {"path": "Readme.md", "sha256": "a" * 64},
                {"path": "README.md", "sha256": "b" * 64},
            ],
            [
                {"path": "café.md", "sha256": "a" * 64},
                {"path": "CAFÉ.md", "sha256": "b" * 64},
            ],
        ):
            with self.subTest(files=files):
                entry = _entry()
                entry["files"] = files
                values = _case(registry_entry=entry)
                with _ready_patch():
                    result = _plan(values)
                self.assertEqual(result["status"], "rejected")
                self.assertIn("phase5e.plan.manifest.collision", result["reason_ids"])

    def test_portable_path_contract_rejects_unsafe_source_paths(self):
        unsafe = (
            "/absolute/path",
            "C:/drive-form",
            "dir\\file",
            "dir//file",
            "dir/./file",
            "dir/../file",
            "dir/file.",
            "dir/file ",
            "dir/CON.txt",
            "dir/NUL",
            "dir/a:b",
            "dir/\x00file",
            "cafe\u0301/file",
        )
        for path in unsafe:
            with self.subTest(path=path):
                entry = _entry()
                entry["source"]["path"] = path
                values = _case(registry_entry=entry)
                with _ready_patch():
                    result = _plan(values)
                self.assertEqual(result["status"], "rejected")
                self.assertIn("phase5e.plan.source-path.unsafe", result["reason_ids"])

    def test_portable_manifest_paths_reject_unsafe_paths(self):
        unsafe = ("/file", "C:/file", "a\\b", "a//b", "a/./b", "a/../b", "a/b.", "a/b ", "a/PRN")
        for path in unsafe:
            with self.subTest(path=path):
                entry = _entry()
                entry["files"] = [{"path": path, "sha256": "a" * 64}]
                values = _case(registry_entry=entry)
                with _ready_patch():
                    result = _plan(values)
                self.assertEqual(result["status"], "rejected")
                self.assertIn("phase5e.plan.source-path.unsafe", result["reason_ids"])

    def test_manifest_file_limit_64_passes_and_65_is_rejected(self):
        files = [
            {"path": "files/%02d.txt" % index, "sha256": "a" * 64}
            for index in range(MAX_DECLARED_FILES)
        ]
        entry = _entry()
        entry["files"] = files
        values = _case(registry_entry=entry)
        with _ready_patch():
            result = _plan(values)
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["file_count"], MAX_DECLARED_FILES)

        too_many = copy.deepcopy(files) + [{"path": "files/64.txt", "sha256": "a" * 64}]
        entry["files"] = too_many
        values = _case(registry_entry=entry)
        with _ready_patch():
            result = _plan(values)
        self.assertEqual(result["status"], "rejected")
        self.assertIn("phase5e.plan.resource-limit", result["reason_ids"])

    def test_path_length_depth_and_segment_limits_are_rejected(self):
        cases = (
            "a" * MAX_RELATIVE_PATH_UTF8_BYTES,
            "/".join("x" for _ in range(MAX_PATH_DEPTH + 1)),
            "a" * (MAX_SEGMENT_UTF8_BYTES + 1),
        )
        for path in cases:
            with self.subTest(path=path):
                entry = _entry()
                entry["source"]["path"] = path
                values = _case(registry_entry=entry)
                with _ready_patch():
                    result = _plan(values)
                self.assertEqual(result["status"], "rejected")
                self.assertIn("phase5e.plan.source-path.unsafe", result["reason_ids"])

    def test_exact_path_bound_is_accepted_when_valid(self):
        path = "a" * 100 + "/" + "b" * 100 + "/" + "c" * 38
        entry = _entry()
        entry["source"]["path"] = path
        values = _case(registry_entry=entry)
        with _ready_patch():
            result = _plan(values)
        self.assertEqual(result["status"], "planned")

    def test_repeated_runs_and_manifest_order_are_deterministic(self):
        first = _plan()
        second = _plan()
        self.assertEqual(first, second)
        entry = _entry()
        entry["files"] = [
            {"path": "z.txt", "sha256": "a" * 64},
            {"path": "a.txt", "sha256": "b" * 64},
        ]
        values = _case(registry_entry=entry)
        with _ready_patch():
            forward = _plan(values)
        entry["files"].reverse()
        values = _case(registry_entry=entry)
        with _ready_patch():
            reverse = _plan(values)
        self.assertEqual(forward, reverse)

    def test_planned_result_explicitly_does_not_verify_byte_sizes(self):
        result = _plan()
        self.assertIn("phase5e.plan.limit.source-bytes-not-verified", result["limitations"])
        self.assertIn("phase5e.plan.limit.byte-size-not-verified", result["limitations"])
        serialized = json.dumps(result, sort_keys=True)
        self.assertNotIn("byte_size", serialized)

    def test_metadata_only_result_does_not_echo_sensitive_inputs(self):
        secret = "super-secret-value"
        path = "private/%s.txt" % secret
        entry = _entry()
        entry["source"]["path"] = path
        entry["files"] = [{"path": path, "sha256": "a" * 64}]
        values = _case(registry_entry=entry)
        with _ready_patch():
            result = _plan(values)
        serialized = json.dumps(result, sort_keys=True)
        for forbidden in (secret, path, "a" * 64, "https://example.invalid"):
            self.assertNotIn(forbidden, serialized)

    def test_live_handoff_is_called_with_complete_evidence_and_exact_request(self):
        with patch(
            "skill_orchestrator.candidate_install_plan.evaluate_execution_handoff",
            return_value={"status": "ready"},
        ) as handoff:
            _plan()
        handoff.assert_called_once()
        called = handoff.call_args.kwargs
        for key in (
            "stored_binding",
            "registry_schema_version",
            "registry_entry",
            "trust_profile_schema_version",
            "trust_policy",
            "trust_evidence",
            "capability_policy",
            "capability_declaration",
            "requested_capabilities",
            "trust_decision",
            "capability_decision",
            "recommendation_decision",
            "installation_decision",
            "operation",
            "target_class",
            "fresh_operator_authorization",
        ):
            self.assertIn(key, called)

    def test_no_external_effect_imports(self):
        import skill_orchestrator.candidate_install_plan as module

        source = inspect.getsource(module)
        for forbidden in (
            "import os",
            "import pathlib",
            "import subprocess",
            "import socket",
            "import urllib",
            "import requests",
            "import shutil",
            "import tempfile",
            "import time",
            "import datetime",
            "import random",
            "import secrets",
            "import uuid",
            "from .engine",
            "from .cli",
            "from .installer",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()

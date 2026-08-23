import ast
import importlib.util
import json
from pathlib import Path
import re
import unittest

from skill_orchestrator import (
    acceptance_criteria,
    completion_gate,
    task_readiness,
    workflow_selection,
)
from skill_orchestrator.completion_gate import evaluate_completion_gate


ROOT = Path(__file__).resolve().parents[1]


def load_rc_gate_module():
    module_path = ROOT / "scripts" / "phase4_rc_gate.py"
    spec = importlib.util.spec_from_file_location("phase4_rc_gate", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load Phase 4 RC gate")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Phase4IntegrationTests(unittest.TestCase):
    def test_invalid_envelope_propagates_without_semantic_upgrade(self) -> None:
        result = evaluate_completion_gate("untrusted task text")

        self.assertEqual(result["status"], "invalid")
        self.assertIsNone(result["selected_workflow_profile"])

    def test_upstream_states_propagate_without_semantic_upgrade(self) -> None:
        cases = (
            ({"workflow_request": {}}, "needs-input"),
            (
                {"workflow_request": {"workflow_kind": "unknown"}},
                "invalid",
            ),
            (
                {"workflow_request": {"workflow_kind": "review"}},
                "needs-input",
            ),
            (
                {
                    "workflow_request": {
                        "workflow_kind": "review",
                        "task_input": object(),
                    }
                },
                "invalid",
            ),
            (
                {
                    "workflow_request": {
                        "workflow_kind": "change",
                        "task_input": "change task",
                    }
                },
                "needs-input",
            ),
            (
                {
                    "workflow_request": {
                        "workflow_kind": "change",
                        "task_input": "change task",
                        "criteria_input": {"criteria": "invalid"},
                    }
                },
                "invalid",
            ),
        )
        for request, expected_status in cases:
            with self.subTest(expected_status=expected_status):
                result = evaluate_completion_gate(request)
                self.assertEqual(result["status"], expected_status)
                self.assertIsNone(result["selected_workflow_profile"])

    def test_optional_criteria_policy_is_preserved_for_review_and_inspect(self) -> None:
        for workflow_kind in ("review", "inspect"):
            with self.subTest(workflow_kind=workflow_kind, criteria="absent"):
                result = evaluate_completion_gate(
                    {
                        "workflow_request": {
                            "workflow_kind": workflow_kind,
                            "task_input": "task",
                        }
                    }
                )
                self.assertEqual(result["status"], "incomplete")
                self.assertEqual(
                    result["selected_workflow_profile"],
                    {"profile_id": "workflow-" + workflow_kind},
                )
            with self.subTest(workflow_kind=workflow_kind, criteria="malformed"):
                result = evaluate_completion_gate(
                    {
                        "workflow_request": {
                            "workflow_kind": workflow_kind,
                            "task_input": "task",
                            "criteria_input": {"criteria": "invalid"},
                        }
                    }
                )
                self.assertEqual(result["status"], "invalid")
                self.assertIsNone(result["selected_workflow_profile"])

    def test_caller_explicit_workflow_ignores_task_language_and_semantics(self) -> None:
        cases = (
            (
                "change",
                "Please review this code without changing it.",
                {"criteria": [{"id": "ac-1", "statement": "works"}]},
                "workflow-change",
            ),
            (
                "review",
                "請修復並立即實作這個功能。",
                None,
                "workflow-review",
            ),
            (
                "inspect",
                "この機能を実装して作成してください。",
                None,
                "workflow-inspect",
            ),
        )
        for workflow_kind, task, criteria_input, profile_id in cases:
            workflow_request = {
                "workflow_kind": workflow_kind,
                "task_input": task,
            }
            if criteria_input is not None:
                workflow_request["criteria_input"] = criteria_input
            result = evaluate_completion_gate(
                {"workflow_request": workflow_request}
            )
            with self.subTest(workflow_kind=workflow_kind):
                self.assertEqual(
                    result["selected_workflow_profile"],
                    {"profile_id": profile_id},
                )
                self.assertEqual(result["status"], "incomplete")

    def test_repository_signal_order_is_canonical_and_unverified(self) -> None:
        base = {
            "workflow_kind": "review",
            "task_input": "task",
        }
        first = evaluate_completion_gate(
            {
                "workflow_request": {
                    **base,
                    "repository_signals": [
                        "repository.tests-present",
                        "repository.ci-present",
                    ],
                }
            }
        )
        second = evaluate_completion_gate(
            {
                "workflow_request": {
                    **base,
                    "repository_signals": [
                        "repository.ci-present",
                        "repository.tests-present",
                    ],
                }
            }
        )

        self.assertEqual(first, second)
        self.assertEqual(
            [item["requirement_id"] for item in first["requirements"]],
            [
                "workflow.requirement.review-evidence",
                "workflow.requirement.test-evidence",
                "workflow.requirement.ci-evidence",
            ],
        )
        self.assertIn(
            "workflow.limit.repository-signals-not-verified",
            [item["reason_id"] for item in first["limitations"]],
        )
        individual_requirements = {
            "repository.tests-present": "workflow.requirement.test-evidence",
            "repository.ci-present": "workflow.requirement.ci-evidence",
        }
        for signal_id, requirement_id in individual_requirements.items():
            result = evaluate_completion_gate(
                {
                    "workflow_request": {
                        **base,
                        "repository_signals": [signal_id],
                    }
                }
            )
            with self.subTest(signal_id=signal_id):
                self.assertIn(
                    requirement_id,
                    [item["requirement_id"] for item in result["requirements"]],
                )

    def test_missing_negative_and_inconclusive_evidence_remain_incomplete(self) -> None:
        base_request = {
            "workflow_kind": "review",
            "task_input": "task",
            "repository_signals": [
                "repository.tests-present",
                "repository.ci-present",
            ],
        }
        review = {
            "evidence_id": "ev-review",
            "requirement_id": "workflow.requirement.review-evidence",
            "evidence_kind": "completion.evidence.review",
            "outcome": "observed",
        }
        cases = (
            ([], "missing"),
            (
                [
                    review,
                    {
                        "evidence_id": "ev-test",
                        "requirement_id": "workflow.requirement.test-evidence",
                        "evidence_kind": "completion.evidence.test",
                        "outcome": "fail",
                    },
                ],
                "negative",
            ),
            (
                [
                    review,
                    {
                        "evidence_id": "ev-ci",
                        "requirement_id": "workflow.requirement.ci-evidence",
                        "evidence_kind": "completion.evidence.ci",
                        "outcome": "fail",
                    },
                ],
                "negative",
            ),
            (
                [
                    {
                        **review,
                        "outcome": "inconclusive",
                    }
                ],
                "inconclusive",
            ),
        )
        for evidence, expected_state in cases:
            result = evaluate_completion_gate(
                {"workflow_request": base_request, "evidence": evidence}
            )
            with self.subTest(expected_state=expected_state):
                self.assertEqual(result["status"], "incomplete")
                self.assertIn(
                    expected_state,
                    {item["state"] for item in result["uncovered_requirements"]},
                )

    def test_not_observed_workflow_evidence_remains_incomplete(self) -> None:
        contracts = (
            (
                "change",
                "workflow.requirement.change-evidence",
                "completion.evidence.change",
                {"criteria": [{"id": "ac-1", "statement": "works"}]},
            ),
            (
                "review",
                "workflow.requirement.review-evidence",
                "completion.evidence.review",
                None,
            ),
            (
                "inspect",
                "workflow.requirement.inspection-evidence",
                "completion.evidence.inspection",
                None,
            ),
        )
        for workflow_kind, requirement_id, evidence_kind, criteria_input in contracts:
            workflow_request = {
                "workflow_kind": workflow_kind,
                "task_input": "task",
            }
            if criteria_input is not None:
                workflow_request["criteria_input"] = criteria_input
            result = evaluate_completion_gate(
                {
                    "workflow_request": workflow_request,
                    "evidence": [
                        {
                            "evidence_id": "ev-1",
                            "requirement_id": requirement_id,
                            "evidence_kind": evidence_kind,
                            "outcome": "not-observed",
                        }
                    ],
                }
            )
            with self.subTest(workflow_kind=workflow_kind):
                self.assertEqual(result["status"], "incomplete")
                self.assertEqual(
                    result["uncovered_requirements"][-1]["state"],
                    "negative",
                )

    def test_malformed_duplicate_and_conflicting_evidence_fail_closed(self) -> None:
        workflow_request = {
            "workflow_kind": "review",
            "task_input": "task",
            "repository_signals": ["repository.tests-present"],
        }
        review = {
            "evidence_id": "ev-review",
            "requirement_id": "workflow.requirement.review-evidence",
            "evidence_kind": "completion.evidence.review",
            "outcome": "observed",
        }
        test_pass = {
            "evidence_id": "ev-test-a",
            "requirement_id": "workflow.requirement.test-evidence",
            "evidence_kind": "completion.evidence.test",
            "outcome": "pass",
        }
        test_fail = {
            **test_pass,
            "evidence_id": "ev-test-b",
            "outcome": "fail",
        }
        malformed = evaluate_completion_gate(
            {"workflow_request": workflow_request, "evidence": [{}]}
        )
        duplicate = evaluate_completion_gate(
            {
                "workflow_request": workflow_request,
                "evidence": [review, test_pass, {**test_pass, "evidence_id": "ev-test-b"}],
            }
        )
        conflict = evaluate_completion_gate(
            {
                "workflow_request": workflow_request,
                "evidence": [review, test_pass, test_fail],
            }
        )
        reversed_conflict = evaluate_completion_gate(
            {
                "workflow_request": workflow_request,
                "evidence": list(reversed([review, test_pass, test_fail])),
            }
        )

        self.assertEqual(malformed["status"], "invalid")
        self.assertEqual(duplicate["status"], "invalid")
        self.assertEqual(conflict["status"], "invalid")
        self.assertEqual(conflict, reversed_conflict)
        self.assertEqual(
            duplicate["reasons"][-1]["reason_id"],
            "completion.evidence.duplicate",
        )
        self.assertEqual(
            conflict["reasons"][-1]["reason_id"],
            "completion.evidence.conflict",
        )

    def test_complete_change_case_is_only_structural_evidence_coverage(self) -> None:
        result = evaluate_completion_gate(
            {
                "workflow_request": {
                    "workflow_kind": "change",
                    "task_input": "task",
                    "criteria_input": {
                        "criteria": [{"id": "ac-1", "statement": "works"}]
                    },
                    "repository_signals": [
                        "repository.ci-present",
                        "repository.tests-present",
                    ],
                },
                "evidence": [
                    {
                        "evidence_id": "ev-change",
                        "requirement_id": "workflow.requirement.change-evidence",
                        "evidence_kind": "completion.evidence.change",
                        "outcome": "observed",
                    },
                    {
                        "evidence_id": "ev-test",
                        "requirement_id": "workflow.requirement.test-evidence",
                        "evidence_kind": "completion.evidence.test",
                        "outcome": "pass",
                    },
                    {
                        "evidence_id": "ev-ci",
                        "requirement_id": "workflow.requirement.ci-evidence",
                        "evidence_kind": "completion.evidence.ci",
                        "outcome": "pass",
                    },
                ],
            }
        )
        rendered = json.dumps(result, ensure_ascii=False, sort_keys=True)

        self.assertEqual(result["status"], "evidence-complete")
        self.assertEqual(result["uncovered_requirements"], [])
        self.assertIn(
            "completion.limit.evidence-not-independently-verified",
            [item["reason_id"] for item in result["limitations"]],
        )
        self.assertIn(
            "completion.limit.execution-not-performed",
            [item["reason_id"] for item in result["limitations"]],
        )
        for unsupported_claim in (
            "correctness",
            "approval",
            "ready-to-merge",
            "executed",
        ):
            self.assertNotIn('"' + unsupported_claim + '"', rendered)

    def test_integrated_privacy_does_not_echo_untrusted_content(self) -> None:
        sentinel = "PRIVATE_SENTINEL_TOKEN_123456789"
        request = {
            "workflow_request": {
                "workflow_kind": "change",
                "task_input": sentinel + " rm -rf /private/host/path",
                "criteria_input": {
                    "criteria": [
                        {
                            "id": "ac-1",
                            "statement": sentinel + " https://private.invalid/data",
                        }
                    ]
                },
            },
            "evidence": [
                {
                    "evidence_id": "INVALID_RAW_ID_" + sentinel,
                    "requirement_id": "workflow.requirement.change-evidence",
                    "evidence_kind": "completion.evidence.change",
                    "outcome": "observed",
                }
            ],
        }

        result = evaluate_completion_gate(request)
        rendered = json.dumps(result, ensure_ascii=False, sort_keys=True)

        self.assertEqual(result["status"], "invalid")
        for forbidden in (
            sentinel,
            "rm -rf",
            "/private/host/path",
            "https://private.invalid/data",
            "INVALID_RAW_ID",
        ):
            self.assertNotIn(forbidden, rendered)

    def test_integrated_product_modules_have_no_external_effect_dependencies(self) -> None:
        forbidden_imports = {
            "os",
            "pathlib",
            "subprocess",
            "socket",
            "requests",
            "urllib",
            "http",
            "importlib",
        }
        forbidden_calls = {"open", "exec", "eval", "compile", "__import__"}
        for module in (
            task_readiness,
            acceptance_criteria,
            workflow_selection,
            completion_gate,
        ):
            source = Path(module.__file__).read_text(encoding="utf-8")
            tree = ast.parse(source)
            imported_roots = set()
            direct_calls = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_roots.update(
                        alias.name.split(".")[0] for alias in node.names
                    )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported_roots.add(node.module.split(".")[0])
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    direct_calls.add(node.func.id)
            with self.subTest(module=module.__name__):
                self.assertFalse(imported_roots & forbidden_imports)
                self.assertFalse(direct_calls & forbidden_calls)
                source_tokens = set(
                    re.findall(r"[a-z0-9_-]+", source.casefold())
                )
                self.assertFalse(source_tokens & {"mcp", "llm", "agents"})
                self.assertNotIn("shell=True", source.replace(" ", ""))

    def test_integrated_result_is_deterministic_and_canonicalizable(self) -> None:
        request = {
            "workflow_request": {
                "workflow_kind": "review",
                "task_input": "task",
                "repository_signals": [
                    "repository.ci-present",
                    "repository.tests-present",
                ],
            },
            "evidence": [
                {
                    "evidence_id": "ev-test",
                    "requirement_id": "workflow.requirement.test-evidence",
                    "evidence_kind": "completion.evidence.test",
                    "outcome": "pass",
                },
                {
                    "evidence_id": "ev-review",
                    "requirement_id": "workflow.requirement.review-evidence",
                    "evidence_kind": "completion.evidence.review",
                    "outcome": "observed",
                },
                {
                    "evidence_id": "ev-ci",
                    "requirement_id": "workflow.requirement.ci-evidence",
                    "evidence_kind": "completion.evidence.ci",
                    "outcome": "pass",
                },
            ],
        }
        reversed_request = {
            "workflow_request": {
                **request["workflow_request"],
                "repository_signals": list(
                    reversed(request["workflow_request"]["repository_signals"])
                ),
            },
            "evidence": list(reversed(request["evidence"])),
        }

        first = evaluate_completion_gate(request)
        second = evaluate_completion_gate(reversed_request)
        first_bytes = (
            json.dumps(first, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")
        second_bytes = (
            json.dumps(second, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            + "\n"
        ).encode("utf-8")

        self.assertEqual(first, second)
        self.assertEqual(first_bytes, second_bytes)
        self.assertNotIn(b"\r", first_bytes)
        self.assertTrue(first_bytes.endswith(b"\n"))


class Phase4ReleaseCandidateGateTests(unittest.TestCase):
    def test_gate_order_and_canonical_serialization_are_fixed(self) -> None:
        rc_gate = load_rc_gate_module()
        statuses = {gate_id: "pass" for gate_id in rc_gate.RC_GATE_IDS}

        payload = rc_gate.build_payload("a" * 40, "b" * 40, statuses)
        encoded = rc_gate.canonical_json_bytes(payload)

        self.assertEqual(
            [entry["gate_id"] for entry in payload["gates"]],
            list(rc_gate.RC_GATE_IDS),
        )
        self.assertEqual(payload["status"], "pass")
        self.assertEqual(json.loads(encoded.decode("utf-8")), payload)
        self.assertFalse(encoded.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", encoded)
        self.assertTrue(encoded.endswith(b"\n"))
        self.assertFalse(encoded.endswith(b"\n\n"))

    def test_identity_and_exit_code_contracts_are_fixed(self) -> None:
        rc_gate = load_rc_gate_module()

        self.assertEqual(rc_gate.EXIT_PASS, 0)
        self.assertEqual(rc_gate.EXIT_USAGE, 2)
        self.assertEqual(rc_gate.EXIT_VALIDATION, 3)
        self.assertEqual(rc_gate.EXIT_INTERNAL, 4)
        self.assertTrue(rc_gate.is_full_sha("a" * 40))
        for invalid in ("a" * 39, "A" * 40, "main", "v0.1.0", "g" * 40):
            with self.subTest(invalid=invalid):
                self.assertFalse(rc_gate.is_full_sha(invalid))

    def test_canonical_payload_excludes_execution_metadata(self) -> None:
        rc_gate = load_rc_gate_module()
        statuses = {gate_id: "not-run" for gate_id in rc_gate.RC_GATE_IDS}
        statuses[rc_gate.RC_GATE_IDS[0]] = "fail"

        payload = rc_gate.build_payload("a" * 40, "b" * 40, statuses)
        rendered = rc_gate.canonical_json_bytes(payload).decode("utf-8")

        self.assertEqual(payload["status"], "fail")
        for forbidden in (
            "platform",
            "hostname",
            "username",
            "absolute_path",
            "temporary_path",
            "timestamp",
            "duration",
            "python_executable",
            "test_timing",
        ):
            self.assertNotIn('"' + forbidden + '"', rendered)

    def test_harness_subprocess_and_network_contract_is_static(self) -> None:
        source = (ROOT / "scripts" / "phase4_rc_gate.py").read_text(
            encoding="utf-8"
        )
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg == "shell":
                    self.assertIsInstance(keyword.value, ast.Constant)
                    self.assertIs(keyword.value.value, False)
        self.assertNotIn("shell=True", source.replace(" ", ""))
        self.assertNotIn("https://", source)
        self.assertNotIn("http://", source)
        self.assertIn('"clone",', source)
        self.assertIn('"--no-local",', source)
        self.assertIn('"--no-checkout",', source)


if __name__ == "__main__":
    unittest.main()

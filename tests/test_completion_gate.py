import ast
import json
from pathlib import Path
import unittest

from skill_orchestrator import completion_gate
from skill_orchestrator.completion_gate import evaluate_completion_gate


class CompletionGateTests(unittest.TestCase):
    def test_non_dict_request_fails_closed(self) -> None:
        self.assertEqual(
            evaluate_completion_gate("invalid"),
            {
                "schema_version": 1,
                "status": "invalid",
                "assessment_scope": "deterministic-completion-evidence",
                "selected_workflow_profile": None,
                "requirements": [],
                "covered_requirements": [],
                "uncovered_requirements": [],
                "reasons": [
                    {
                        "reason_id": "completion.request.invalid",
                        "evidence_ref": {
                            "source": "completion-request",
                            "identity": {"state": "invalid"},
                        },
                    }
                ],
                "limitations": [
                    {
                        "reason_id": "completion.limit.workflow-intent-not-independently-verified"
                    },
                    {"reason_id": "completion.limit.execution-not-performed"},
                    {
                        "reason_id": "completion.limit.semantic-correctness-not-evaluated"
                    },
                    {
                        "reason_id": (
                            "completion.limit.tas"
                            "k-intent-completeness-not-evaluated"
                        )
                    },
                ],
                "truncated": False,
            },
        )

    def test_unknown_top_level_field_fails_before_descent(self) -> None:
        result = evaluate_completion_gate(
            {"workflow_request": object(), "workflow_report": object()}
        )

        self.assertEqual(result["status"], "invalid")
        self.assertEqual(
            [reason["reason_id"] for reason in result["reasons"]],
            ["completion.request.invalid"],
        )

    def test_missing_workflow_request_needs_input(self) -> None:
        result = evaluate_completion_gate({})

        self.assertEqual(result["status"], "needs-input")
        self.assertIsNone(result["selected_workflow_profile"])
        self.assertEqual(
            [reason["reason_id"] for reason in result["reasons"]],
            ["completion.workflow.needs-input"],
        )

    def test_phase4c_blocked_statuses_are_recomputed_and_propagated(self) -> None:
        cases = (
            ({"workflow_kind": "unknown"}, "invalid", "completion.workflow.invalid"),
            ({}, "needs-input", "completion.workflow.needs-input"),
        )
        for workflow_request, status, reason_id in cases:
            with self.subTest(status=status):
                result = evaluate_completion_gate(
                    {"workflow_request": workflow_request}
                )
                self.assertEqual(result["status"], status)
                self.assertEqual(result["reasons"][0]["reason_id"], reason_id)
                self.assertIsNone(result["selected_workflow_profile"])

    def test_selected_review_without_evidence_is_incomplete(self) -> None:
        result = evaluate_completion_gate(
            {
                "workflow_request": {
                    "workflow_kind": "review",
                    "task_input": "review task",
                }
            }
        )

        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(
            result["selected_workflow_profile"],
            {"profile_id": "workflow-review"},
        )
        self.assertEqual(
            result["requirements"],
            [{"requirement_id": "workflow.requirement.review-evidence"}],
        )
        self.assertEqual(result["covered_requirements"], [])
        self.assertEqual(
            result["uncovered_requirements"],
            [
                {
                    "requirement_id": "workflow.requirement.review-evidence",
                    "state": "missing",
                }
            ],
        )
        self.assertEqual(
            [reason["reason_id"] for reason in result["reasons"]],
            [
                "completion.workflow.selected",
                "completion.requirement.missing",
                "completion.gate.incomplete",
            ],
        )
        self.assertNotIn(
            "completion.limit.evidence-not-independently-verified",
            [item["reason_id"] for item in result["limitations"]],
        )

    def test_acceptance_criteria_is_structurally_covered_upstream(self) -> None:
        result = evaluate_completion_gate(
            {
                "workflow_request": {
                    "workflow_kind": "change",
                    "task_input": "change task",
                    "criteria_input": {
                        "criteria": [{"id": "ac-1", "statement": "works"}]
                    },
                }
            }
        )

        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(
            result["covered_requirements"],
            [
                {
                    "requirement_id": "workflow.requirement.acceptance-criteria"
                }
            ],
        )
        self.assertEqual(
            result["uncovered_requirements"],
            [
                {
                    "requirement_id": "workflow.requirement.change-evidence",
                    "state": "missing",
                }
            ],
        )
        self.assertEqual(
            [reason["reason_id"] for reason in result["reasons"]],
            [
                "completion.workflow.selected",
                "completion.requirement.covered",
                "completion.requirement.missing",
                "completion.gate.incomplete",
            ],
        )

    def test_evidence_container_is_bounded_and_strict(self) -> None:
        base = {
            "workflow_request": {
                "workflow_kind": "review",
                "task_input": "review task",
            }
        }
        for evidence in ({}, "evidence", [None] * 33):
            with self.subTest(evidence_type=type(evidence).__name__):
                result = evaluate_completion_gate({**base, "evidence": evidence})
                self.assertEqual(result["status"], "invalid")
                self.assertEqual(
                    result["reasons"][-1]["reason_id"],
                    "completion.evidence.invalid",
                )
                self.assertFalse(result["truncated"])

    def test_positive_review_evidence_completes_the_structural_gate(self) -> None:
        result = evaluate_completion_gate(
            {
                "workflow_request": {
                    "workflow_kind": "review",
                    "task_input": "review task",
                },
                "evidence": [
                    {
                        "evidence_id": "ev-1",
                        "requirement_id": "workflow.requirement.review-evidence",
                        "evidence_kind": "completion.evidence.review",
                        "outcome": "observed",
                    }
                ],
            }
        )

        self.assertEqual(result["status"], "evidence-complete")
        self.assertEqual(
            result["covered_requirements"],
            [{"requirement_id": "workflow.requirement.review-evidence"}],
        )
        self.assertEqual(result["uncovered_requirements"], [])
        self.assertEqual(
            [reason["reason_id"] for reason in result["reasons"]],
            [
                "completion.workflow.selected",
                "completion.requirement.covered",
                "completion.gate.evidence-complete",
            ],
        )
        self.assertEqual(
            result["reasons"][1]["evidence_ref"]["identity"],
            {
                "evidence_id": "ev-1",
                "requirement_id": "workflow.requirement.review-evidence",
                "evidence_kind": "completion.evidence.review",
                "outcome": "observed",
            },
        )
        self.assertIn(
            "completion.limit.evidence-not-independently-verified",
            [item["reason_id"] for item in result["limitations"]],
        )

    def test_negative_and_inconclusive_review_evidence_are_incomplete(self) -> None:
        cases = (
            ("not-observed", "negative", "completion.requirement.negative"),
            ("inconclusive", "inconclusive", "completion.requirement.inconclusive"),
        )
        for outcome, state, reason_id in cases:
            with self.subTest(outcome=outcome):
                result = evaluate_completion_gate(
                    {
                        "workflow_request": {
                            "workflow_kind": "review",
                            "task_input": "review task",
                        },
                        "evidence": [
                            {
                                "evidence_id": "ev-1",
                                "requirement_id": "workflow.requirement.review-evidence",
                                "evidence_kind": "completion.evidence.review",
                                "outcome": outcome,
                            }
                        ],
                    }
                )
                self.assertEqual(result["status"], "incomplete")
                self.assertEqual(
                    result["uncovered_requirements"],
                    [
                        {
                            "requirement_id": "workflow.requirement.review-evidence",
                            "state": state,
                        }
                    ],
                )
                self.assertEqual(result["reasons"][1]["reason_id"], reason_id)

    def test_all_evidence_kinds_use_requirement_specific_outcomes(self) -> None:
        cases = (
            (
                "change",
                [],
                "workflow.requirement.change-evidence",
                "completion.evidence.change",
                "observed",
                "not-observed",
            ),
            (
                "review",
                [],
                "workflow.requirement.review-evidence",
                "completion.evidence.review",
                "observed",
                "not-observed",
            ),
            (
                "inspect",
                [],
                "workflow.requirement.inspection-evidence",
                "completion.evidence.inspection",
                "observed",
                "not-observed",
            ),
            (
                "review",
                ["repository.tests-present"],
                "workflow.requirement.test-evidence",
                "completion.evidence.test",
                "pass",
                "fail",
            ),
            (
                "review",
                ["repository.ci-present"],
                "workflow.requirement.ci-evidence",
                "completion.evidence.ci",
                "pass",
                "fail",
            ),
        )
        for workflow_kind, signals, requirement_id, evidence_kind, positive, negative in cases:
            for outcome, expected_status in (
                (positive, "evidence-complete"),
                (negative, "incomplete"),
                ("inconclusive", "incomplete"),
            ):
                with self.subTest(evidence_kind=evidence_kind, outcome=outcome):
                    workflow_request = {
                        "workflow_kind": workflow_kind,
                        "task_input": "task",
                        "repository_signals": signals,
                    }
                    if workflow_kind == "change":
                        workflow_request["criteria_input"] = {
                            "criteria": [{"id": "ac-1", "statement": "works"}]
                        }
                    evidence = []
                    if signals:
                        evidence.append(
                            {
                                "evidence_id": "ev-base",
                                "requirement_id": "workflow.requirement.review-evidence",
                                "evidence_kind": "completion.evidence.review",
                                "outcome": "observed",
                            }
                        )
                    evidence.append(
                        {
                            "evidence_id": "ev-target",
                            "requirement_id": requirement_id,
                            "evidence_kind": evidence_kind,
                            "outcome": outcome,
                        }
                    )
                    result = evaluate_completion_gate(
                        {"workflow_request": workflow_request, "evidence": evidence}
                    )
                    self.assertEqual(result["status"], expected_status)
                    target = next(
                        (
                            item
                            for item in result["uncovered_requirements"]
                            if item["requirement_id"] == requirement_id
                        ),
                        None,
                    )
                    if expected_status == "evidence-complete":
                        self.assertIsNone(target)
                    else:
                        self.assertEqual(
                            target["state"],
                            "negative" if outcome == negative else "inconclusive",
                        )

    def test_malformed_or_unexpected_evidence_fails_closed_without_echo(self) -> None:
        valid = {
            "evidence_id": "ev-1",
            "requirement_id": "workflow.requirement.review-evidence",
            "evidence_kind": "completion.evidence.review",
            "outcome": "observed",
        }
        invalid_records = (
            None,
            {},
            {key: value for key, value in valid.items() if key != "outcome"},
            {**valid, "content": "SECRET-CONTENT"},
            {**valid, "evidence_id": 1},
            {**valid, "evidence_id": ""},
            {**valid, "evidence_id": "EV-UPPER"},
            {**valid, "evidence_id": "機密"},
            {**valid, "evidence_id": "\ud800"},
            {**valid, "evidence_id": "x" * 65},
            {**valid, "requirement_id": "SECRET-UNKNOWN-REQUIREMENT"},
            {**valid, "evidence_kind": "SECRET-UNKNOWN-KIND"},
            {**valid, "outcome": "SECRET-UNKNOWN-OUTCOME"},
            {
                **valid,
                "evidence_kind": "completion.evidence.test",
                "outcome": "pass",
            },
            {
                **valid,
                "requirement_id": "workflow.requirement.acceptance-criteria",
                "evidence_kind": "completion.evidence.change",
            },
            {
                **valid,
                "requirement_id": "workflow.requirement.test-evidence",
                "evidence_kind": "completion.evidence.test",
                "outcome": "pass",
            },
        )
        for record in invalid_records:
            with self.subTest(record_type=type(record).__name__):
                result = evaluate_completion_gate(
                    {
                        "workflow_request": {
                            "workflow_kind": "review",
                            "task_input": "task",
                        },
                        "evidence": [record],
                    }
                )
                self.assertEqual(result["status"], "invalid")
                self.assertEqual(
                    result["reasons"][-1]["reason_id"],
                    "completion.evidence.invalid",
                )
                rendered = repr(result)
                for secret in (
                    "SECRET-CONTENT",
                    "SECRET-UNKNOWN-REQUIREMENT",
                    "SECRET-UNKNOWN-KIND",
                    "SECRET-UNKNOWN-OUTCOME",
                    "機密",
                ):
                    self.assertNotIn(secret, rendered)

    def test_evidence_id_exact_byte_bound_is_valid(self) -> None:
        evidence_id = "x" * 64
        result = evaluate_completion_gate(
            {
                "workflow_request": {
                    "workflow_kind": "review",
                    "task_input": "task",
                },
                "evidence": [
                    {
                        "evidence_id": evidence_id,
                        "requirement_id": "workflow.requirement.review-evidence",
                        "evidence_kind": "completion.evidence.review",
                        "outcome": "observed",
                    }
                ],
            }
        )

        self.assertEqual(result["status"], "evidence-complete")

    def test_duplicate_and_conflicting_evidence_fail_deterministically(self) -> None:
        base_record = {
            "requirement_id": "workflow.requirement.test-evidence",
            "evidence_kind": "completion.evidence.test",
        }
        cases = (
            (
                [
                    {**base_record, "evidence_id": "ev-1", "outcome": "pass"},
                    {**base_record, "evidence_id": "ev-1", "outcome": "fail"},
                ],
                "completion.evidence.duplicate",
            ),
            (
                [
                    {**base_record, "evidence_id": "ev-1", "outcome": "pass"},
                    {**base_record, "evidence_id": "ev-2", "outcome": "pass"},
                ],
                "completion.evidence.duplicate",
            ),
            (
                [
                    {**base_record, "evidence_id": "ev-1", "outcome": "fail"},
                    {**base_record, "evidence_id": "ev-2", "outcome": "fail"},
                ],
                "completion.evidence.duplicate",
            ),
            (
                [
                    {**base_record, "evidence_id": "ev-1", "outcome": "pass"},
                    {**base_record, "evidence_id": "ev-2", "outcome": "fail"},
                ],
                "completion.evidence.conflict",
            ),
            (
                [
                    {**base_record, "evidence_id": "ev-1", "outcome": "pass"},
                    {
                        **base_record,
                        "evidence_id": "ev-2",
                        "outcome": "inconclusive",
                    },
                ],
                "completion.evidence.conflict",
            ),
        )
        workflow_request = {
            "workflow_kind": "review",
            "task_input": "task",
            "repository_signals": ["repository.tests-present"],
        }
        review_record = {
            "evidence_id": "ev-review",
            "requirement_id": "workflow.requirement.review-evidence",
            "evidence_kind": "completion.evidence.review",
            "outcome": "observed",
        }
        for records, reason_id in cases:
            with self.subTest(reason_id=reason_id):
                first = evaluate_completion_gate(
                    {
                        "workflow_request": workflow_request,
                        "evidence": [review_record, *records],
                    }
                )
                second = evaluate_completion_gate(
                    {
                        "workflow_request": workflow_request,
                        "evidence": list(reversed([review_record, *records])),
                    }
                )
                self.assertEqual(first, second)
                self.assertEqual(first["status"], "invalid")
                self.assertEqual(first["reasons"][-1]["reason_id"], reason_id)

    def test_requirement_order_and_repository_provenance_are_preserved(self) -> None:
        workflow_request = {
            "workflow_kind": "change",
            "task_input": "task",
            "criteria_input": {
                "criteria": [{"id": "ac-1", "statement": "works"}]
            },
            "repository_signals": [
                "repository.ci-present",
                "repository.tests-present",
            ],
        }
        evidence = [
            {
                "evidence_id": "ev-ci",
                "requirement_id": "workflow.requirement.ci-evidence",
                "evidence_kind": "completion.evidence.ci",
                "outcome": "pass",
            },
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
        ]
        first = evaluate_completion_gate(
            {"workflow_request": workflow_request, "evidence": evidence}
        )
        second = evaluate_completion_gate(
            {
                "workflow_request": workflow_request,
                "evidence": list(reversed(evidence)),
            }
        )

        self.assertEqual(first, second)
        self.assertEqual(first["status"], "evidence-complete")
        expected_requirements = [
            "workflow.requirement.acceptance-criteria",
            "workflow.requirement.change-evidence",
            "workflow.requirement.test-evidence",
            "workflow.requirement.ci-evidence",
        ]
        self.assertEqual(
            [item["requirement_id"] for item in first["requirements"]],
            expected_requirements,
        )
        self.assertEqual(
            [item["requirement_id"] for item in first["covered_requirements"]],
            expected_requirements,
        )
        self.assertEqual(
            [item["reason_id"] for item in first["limitations"]],
            [
                "completion.limit.workflow-intent-not-independently-verified",
                "completion.limit.execution-not-performed",
                "completion.limit.semantic-correctness-not-evaluated",
                "completion.limit.tas" "k-intent-completeness-not-evaluated",
                "completion.limit.evidence-not-independently-verified",
                "workflow.limit.repository-signals-not-verified",
            ],
        )

    def test_public_contract_allowlists_bounds_and_key_order_are_fixed(self) -> None:
        self.assertEqual(completion_gate.__all__, ["evaluate_completion_gate"])
        self.assertEqual(completion_gate.MAX_COMPLETION_EVIDENCE, 32)
        self.assertEqual(completion_gate.MAX_COMPLETION_REASONS, 64)
        self.assertEqual(completion_gate.MAX_COMPLETION_REQUIREMENTS, 16)
        self.assertEqual(completion_gate.MAX_COMPLETION_LIMITATIONS, 16)
        self.assertEqual(completion_gate.MAX_EVIDENCE_ID_BYTES, 64)
        self.assertEqual(
            completion_gate.COMPLETION_REASON_IDS,
            frozenset(
                {
                    "completion.request.invalid",
                    "completion.workflow.needs-input",
                    "completion.workflow.invalid",
                    "completion.workflow.selected",
                    "completion.evidence.invalid",
                    "completion.evidence.duplicate",
                    "completion.evidence.conflict",
                    "completion.requirement.covered",
                    "completion.requirement.missing",
                    "completion.requirement.negative",
                    "completion.requirement.inconclusive",
                    "completion.gate.evidence-complete",
                    "completion.gate.incomplete",
                }
            ),
        )
        self.assertEqual(
            completion_gate.COMPLETION_LIMITATION_IDS,
            frozenset(
                {
                    "completion.limit.workflow-intent-not-independently-verified",
                    "completion.limit.execution-not-performed",
                    "completion.limit.semantic-correctness-not-evaluated",
                    "completion.limit.tas"
                    "k-intent-completeness-not-evaluated",
                    "completion.limit.evidence-not-independently-verified",
                    "workflow.limit.repository-signals-not-verified",
                }
            ),
        )
        result = evaluate_completion_gate({})
        self.assertEqual(
            list(result),
            [
                "schema_version",
                "status",
                "assessment_scope",
                "selected_workflow_profile",
                "requirements",
                "covered_requirements",
                "uncovered_requirements",
                "reasons",
                "limitations",
                "truncated",
            ],
        )

    def test_invalid_evidence_validation_precedence_is_order_independent(self) -> None:
        malformed = {"evidence_id": "broken"}
        extra = {
            "evidence_id": "ev-extra",
            "requirement_id": "workflow.requirement.test-evidence",
            "evidence_kind": "completion.evidence.test",
            "outcome": "pass",
        }
        request = {
            "workflow_request": {
                "workflow_kind": "review",
                "task_input": "task",
            }
        }
        first = evaluate_completion_gate(
            {**request, "evidence": [malformed, extra]}
        )
        second = evaluate_completion_gate(
            {**request, "evidence": [extra, malformed]}
        )

        self.assertEqual(first, second)
        self.assertEqual(first["status"], "invalid")
        self.assertEqual(
            first["reasons"][-1]["evidence_ref"]["identity"],
            {"state": "invalid-record"},
        )

    def test_output_is_content_free_and_makes_no_correctness_claim(self) -> None:
        secret = "PHASE4D-SECRET-SENTINEL"
        result = evaluate_completion_gate(
            {
                "workflow_request": {
                    "workflow_kind": "change",
                    "task_input": secret + " /private/path; rm -rf sentinel",
                    "criteria_input": {
                        "criteria": [
                            {
                                "id": "ac-1",
                                "statement": secret + " https://private.invalid/log",
                            }
                        ]
                    },
                },
                "evidence": [
                    {
                        "evidence_id": "ev-1",
                        "requirement_id": "workflow.requirement.change-evidence",
                        "evidence_kind": "completion.evidence.change",
                        "outcome": "observed",
                    }
                ],
            }
        )
        rendered = json.dumps(result, ensure_ascii=False, sort_keys=True)

        self.assertEqual(result["status"], "evidence-complete")
        for forbidden in (
            secret,
            "/private/path",
            "rm -rf",
            "https://private.invalid/log",
            '"approved"',
            '"correct"',
            '"satisfied"',
            '"tests_passed"',
            '"review_passed"',
            '"inspection_passed"',
            '"evidence_complete"',
        ):
            self.assertNotIn(forbidden, rendered)

    def test_module_has_no_execution_or_external_effect_dependencies(self) -> None:
        source = Path(completion_gate.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported_roots = set()
        direct_calls = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                direct_calls.add(node.func.id)

        self.assertFalse(
            imported_roots
            & {
                "os",
                "pathlib",
                "subprocess",
                "socket",
                "requests",
                "urllib",
                "http",
                "importlib",
            }
        )
        self.assertFalse(
            direct_calls & {"open", "exec", "eval", "compile", "__import__"}
        )
        for forbidden_reference in (
            "required_skill_ids",
            "recommended_skill_ids",
            "route_task",
            "analyze_project",
            "skill install",
            "skill activation",
        ):
            self.assertNotIn(forbidden_reference, source)

    def test_absent_none_and_empty_evidence_are_equivalent(self) -> None:
        workflow_request = {
            "workflow_kind": "review",
            "task_input": "task",
        }
        absent = evaluate_completion_gate(
            {"workflow_request": workflow_request}
        )
        explicit_none = evaluate_completion_gate(
            {"workflow_request": workflow_request, "evidence": None}
        )
        explicit_empty = evaluate_completion_gate(
            {"workflow_request": workflow_request, "evidence": []}
        )

        self.assertEqual(absent, explicit_none)
        self.assertEqual(absent, explicit_empty)

    def test_each_required_evidence_field_is_enforced(self) -> None:
        valid = {
            "evidence_id": "ev-1",
            "requirement_id": "workflow.requirement.review-evidence",
            "evidence_kind": "completion.evidence.review",
            "outcome": "observed",
        }
        for missing_field in valid:
            with self.subTest(missing_field=missing_field):
                record = {
                    key: value
                    for key, value in valid.items()
                    if key != missing_field
                }
                result = evaluate_completion_gate(
                    {
                        "workflow_request": {
                            "workflow_kind": "review",
                            "task_input": "task",
                        },
                        "evidence": [record],
                    }
                )
                self.assertEqual(result["status"], "invalid")
                self.assertEqual(
                    result["reasons"][-1]["reason_id"],
                    "completion.evidence.invalid",
                )

    def test_caller_cannot_supply_phase4c_report_or_requirements(self) -> None:
        for forbidden_field in (
            "workflow_report",
            "requirements",
            "selected_profile",
        ):
            with self.subTest(forbidden_field=forbidden_field):
                result = evaluate_completion_gate(
                    {
                        "workflow_request": {
                            "workflow_kind": "review",
                            "task_input": "task",
                        },
                        forbidden_field: [],
                    }
                )
                self.assertEqual(result["status"], "invalid")
                self.assertEqual(
                    result["reasons"][0]["reason_id"],
                    "completion.request.invalid",
                )

    def test_blocked_upstream_preserves_repository_provenance(self) -> None:
        result = evaluate_completion_gate(
            {
                "workflow_request": {
                    "workflow_kind": "review",
                    "repository_signals": ["repository.tests-present"],
                }
            }
        )

        self.assertEqual(result["status"], "needs-input")
        self.assertEqual(
            result["limitations"][-1]["reason_id"],
            "workflow.limit.repository-signals-not-verified",
        )

    def test_all_public_results_are_bounded_allowlisted_and_deterministic(self) -> None:
        review_request = {
            "workflow_kind": "review",
            "task_input": "task",
        }
        evidence = {
            "evidence_id": "ev-1",
            "requirement_id": "workflow.requirement.review-evidence",
            "evidence_kind": "completion.evidence.review",
            "outcome": "observed",
        }
        requests = (
            None,
            {},
            {"workflow_request": {"workflow_kind": "unknown"}},
            {"workflow_request": review_request},
            {"workflow_request": review_request, "evidence": [evidence]},
            {
                "workflow_request": review_request,
                "evidence": [{**evidence, "outcome": "not-observed"}],
            },
            {"workflow_request": review_request, "evidence": [{}]},
        )
        expected_keys = [
            "schema_version",
            "status",
            "assessment_scope",
            "selected_workflow_profile",
            "requirements",
            "covered_requirements",
            "uncovered_requirements",
            "reasons",
            "limitations",
            "truncated",
        ]
        for request in requests:
            with self.subTest(request_type=type(request).__name__):
                first = evaluate_completion_gate(request)
                second = evaluate_completion_gate(request)
                self.assertEqual(first, second)
                self.assertEqual(list(first), expected_keys)
                self.assertIn(
                    first["status"],
                    {"evidence-complete", "incomplete", "needs-input", "invalid"},
                )
                self.assertFalse(first["truncated"])
                self.assertLessEqual(
                    len(first["requirements"]),
                    completion_gate.MAX_COMPLETION_REQUIREMENTS,
                )
                self.assertLessEqual(
                    len(first["reasons"]),
                    completion_gate.MAX_COMPLETION_REASONS,
                )
                self.assertLessEqual(
                    len(first["limitations"]),
                    completion_gate.MAX_COMPLETION_LIMITATIONS,
                )
                self.assertLessEqual(
                    {item["reason_id"] for item in first["reasons"]},
                    completion_gate.COMPLETION_REASON_IDS,
                )
                self.assertLessEqual(
                    {item["reason_id"] for item in first["limitations"]},
                    completion_gate.COMPLETION_LIMITATION_IDS,
                )


if __name__ == "__main__":
    unittest.main()

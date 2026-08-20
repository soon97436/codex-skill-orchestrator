import ast
import json
from pathlib import Path
import unittest
from unittest import mock

from skill_orchestrator import workflow_selection
from skill_orchestrator.workflow_selection import select_workflow_profile


class WorkflowSelectionTests(unittest.TestCase):
    def test_non_dict_request_fails_closed(self) -> None:
        self.assertEqual(
            select_workflow_profile("change"),
            {
                "schema_version": 1,
                "status": "invalid",
                "assessment_scope": "deterministic-workflow-selection",
                "selected_workflow_profile": None,
                "requirements": [],
                "reasons": [
                    {
                        "reason_id": "workflow.request.invalid",
                        "evidence_ref": {
                            "source": "workflow-request",
                            "identity": {"state": "invalid"},
                        },
                    }
                ],
                "limitations": [
                    {
                        "reason_id": "workflow.limit.semantic-intent-not-inferred"
                    },
                    {"reason_id": "workflow.limit.execution-not-performed"},
                    {"reason_id": "workflow.limit.completion-not-evaluated"},
                ],
                "truncated": False,
            },
        )

    def test_unknown_request_field_fails_before_descent(self) -> None:
        result = select_workflow_profile(
            {
                "workflow_kind": "change",
                "task_input": "task",
                "criteria_input": {"criteria": []},
                "unexpected": object(),
            }
        )

        self.assertEqual(result["status"], "invalid")
        self.assertEqual(
            [reason["reason_id"] for reason in result["reasons"]],
            ["workflow.request.invalid"],
        )

    def test_missing_workflow_kind_needs_input(self) -> None:
        result = select_workflow_profile({})

        self.assertEqual(result["status"], "needs-input")
        self.assertIsNone(result["selected_workflow_profile"])
        self.assertEqual(
            [reason["reason_id"] for reason in result["reasons"]],
            ["workflow.intent.missing"],
        )

    def test_invalid_workflow_kinds_fail_closed_without_echo(self) -> None:
        invalid_values = (1, "unknown", "Change", "REVIEW", " inspect", "修正", "レビュー")
        for invalid_value in invalid_values:
            with self.subTest(workflow_kind=invalid_value):
                result = select_workflow_profile({"workflow_kind": invalid_value})
                self.assertEqual(result["status"], "invalid")
                self.assertEqual(
                    [reason["reason_id"] for reason in result["reasons"]],
                    ["workflow.intent.invalid"],
                )
                if type(invalid_value) is str:
                    self.assertNotIn(invalid_value, str(result))

    def test_valid_change_selects_one_profile_with_stable_contract(self) -> None:
        result = select_workflow_profile(
            {
                "workflow_kind": "change",
                "task_input": "task",
                "criteria_input": {
                    "criteria": [{"id": "ac-1", "statement": "works"}]
                },
            }
        )

        self.assertEqual(result["status"], "selected")
        self.assertEqual(
            result["selected_workflow_profile"],
            {"profile_id": "workflow-change"},
        )
        self.assertEqual(
            [item["requirement_id"] for item in result["requirements"]],
            [
                "workflow.requirement.acceptance-criteria",
                "workflow.requirement.change-evidence",
            ],
        )
        self.assertEqual(
            [reason["reason_id"] for reason in result["reasons"]],
            [
                "workflow.intent.explicit",
                "workflow.task.structurally-ready",
                "workflow.criteria.structurally-valid",
                "workflow.profile.selected",
            ],
        )
        self.assertEqual(
            [item["reason_id"] for item in result["limitations"]],
            [
                "workflow.limit.semantic-intent-not-inferred",
                "workflow.limit.execution-not-performed",
                "workflow.limit.completion-not-evaluated",
            ],
        )
        self.assertFalse(result["truncated"])

    def test_task_language_never_changes_explicit_workflow_kind(self) -> None:
        cases = (
            ("change", "review this", "workflow-change"),
            ("change", "幫我檢查", "workflow-change"),
            ("change", "レビューしてください", "workflow-change"),
            ("review", "fix login", "workflow-review"),
            ("inspect", "implement feature", "workflow-inspect"),
        )
        criteria = {"criteria": [{"id": "ac-1", "statement": "works"}]}
        for workflow_kind, task_input, profile_id in cases:
            with self.subTest(workflow_kind=workflow_kind, task_input=task_input):
                result = select_workflow_profile(
                    {
                        "workflow_kind": workflow_kind,
                        "task_input": task_input,
                        "criteria_input": criteria,
                    }
                )
                self.assertEqual(result["status"], "selected")
                self.assertEqual(
                    result["selected_workflow_profile"],
                    {"profile_id": profile_id},
                )

    def test_phase4a_statuses_gate_selection(self) -> None:
        criteria = {"criteria": [{"id": "ac-1", "statement": "works"}]}
        cases = (
            ({}, "needs-input", "workflow.task.needs-input"),
            ({"task_input": object()}, "invalid", "workflow.task.invalid"),
            ({"task_input": "task"}, "selected", "workflow.task.structurally-ready"),
        )
        for task_fields, status, task_reason in cases:
            with self.subTest(status=status):
                result = select_workflow_profile(
                    {
                        "workflow_kind": "change",
                        "criteria_input": criteria,
                        **task_fields,
                    }
                )
                self.assertEqual(result["status"], status)
                self.assertIn(
                    task_reason,
                    [reason["reason_id"] for reason in result["reasons"]],
                )

    def test_caller_supplied_phase4a_report_is_not_accepted(self) -> None:
        result = select_workflow_profile(
            {
                "workflow_kind": "change",
                "task_readiness": {"status": "structurally-ready"},
            }
        )

        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["reasons"][0]["reason_id"], "workflow.request.invalid")

    def test_phase4b_policy_depends_only_on_explicit_workflow_kind(self) -> None:
        valid = {"criteria": [{"id": "ac-1", "statement": "works"}]}
        invalid = {"criteria": "not-a-list"}
        change_cases = (
            ({}, "needs-input", "workflow.criteria.needs-input"),
            ({"criteria_input": None}, "needs-input", "workflow.criteria.needs-input"),
            ({"criteria_input": {"criteria": []}}, "needs-input", "workflow.criteria.needs-input"),
            ({"criteria_input": invalid}, "invalid", "workflow.criteria.invalid"),
            ({"criteria_input": valid}, "selected", "workflow.criteria.structurally-valid"),
        )
        for criteria_fields, status, criteria_reason in change_cases:
            with self.subTest(workflow_kind="change", status=status):
                result = select_workflow_profile(
                    {
                        "workflow_kind": "change",
                        "task_input": "task",
                        **criteria_fields,
                    }
                )
                self.assertEqual(result["status"], status)
                self.assertIn(
                    criteria_reason,
                    [reason["reason_id"] for reason in result["reasons"]],
                )

        optional_cases = (
            ({}, "selected", "workflow.criteria.not-required"),
            ({"criteria_input": None}, "selected", "workflow.criteria.not-required"),
            ({"criteria_input": {"criteria": []}}, "selected", "workflow.criteria.not-required"),
            ({"criteria_input": invalid}, "invalid", "workflow.criteria.invalid"),
            ({"criteria_input": valid}, "selected", "workflow.criteria.structurally-valid"),
        )
        for workflow_kind in ("review", "inspect"):
            for criteria_fields, status, criteria_reason in optional_cases:
                with self.subTest(workflow_kind=workflow_kind, status=status):
                    result = select_workflow_profile(
                        {
                            "workflow_kind": workflow_kind,
                            "task_input": "task",
                            **criteria_fields,
                        }
                    )
                    self.assertEqual(result["status"], status)
                    self.assertIn(
                        criteria_reason,
                        [reason["reason_id"] for reason in result["reasons"]],
                    )

    def test_invalid_repository_signals_fail_before_task_readiness(self) -> None:
        invalid_signal_inputs = (
            "repository.tests-present",
            [1],
            ["repository.unknown"],
            ["repository.tests-present", "repository.tests-present"],
            ["repository.tests-present"] * 17,
        )
        for repository_signals in invalid_signal_inputs:
            with self.subTest(repository_signals=repository_signals):
                result = select_workflow_profile(
                    {
                        "workflow_kind": "change",
                        "task_input": None,
                        "repository_signals": repository_signals,
                    }
                )
                self.assertEqual(result["status"], "invalid")
                reason_ids = [reason["reason_id"] for reason in result["reasons"]]
                self.assertEqual(
                    reason_ids,
                    [
                        "workflow.intent.explicit",
                        "workflow.repository.signals-invalid",
                    ],
                )
                self.assertNotIn("workflow.task.needs-input", reason_ids)

    def test_repository_signal_order_is_canonical_and_profile_is_unchanged(self) -> None:
        base = {
            "workflow_kind": "review",
            "task_input": "task",
        }
        first = select_workflow_profile(
            {
                **base,
                "repository_signals": [
                    "repository.ci-present",
                    "repository.tests-present",
                ],
            }
        )
        second = select_workflow_profile(
            {
                **base,
                "repository_signals": [
                    "repository.tests-present",
                    "repository.ci-present",
                ],
            }
        )

        self.assertEqual(first, second)
        self.assertEqual(
            first["selected_workflow_profile"],
            {"profile_id": "workflow-review"},
        )
        self.assertEqual(
            [item["requirement_id"] for item in first["requirements"]],
            [
                "workflow.requirement.review-evidence",
                "workflow.requirement.test-evidence",
                "workflow.requirement.ci-evidence",
            ],
        )
        self.assertEqual(
            [reason["reason_id"] for reason in first["reasons"]],
            [
                "workflow.intent.explicit",
                "workflow.task.structurally-ready",
                "workflow.criteria.not-required",
                "workflow.repository.tests-present",
                "workflow.repository.ci-present",
                "workflow.profile.selected",
            ],
        )
        self.assertEqual(
            first["limitations"][-1],
            {"reason_id": "workflow.limit.repository-signals-not-verified"},
        )
        without_signals = select_workflow_profile(base)
        self.assertNotIn(
            {"reason_id": "workflow.limit.repository-signals-not-verified"},
            without_signals["limitations"],
        )

    def test_ambiguous_catalog_fails_closed(self) -> None:
        ambiguous_catalog = (
            (
                "change",
                "workflow-change-a",
                ("workflow.requirement.change-evidence",),
            ),
            (
                "change",
                "workflow-change-b",
                ("workflow.requirement.change-evidence",),
            ),
        )
        with mock.patch.object(
            workflow_selection,
            "_WORKFLOW_PROFILES",
            ambiguous_catalog,
        ):
            result = select_workflow_profile(
                {
                    "workflow_kind": "change",
                    "task_input": "task",
                    "criteria_input": {
                        "criteria": [{"id": "ac-1", "statement": "works"}]
                    },
                }
            )

        self.assertEqual(result["status"], "invalid")
        self.assertIsNone(result["selected_workflow_profile"])
        self.assertEqual(
            result["reasons"][-1]["reason_id"],
            "workflow.catalog.ambiguous",
        )

    def test_malformed_or_unbounded_catalog_fails_closed(self) -> None:
        valid_request = {
            "workflow_kind": "change",
            "task_input": "task",
            "criteria_input": {
                "criteria": [{"id": "ac-1", "statement": "works"}]
            },
        }
        invalid_catalogs = (
            (),
            (("change",),),
            (("unsupported", "workflow-change", ()),),
            (("change", "Workflow-Change", ()),),
            (("change", "workflow-change", ("workflow.requirement.unknown",)),),
            (
                ("change", "workflow-shared", ()),
                ("review", "workflow-shared", ()),
            ),
            tuple(
                (
                    "change",
                    f"workflow-change-{index}",
                    ("workflow.requirement.change-evidence",),
                )
                for index in range(17)
            ),
        )
        for catalog in invalid_catalogs:
            with self.subTest(catalog=catalog):
                with mock.patch.object(
                    workflow_selection,
                    "_WORKFLOW_PROFILES",
                    catalog,
                ):
                    result = select_workflow_profile(valid_request)
                self.assertEqual(result["status"], "invalid")
                self.assertIsNone(result["selected_workflow_profile"])
                self.assertEqual(
                    result["reasons"][-1]["reason_id"],
                    "workflow.catalog.invalid",
                )

        with mock.patch.object(
            workflow_selection,
            "MAX_WORKFLOW_REQUIREMENTS",
            1,
        ):
            result = select_workflow_profile(valid_request)
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["reasons"][-1]["reason_id"], "workflow.catalog.invalid")

    def test_allowlists_contract_and_output_order_are_stable(self) -> None:
        self.assertEqual(workflow_selection.__all__, ["select_workflow_profile"])
        self.assertIsInstance(workflow_selection._WORKFLOW_REASON_IDS, frozenset)
        self.assertEqual(
            workflow_selection._WORKFLOW_REASON_IDS,
            frozenset(
                {
                    "workflow.request.invalid",
                    "workflow.intent.missing",
                    "workflow.intent.invalid",
                    "workflow.intent.explicit",
                    "workflow.task.structurally-ready",
                    "workflow.task.needs-input",
                    "workflow.task.invalid",
                    "workflow.criteria.structurally-valid",
                    "workflow.criteria.not-required",
                    "workflow.criteria.needs-input",
                    "workflow.criteria.invalid",
                    "workflow.repository.signals-invalid",
                    "workflow.repository.tests-present",
                    "workflow.repository.ci-present",
                    "workflow.profile.selected",
                    "workflow.catalog.invalid",
                    "workflow.catalog.ambiguous",
                }
            ),
        )
        self.assertEqual(
            workflow_selection._WORKFLOW_REQUIREMENT_IDS,
            frozenset(
                {
                    "workflow.requirement.acceptance-criteria",
                    "workflow.requirement.change-evidence",
                    "workflow.requirement.review-evidence",
                    "workflow.requirement.inspection-evidence",
                    "workflow.requirement.test-evidence",
                    "workflow.requirement.ci-evidence",
                }
            ),
        )
        self.assertEqual(
            workflow_selection._WORKFLOW_LIMITATION_IDS,
            frozenset(
                {
                    "workflow.limit.semantic-intent-not-inferred",
                    "workflow.limit.execution-not-performed",
                    "workflow.limit.completion-not-evaluated",
                    "workflow.limit.repository-signals-not-verified",
                }
            ),
        )

        expected_requirements = {
            "change": (
                "workflow.requirement.acceptance-criteria",
                "workflow.requirement.change-evidence",
            ),
            "review": ("workflow.requirement.review-evidence",),
            "inspect": ("workflow.requirement.inspection-evidence",),
        }
        valid_criteria = {"criteria": [{"id": "ac-1", "statement": "works"}]}
        for workflow_kind, requirement_ids in expected_requirements.items():
            with self.subTest(workflow_kind=workflow_kind):
                result = select_workflow_profile(
                    {
                        "workflow_kind": workflow_kind,
                        "task_input": "task",
                        "criteria_input": valid_criteria,
                    }
                )
                self.assertEqual(
                    tuple(item["requirement_id"] for item in result["requirements"]),
                    requirement_ids,
                )
                self.assertEqual(
                    list(result),
                    [
                        "schema_version",
                        "status",
                        "assessment_scope",
                        "selected_workflow_profile",
                        "requirements",
                        "reasons",
                        "limitations",
                        "truncated",
                    ],
                )
                self.assertIn(result["status"], {"selected", "needs-input", "invalid"})
                self.assertLessEqual(
                    len(result["reasons"]),
                    workflow_selection.MAX_WORKFLOW_REASONS,
                )
                self.assertLessEqual(
                    len(result["requirements"]),
                    workflow_selection.MAX_WORKFLOW_REQUIREMENTS,
                )
                self.assertLessEqual(
                    len(result["limitations"]),
                    workflow_selection.MAX_WORKFLOW_LIMITATIONS,
                )

    def test_diagnostics_are_content_free_and_do_not_claim_completion(self) -> None:
        secret = "SECRET_SENTINEL_DO_NOT_ECHO"
        result = select_workflow_profile(
            {
                "workflow_kind": "review",
                "task_input": (
                    secret
                    + " content-path/.ssh/id_rsa; rm -rf sentinel; review this"
                ),
                "criteria_input": {
                    "criteria": [
                        {
                            "id": "ac-1",
                            "statement": secret + " powershell Remove-Item C:\\private",
                        }
                    ]
                },
            }
        )
        encoded = json.dumps(result, ensure_ascii=False, sort_keys=True)

        self.assertEqual(result["status"], "selected")
        for raw_content in (
            secret,
            "content-path",
            ".ssh/id_rsa",
            "rm -rf",
            "powershell",
            "Remove-Item",
            "C:\\private",
            "review this",
        ):
            self.assertNotIn(raw_content, encoded)
        for forbidden_state in (
            '"satisfied"',
            '"completed"',
            '"tests_passed"',
            '"review_passed"',
            '"inspection_passed"',
            '"evidence_complete"',
        ):
            self.assertNotIn(forbidden_state, encoded)

        invalid_intent = secret + "-invalid"
        invalid_result = select_workflow_profile(
            {"workflow_kind": invalid_intent, "task_input": secret}
        )
        self.assertNotIn(
            invalid_intent,
            json.dumps(invalid_result, ensure_ascii=False),
        )

    def test_module_has_no_execution_or_external_effect_dependencies(self) -> None:
        source = Path(workflow_selection.__file__).read_text(encoding="utf-8")
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
            "route_task",
            "analyze_project",
            "validate_registry",
            "required_skill_ids",
            "recommended_skill_ids",
            "skill install",
        ):
            self.assertNotIn(forbidden_reference, source)

    def test_repository_signal_individual_and_empty_cases(self) -> None:
        base = {"workflow_kind": "inspect", "task_input": "task"}
        self.assertEqual(
            select_workflow_profile(base),
            select_workflow_profile({**base, "repository_signals": []}),
        )
        cases = (
            (
                "repository.tests-present",
                "workflow.repository.tests-present",
                "workflow.requirement.test-evidence",
            ),
            (
                "repository.ci-present",
                "workflow.repository.ci-present",
                "workflow.requirement.ci-evidence",
            ),
        )
        for signal_id, reason_id, requirement_id in cases:
            with self.subTest(signal_id=signal_id):
                result = select_workflow_profile(
                    {**base, "repository_signals": [signal_id]}
                )
                reason_ids = [reason["reason_id"] for reason in result["reasons"]]
                requirement_ids = [
                    item["requirement_id"] for item in result["requirements"]
                ]
                self.assertIn(reason_id, reason_ids)
                self.assertIn(requirement_id, requirement_ids)
                self.assertEqual(len(requirement_ids), len(set(requirement_ids)))
                self.assertEqual(
                    result["limitations"][-1]["reason_id"],
                    "workflow.limit.repository-signals-not-verified",
                )

    def test_custom_envelope_and_intent_types_are_inert(self) -> None:
        class ExplosiveDict(dict):
            def __iter__(self):
                raise AssertionError("custom request must not be iterated")

        class ExplosiveString(str):
            def __eq__(self, other):
                raise AssertionError("custom workflow kind must not be compared")

            def __hash__(self):
                raise AssertionError("custom workflow kind must not be hashed")

        envelope_result = select_workflow_profile(
            ExplosiveDict(workflow_kind="change")
        )
        intent_result = select_workflow_profile(
            {"workflow_kind": ExplosiveString("change")}
        )

        self.assertEqual(envelope_result["status"], "invalid")
        self.assertEqual(
            envelope_result["reasons"][0]["reason_id"],
            "workflow.request.invalid",
        )
        self.assertEqual(intent_result["status"], "invalid")
        self.assertEqual(
            intent_result["reasons"][0]["reason_id"],
            "workflow.intent.invalid",
        )

    def test_optional_criteria_only_accepts_the_explicit_absence_forms(self) -> None:
        for workflow_kind in ("review", "inspect"):
            with self.subTest(workflow_kind=workflow_kind):
                result = select_workflow_profile(
                    {
                        "workflow_kind": workflow_kind,
                        "task_input": "task",
                        "criteria_input": {},
                    }
                )
                self.assertEqual(result["status"], "invalid")
                self.assertEqual(
                    result["reasons"][-1]["reason_id"],
                    "workflow.criteria.invalid",
                )

        caller_report = select_workflow_profile(
            {
                "workflow_kind": "review",
                "task_input": "task",
                "criteria_report": {"status": "structurally-valid"},
            }
        )
        self.assertEqual(caller_report["status"], "invalid")
        self.assertEqual(
            caller_report["reasons"][0]["reason_id"],
            "workflow.request.invalid",
        )

    def test_catalog_must_cover_every_allowed_workflow_kind_once(self) -> None:
        incomplete_catalog = (
            (
                "change",
                "workflow-change",
                ("workflow.requirement.change-evidence",),
            ),
        )
        with mock.patch.object(
            workflow_selection,
            "_WORKFLOW_PROFILES",
            incomplete_catalog,
        ):
            result = select_workflow_profile(
                {
                    "workflow_kind": "change",
                    "task_input": "task",
                    "criteria_input": {
                        "criteria": [{"id": "ac-1", "statement": "works"}]
                    },
                }
            )

        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["reasons"][-1]["reason_id"], "workflow.catalog.invalid")

    def test_every_public_result_is_allowlisted_bounded_and_deterministic(self) -> None:
        valid_criteria = {"criteria": [{"id": "ac-1", "statement": "works"}]}
        requests = (
            None,
            {},
            {"workflow_kind": "unknown"},
            {"workflow_kind": "review", "repository_signals": ["unknown"]},
            {
                "workflow_kind": "review",
                "repository_signals": ["repository.tests-present"],
            },
            {"workflow_kind": "change", "task_input": "task"},
            {
                "workflow_kind": "change",
                "task_input": "task",
                "criteria_input": {"criteria": "invalid"},
            },
            {
                "workflow_kind": "change",
                "task_input": "task",
                "criteria_input": valid_criteria,
                "repository_signals": [
                    "repository.ci-present",
                    "repository.tests-present",
                ],
            },
        )
        for request in requests:
            with self.subTest(request=request):
                first = select_workflow_profile(request)
                second = select_workflow_profile(request)
                self.assertEqual(first, second)
                reason_ids = [item["reason_id"] for item in first["reasons"]]
                limitation_ids = [
                    item["reason_id"] for item in first["limitations"]
                ]
                requirement_ids = [
                    item["requirement_id"] for item in first["requirements"]
                ]
                self.assertLessEqual(
                    set(reason_ids),
                    workflow_selection._WORKFLOW_REASON_IDS,
                )
                self.assertLessEqual(
                    set(limitation_ids),
                    workflow_selection._WORKFLOW_LIMITATION_IDS,
                )
                self.assertLessEqual(
                    set(requirement_ids),
                    workflow_selection._WORKFLOW_REQUIREMENT_IDS,
                )
                self.assertEqual(len(reason_ids), len(set(reason_ids)))
                self.assertEqual(len(limitation_ids), len(set(limitation_ids)))
                self.assertEqual(len(requirement_ids), len(set(requirement_ids)))
                self.assertLessEqual(
                    len(reason_ids), workflow_selection.MAX_WORKFLOW_REASONS
                )
                self.assertLessEqual(
                    len(limitation_ids), workflow_selection.MAX_WORKFLOW_LIMITATIONS
                )
                self.assertLessEqual(
                    len(requirement_ids), workflow_selection.MAX_WORKFLOW_REQUIREMENTS
                )

    def test_catalog_rejects_allowed_requirements_on_the_wrong_profile(self) -> None:
        wrong_catalog = (
            (
                "change",
                "workflow-change",
                ("workflow.requirement.review-evidence",),
            ),
            (
                "review",
                "workflow-review",
                ("workflow.requirement.review-evidence",),
            ),
            (
                "inspect",
                "workflow-inspect",
                ("workflow.requirement.inspection-evidence",),
            ),
        )
        with mock.patch.object(
            workflow_selection,
            "_WORKFLOW_PROFILES",
            wrong_catalog,
        ):
            result = select_workflow_profile(
                {
                    "workflow_kind": "change",
                    "task_input": "task",
                    "criteria_input": {
                        "criteria": [{"id": "ac-1", "statement": "works"}]
                    },
                }
            )

        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["reasons"][-1]["reason_id"], "workflow.catalog.invalid")

    def test_catalog_rejects_unapproved_but_well_formed_profile_id(self) -> None:
        wrong_catalog = (
            (
                "change",
                "workflow-change-alternate",
                (
                    "workflow.requirement.acceptance-criteria",
                    "workflow.requirement.change-evidence",
                ),
            ),
            (
                "review",
                "workflow-review",
                ("workflow.requirement.review-evidence",),
            ),
            (
                "inspect",
                "workflow-inspect",
                ("workflow.requirement.inspection-evidence",),
            ),
        )
        with mock.patch.object(
            workflow_selection,
            "_WORKFLOW_PROFILES",
            wrong_catalog,
        ):
            result = select_workflow_profile(
                {
                    "workflow_kind": "change",
                    "task_input": "task",
                    "criteria_input": {
                        "criteria": [{"id": "ac-1", "statement": "works"}]
                    },
                }
            )

        self.assertEqual(result["status"], "invalid")
        self.assertEqual(result["reasons"][-1]["reason_id"], "workflow.catalog.invalid")


if __name__ == "__main__":
    unittest.main()

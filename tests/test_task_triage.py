import json
import unittest

from skill_orchestrator.task_triage import (
    TRIAGE_LIMITATION_IDS,
    TRIAGE_REASON_IDS,
    analyze_task_triage,
)


class TaskTriageTests(unittest.TestCase):
    def test_non_dict_request_fails_closed_without_echo(self) -> None:
        result = analyze_task_triage("PRIVATE TASK")

        self.assertEqual(result["status"], "invalid")
        self.assertIsNone(result["selected_layer"])
        self.assertEqual(
            [reason["reason_id"] for reason in result["reasons"]],
            ["triage.request.invalid"],
        )
        self.assertNotIn("PRIVATE TASK", json.dumps(result))

    def test_unknown_request_field_fails_closed_before_descent(self) -> None:
        result = analyze_task_triage(
            {
                "task_input": "task",
                "requirements": {"graph": True},
                "unknown": "SECRET",
            }
        )

        self.assertEqual(result["status"], "invalid")
        self.assertEqual(
            [reason["reason_id"] for reason in result["reasons"]],
            ["triage.request.invalid"],
        )
        self.assertNotIn("SECRET", json.dumps(result))

    def test_missing_and_empty_task_input_need_input(self) -> None:
        for request in ({}, {"task_input": None}, {"task_input": "\t \r\n"}):
            with self.subTest(request=request):
                result = analyze_task_triage(request)
                self.assertEqual(result["status"], "needs-input")
                self.assertIsNone(result["selected_layer"])
                self.assertEqual(
                    result["reasons"][0]["reason_id"],
                    "triage.task.needs-input",
                )

    def test_missing_requirements_need_input_instead_of_assuming_l1(self) -> None:
        result = analyze_task_triage({"task_input": "task"})

        self.assertEqual(result["status"], "needs-input")
        self.assertIsNone(result["selected_layer"])
        self.assertEqual(
            [reason["reason_id"] for reason in result["reasons"]],
            [
                "triage.task.structurally-ready",
                "triage.requirements.needs-input",
            ],
        )

    def test_explicit_empty_requirements_select_l1(self) -> None:
        result = analyze_task_triage(
            {"task_input": "task", "requirements": {}}
        )

        self.assertEqual(result["status"], "selected")
        self.assertEqual(result["selected_layer"], "L1")
        self.assertEqual(
            [reason["reason_id"] for reason in result["reasons"]],
            [
                "triage.task.structurally-ready",
                "triage.layer.selected",
            ],
        )

    def test_each_explicit_requirement_selects_its_layer(self) -> None:
        cases = (
            ("context", "L2"),
            ("harness", "L3"),
            ("loop", "L4"),
            ("graph", "L5"),
            ("human_approval", "L5"),
        )
        for requirement, expected_layer in cases:
            with self.subTest(requirement=requirement):
                result = analyze_task_triage(
                    {
                        "task_input": "task",
                        "requirements": {requirement: True},
                    }
                )
                self.assertEqual(result["status"], "selected")
                self.assertEqual(result["selected_layer"], expected_layer)
                self.assertIn(
                    "triage.requirement." + requirement.replace("_", "-"),
                    [reason["reason_id"] for reason in result["reasons"]],
                )

    def test_highest_layer_wins_in_fixed_order(self) -> None:
        result = analyze_task_triage(
            {
                "task_input": "task",
                "requirements": {
                    "loop": True,
                    "context": True,
                    "harness": True,
                    "human_approval": True,
                    "graph": True,
                },
            }
        )

        self.assertEqual(result["selected_layer"], "L5")
        self.assertEqual(
            [reason["reason_id"] for reason in result["reasons"]],
            [
                "triage.task.structurally-ready",
                "triage.requirement.context",
                "triage.requirement.harness",
                "triage.requirement.loop",
                "triage.requirement.graph",
                "triage.requirement.human-approval",
                "triage.layer.selected",
            ],
        )

    def test_requirement_order_does_not_depend_on_mapping_order(self) -> None:
        first = analyze_task_triage(
            {
                "task_input": "task",
                "requirements": {"loop": True, "context": True},
            }
        )
        second = analyze_task_triage(
            {
                "requirements": {"context": True, "loop": True},
                "task_input": "task",
            }
        )

        self.assertEqual(first, second)

    def test_invalid_requirements_fail_closed(self) -> None:
        cases = (
            None,
            [],
            {"unknown": True},
            {"context": 1},
            {"graph": "true"},
            {1: True},
        )
        for requirements in cases:
            with self.subTest(requirements=requirements):
                result = analyze_task_triage(
                    {"task_input": "task", "requirements": requirements}
                )
                self.assertEqual(result["status"], "invalid")
                self.assertEqual(
                    result["reasons"][0]["reason_id"],
                    "triage.request.invalid",
                )

    def test_invalid_task_input_is_propagated_without_content(self) -> None:
        for task_input in (123, b"\xff", b"x" * 32769, "SECRET-TASK\x00CONTENT"):
            with self.subTest(task_input=repr(task_input)):
                result = analyze_task_triage({"task_input": task_input})
                self.assertEqual(result["status"], "invalid")
                self.assertIsNone(result["selected_layer"])
                rendered = json.dumps(result, ensure_ascii=False)
                self.assertNotIn("SECRET-TASK", rendered)

    def test_unicode_and_utf8_bytes_are_structurally_supported(self) -> None:
        cases = (
            "請檢查這項變更",
            "この変更を確認してください",
            "Review the change",
        )
        for task_input in cases:
            with self.subTest(task_input=task_input):
                text_result = analyze_task_triage(
                    {"task_input": task_input, "requirements": {}}
                )
                byte_result = analyze_task_triage(
                    {
                        "task_input": task_input.encode("utf-8"),
                        "requirements": {},
                    }
                )
                self.assertEqual(text_result, byte_result)
                self.assertEqual(text_result["selected_layer"], "L1")

    def test_bom_and_line_endings_do_not_change_layer(self) -> None:
        base = "task\nwith details"
        for task_input in (base, base.replace("\n", "\r\n"), "\ufeff" + base):
            with self.subTest(task_input=repr(task_input)):
                result = analyze_task_triage(
                    {"task_input": task_input, "requirements": {}}
                )
                self.assertEqual(result["status"], "selected")
                self.assertEqual(result["selected_layer"], "L1")

    def test_l5_reports_approval_and_execution_boundaries(self) -> None:
        result = analyze_task_triage(
            {"task_input": "high risk task", "requirements": {"graph": True}}
        )
        limitation_ids = [item["reason_id"] for item in result["limitations"]]

        self.assertIn("triage.limit.execution-not-performed", limitation_ids)
        self.assertIn("triage.limit.human-approval-not-granted", limitation_ids)
        self.assertIn("triage.limit.graph-orchestration-not-implemented", limitation_ids)

    def test_result_is_metadata_only_and_reason_ids_are_allowlisted(self) -> None:
        sentinel = "SECRET task <USER_HOME>/private rm -rf /"
        result = analyze_task_triage(
            {
                "task_input": sentinel,
                "requirements": {"harness": True},
            }
        )
        rendered = json.dumps(result, ensure_ascii=False, sort_keys=True)

        self.assertNotIn(sentinel, rendered)
        self.assertNotIn("SECRET task", rendered)
        self.assertTrue(
            set(reason["reason_id"] for reason in result["reasons"])
            <= TRIAGE_REASON_IDS
        )
        self.assertTrue(
            set(item["reason_id"] for item in result["limitations"])
            <= TRIAGE_LIMITATION_IDS
        )
        self.assertFalse(result["truncated"])

    def test_repeated_calls_are_byte_deterministic(self) -> None:
        request = {
            "task_input": "task",
            "requirements": {"graph": True, "context": True},
        }
        first = json.dumps(
            analyze_task_triage(request), ensure_ascii=False, sort_keys=True
        ).encode("utf-8")
        second = json.dumps(
            analyze_task_triage(request), ensure_ascii=False, sort_keys=True
        ).encode("utf-8")

        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()

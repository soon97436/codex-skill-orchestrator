import json
import tempfile
import unittest
from pathlib import Path

from skill_orchestrator.recommendations import analyze_and_recommend
from skill_orchestrator.task_readiness import (
    MAX_TASK_INPUT_BYTES,
    TASK_READINESS_REASON_IDS,
    analyze_task_readiness,
)


ROOT = Path(__file__).resolve().parents[1]


class TaskReadinessTests(unittest.TestCase):
    def assertDecision(self, task_input, status: str, reason_id: str, state: str):
        result = analyze_task_readiness(task_input)
        self.assertEqual(result["status"], status)
        self.assertEqual(
            result["reasons"],
            [
                {
                    "reason_id": reason_id,
                    "evidence_ref": {
                        "source": "task-input",
                        "identity": {"state": state},
                    },
                }
            ],
        )
        return result

    def test_task_readiness_missing_input(self) -> None:
        result = self.assertDecision(
            None,
            "needs-input",
            "task.readiness.input-missing",
            "missing",
        )
        self.assertEqual(
            result,
            {
                "schema_version": 1,
                "status": "needs-input",
                "assessment_scope": "structural-only",
                "reasons": [
                    {
                        "reason_id": "task.readiness.input-missing",
                        "evidence_ref": {
                            "source": "task-input",
                            "identity": {"state": "missing"},
                        },
                    }
                ],
                "limitations": [
                    {
                        "reason_id": "task.readiness.limit.semantic-sufficiency-not-evaluated"
                    }
                ],
                "truncated": False,
            },
        )

    def test_task_readiness_empty_input(self) -> None:
        self.assertDecision(
            "",
            "needs-input",
            "task.readiness.input-empty",
            "empty",
        )

    def test_task_readiness_whitespace_only(self) -> None:
        self.assertDecision(
            "\t \r\n",
            "needs-input",
            "task.readiness.input-empty",
            "empty",
        )

    def test_task_readiness_valid_utf8(self) -> None:
        self.assertDecision(
            "Review the change",
            "structurally-ready",
            "task.readiness.input-present",
            "present",
        )

    def test_task_readiness_traditional_chinese(self) -> None:
        self.assertDecision(
            "請檢查這項變更",
            "structurally-ready",
            "task.readiness.input-present",
            "present",
        )

    def test_task_readiness_japanese(self) -> None:
        self.assertDecision(
            "この変更を確認してください",
            "structurally-ready",
            "task.readiness.input-present",
            "present",
        )

    def test_task_readiness_non_english_text(self) -> None:
        self.assertDecision(
            "تحقق من هذا التغيير",
            "structurally-ready",
            "task.readiness.input-present",
            "present",
        )

    def test_task_readiness_invalid_type(self) -> None:
        for task_input in (123, True, ["task"], bytearray(b"task")):
            with self.subTest(task_input=task_input):
                self.assertDecision(
                    task_input,
                    "invalid",
                    "task.readiness.input-invalid-type",
                    "invalid-type",
                )

    def test_task_readiness_malformed_utf8(self) -> None:
        self.assertDecision(
            b"\xff\xfe",
            "invalid",
            "task.readiness.input-invalid-utf8",
            "invalid-utf8",
        )

    def test_task_readiness_oversized_input(self) -> None:
        self.assertDecision(
            b"x" * (MAX_TASK_INPUT_BYTES + 1),
            "invalid",
            "task.readiness.input-too-large",
            "too-large",
        )

    def test_task_readiness_exact_bound(self) -> None:
        self.assertDecision(
            b"x" * MAX_TASK_INPUT_BYTES,
            "structurally-ready",
            "task.readiness.input-present",
            "present",
        )

    def test_task_readiness_str_bom_exact_bound(self) -> None:
        self.assertDecision(
            "\ufeff" + ("a" * MAX_TASK_INPUT_BYTES),
            "structurally-ready",
            "task.readiness.input-present",
            "present",
        )

    def test_task_readiness_bytes_bom_exact_bound(self) -> None:
        self.assertDecision(
            b"\xef\xbb\xbf" + (b"a" * MAX_TASK_INPUT_BYTES),
            "structurally-ready",
            "task.readiness.input-present",
            "present",
        )

    def test_task_readiness_str_bom_over_bound(self) -> None:
        self.assertDecision(
            "\ufeff" + ("a" * (MAX_TASK_INPUT_BYTES + 1)),
            "invalid",
            "task.readiness.input-too-large",
            "too-large",
        )

    def test_task_readiness_bytes_bom_over_bound(self) -> None:
        self.assertDecision(
            b"\xef\xbb\xbf" + (b"a" * (MAX_TASK_INPUT_BYTES + 1)),
            "invalid",
            "task.readiness.input-too-large",
            "too-large",
        )

    def test_task_readiness_multibyte_over_bound(self) -> None:
        self.assertDecision(
            "界" * (MAX_TASK_INPUT_BYTES // 3 + 1),
            "invalid",
            "task.readiness.input-too-large",
            "too-large",
        )

    def test_task_readiness_embedded_nul(self) -> None:
        for task_input in ("review\x00task", b"review\x00task"):
            with self.subTest(task_input=task_input):
                self.assertDecision(
                    task_input,
                    "invalid",
                    "task.readiness.input-invalid-nul",
                    "invalid-nul",
                )

    def test_task_readiness_utf8_bom_is_equivalent(self) -> None:
        expected = analyze_task_readiness("\u3000")
        self.assertEqual(analyze_task_readiness("\ufeff\u3000"), expected)
        self.assertEqual(
            analyze_task_readiness(b"\xef\xbb\xbf" + "\u3000".encode("utf-8")),
            expected,
        )
        double_bom_inputs = (
            "\ufeff\ufeff\u3000",
            b"\xef\xbb\xbf\xef\xbb\xbf" + "\u3000".encode("utf-8"),
        )
        for task_input in double_bom_inputs:
            with self.subTest(task_input=task_input):
                self.assertEqual(
                    analyze_task_readiness(task_input)["status"],
                    "structurally-ready",
                )

    def test_task_readiness_fixed_unicode_whitespace(self) -> None:
        codepoints = (
            *range(0x0009, 0x000E),
            0x0020,
            0x0085,
            0x00A0,
            0x1680,
            *range(0x2000, 0x200B),
            0x2028,
            0x2029,
            0x202F,
            0x205F,
            0x3000,
        )
        self.assertDecision(
            "".join(chr(codepoint) for codepoint in codepoints),
            "needs-input",
            "task.readiness.input-empty",
            "empty",
        )

    def test_task_readiness_zero_width_space_is_content(self) -> None:
        self.assertDecision(
            "\u200b",
            "structurally-ready",
            "task.readiness.input-present",
            "present",
        )

    def test_task_readiness_line_endings_are_equivalent(self) -> None:
        self.assertEqual(
            analyze_task_readiness("review\nchange"),
            analyze_task_readiness("review\r\nchange"),
        )

    def test_task_readiness_unicode_forms_have_same_status(self) -> None:
        self.assertEqual(
            analyze_task_readiness("caf\u00e9")["status"],
            analyze_task_readiness("cafe\u0301")["status"],
        )

    def test_task_readiness_bytes_and_str_are_equivalent(self) -> None:
        task = "請確認この変更"
        self.assertEqual(
            analyze_task_readiness(task),
            analyze_task_readiness(task.encode("utf-8")),
        )

    def test_task_readiness_reason_ids_are_allowlisted(self) -> None:
        task_inputs = (
            None,
            "",
            "task",
            1,
            b"\xff",
            "task\x00data",
            b"x" * (MAX_TASK_INPUT_BYTES + 1),
        )
        observed = {
            entry["reason_id"]
            for task_input in task_inputs
            for key in ("reasons", "limitations")
            for entry in analyze_task_readiness(task_input)[key]
        }
        self.assertLessEqual(observed, TASK_READINESS_REASON_IDS)
        self.assertEqual(
            TASK_READINESS_REASON_IDS,
            frozenset(
                {
                    "task.readiness.input-present",
                    "task.readiness.input-missing",
                    "task.readiness.input-empty",
                    "task.readiness.input-invalid-type",
                    "task.readiness.input-invalid-utf8",
                    "task.readiness.input-invalid-nul",
                    "task.readiness.input-too-large",
                    "task.readiness.limit.semantic-sufficiency-not-evaluated",
                }
            ),
        )

    def test_task_readiness_does_not_echo_task(self) -> None:
        task = (
            "API_TOKEN=phase4a-private-value "
            "/private/task-input/project C:\\TaskInput\\project"
        )
        serialized = json.dumps(
            analyze_task_readiness(task),
            ensure_ascii=False,
            sort_keys=True,
        )
        for forbidden in (
            task,
            "phase4a-private-value",
            "/private/task-input/project",
            "C:\\TaskInput\\project",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_task_readiness_does_not_execute_content(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-task-readiness-") as temporary:
            sentinel = Path(temporary) / "must-not-exist"
            result = analyze_task_readiness(f"touch {sentinel}")
            self.assertEqual(result["status"], "structurally-ready")
            self.assertFalse(sentinel.exists())

    def test_task_readiness_output_order_is_deterministic(self) -> None:
        first = analyze_task_readiness("task")
        second = analyze_task_readiness(b"task")
        self.assertEqual(first, second)
        self.assertEqual(
            list(first),
            [
                "schema_version",
                "status",
                "assessment_scope",
                "reasons",
                "limitations",
                "truncated",
            ],
        )
        self.assertEqual(
            json.dumps(first, ensure_ascii=False),
            json.dumps(second, ensure_ascii=False),
        )

    def test_task_readiness_existing_phase3_contract_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-phase3-regression-") as temporary:
            result = analyze_and_recommend(Path(temporary), ROOT)
        self.assertNotIn("task_readiness", result)
        self.assertIn("recommended_skills", result)
        self.assertIn("recommendations_complete", result)
        self.assertIn("recommendation_explanations", result)
        self.assertIn("capability_analysis", result)


if __name__ == "__main__":
    unittest.main()

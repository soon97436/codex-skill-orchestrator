import json
import tempfile
import unittest
from pathlib import Path

from skill_orchestrator.acceptance_criteria import (
    ACCEPTANCE_CRITERIA_REASON_IDS,
    MAX_ACCEPTANCE_CRITERIA,
    MAX_ACCEPTANCE_FINDINGS,
    MAX_CRITERION_ID_BYTES,
    MAX_CRITERION_STATEMENT_BYTES,
    validate_acceptance_criteria,
)
from skill_orchestrator.recommendations import analyze_and_recommend
from skill_orchestrator.task_readiness import analyze_task_readiness


ROOT = Path(__file__).resolve().parents[1]


class AcceptanceCriteriaTests(unittest.TestCase):
    def assertStatus(self, criteria_input, status: str, reason_ids):
        result = validate_acceptance_criteria(criteria_input)
        self.assertEqual(result["status"], status)
        self.assertEqual(
            [finding["reason_id"] for finding in result["findings"]],
            list(reason_ids),
        )
        return result

    def test_acceptance_criteria_missing_input(self) -> None:
        result = self.assertStatus(
            None,
            "needs-criteria",
            ["acceptance.criteria.missing"],
        )
        self.assertEqual(
            result,
            {
                "schema_version": 1,
                "status": "needs-criteria",
                "assessment_scope": "structural-only",
                "findings": [
                    {
                        "reason_id": "acceptance.criteria.missing",
                        "evidence_ref": {
                            "source": "acceptance-criteria",
                            "identity": {"state": "missing"},
                        },
                    }
                ],
                "limitations": [
                    {
                        "reason_id": "acceptance.limit.semantic-quality-not-evaluated"
                    },
                    {
                        "reason_id": "acceptance.limit.satisfaction-not-evaluated"
                    },
                ],
                "truncated": False,
            },
        )

    def test_acceptance_criteria_empty_top_level_dict(self) -> None:
        self.assertStatus(
            {},
            "needs-criteria",
            ["acceptance.criteria.missing"],
        )

    def test_acceptance_criteria_wrong_top_level_type(self) -> None:
        for criteria_input in ([], "criteria", 1, True, b"criteria"):
            with self.subTest(criteria_input=criteria_input):
                self.assertStatus(
                    criteria_input,
                    "invalid",
                    ["acceptance.criteria.invalid-container"],
                )

    def test_acceptance_criteria_custom_top_level_object_is_inert(self) -> None:
        class HostileEquality:
            def __eq__(self, _other):
                raise AssertionError("custom equality must not run")

        self.assertStatus(
            HostileEquality(),
            "invalid",
            ["acceptance.criteria.invalid-container"],
        )

    def test_acceptance_criteria_unknown_top_level_field(self) -> None:
        for criteria_input in (
            {"criteria": [], "unknown": True},
            {"unknown": []},
        ):
            with self.subTest(criteria_input=criteria_input):
                result = self.assertStatus(
                    criteria_input,
                    "invalid",
                    ["acceptance.criteria.invalid-container"],
                )
                self.assertFalse(result["truncated"])

    def test_acceptance_criteria_wrong_container_type(self) -> None:
        for container in (None, {}, (), "criteria", b"criteria"):
            with self.subTest(container=container):
                self.assertStatus(
                    {"criteria": container},
                    "invalid",
                    ["acceptance.criteria.invalid-container"],
                )

    def test_acceptance_criteria_empty_list(self) -> None:
        self.assertStatus(
            {"criteria": []},
            "needs-criteria",
            ["acceptance.criteria.empty"],
        )

    def test_acceptance_criteria_valid_single_criterion(self) -> None:
        result = self.assertStatus(
            {"criteria": [{"id": "ac-1", "statement": "Ship the change"}]},
            "structurally-valid",
            ["acceptance.criteria.present"],
        )
        self.assertFalse(result["truncated"])

    def test_acceptance_criteria_valid_multiple_criteria(self) -> None:
        self.assertStatus(
            {
                "criteria": [
                    {"id": "ac-1", "statement": "First"},
                    {"id": "ac-2", "statement": "Second"},
                ]
            },
            "structurally-valid",
            ["acceptance.criteria.present"],
        )

    def test_acceptance_criteria_criterion_wrong_type(self) -> None:
        for criterion in (None, [], "criterion", 1, True):
            with self.subTest(criterion=criterion):
                result = self.assertStatus(
                    {"criteria": [criterion]},
                    "invalid",
                    ["acceptance.criterion.invalid-type"],
                )
                self.assertEqual(
                    result["findings"][0]["evidence_ref"]["identity"],
                    {"criterion_index": 0},
                )

    def test_acceptance_criteria_unknown_criterion_field(self) -> None:
        result = self.assertStatus(
            {
                "criteria": [
                    {"id": "ac-1", "statement": "Ship", "command": "echo no"}
                ]
            },
            "invalid",
            ["acceptance.criterion.invalid-fields"],
        )
        self.assertEqual(
            result["findings"][0]["evidence_ref"]["identity"],
            {"criterion_index": 0},
        )

    def test_acceptance_criteria_missing_id(self) -> None:
        self.assertStatus(
            {"criteria": [{"statement": "Ship"}]},
            "invalid",
            ["acceptance.criterion.id-missing"],
        )

    def test_acceptance_criteria_id_wrong_type(self) -> None:
        for criterion_id in (None, 1, True, b"ac-1", ["ac-1"]):
            with self.subTest(criterion_id=criterion_id):
                self.assertStatus(
                    {"criteria": [{"id": criterion_id, "statement": "Ship"}]},
                    "invalid",
                    ["acceptance.criterion.id-invalid"],
                )

    def test_acceptance_criteria_invalid_id_syntax(self) -> None:
        for criterion_id in (
            "",
            "AC-1",
            "ac_1",
            "-ac",
            "ac-",
            "ac--1",
            "a/b",
            "ac 1",
            "非英文",
        ):
            with self.subTest(criterion_id=criterion_id):
                self.assertStatus(
                    {"criteria": [{"id": criterion_id, "statement": "Ship"}]},
                    "invalid",
                    ["acceptance.criterion.id-invalid"],
                )

    def test_acceptance_criteria_id_exact_bound(self) -> None:
        self.assertStatus(
            {"criteria": [{"id": "a" * MAX_CRITERION_ID_BYTES, "statement": "Ship"}]},
            "structurally-valid",
            ["acceptance.criteria.present"],
        )

    def test_acceptance_criteria_id_over_bound(self) -> None:
        self.assertStatus(
            {
                "criteria": [
                    {"id": "a" * (MAX_CRITERION_ID_BYTES + 1), "statement": "Ship"}
                ]
            },
            "invalid",
            ["acceptance.criterion.id-invalid"],
        )

    def test_acceptance_criteria_duplicate_id(self) -> None:
        result = self.assertStatus(
            {
                "criteria": [
                    {"id": "ac-1", "statement": "First"},
                    {"id": "ac-1", "statement": "Second"},
                ]
            },
            "invalid",
            ["acceptance.criterion.id-duplicate"],
        )
        self.assertEqual(
            result["findings"][0]["evidence_ref"]["identity"],
            {"criterion_index": 1, "field": "id", "criterion_id": "ac-1"},
        )

    def test_acceptance_criteria_duplicate_occurrence_ordering(self) -> None:
        result = validate_acceptance_criteria(
            {
                "criteria": [
                    {"id": "ac-1", "statement": "First"},
                    {"id": "ac-1", "statement": "Second"},
                    {"id": "ac-1", "statement": "Third"},
                ]
            }
        )
        self.assertEqual(
            [
                finding["evidence_ref"]["identity"]["criterion_index"]
                for finding in result["findings"]
            ],
            [1, 2],
        )

    def test_acceptance_criteria_invalid_ids_are_not_duplicates(self) -> None:
        self.assertStatus(
            {
                "criteria": [
                    {"id": "AC-1", "statement": "First"},
                    {"id": "AC-1", "statement": "Second"},
                ]
            },
            "invalid",
            [
                "acceptance.criterion.id-invalid",
                "acceptance.criterion.id-invalid",
            ],
        )

    def test_acceptance_criteria_missing_statement(self) -> None:
        self.assertStatus(
            {"criteria": [{"id": "ac-1"}]},
            "invalid",
            ["acceptance.criterion.statement-missing"],
        )

    def test_acceptance_criteria_statement_wrong_type(self) -> None:
        for statement in (None, 1, True, b"Ship", ["Ship"], {"text": "Ship"}):
            with self.subTest(statement=statement):
                self.assertStatus(
                    {"criteria": [{"id": "ac-1", "statement": statement}]},
                    "invalid",
                    ["acceptance.criterion.statement-invalid-type"],
                )

    def test_acceptance_criteria_statement_invalid_unicode(self) -> None:
        self.assertStatus(
            {"criteria": [{"id": "ac-1", "statement": "\ud800"}]},
            "invalid",
            ["acceptance.criterion.statement-invalid-unicode"],
        )

    def test_acceptance_criteria_empty_statement(self) -> None:
        self.assertStatus(
            {"criteria": [{"id": "ac-1", "statement": ""}]},
            "invalid",
            ["acceptance.criterion.statement-empty"],
        )

    def test_acceptance_criteria_fixed_whitespace_only_statement(self) -> None:
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
        self.assertStatus(
            {
                "criteria": [
                    {"id": "ac-1", "statement": "".join(map(chr, codepoints))}
                ]
            },
            "invalid",
            ["acceptance.criterion.statement-empty"],
        )

    def test_acceptance_criteria_embedded_nul(self) -> None:
        self.assertStatus(
            {"criteria": [{"id": "ac-1", "statement": "Ship\x00now"}]},
            "invalid",
            ["acceptance.criterion.statement-invalid-nul"],
        )

    def test_acceptance_criteria_statement_exact_bound(self) -> None:
        self.assertStatus(
            {
                "criteria": [
                    {"id": "ac-1", "statement": "a" * MAX_CRITERION_STATEMENT_BYTES}
                ]
            },
            "structurally-valid",
            ["acceptance.criteria.present"],
        )

    def test_acceptance_criteria_statement_over_bound(self) -> None:
        self.assertStatus(
            {
                "criteria": [
                    {
                        "id": "ac-1",
                        "statement": "a" * (MAX_CRITERION_STATEMENT_BYTES + 1),
                    }
                ]
            },
            "invalid",
            ["acceptance.criterion.statement-too-large"],
        )

    def test_acceptance_criteria_multibyte_statement_boundary(self) -> None:
        exact = ("界" * 1365) + "a"
        over = "界" * 1366
        self.assertEqual(len(exact.encode("utf-8")), MAX_CRITERION_STATEMENT_BYTES)
        self.assertGreater(len(over.encode("utf-8")), MAX_CRITERION_STATEMENT_BYTES)
        self.assertStatus(
            {"criteria": [{"id": "ac-1", "statement": exact}]},
            "structurally-valid",
            ["acceptance.criteria.present"],
        )
        self.assertStatus(
            {"criteria": [{"id": "ac-1", "statement": over}]},
            "invalid",
            ["acceptance.criterion.statement-too-large"],
        )

    def test_acceptance_criteria_statement_reason_order(self) -> None:
        result = validate_acceptance_criteria(
            {
                "criteria": [
                    {
                        "id": "ac-1",
                        "statement": "\x00" + ("a" * MAX_CRITERION_STATEMENT_BYTES),
                    }
                ]
            }
        )
        self.assertEqual(
            [finding["reason_id"] for finding in result["findings"]],
            [
                "acceptance.criterion.statement-invalid-nul",
                "acceptance.criterion.statement-too-large",
            ],
        )

    def test_acceptance_criteria_too_many_stops_before_items(self) -> None:
        result = self.assertStatus(
            {"criteria": [object()] * (MAX_ACCEPTANCE_CRITERIA + 1)},
            "invalid",
            ["acceptance.criteria.too-many"],
        )
        self.assertFalse(result["truncated"])

    def test_acceptance_criteria_finding_limit(self) -> None:
        result = validate_acceptance_criteria(
            {"criteria": [{"extra": index} for index in range(22)]}
        )
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(len(result["findings"]), MAX_ACCEPTANCE_FINDINGS)
        self.assertEqual(
            result["findings"][-1]["reason_id"],
            "acceptance.analysis.finding-limit",
        )
        self.assertTrue(result["truncated"])
        self.assertEqual(
            sum(
                finding["reason_id"] != "acceptance.analysis.finding-limit"
                for finding in result["findings"]
            ),
            MAX_ACCEPTANCE_FINDINGS - 1,
        )

    def test_acceptance_criteria_findings_that_fit_are_not_truncated(self) -> None:
        result = validate_acceptance_criteria(
            {"criteria": [{"extra": index} for index in range(21)]}
        )
        self.assertEqual(len(result["findings"]), 63)
        self.assertFalse(result["truncated"])
        self.assertNotIn(
            "acceptance.analysis.finding-limit",
            [finding["reason_id"] for finding in result["findings"]],
        )

    def test_acceptance_criteria_exact_finding_bound_is_not_truncated(self) -> None:
        criteria = [{"extra": index} for index in range(21)]
        criteria.append("invalid criterion")
        result = validate_acceptance_criteria({"criteria": criteria})
        self.assertEqual(len(result["findings"]), MAX_ACCEPTANCE_FINDINGS)
        self.assertFalse(result["truncated"])
        self.assertEqual(
            result["findings"][-1]["reason_id"],
            "acceptance.criterion.invalid-type",
        )
        self.assertNotIn(
            "acceptance.analysis.finding-limit",
            [finding["reason_id"] for finding in result["findings"]],
        )

    def test_acceptance_criteria_non_english_statements(self) -> None:
        for statement in ("請確認這項結果", "この結果を確認する"):
            with self.subTest(statement=statement):
                self.assertStatus(
                    {"criteria": [{"id": "ac-1", "statement": statement}]},
                    "structurally-valid",
                    ["acceptance.criteria.present"],
                )

    def test_acceptance_criteria_unicode_forms_are_both_valid(self) -> None:
        for statement in ("caf\u00e9", "cafe\u0301"):
            with self.subTest(statement=statement):
                self.assertEqual(
                    validate_acceptance_criteria(
                        {"criteria": [{"id": "ac-1", "statement": statement}]}
                    )["status"],
                    "structurally-valid",
                )

    def test_acceptance_criteria_zero_width_space_is_content(self) -> None:
        self.assertStatus(
            {"criteria": [{"id": "ac-1", "statement": "\u200b"}]},
            "structurally-valid",
            ["acceptance.criteria.present"],
        )

    def test_acceptance_criteria_bom_is_content(self) -> None:
        for statement in ("\ufeffShip", "\ufeff"):
            with self.subTest(statement=statement):
                self.assertStatus(
                    {"criteria": [{"id": "ac-1", "statement": statement}]},
                    "structurally-valid",
                    ["acceptance.criteria.present"],
                )

    def test_acceptance_criteria_does_not_execute_statement(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-acceptance-") as temporary:
            sentinel = Path(temporary) / "must-not-exist"
            result = validate_acceptance_criteria(
                {
                    "criteria": [
                        {"id": "ac-1", "statement": f"touch {sentinel}"},
                        {"id": "ac-2", "statement": "read ~/.ssh/id_rsa"},
                        {"id": "ac-3", "statement": "$HOME/secrets"},
                    ]
                }
            )
            self.assertEqual(result["status"], "structurally-valid")
            self.assertFalse(sentinel.exists())

    def test_acceptance_criteria_diagnostics_do_not_echo_statement(self) -> None:
        statement = "API_TOKEN=private-value /private/task/project"
        serialized = json.dumps(
            validate_acceptance_criteria(
                {"criteria": [{"id": "ac-1", "statement": statement + "\x00"}]}
            ),
            ensure_ascii=False,
        )
        for forbidden in (statement, "private-value", "/private/task/project"):
            self.assertNotIn(forbidden, serialized)

    def test_acceptance_criteria_invalid_id_is_not_echoed(self) -> None:
        criterion_id = "SECRET/INVALID/IDENTIFIER"
        result = validate_acceptance_criteria(
            {"criteria": [{"id": criterion_id, "statement": "Ship"}]}
        )
        self.assertNotIn(criterion_id, json.dumps(result))
        self.assertNotIn(
            "criterion_id",
            result["findings"][0]["evidence_ref"]["identity"],
        )

    def test_acceptance_criteria_valid_id_is_safe_evidence(self) -> None:
        result = validate_acceptance_criteria(
            {"criteria": [{"id": "ac-1", "statement": "Ship\x00now"}]}
        )
        self.assertEqual(
            result["findings"][0]["evidence_ref"]["identity"],
            {"criterion_index": 0, "field": "statement", "criterion_id": "ac-1"},
        )

    def test_acceptance_criteria_finding_order_is_deterministic(self) -> None:
        result = validate_acceptance_criteria(
            {
                "criteria": [
                    {"extra": True},
                    {"id": "AC-2", "statement": "\x00"},
                ]
            }
        )
        self.assertEqual(
            [finding["reason_id"] for finding in result["findings"]],
            [
                "acceptance.criterion.invalid-fields",
                "acceptance.criterion.id-missing",
                "acceptance.criterion.statement-missing",
                "acceptance.criterion.id-invalid",
                "acceptance.criterion.statement-invalid-nul",
            ],
        )

    def test_acceptance_criteria_output_is_deterministic(self) -> None:
        criteria_input = {
            "criteria": [
                {"id": "ac-1", "statement": "First"},
                {"id": "ac-1", "statement": "Second"},
            ]
        }
        first = validate_acceptance_criteria(criteria_input)
        second = validate_acceptance_criteria(criteria_input)
        self.assertEqual(first, second)
        self.assertEqual(
            list(first),
            [
                "schema_version",
                "status",
                "assessment_scope",
                "findings",
                "limitations",
                "truncated",
            ],
        )
        self.assertEqual(
            json.dumps(first, ensure_ascii=False),
            json.dumps(second, ensure_ascii=False),
        )

    def test_acceptance_criteria_reason_ids_are_allowlisted(self) -> None:
        result = validate_acceptance_criteria(
            {"criteria": [{"extra": True} for _index in range(22)]}
        )
        observed = {
            item["reason_id"]
            for key in ("findings", "limitations")
            for item in result[key]
        }
        self.assertLessEqual(observed, ACCEPTANCE_CRITERIA_REASON_IDS)
        self.assertEqual(len(ACCEPTANCE_CRITERIA_REASON_IDS), 19)

    def test_acceptance_criteria_limits_are_stable(self) -> None:
        self.assertEqual(MAX_ACCEPTANCE_CRITERIA, 64)
        self.assertEqual(MAX_CRITERION_ID_BYTES, 64)
        self.assertEqual(MAX_CRITERION_STATEMENT_BYTES, 4096)
        self.assertEqual(MAX_ACCEPTANCE_FINDINGS, 64)

    def test_acceptance_criteria_phase4a_contract_unchanged(self) -> None:
        self.assertEqual(
            analyze_task_readiness("\ufeff" + ("a" * 32_768))["status"],
            "structurally-ready",
        )
        self.assertEqual(
            analyze_task_readiness("\u200b")["status"],
            "structurally-ready",
        )

    def test_acceptance_criteria_phase3_contract_unchanged(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cso-phase3-regression-") as temporary:
            result = analyze_and_recommend(Path(temporary), ROOT)
        self.assertNotIn("acceptance_criteria", result)
        self.assertIn("recommended_skills", result)
        self.assertIn("recommendation_explanations", result)
        self.assertIn("capability_analysis", result)


if __name__ == "__main__":
    unittest.main()

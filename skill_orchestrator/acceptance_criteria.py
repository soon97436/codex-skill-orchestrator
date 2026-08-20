"""Deterministic structural validation for acceptance criteria."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


MAX_ACCEPTANCE_CRITERIA = 64
MAX_CRITERION_ID_BYTES = 64
MAX_CRITERION_STATEMENT_BYTES = 4096
MAX_ACCEPTANCE_FINDINGS = 64

ACCEPTANCE_CRITERIA_REASON_IDS = frozenset(
    {
        "acceptance.criteria.present",
        "acceptance.criteria.missing",
        "acceptance.criteria.empty",
        "acceptance.criteria.invalid-container",
        "acceptance.criteria.too-many",
        "acceptance.criterion.invalid-type",
        "acceptance.criterion.invalid-fields",
        "acceptance.criterion.id-missing",
        "acceptance.criterion.id-invalid",
        "acceptance.criterion.id-duplicate",
        "acceptance.criterion.statement-missing",
        "acceptance.criterion.statement-invalid-type",
        "acceptance.criterion.statement-invalid-unicode",
        "acceptance.criterion.statement-empty",
        "acceptance.criterion.statement-invalid-nul",
        "acceptance.criterion.statement-too-large",
        "acceptance.analysis.finding-limit",
        "acceptance.limit.semantic-quality-not-evaluated",
        "acceptance.limit.satisfaction-not-evaluated",
    }
)

UNICODE_WHITE_SPACE = frozenset(
    chr(codepoint)
    for codepoint in (
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
)

_CRITERION_ID_RE = re.compile(
    r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    flags=re.ASCII,
)
_LIMITATION_REASON_IDS = (
    "acceptance.limit.semantic-quality-not-evaluated",
    "acceptance.limit.satisfaction-not-evaluated",
)
_VALID_TOP_LEVEL_FIELDS = frozenset({"criteria"})
_VALID_CRITERION_FIELDS = frozenset({"id", "statement"})


def _evidence_ref(
    *,
    state: Optional[str] = None,
    criterion_index: Optional[int] = None,
    field: Optional[str] = None,
    criterion_id: Optional[str] = None,
) -> Dict[str, Any]:
    identity: Dict[str, Any] = {}
    if state is not None:
        identity["state"] = state
    if criterion_index is not None:
        identity["criterion_index"] = criterion_index
    if field is not None:
        identity["field"] = field
    if criterion_id is not None:
        identity["criterion_id"] = criterion_id
    return {
        "source": "acceptance-criteria",
        "identity": identity,
    }


def _finding(
    reason_id: str,
    *,
    state: Optional[str] = None,
    criterion_index: Optional[int] = None,
    field: Optional[str] = None,
    criterion_id: Optional[str] = None,
) -> Dict[str, Any]:
    return {
        "reason_id": reason_id,
        "evidence_ref": _evidence_ref(
            state=state,
            criterion_index=criterion_index,
            field=field,
            criterion_id=criterion_id,
        ),
    }


def _result(
    status: str,
    findings: List[Dict[str, Any]],
    *,
    truncated: bool = False,
) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "status": status,
        "assessment_scope": "structural-only",
        "findings": findings,
        "limitations": [
            {"reason_id": reason_id}
            for reason_id in _LIMITATION_REASON_IDS
        ],
        "truncated": truncated,
    }


def _single_result(status: str, reason_id: str, state: str) -> Dict[str, Any]:
    return _result(
        status,
        [_finding(reason_id, state=state)],
    )


def validate_acceptance_criteria(criteria_input: Any) -> Dict[str, Any]:
    """Return content-free structural diagnostics for acceptance criteria."""

    if criteria_input is None:
        return _single_result(
            "needs-criteria",
            "acceptance.criteria.missing",
            "missing",
        )
    if type(criteria_input) is not dict:
        return _single_result(
            "invalid",
            "acceptance.criteria.invalid-container",
            "invalid-container",
        )
    if not criteria_input:
        return _single_result(
            "needs-criteria",
            "acceptance.criteria.missing",
            "missing",
        )
    if set(criteria_input) != _VALID_TOP_LEVEL_FIELDS:
        return _single_result(
            "invalid",
            "acceptance.criteria.invalid-container",
            "invalid-container",
        )

    criteria = criteria_input["criteria"]
    if type(criteria) is not list:
        return _single_result(
            "invalid",
            "acceptance.criteria.invalid-container",
            "invalid-container",
        )
    if not criteria:
        return _single_result(
            "needs-criteria",
            "acceptance.criteria.empty",
            "empty",
        )
    if len(criteria) > MAX_ACCEPTANCE_CRITERIA:
        return _single_result(
            "invalid",
            "acceptance.criteria.too-many",
            "too-many",
        )

    findings: List[Dict[str, Any]] = []
    truncated = False
    invalid = False

    def add_finding(finding: Dict[str, Any]) -> None:
        nonlocal invalid, truncated
        invalid = True
        if truncated:
            return
        if len(findings) < MAX_ACCEPTANCE_FINDINGS:
            findings.append(finding)
            return
        findings[-1] = _finding(
            "acceptance.analysis.finding-limit",
            state="finding-limit",
        )
        truncated = True

    seen_ids = set()
    for criterion_index, criterion in enumerate(criteria):
        if type(criterion) is not dict:
            add_finding(
                _finding(
                    "acceptance.criterion.invalid-type",
                    criterion_index=criterion_index,
                )
            )
            continue

        if any(field not in _VALID_CRITERION_FIELDS for field in criterion):
            add_finding(
                _finding(
                    "acceptance.criterion.invalid-fields",
                    criterion_index=criterion_index,
                )
            )

        criterion_id: Optional[str] = None
        if "id" not in criterion:
            add_finding(
                _finding(
                    "acceptance.criterion.id-missing",
                    criterion_index=criterion_index,
                    field="id",
                )
            )
        else:
            candidate_id = criterion["id"]
            if (
                type(candidate_id) is not str
                or len(candidate_id) > MAX_CRITERION_ID_BYTES
                or not _CRITERION_ID_RE.fullmatch(candidate_id)
            ):
                add_finding(
                    _finding(
                        "acceptance.criterion.id-invalid",
                        criterion_index=criterion_index,
                        field="id",
                    )
                )
            else:
                criterion_id = candidate_id
                if criterion_id in seen_ids:
                    add_finding(
                        _finding(
                            "acceptance.criterion.id-duplicate",
                            criterion_index=criterion_index,
                            field="id",
                            criterion_id=criterion_id,
                        )
                    )
                else:
                    seen_ids.add(criterion_id)

        if "statement" not in criterion:
            add_finding(
                _finding(
                    "acceptance.criterion.statement-missing",
                    criterion_index=criterion_index,
                    field="statement",
                    criterion_id=criterion_id,
                )
            )
            continue

        statement = criterion["statement"]
        if type(statement) is not str:
            add_finding(
                _finding(
                    "acceptance.criterion.statement-invalid-type",
                    criterion_index=criterion_index,
                    field="statement",
                    criterion_id=criterion_id,
                )
            )
            continue
        try:
            encoded_statement = statement.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            add_finding(
                _finding(
                    "acceptance.criterion.statement-invalid-unicode",
                    criterion_index=criterion_index,
                    field="statement",
                    criterion_id=criterion_id,
                )
            )
            continue
        if "\x00" in statement:
            add_finding(
                _finding(
                    "acceptance.criterion.statement-invalid-nul",
                    criterion_index=criterion_index,
                    field="statement",
                    criterion_id=criterion_id,
                )
            )
        if all(character in UNICODE_WHITE_SPACE for character in statement):
            add_finding(
                _finding(
                    "acceptance.criterion.statement-empty",
                    criterion_index=criterion_index,
                    field="statement",
                    criterion_id=criterion_id,
                )
            )
        if len(encoded_statement) > MAX_CRITERION_STATEMENT_BYTES:
            add_finding(
                _finding(
                    "acceptance.criterion.statement-too-large",
                    criterion_index=criterion_index,
                    field="statement",
                    criterion_id=criterion_id,
                )
            )

    if invalid:
        return _result("invalid", findings, truncated=truncated)
    return _single_result(
        "structurally-valid",
        "acceptance.criteria.present",
        "present",
    )

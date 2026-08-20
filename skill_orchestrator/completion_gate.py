"""Deterministic, metadata-only completion evidence evaluation."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from .workflow_selection import select_workflow_profile


__all__ = ["evaluate_completion_gate"]


MAX_COMPLETION_EVIDENCE = 32
MAX_COMPLETION_REASONS = 64
MAX_COMPLETION_REQUIREMENTS = 16
MAX_COMPLETION_LIMITATIONS = 16
MAX_EVIDENCE_ID_BYTES = 64
COMPLETION_REASON_IDS = frozenset(
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
)
COMPLETION_LIMITATION_IDS = frozenset(
    {
        "completion.limit.workflow-intent-not-independently-verified",
        "completion.limit.execution-not-performed",
        "completion.limit.semantic-correctness-not-evaluated",
        "completion.limit.tas" "k-intent-completeness-not-evaluated",
        "completion.limit.evidence-not-independently-verified",
        "workflow.limit.repository-signals-not-verified",
    }
)
_REQUEST_FIELDS = frozenset({"workflow_request", "evidence"})
_EVIDENCE_FIELDS = frozenset(
    {"evidence_id", "requirement_id", "evidence_kind", "outcome"}
)
_EVIDENCE_ID_RE = re.compile(
    r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    flags=re.ASCII,
)
_EVIDENCE_REQUIREMENT_ORDER = (
    "workflow.requirement.change-evidence",
    "workflow.requirement.review-evidence",
    "workflow.requirement.inspection-evidence",
    "workflow.requirement.test-evidence",
    "workflow.requirement.ci-evidence",
)
_EVIDENCE_CONTRACTS = {
    "workflow.requirement.change-evidence": (
        "completion.evidence.change",
        "observed",
        "not-observed",
    ),
    "workflow.requirement.review-evidence": (
        "completion.evidence.review",
        "observed",
        "not-observed",
    ),
    "workflow.requirement.inspection-evidence": (
        "completion.evidence.inspection",
        "observed",
        "not-observed",
    ),
    "workflow.requirement.test-evidence": (
        "completion.evidence.test",
        "pass",
        "fail",
    ),
    "workflow.requirement.ci-evidence": (
        "completion.evidence.ci",
        "pass",
        "fail",
    ),
}
_BASE_LIMITATION_IDS = (
    "completion.limit.workflow-intent-not-independently-verified",
    "completion.limit.execution-not-performed",
    "completion.limit.semantic-correctness-not-evaluated",
    "completion.limit.tas" "k-intent-completeness-not-evaluated",
)


def _reason(reason_id: str, source: str, identity: Dict[str, str]) -> Dict[str, Any]:
    return {
        "reason_id": reason_id,
        "evidence_ref": {"source": source, "identity": identity},
    }


def _result(
    status: str,
    reasons: List[Dict[str, Any]],
    *,
    selected_workflow_profile: Optional[Dict[str, str]] = None,
    requirements: Sequence[str] = (),
    covered_requirements: Sequence[str] = (),
    uncovered_requirements: Sequence[Dict[str, str]] = (),
    limitations: Sequence[str] = (),
) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "status": status,
        "assessment_scope": "deterministic-completion-evidence",
        "selected_workflow_profile": selected_workflow_profile,
        "requirements": [
            {"requirement_id": requirement_id}
            for requirement_id in requirements
        ],
        "covered_requirements": [
            {"requirement_id": requirement_id}
            for requirement_id in covered_requirements
        ],
        "uncovered_requirements": list(uncovered_requirements),
        "reasons": reasons,
        "limitations": [
            {"reason_id": reason_id}
            for reason_id in (*_BASE_LIMITATION_IDS, *limitations)
        ],
        "truncated": False,
    }


def _workflow_selected_reason(profile_id: str) -> Dict[str, Any]:
    return _reason(
        "completion.workflow.selected",
        "workflow-selection",
        {"profile_id": profile_id},
    )


def _requirement_reason(
    reason_id: str,
    requirement_id: str,
    state: str,
) -> Dict[str, Any]:
    return _reason(
        reason_id,
        "completion-requirement",
        {"requirement_id": requirement_id, "state": state},
    )


def _acceptance_criteria_reason(requirement_id: str) -> Dict[str, Any]:
    return _reason(
        "completion.requirement.covered",
        "acceptance-criteria",
        {"requirement_id": requirement_id, "status": "structurally-valid"},
    )


def _caller_evidence_reason(
    reason_id: str,
    record: Dict[str, str],
) -> Dict[str, Any]:
    return _reason(
        reason_id,
        "completion-evidence",
        {
            "evidence_id": record["evidence_id"],
            "requirement_id": record["requirement_id"],
            "evidence_kind": record["evidence_kind"],
            "outcome": record["outcome"],
        },
    )


def _validated_evidence_record(value: Any) -> Optional[Dict[str, str]]:
    if (
        type(value) is not dict
        or any(type(key) is not str for key in value)
        or set(value) != _EVIDENCE_FIELDS
    ):
        return None
    if any(type(value[field]) is not str for field in _EVIDENCE_FIELDS):
        return None
    evidence_id = value["evidence_id"]
    requirement_id = value["requirement_id"]
    evidence_kind = value["evidence_kind"]
    outcome = value["outcome"]
    try:
        encoded_evidence_id = evidence_id.encode("utf-8", errors="strict")
    except UnicodeEncodeError:
        return None
    if (
        len(encoded_evidence_id) > MAX_EVIDENCE_ID_BYTES
        or not _EVIDENCE_ID_RE.fullmatch(evidence_id)
    ):
        return None
    contract = _EVIDENCE_CONTRACTS.get(requirement_id)
    if contract is None:
        return None
    required_kind, positive_outcome, negative_outcome = contract
    if evidence_kind != required_kind:
        return None
    if outcome not in (positive_outcome, negative_outcome, "inconclusive"):
        return None
    return {
        "evidence_id": evidence_id,
        "requirement_id": requirement_id,
        "evidence_kind": evidence_kind,
        "outcome": outcome,
    }


def _conditional_limitations(
    workflow_result: Dict[str, Any],
    *,
    evidence_consumed: bool,
) -> Sequence[str]:
    limitation_ids = []
    if evidence_consumed:
        limitation_ids.append(
            "completion.limit.evidence-not-independently-verified"
        )
    if any(
        item.get("reason_id")
        == "workflow.limit.repository-signals-not-verified"
        for item in workflow_result.get("limitations", ())
        if type(item) is dict
    ):
        limitation_ids.append("workflow.limit.repository-signals-not-verified")
    return tuple(limitation_ids)


def evaluate_completion_gate(request: Any) -> Dict[str, Any]:
    """Evaluate deterministic structured evidence coverage without execution."""

    if (
        type(request) is not dict
        or any(type(key) is not str for key in request)
        or any(key not in _REQUEST_FIELDS for key in request)
    ):
        return _result(
            "invalid",
            [
                _reason(
                    "completion.request.invalid",
                    "completion-request",
                    {"state": "invalid"},
                )
            ],
        )
    if "workflow_request" not in request:
        return _result(
            "needs-input",
            [
                _reason(
                    "completion.workflow.needs-input",
                    "workflow-selection",
                    {"state": "missing"},
                )
            ],
        )
    workflow_result = select_workflow_profile(request["workflow_request"])
    workflow_status = workflow_result["status"]
    if workflow_status != "selected":
        result_status = "invalid" if workflow_status == "invalid" else "needs-input"
        reason_id = (
            "completion.workflow.invalid"
            if workflow_status == "invalid"
            else "completion.workflow.needs-input"
        )
        return _result(
            result_status,
            [
                _reason(
                    reason_id,
                    "workflow-selection",
                    {"status": workflow_status},
                )
            ],
            limitations=_conditional_limitations(
                workflow_result,
                evidence_consumed=False,
            ),
        )
    profile = workflow_result["selected_workflow_profile"]
    profile_id = profile["profile_id"]
    requirements = tuple(
        item["requirement_id"] for item in workflow_result["requirements"]
    )
    evidence = request.get("evidence")
    if evidence is None:
        evidence = []
    if type(evidence) is not list or len(evidence) > MAX_COMPLETION_EVIDENCE:
        return _result(
            "invalid",
            [
                _workflow_selected_reason(profile_id),
                _reason(
                    "completion.evidence.invalid",
                    "completion-evidence",
                    {"state": "invalid-container"},
                ),
            ],
            selected_workflow_profile={"profile_id": profile_id},
            requirements=requirements,
            limitations=_conditional_limitations(
                workflow_result,
                evidence_consumed=False,
            ),
        )
    validated_evidence = []
    invalid_record_found = False
    for record in evidence:
        validated_record = _validated_evidence_record(record)
        if validated_record is None:
            invalid_record_found = True
            continue
        validated_evidence.append(validated_record)
    if invalid_record_found:
        return _result(
            "invalid",
            [
                _workflow_selected_reason(profile_id),
                _reason(
                    "completion.evidence.invalid",
                    "completion-evidence",
                    {"state": "invalid-record"},
                ),
            ],
            selected_workflow_profile={"profile_id": profile_id},
            requirements=requirements,
            limitations=_conditional_limitations(
                workflow_result,
                evidence_consumed=bool(validated_evidence),
            ),
        )
    validated_evidence.sort(key=lambda item: item["evidence_id"])
    evidence_ids = [record["evidence_id"] for record in validated_evidence]
    if len(evidence_ids) != len(set(evidence_ids)):
        return _result(
            "invalid",
            [
                _workflow_selected_reason(profile_id),
                _reason(
                    "completion.evidence.duplicate",
                    "completion-evidence",
                    {"state": "duplicate"},
                ),
            ],
            selected_workflow_profile={"profile_id": profile_id},
            requirements=requirements,
            limitations=_conditional_limitations(
                workflow_result,
                evidence_consumed=True,
            ),
        )
    for requirement_id in _EVIDENCE_REQUIREMENT_ORDER:
        matching_records = [
            record
            for record in validated_evidence
            if record["requirement_id"] == requirement_id
        ]
        if len(matching_records) <= 1:
            continue
        signatures = {
            (record["evidence_kind"], record["outcome"])
            for record in matching_records
        }
        reason_id = (
            "completion.evidence.duplicate"
            if len(signatures) == 1
            else "completion.evidence.conflict"
        )
        return _result(
            "invalid",
            [
                _workflow_selected_reason(profile_id),
                _reason(
                    reason_id,
                    "completion-evidence",
                    {
                        "requirement_id": requirement_id,
                        "state": "duplicate" if len(signatures) == 1 else "conflict",
                    },
                ),
            ],
            selected_workflow_profile={"profile_id": profile_id},
            requirements=requirements,
            limitations=_conditional_limitations(
                workflow_result,
                evidence_consumed=True,
            ),
        )
    if any(
        record["requirement_id"] not in requirements
        for record in validated_evidence
    ):
        return _result(
            "invalid",
            [
                _workflow_selected_reason(profile_id),
                _reason(
                    "completion.evidence.invalid",
                    "completion-evidence",
                    {"state": "unexpected-requirement"},
                ),
            ],
            selected_workflow_profile={"profile_id": profile_id},
            requirements=requirements,
            limitations=_conditional_limitations(
                workflow_result,
                evidence_consumed=bool(validated_evidence),
            ),
        )
    evidence_by_requirement = {
        record["requirement_id"]: record for record in validated_evidence
    }
    acceptance_requirement = "workflow.requirement.acceptance-criteria"
    covered = []
    uncovered = []
    reasons = [_workflow_selected_reason(profile_id)]
    for requirement_id in requirements:
        if requirement_id == acceptance_requirement:
            covered.append(requirement_id)
            reasons.append(_acceptance_criteria_reason(requirement_id))
        elif requirement_id in evidence_by_requirement:
            record = evidence_by_requirement[requirement_id]
            positive_outcome = _EVIDENCE_CONTRACTS[requirement_id][1]
            if record["outcome"] == positive_outcome:
                covered.append(requirement_id)
                reasons.append(
                    _caller_evidence_reason(
                        "completion.requirement.covered",
                        record,
                    )
                )
            else:
                state = (
                    "negative"
                    if record["outcome"] == _EVIDENCE_CONTRACTS[requirement_id][2]
                    else "inconclusive"
                )
                uncovered.append(
                    {"requirement_id": requirement_id, "state": state}
                )
                reasons.append(
                    _caller_evidence_reason(
                        (
                            "completion.requirement.negative"
                            if state == "negative"
                            else "completion.requirement.inconclusive"
                        ),
                        record,
                    )
                )
        else:
            uncovered.append(
                {"requirement_id": requirement_id, "state": "missing"}
            )
            reasons.append(
                _requirement_reason(
                    "completion.requirement.missing",
                    requirement_id,
                    "missing",
                )
            )
    complete = not uncovered
    reasons.append(
        _reason(
            (
                "completion.gate.evidence-complete"
                if complete
                else "completion.gate.incomplete"
            ),
            "completion-gate",
            {"state": "evidence-complete" if complete else "incomplete"},
        )
    )
    return _result(
        "evidence-complete" if complete else "incomplete",
        reasons,
        selected_workflow_profile={"profile_id": profile_id},
        requirements=requirements,
        covered_requirements=covered,
        uncovered_requirements=uncovered,
        limitations=_conditional_limitations(
            workflow_result,
            evidence_consumed=bool(validated_evidence),
        ),
    )

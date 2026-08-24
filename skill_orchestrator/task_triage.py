"""Deterministic, metadata-only task triage across five safety layers."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .task_readiness import analyze_task_readiness


__all__ = ["analyze_task_triage"]


_REQUEST_FIELDS = frozenset({"task_input", "requirements"})
_REQUIREMENT_ORDER = (
    "context",
    "harness",
    "loop",
    "graph",
    "human_approval",
)
_REQUIREMENT_REASON_IDS = {
    "context": "triage.requirement.context",
    "harness": "triage.requirement.harness",
    "loop": "triage.requirement.loop",
    "graph": "triage.requirement.graph",
    "human_approval": "triage.requirement.human-approval",
}
_TASK_READINESS_STATES = {
    "missing",
    "empty",
    "invalid-type",
    "invalid-utf8",
    "invalid-nul",
    "too-large",
    "present",
}
_BASE_LIMITATION_IDS = (
    "triage.limit.semantic-intent-not-inferred",
    "triage.limit.execution-not-performed",
    "triage.limit.requirements-caller-declared",
)
TRIAGE_LIMITATION_IDS = frozenset(
    {
        *_BASE_LIMITATION_IDS,
        "triage.limit.human-approval-not-granted",
        "triage.limit.graph-orchestration-not-implemented",
    }
)
TRIAGE_REASON_IDS = frozenset(
    {
        "triage.request.invalid",
        "triage.task.needs-input",
        "triage.requirements.needs-input",
        "triage.task.invalid",
        "triage.task.structurally-ready",
        *_REQUIREMENT_REASON_IDS.values(),
        "triage.layer.selected",
    }
)


def _reason(
    reason_id: str,
    source: str,
    identity: Dict[str, str],
) -> Dict[str, Any]:
    return {
        "reason_id": reason_id,
        "evidence_ref": {"source": source, "identity": identity},
    }


def _result(
    status: str,
    reasons: List[Dict[str, Any]],
    *,
    selected_layer: Optional[str] = None,
    additional_limitations: Sequence[str] = (),
) -> Dict[str, Any]:
    limitation_ids = list(_BASE_LIMITATION_IDS)
    for reason_id in additional_limitations:
        if reason_id not in limitation_ids:
            limitation_ids.append(reason_id)
    return {
        "schema_version": 1,
        "status": status,
        "assessment_scope": "deterministic-task-triage",
        "selected_layer": selected_layer,
        "reasons": reasons,
        "limitations": [
            {"reason_id": reason_id} for reason_id in limitation_ids
        ],
        "truncated": False,
    }


def _invalid_request() -> Dict[str, Any]:
    return _result(
        "invalid",
        [
            _reason(
                "triage.request.invalid",
                "triage-request",
                {"state": "invalid"},
            )
        ],
    )


def _readiness_reason(task_report: Dict[str, Any]) -> Dict[str, Any]:
    state = "invalid"
    reasons = task_report.get("reasons")
    if type(reasons) is list and reasons:
        first = reasons[0]
        if type(first) is dict:
            evidence_ref = first.get("evidence_ref")
            if type(evidence_ref) is dict:
                identity = evidence_ref.get("identity")
                if (
                    type(identity) is dict
                    and identity.get("state") in _TASK_READINESS_STATES
                ):
                    state = identity["state"]
    status = task_report.get("status")
    if status == "structurally-ready":
        reason_id = "triage.task.structurally-ready"
    elif status == "needs-input":
        reason_id = "triage.task.needs-input"
    else:
        reason_id = "triage.task.invalid"
        state = state if state != "present" else "invalid"
    return _reason(
        reason_id,
        "task-readiness",
        {"state": state},
    )


def _validated_requirements(value: Any) -> Optional[Tuple[str, ...]]:
    if type(value) is not dict:
        return None
    if any(type(key) is not str for key in value):
        return None
    if any(key not in _REQUIREMENT_ORDER for key in value):
        return None
    if any(type(value[key]) is not bool for key in value):
        return None
    return tuple(
        requirement
        for requirement in _REQUIREMENT_ORDER
        if value.get(requirement, False)
    )


def _selected_layer(requirements: Sequence[str]) -> str:
    if "graph" in requirements or "human_approval" in requirements:
        return "L5"
    if "loop" in requirements:
        return "L4"
    if "harness" in requirements:
        return "L3"
    if "context" in requirements:
        return "L2"
    return "L1"


def analyze_task_triage(request: Any) -> Dict[str, Any]:
    """Select a safety layer from an explicit, bounded triage request.

    The function validates structure and task-input readiness only. It does
    not inspect task wording, execute task content, access a repository, or
    resolve skills and workflow profiles.
    """

    if (
        type(request) is not dict
        or any(type(key) is not str for key in request)
        or any(key not in _REQUEST_FIELDS for key in request)
    ):
        return _invalid_request()

    task_report = analyze_task_readiness(request.get("task_input"))
    task_status = task_report["status"]
    task_reason = _readiness_reason(task_report)
    if task_status != "structurally-ready":
        return _result(
            "invalid" if task_status == "invalid" else "needs-input",
            [task_reason],
        )

    if "requirements" not in request:
        return _result(
            "needs-input",
            [
                task_reason,
                _reason(
                    "triage.requirements.needs-input",
                    "triage-requirements",
                    {"state": "missing"},
                ),
            ],
        )

    requirements = _validated_requirements(request["requirements"])
    if requirements is None:
        return _invalid_request()

    layer = _selected_layer(requirements)
    reasons: List[Dict[str, Any]] = [task_reason]
    reasons.extend(
        _reason(
            _REQUIREMENT_REASON_IDS[requirement],
            "triage-requirements",
            {"requirement": requirement},
        )
        for requirement in requirements
    )
    reasons.append(
        _reason(
            "triage.layer.selected",
            "triage-layer",
            {"layer": layer},
        )
    )
    limitations: List[str] = []
    if layer == "L5":
        limitations.extend(
            (
                "triage.limit.human-approval-not-granted",
                "triage.limit.graph-orchestration-not-implemented",
            )
        )
    return _result(
        "selected",
        reasons,
        selected_layer=layer,
        additional_limitations=limitations,
    )

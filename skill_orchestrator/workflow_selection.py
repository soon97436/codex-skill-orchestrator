"""Deterministic, content-free workflow profile selection."""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .acceptance_criteria import validate_acceptance_criteria
from .task_readiness import analyze_task_readiness


__all__ = ["select_workflow_profile"]


MAX_WORKFLOW_PROFILES = 16
MAX_WORKFLOW_REASONS = 64
MAX_WORKFLOW_REQUIREMENTS = 16
MAX_WORKFLOW_LIMITATIONS = 16
MAX_REPOSITORY_SIGNALS_CONSUMED = 16

_BASE_LIMITATION_IDS = (
    "workflow.limit.semantic-intent-not-inferred",
    "workflow.limit.execution-not-performed",
    "workflow.limit.completion-not-evaluated",
)
_REQUEST_FIELDS = frozenset(
    {"workflow_kind", "task_input", "criteria_input", "repository_signals"}
)
_WORKFLOW_KINDS = ("change", "review", "inspect")
_REPOSITORY_SIGNAL_IDS = (
    "repository.tests-present",
    "repository.ci-present",
)
_REPOSITORY_REASON_IDS = {
    "repository.tests-present": "workflow.repository.tests-present",
    "repository.ci-present": "workflow.repository.ci-present",
}
_REPOSITORY_REQUIREMENT_IDS = {
    "repository.tests-present": "workflow.requirement.test-evidence",
    "repository.ci-present": "workflow.requirement.ci-evidence",
}
_WORKFLOW_REQUIREMENT_IDS = frozenset(
    {
        "workflow.requirement.acceptance-criteria",
        "workflow.requirement.change-evidence",
        "workflow.requirement.review-evidence",
        "workflow.requirement.inspection-evidence",
        "workflow.requirement.test-evidence",
        "workflow.requirement.ci-evidence",
    }
)
_WORKFLOW_REASON_IDS = frozenset(
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
)
_WORKFLOW_LIMITATION_IDS = frozenset(
    {
        *_BASE_LIMITATION_IDS,
        "workflow.limit.repository-signals-not-verified",
    }
)
_BASE_REQUIREMENTS_BY_KIND = {
    "change": (
        "workflow.requirement.acceptance-criteria",
        "workflow.requirement.change-evidence",
    ),
    "review": ("workflow.requirement.review-evidence",),
    "inspect": ("workflow.requirement.inspection-evidence",),
}
_PROFILE_IDS_BY_KIND = {
    "change": "workflow-change",
    "review": "workflow-review",
    "inspect": "workflow-inspect",
}
_PROFILE_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$", flags=re.ASCII)
_WORKFLOW_PROFILES = (
    (
        "change",
        "workflow-change",
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


def _reason(reason_id: str, source: str, identity: Dict[str, str]) -> Dict[str, Any]:
    return {
        "reason_id": reason_id,
        "evidence_ref": {
            "source": source,
            "identity": identity,
        },
    }


def _result(
    status: str,
    reasons: List[Dict[str, Any]],
    *,
    selected_profile_id: Optional[str] = None,
    requirements: Sequence[str] = (),
    repository_signals: Sequence[str] = (),
) -> Dict[str, Any]:
    limitation_ids = list(_BASE_LIMITATION_IDS)
    if repository_signals:
        limitation_ids.append("workflow.limit.repository-signals-not-verified")
    return {
        "schema_version": 1,
        "status": status,
        "assessment_scope": "deterministic-workflow-selection",
        "selected_workflow_profile": (
            {"profile_id": selected_profile_id}
            if selected_profile_id is not None
            else None
        ),
        "requirements": [
            {"requirement_id": requirement_id}
            for requirement_id in requirements
        ],
        "reasons": reasons,
        "limitations": [
            {"reason_id": reason_id} for reason_id in limitation_ids
        ],
        "truncated": False,
    }


def _intent_reason(workflow_kind: str) -> Dict[str, Any]:
    return _reason(
        "workflow.intent.explicit",
        "workflow-intent",
        {"workflow_kind": workflow_kind},
    )


def _task_reason(status: str) -> Dict[str, Any]:
    reason_id = {
        "structurally-ready": "workflow.task.structurally-ready",
        "needs-input": "workflow.task.needs-input",
        "invalid": "workflow.task.invalid",
    }[status]
    return _reason(
        reason_id,
        "task-readiness",
        {"status": status},
    )


def _criteria_reason(reason_id: str, status: str) -> Dict[str, Any]:
    return _reason(
        reason_id,
        "acceptance-criteria",
        {"status": status},
    )


def _validated_repository_signals(value: Any) -> Optional[Tuple[str, ...]]:
    if type(value) is not list or len(value) > MAX_REPOSITORY_SIGNALS_CONSUMED:
        return None
    if any(type(signal) is not str for signal in value):
        return None
    if len(set(value)) != len(value):
        return None
    if any(signal not in _REPOSITORY_SIGNAL_IDS for signal in value):
        return None
    return tuple(signal for signal in _REPOSITORY_SIGNAL_IDS if signal in value)


def _optional_criteria_absent(value: Any) -> bool:
    if value is None:
        return True
    return (
        type(value) is dict
        and tuple(value.keys()) == ("criteria",)
        and type(value["criteria"]) is list
        and not value["criteria"]
    )


def _select_catalog_entry(
    workflow_kind: str,
) -> Tuple[str, Optional[Tuple[str, Tuple[str, ...]]]]:
    catalog = _WORKFLOW_PROFILES
    if type(catalog) is not tuple or len(catalog) > MAX_WORKFLOW_PROFILES:
        return "invalid", None
    matches: List[Tuple[str, Tuple[str, ...]]] = []
    catalog_entries: List[Tuple[str, str, Tuple[str, ...]]] = []
    kind_counts = {kind: 0 for kind in _WORKFLOW_KINDS}
    profile_ids = set()
    for entry in catalog:
        if type(entry) is not tuple or len(entry) != 3:
            return "invalid", None
        candidate_kind, profile_id, requirements = entry
        if (
            type(candidate_kind) is not str
            or candidate_kind not in _WORKFLOW_KINDS
            or type(profile_id) is not str
            or not _PROFILE_ID_RE.fullmatch(profile_id)
            or type(requirements) is not tuple
            or len(requirements) > MAX_WORKFLOW_REQUIREMENTS
            or any(type(item) is not str for item in requirements)
            or any(item not in _WORKFLOW_REQUIREMENT_IDS for item in requirements)
            or len(set(requirements)) != len(requirements)
            or profile_id in profile_ids
        ):
            return "invalid", None
        profile_ids.add(profile_id)
        kind_counts[candidate_kind] += 1
        catalog_entries.append((candidate_kind, profile_id, requirements))
        if candidate_kind == workflow_kind:
            matches.append((profile_id, requirements))
    if len(matches) > 1:
        return "ambiguous", None
    if any(kind_counts[kind] != 1 for kind in _WORKFLOW_KINDS):
        return "invalid", None
    if any(
        profile_id != _PROFILE_IDS_BY_KIND[candidate_kind]
        or requirements != _BASE_REQUIREMENTS_BY_KIND[candidate_kind]
        for candidate_kind, profile_id, requirements in catalog_entries
    ):
        return "invalid", None
    if not matches:
        return "invalid", None
    return "valid", matches[0]


def select_workflow_profile(request: Any) -> Dict[str, Any]:
    """Select one deterministic workflow profile without executing it."""

    if type(request) is not dict or any(type(key) is not str for key in request):
        return _result(
            "invalid",
            [
                _reason(
                    "workflow.request.invalid",
                    "workflow-request",
                    {"state": "invalid"},
                )
            ],
        )
    if any(key not in _REQUEST_FIELDS for key in request):
        return _result(
            "invalid",
            [
                _reason(
                    "workflow.request.invalid",
                    "workflow-request",
                    {"state": "invalid"},
                )
            ],
        )
    if "workflow_kind" not in request:
        return _result(
            "needs-input",
            [
                _reason(
                    "workflow.intent.missing",
                    "workflow-intent",
                    {"state": "missing"},
                )
            ],
        )
    workflow_kind = request["workflow_kind"]
    if type(workflow_kind) is not str or workflow_kind not in _WORKFLOW_KINDS:
        return _result(
            "invalid",
            [
                _reason(
                    "workflow.intent.invalid",
                    "workflow-intent",
                    {"state": "invalid"},
                )
            ],
        )
    repository_signals = _validated_repository_signals(
        request.get("repository_signals", [])
    )
    if repository_signals is None:
        return _result(
            "invalid",
            [
                _intent_reason(workflow_kind),
                _reason(
                    "workflow.repository.signals-invalid",
                    "repository-signals",
                    {"state": "invalid"},
                ),
            ],
        )

    task_report = analyze_task_readiness(request.get("task_input"))
    task_status = task_report["status"]
    if task_status != "structurally-ready":
        return _result(
            "invalid" if task_status == "invalid" else "needs-input",
            [_intent_reason(workflow_kind), _task_reason(task_status)],
            repository_signals=repository_signals,
        )

    criteria_input = request.get("criteria_input")
    if workflow_kind in ("review", "inspect") and _optional_criteria_absent(
        criteria_input
    ):
        criteria_reason = _criteria_reason(
            "workflow.criteria.not-required",
            "not-required",
        )
    else:
        criteria_report = validate_acceptance_criteria(criteria_input)
        criteria_status = criteria_report["status"]
        if criteria_status != "structurally-valid":
            if workflow_kind == "change" and criteria_status == "needs-criteria":
                result_status = "needs-input"
                criteria_reason_id = "workflow.criteria.needs-input"
            else:
                result_status = "invalid"
                criteria_reason_id = "workflow.criteria.invalid"
            return _result(
                result_status,
                [
                    _intent_reason(workflow_kind),
                    _task_reason(task_status),
                    _criteria_reason(criteria_reason_id, criteria_status),
                ],
                repository_signals=repository_signals,
            )
        criteria_reason = _criteria_reason(
            "workflow.criteria.structurally-valid",
            criteria_status,
        )

    catalog_status, catalog_entry = _select_catalog_entry(workflow_kind)
    if catalog_entry is None:
        catalog_reason_id = (
            "workflow.catalog.ambiguous"
            if catalog_status == "ambiguous"
            else "workflow.catalog.invalid"
        )
        return _result(
            "invalid",
            [
                _intent_reason(workflow_kind),
                _task_reason(task_status),
                criteria_reason,
                _reason(
                    catalog_reason_id,
                    "workflow-catalog",
                    {"state": catalog_status},
                ),
            ],
            repository_signals=repository_signals,
        )

    profile_id, base_requirements = catalog_entry
    requirements = list(base_requirements)
    for signal in repository_signals:
        requirement_id = _REPOSITORY_REQUIREMENT_IDS[signal]
        if requirement_id not in requirements:
            requirements.append(requirement_id)
    if len(requirements) > MAX_WORKFLOW_REQUIREMENTS:
        return _result(
            "invalid",
            [
                _intent_reason(workflow_kind),
                _task_reason(task_status),
                criteria_reason,
                _reason(
                    "workflow.catalog.invalid",
                    "workflow-catalog",
                    {"state": "invalid"},
                ),
            ],
            repository_signals=repository_signals,
        )

    reasons = [
        _intent_reason(workflow_kind),
        _task_reason(task_status),
        criteria_reason,
    ]
    reasons.extend(
        _reason(
            _REPOSITORY_REASON_IDS[signal],
            "repository-signals",
            {"signal_id": signal},
        )
        for signal in repository_signals
    )
    reasons.append(
        _reason(
            "workflow.profile.selected",
            "workflow-profile",
            {"profile_id": profile_id},
        )
    )
    if len(reasons) > MAX_WORKFLOW_REASONS:
        return _result(
            "invalid",
            [
                _reason(
                    "workflow.catalog.invalid",
                    "workflow-catalog",
                    {"state": "invalid"},
                )
            ],
            repository_signals=repository_signals,
        )
    return _result(
        "selected",
        reasons,
        selected_profile_id=profile_id,
        requirements=requirements,
        repository_signals=repository_signals,
    )

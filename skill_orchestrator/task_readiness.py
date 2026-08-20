"""Deterministic structural readiness analysis for untrusted task input."""

from __future__ import annotations

from typing import Any, Dict, Optional, Union


MAX_TASK_INPUT_BYTES = 32_768

_DECISIONS = {
    "present": ("structurally-ready", "task.readiness.input-present"),
    "missing": ("needs-input", "task.readiness.input-missing"),
    "empty": ("needs-input", "task.readiness.input-empty"),
    "invalid-type": ("invalid", "task.readiness.input-invalid-type"),
    "invalid-utf8": ("invalid", "task.readiness.input-invalid-utf8"),
    "invalid-nul": ("invalid", "task.readiness.input-invalid-nul"),
    "too-large": ("invalid", "task.readiness.input-too-large"),
}
SEMANTIC_LIMITATION_REASON_ID = (
    "task.readiness.limit.semantic-sufficiency-not-evaluated"
)
TASK_READINESS_REASON_IDS = frozenset(
    [reason_id for _status, reason_id in _DECISIONS.values()]
    + [SEMANTIC_LIMITATION_REASON_ID]
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


def _result(state: str) -> Dict[str, Any]:
    status, reason_id = _DECISIONS[state]
    return {
        "schema_version": 1,
        "status": status,
        "assessment_scope": "structural-only",
        "reasons": [
            {
                "reason_id": reason_id,
                "evidence_ref": {
                    "source": "task-input",
                    "identity": {"state": state},
                },
            }
        ],
        "limitations": [{"reason_id": SEMANTIC_LIMITATION_REASON_ID}],
        "truncated": False,
    }


def analyze_task_readiness(
    task_input: Optional[Union[str, bytes]],
) -> Dict[str, Any]:
    """Return a metadata-only structural readiness decision for *task_input*."""

    if task_input is None:
        return _result("missing")
    if not isinstance(task_input, (str, bytes)):
        return _result("invalid-type")
    if isinstance(task_input, str):
        if task_input.startswith("\ufeff"):
            task_input = task_input[1:]
        if len(task_input) > MAX_TASK_INPUT_BYTES:
            return _result("too-large")
        try:
            encoded = task_input.encode("utf-8", errors="strict")
        except UnicodeEncodeError:
            return _result("invalid-utf8")
        if len(encoded) > MAX_TASK_INPUT_BYTES:
            return _result("too-large")
    else:
        if task_input.startswith(b"\xef\xbb\xbf"):
            task_input = task_input[3:]
        if len(task_input) > MAX_TASK_INPUT_BYTES:
            return _result("too-large")
        try:
            task_input = task_input.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            return _result("invalid-utf8")
    if "\x00" in task_input:
        return _result("invalid-nul")
    if all(
        character in UNICODE_WHITE_SPACE for character in task_input
    ):
        return _result("empty")
    return _result("present")

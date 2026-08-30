"""Read-only candidate recovery observations.

This module combines existing durable-journal and durable-stage observations.
It never grants recovery authority and never reads, writes, or defines
installed-state storage.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

from .durable_journal import RecoveryRecord, scan_durable_journals
from .durable_stage_reopen import OBSERVATION_STATUSES, observe_durable_stage


ASSESSMENT_STATUSES = ("clean", "recovery-required", "unsupported")
INSTALLED_STATE_CAPABILITIES = ("not-implemented",)
STAGE_ASSESSMENT_STATUSES = OBSERVATION_STATUSES + ("not-observed",)
MAX_ASSESSMENT_RECORDS = 1024


@dataclass(frozen=True)
class RecoveryAssessmentRecord:
    """One non-authoritative journal and optional stage observation."""

    transaction_id: Optional[str]
    phase: Optional[str]
    journal_status: str
    stage_status: str
    installed_state_capability: str
    reason_ids: Tuple[str, ...]


@dataclass(frozen=True)
class DurableRecoveryAssessment:
    """Bounded observations only; this result is never recovery authority."""

    status: str
    records: Tuple[RecoveryAssessmentRecord, ...]
    installed_state_capability: str
    reason_ids: Tuple[str, ...]
    truncated: bool = False


def _record(skills_root: Path, record: RecoveryRecord) -> RecoveryAssessmentRecord:
    stage_status = "not-observed"
    reason_ids = list(record.reason_ids)
    if record.status == "terminal":
        stage_status = "not-applicable"
    elif record.transaction_id is not None:
        stage = observe_durable_stage(skills_root, record.transaction_id)
        stage_status = stage.status
        reason_ids.extend(stage.reason_ids)
    return RecoveryAssessmentRecord(
        transaction_id=record.transaction_id,
        phase=record.phase,
        journal_status=record.status,
        stage_status=stage_status,
        installed_state_capability="not-implemented",
        reason_ids=tuple(reason_ids),
    )


def assess_durable_recovery(skills_root: Path) -> DurableRecoveryAssessment:
    """Return current durable observations without changing any filesystem state.

    Journal scanning and stage observation are separate reads.  A returned
    stage status has no continuity guarantee and does not authorize recovery,
    publication, rollback, cleanup, or target mutation.
    """

    if os.name != "posix":
        return DurableRecoveryAssessment(
            "unsupported", (), "not-implemented", ("platform.unsupported",)
        )
    scan = scan_durable_journals(skills_root)
    if scan.status == "unsupported":
        return DurableRecoveryAssessment(
            "unsupported", (), "not-implemented", scan.reason_ids
        )
    records = scan.records[:MAX_ASSESSMENT_RECORDS]
    truncated = len(scan.records) > MAX_ASSESSMENT_RECORDS
    assessed = tuple(_record(skills_root, record) for record in records)
    reasons = list(scan.reason_ids)
    if truncated:
        reasons.append("assessment.record-limit-exceeded")
    return DurableRecoveryAssessment(
        scan.status,
        assessed,
        "not-implemented",
        tuple(reasons),
        truncated,
    )


__all__ = [
    "ASSESSMENT_STATUSES",
    "INSTALLED_STATE_CAPABILITIES",
    "MAX_ASSESSMENT_RECORDS",
    "STAGE_ASSESSMENT_STATUSES",
    "DurableRecoveryAssessment",
    "RecoveryAssessmentRecord",
    "assess_durable_recovery",
]

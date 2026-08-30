"""Read-only observation of a stage named by a durable candidate journal.

This module never recreates an owned-stage lease and never grants recovery,
publication, target, or authorization authority.  It observes only the
existing ``.cso-staging`` entry named by a validated journal binding.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

from .durable_journal import load_durable_journal
from .errors import IntegrityError, SecurityError, ValidationError
from .transaction_journal import TERMINAL_PHASES, validate_transaction_id
from .transactional_fs import inspect_declared_stage


OBSERVATION_STATUSES = (
    "matching",
    "missing",
    "unsafe",
    "unstable",
    "not-applicable",
    "unsupported",
)


@dataclass(frozen=True)
class DurableStageObservation:
    """Bounded, non-authoritative result of one stage observation."""

    status: str
    reason_ids: Tuple[str, ...]


def _result(status: str, reason_id: str) -> DurableStageObservation:
    return DurableStageObservation(status, (reason_id,))


def observe_durable_stage(
    skills_root: Path, transaction_id: str
) -> DurableStageObservation:
    """Observe the current bytes of a bound stage without any filesystem write.

    A ``matching`` result says only that the stage bytes and structure observed
    during this call match the durable journal's immutable manifest.  It is not
    a restored lease, authorization, target admission, mutation permission, or
    recovery decision.
    """

    if os.name != "posix":
        return _result("unsupported", "platform.unsupported")
    try:
        transaction_id = validate_transaction_id(transaction_id)
        document = load_durable_journal(skills_root, transaction_id)
    except (IntegrityError, SecurityError, ValidationError, OSError):
        return _result("unsafe", "journal.unsafe")

    binding = document["stage_binding"]
    if document["phase"] in TERMINAL_PHASES or binding is None:
        return _result("not-applicable", "stage.not-applicable")
    if binding["manifest_digest"] != document["new_manifest_digest"]:
        return _result("unsafe", "stage.binding-manifest-mismatch")

    observation = inspect_declared_stage(
        skills_root,
        binding["relative_name"],
        document["new_manifest"],
        document["skills_root_identity"],
    )
    return _result(observation.status, "stage." + observation.status)


__all__ = [
    "DurableStageObservation",
    "OBSERVATION_STATUSES",
    "observe_durable_stage",
]

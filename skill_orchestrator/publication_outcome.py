"""Pure metadata contract for future candidate-publication outcomes.

This module validates descriptive facts supplied by a future trusted caller.
It does not observe a filesystem, invoke a native operation, or grant any
publication, target, stage, lease, lock, authorization, or recovery authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Tuple


NAMESPACE_OUTCOMES = (
    "not-attempted",
    "definitely-not-published",
    "published",
    "indeterminate",
)
DURABILITY_OUTCOMES = ("not-applicable", "confirmed", "uncertain", "unknown")
RETRY_SAFETIES = ("may-retry-after-revalidation", "must-not-retry")
LEASE_DISPOSITIONS = ("live", "consumed", "tainted")
JOURNAL_DISPOSITIONS = (
    "no-transition-required",
    "rollback-required",
    "mark-published",
    "recovery-required",
    "mark-verified",
)

REASON_IDS = (
    "publication.validation-failed",
    "publication.destination-exists",
    "publication.source-missing",
    "publication.source-identity-mismatch",
    "publication.known-no-mutation-failure",
    "publication.native-success",
    "publication.parent-sync-failed",
    "publication.native-outcome-indeterminate",
    "publication.verification-mismatch",
    "publication.verification-unsafe",
    "publication.verification-unstable",
    "publication.verification-exact",
)
MAX_REASON_IDS = len(REASON_IDS)

_REASON_ORDER = {reason_id: index for index, reason_id in enumerate(REASON_IDS)}


def _choice(value: Any, allowed: Tuple[str, ...], label: str) -> str:
    if type(value) is not str or value not in allowed:
        raise ValueError("%s is unsupported" % label)
    return value


def _reason_ids(value: Iterable[str]) -> Tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError("reason_ids must be a finite collection of known identifiers")
    try:
        values = tuple(value)
    except TypeError as exc:
        raise ValueError("reason_ids must be iterable") from exc
    if len(values) > MAX_REASON_IDS:
        raise ValueError("reason_ids exceeds the maximum count")
    if any(type(reason_id) is not str or reason_id not in _REASON_ORDER for reason_id in values):
        raise ValueError("reason_ids contains an unsupported identifier")
    return tuple(sorted(set(values), key=_REASON_ORDER.__getitem__))


@dataclass(frozen=True)
class PublicationOutcome:
    """Immutable, non-authoritative facts and dispositions for future publication.

    The values do not prove that a syscall, target, durability event, or
    recovery condition occurred.  They are a closed vocabulary that a future
    trusted caller may construct only from its own observed runtime facts.
    """

    namespace_outcome: str
    durability_outcome: str
    retry_safety: str
    lease_disposition: str
    journal_disposition: str
    recovery_required: bool
    reason_ids: Tuple[str, ...]

    def __post_init__(self) -> None:
        namespace = _choice(self.namespace_outcome, NAMESPACE_OUTCOMES, "namespace_outcome")
        durability = _choice(self.durability_outcome, DURABILITY_OUTCOMES, "durability_outcome")
        retry = _choice(self.retry_safety, RETRY_SAFETIES, "retry_safety")
        lease = _choice(self.lease_disposition, LEASE_DISPOSITIONS, "lease_disposition")
        journal = _choice(self.journal_disposition, JOURNAL_DISPOSITIONS, "journal_disposition")
        if type(self.recovery_required) is not bool:
            raise ValueError("recovery_required must be a boolean")

        reasons = _reason_ids(self.reason_ids)
        object.__setattr__(self, "reason_ids", reasons)

        if namespace in {"not-attempted", "definitely-not-published"}:
            if durability != "not-applicable":
                raise ValueError("non-publication cannot claim publication durability")
        elif namespace == "published":
            if durability not in {"confirmed", "uncertain"}:
                raise ValueError("published namespace requires known durability disposition")
        else:
            if durability != "unknown":
                raise ValueError("indeterminate namespace requires unknown durability")

        if namespace == "published" and lease == "live":
            raise ValueError("published namespace cannot retain a live lease")
        if namespace == "published" and retry != "must-not-retry":
            raise ValueError("published namespace must not be retried")
        if namespace == "indeterminate":
            if lease != "tainted" or retry != "must-not-retry" or not self.recovery_required:
                raise ValueError("indeterminate namespace requires taint, no retry, and recovery")
        if namespace in {"not-attempted", "definitely-not-published"} and lease == "consumed":
            raise ValueError("non-publication cannot consume a lease")
        if durability == "confirmed" and namespace != "published":
            raise ValueError("confirmed durability requires published namespace")
        if durability == "uncertain":
            if namespace != "published" or lease != "tainted" or not self.recovery_required:
                raise ValueError("uncertain durability requires published tainted recovery state")
        if lease in {"consumed", "tainted"} and retry != "must-not-retry":
            raise ValueError("consumed or tainted lease must not be retried")
        if lease == "tainted" and not self.recovery_required:
            raise ValueError("tainted lease requires recovery")

        if journal == "mark-published":
            if namespace != "published" or durability != "confirmed" or self.recovery_required:
                raise ValueError("mark-published requires durable publication without recovery")
        if journal == "mark-verified":
            if namespace != "published" or durability != "confirmed" or self.recovery_required:
                raise ValueError("mark-verified requires durable publication without recovery")
        if journal == "recovery-required" and not self.recovery_required:
            raise ValueError("recovery journal disposition requires recovery")
        if self.recovery_required and journal != "recovery-required":
            raise ValueError("recovery-required outcome needs recovery journal disposition")
        if journal == "rollback-required":
            if namespace != "definitely-not-published" or lease != "live" or self.recovery_required:
                raise ValueError("rollback-required describes a safe non-publication refusal")
        if journal == "no-transition-required":
            if namespace != "not-attempted" or lease != "live" or self.recovery_required:
                raise ValueError("no-transition-required describes a live pre-syscall outcome")

    def to_dict(self) -> Dict[str, object]:
        """Return a detached, deterministic, JSON-safe metadata mapping."""

        return {
            "namespace_outcome": self.namespace_outcome,
            "durability_outcome": self.durability_outcome,
            "retry_safety": self.retry_safety,
            "lease_disposition": self.lease_disposition,
            "journal_disposition": self.journal_disposition,
            "recovery_required": self.recovery_required,
            "reason_ids": list(self.reason_ids),
        }


def normalize_publication_outcome(
    *,
    namespace_outcome: str,
    durability_outcome: str,
    retry_safety: str,
    lease_disposition: str,
    journal_disposition: str,
    recovery_required: bool,
    reason_ids: Iterable[str],
) -> Dict[str, object]:
    """Validate and detach one publication-outcome metadata mapping."""

    return PublicationOutcome(
        namespace_outcome=namespace_outcome,
        durability_outcome=durability_outcome,
        retry_safety=retry_safety,
        lease_disposition=lease_disposition,
        journal_disposition=journal_disposition,
        recovery_required=recovery_required,
        reason_ids=tuple(reason_ids),
    ).to_dict()


__all__ = [
    "DURABILITY_OUTCOMES",
    "JOURNAL_DISPOSITIONS",
    "LEASE_DISPOSITIONS",
    "MAX_REASON_IDS",
    "NAMESPACE_OUTCOMES",
    "PublicationOutcome",
    "REASON_IDS",
    "RETRY_SAFETIES",
    "normalize_publication_outcome",
]

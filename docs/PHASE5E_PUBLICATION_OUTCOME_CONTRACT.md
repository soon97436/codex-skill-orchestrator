# Publication Outcome and Durability Contract

This descriptive working increment adds a pure, immutable metadata vocabulary
before any native publication adapter or lease lifecycle change.  The contract
comes first so a later adapter, lease, coordinator, journal orchestration, and
recovery design use one reviewed classification instead of independently
inventing retry and cleanup semantics.

## Authority boundary

`PublicationOutcome` is descriptive metadata only.  It grants no filesystem
authority, no publication authority, no target authority, no stage authority,
no lease authority, no lock authority, no authorization capability, and no
recovery authority.  It neither observes nor proves that a syscall, target,
durability event, or recovery condition occurred.  Only a future trusted caller
may construct an outcome from actual runtime facts.

There is no filesystem access or mutation, target access or mutation, staging
access or mutation, native syscall, `ctypes`, capability probe, lock use, lease
mutation, journal read/write, authorization/context integration, recovery
execution, engine integration, or CLI integration in this increment.

## Closed dimensions

- Namespace: `not-attempted`, `definitely-not-published`, `published`, or
  `indeterminate`.
- Durability: `not-applicable`, `confirmed`, `uncertain`, or `unknown`.
- Retry: `may-retry-after-revalidation` or `must-not-retry`.  The former never
  means retry immediately or guarantees current source, destination, or
  authorization validity.
- Future lease disposition: `live`, `consumed`, or `tainted`.  This is
  declarative only; the current `OwnedStageLease` lifecycle is unchanged.
- Future journal disposition: `no-transition-required`, `rollback-required`,
  `mark-published`, `recovery-required`, or `mark-verified`.  This module does
  not inspect or transition a journal.

The vocabulary rejects contradictions.  In particular, published or
indeterminate outcomes cannot be retried; published outcomes cannot retain a
live lease; indeterminate outcomes require taint and recovery; confirmed
durability requires known publication; uncertain durability requires a tainted
recovery outcome; and `mark-published` / `mark-verified` require confirmed
durable publication.

## Canonical semantic profiles

| Event | Namespace / durability | Lease / retry | Journal / recovery |
|---|---|---|---|
| Validation failure before syscall | not-attempted / not-applicable | live / revalidate | no transition / false |
| Destination exists refusal | definitely-not-published / not-applicable | live / revalidate | rollback required / false |
| Source binding lost before syscall | not-attempted / not-applicable | tainted / no retry | recovery required / true |
| Documented no-mutation failure | definitely-not-published / not-applicable | live / revalidate | rollback required / false |
| Rename plus both parent syncs | published / confirmed | consumed / no retry | mark published / false |
| Rename plus parent sync failure | published / uncertain | tainted / no retry | recovery required / true |
| Native outcome indeterminate | indeterminate / unknown | tainted / no retry | recovery required / true |
| Durable publication plus verifier failure | published / confirmed | consumed / no retry | recovery required / true |
| Durable publication plus exact verification | published / confirmed | consumed / no retry | mark verified / false |

`mark-published` preserves the future runtime meaning of `PUBLISHED`: rename
succeeded and both required post-rename parent synchronizations succeeded.
`mark-verified` preserves the future meaning of `VERIFIED`: exact target
verification succeeded afterwards.

An exclusive destination-exists refusal is descriptive only.  A future
orchestrator may use the existing `PUBLISH_INTENT -> ROLLING_BACK ->
ROLLED_BACK` route, but this module performs no cleanup, journal inspection, or
journal transition.

## Security and linearization boundaries

This contract does not solve hostile same-user source-leaf substitution.  The
existing mutation lock coordinates cooperating CSO writers only; it is not a
reservation or hostile-writer exclusion mechanism.

Target observation is not admission, target absence observation is not a
reservation, and no reusable target-admission authority exists.  A future
native no-replace syscall remains the target-absence linearization point.  No
errno mapping is defined here; platform-specific mapping belongs to the later
native adapter and its semantic tests.

Windows may construct and validate this metadata because it is pure data.  That
portability does not imply Windows publication support: future Windows
publication remains unsupported and not implemented.

## Serialization

`to_dict()` returns a detached deterministic JSON-safe mapping.  Reason IDs
are bounded, closed, machine-oriented identifiers, deduplicated into canonical
order, and returned as a list only in the detached mapping.  The immutable
object contains no paths, descriptors, exceptions, errno prose, credentials,
capabilities, lock proofs, or lease objects.

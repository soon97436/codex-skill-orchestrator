# Read-only durable recovery assessment

This descriptive working increment combines existing durable-journal and
durable-stage observations.  It is not an authoritative 4C increment
identifier.

## Scope

`assess_durable_recovery(skills_root)` returns a bounded immutable assessment
containing the current journal classification, journal phase when available,
and a durable-stage observation for each non-terminal journal that has a valid
transaction identifier.  Terminal journals have a `not-applicable` stage
status.  Unsafe journal entries without an identifier have a `not-observed`
stage status.

Installed-state capability is always `not-implemented`.  This increment does
not define an installed-state path, read an installed-state file, write one,
or interpret an absent record as any fact about a target.

The overall statuses are `clean`, `recovery-required`, and `unsupported`.
They are observations and classifications, not recovery permissions.

## Authority boundary

Neither a journal record nor a matching stage grants authorization, mutation,
publication, rollback, recovery, target ownership, managed-current ownership,
stage ownership, or an `OwnedStageLease`.  In particular,
`journal=recovery-required` plus `stage=matching` does not permit resumption
or recovery.

Journal scan and stage observation are separate reads.  A returned stage
status describes only its own observation during this invocation; it has no
cross-object atomicity or continuity guarantee.  The assessor does not retry
an unsafe, missing, unstable, or unsupported observation until it appears
favourable.

## Filesystem boundary

The assessor calls only `scan_durable_journals()` and, where applicable,
`observe_durable_stage()`.  It does not acquire a mutation lock and does not
create, repair, or mutate `.cso-state`, `.cso-staging`, a journal, a stage, or
a target.  It does not inspect, stat, open, resolve, enumerate, create, or
otherwise access `<skills_root>/<target_key>`.

No engine, CLI, executor, legacy recovery, network, subprocess, activation,
or runtime-capability path is used.

## Platform behavior

Windows is unsupported and fails closed before durable candidate state, stage,
or target access.  There is no path-based fallback.

# Durable Stage Re-open Observation — Read-only

This working-name increment observes a current stage named by a validated
durable candidate journal.  It is POSIX-only and performs no explicit
filesystem write or namespace mutation.

## Scope

`observe_durable_stage(skills_root, transaction_id)` loads one validated
journal read-only.  A nonterminal journal with a stage binding is then checked
only at:

```text
<skills_root>/.cso-staging/<stage_binding.relative_name>
```

The reader uses descriptor-relative no-follow opens and requires a private,
user-owned same-filesystem staging namespace and stage.  It rejects links,
special files, unsafe hard-link aliases, unexpected leaves, unsafe modes,
ownership changes, manifest/hash/size mismatches, and observed instability.

The stage binding compares to the existing 4C1B normalized manifest identity:
`cso-candidate-manifest-v1\0`.  It does not compare to the separate 4B
content-only stage-manifest digest:
`cso-stage-manifest-v1\0`.

## Result boundary

The closed result statuses are `matching`, `missing`, `unsafe`, `unstable`,
`not-applicable`, and `unsupported`.

`matching` means only that the bytes and structure observed during this call
matched the journal's immutable manifest.  It does not restore an
`OwnedStageLease`, ownership, authorization, admission, mutation permission,
publication permission, or recovery authority.

The observer does not inspect, open, stat, create, rename, delete, chmod, or
otherwise access `<skills_root>/<target_key>`.  It does not validate sources,
acquire a mutation lock, create or repair state, write a journal phase,
perform cleanup, persist a classification, access installed state, execute
recovery, invoke an engine/CLI/executor, or use network access.

On Windows, it fails closed before accessing `.cso-state`, `.cso-staging`, or
a candidate target.

## Concurrency

No mutation lock is acquired.  The reader holds directory/file descriptors
while it verifies leaves and compares identities and metadata before and after
the observation.  Detected change produces `unstable`; the reader never
retries until success.  A stable `matching` observation is informational only
and is not a global concurrency guarantee.

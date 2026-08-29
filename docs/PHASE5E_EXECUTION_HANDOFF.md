# Phase 5E Increment 3 — Execution Handoff

Phase 5E-3 adds a small, pure seam between deterministic admission evidence
and a future execution adapter.  It answers one narrow question:

> Is this exact, currently valid registry candidate eligible to be handed to
> a future executor in this invocation?

The answer is a metadata-only decision.  It is not installation, activation,
execution, a credential, or a reusable authorization token.

## Why this increment exists

Phase 5E-1 provides the deterministic admission pipeline.  Phase 5E-2 binds
the complete evidence set and can verify that evidence later.  A detached
verification envelope is not sufficient for handoff: a current result for one
stored binding could otherwise be paired with a different stored binding.

The handoff module therefore calls `verify_admission_binding()` itself with the
exact stored binding and the complete current evidence supplied for this
invocation.  The current result is consequently tied to the binding that is
actually being considered.

## Public interface

`evaluate_execution_handoff(...)` accepts the stored binding, all deterministic
registry/trust/capability/recommendation/installation evidence, an operation,
a target class, and an invocation-local fresh operator authorization assertion.
The interface is keyword-only and has no CLI surface.

The output is a fixed structure:

```text
schema_version
status
assessment_scope
operation
target_class
execution_status
reason_ids
limitations
truncated
```

Statuses are exactly `ready`, `rejected`, `unknown`, and `invalid`.
`execution_status` is always `not-performed`.

## Verification and authorization

The existing binding verification statuses map as follows:

- `current` continues to invocation checks.
- `stale` becomes handoff `rejected`.
- `invalid` becomes handoff `invalid`.

Fresh operator authorization is one of `granted`, `denied`, or
`not-provided`.  It is only a caller assertion for this invocation; the module
does not identify a person or cryptographically prove freshness.  A stored
Phase 5D authorization value cannot satisfy this fresh assertion.

The only operation that can become `ready` in this increment is an exact
`install` operation.  `activate` is recognized but remains reserved and is
rejected because arbitrary registry-candidate activation semantics do not yet
exist.  The only supported target class is
`registry-skill-user-scope`; target classes are labels, never paths.

`ready` means only that a future candidate-install adapter may receive this
same invocation after it performs its own immediate checks.  It does not mean
that a destination is resolved, source bytes are reverified, OS permissions
are granted, runtime capabilities are granted, or installation completed.

## Replay and capability boundaries

This increment does not cryptographically prevent replay.  A `ready` result
must not be persisted as consent or accepted later as a bearer credential.  A
future executor must require the live invocation context and revalidate before
any mutation.

Runtime capability enforcement is separate from management-plane install
eligibility.  A binding with no requested runtime capability may still be
`ready` for install, while receiving zero runtime capability.

## Privacy and security

The module accepts supplied mappings and returns only fixed enums, reason IDs,
limitations, and execution state.  It never emits skill identity, versions,
revisions, URLs, paths, hashes, publisher data, commands, task text,
credentials, host/user/environment metadata, exceptions, or timestamps.

There is no filesystem or registry loading, destination resolution, shell,
subprocess, network, Git/GitHub, engine, CLI, installer, clock, randomness,
LLM, or runtime execution.  Remote fetching remains disabled.

## Phase 5E-4 responsibility

The next execution-plane increment, Phase 5E-4, owns all mutation-time work:

- target-class to destination mapping
- destination validation
- `MutationLock`
- source-byte manifest and staged-manifest verification
- symlink/reparse race handling
- staging and atomic replacement
- transaction state and rollback
- post-commit verification and installed state

Phase 5E-3 performs none of these operations.

## Relationship to other phases

Phase 5E-3 consumes the deterministic evidence produced by Phase 5A through
Phase 5E-2 without changing those modules.  Phase 6 reproducibility work may
later provide durable evidence or synchronization, but no lockfile, remote
fetch, or cross-machine state is introduced here.

The boundaries remain explicit:

```text
trustworthiness
  != capability admission
  != recommendability
  != installation authorization
  != runtime capability authorization
```

No persistent authorization, identity provider, password, OAuth flow, TTL,
nonce, signature, bearer token, or secret is introduced.

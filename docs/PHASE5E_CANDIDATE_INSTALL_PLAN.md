# Phase 5E-4A — Pure Candidate Install Plan

Phase 5E-4A is the first, non-mutating increment of the candidate-install
executor design. The architecture review passed for this pure planning seam
only; a mutating executor remains blocked until the filesystem primitives and
resource policy described below exist.

## Why 5E is split

The existing execution handoff is an invocation-local eligibility result. It
is recomputed from the stored evidence binding, complete current evidence, and
fresh operator authorization. 4A adds a second deep module that consumes that
live result and produces a structural candidate plan. The plan stops before
filesystem inspection or mutation.

The registry schema currently requires at least one entry in the top-level
files array but does not declare a maxItems bound. MAX_DECLARED_FILES = 64 is
therefore a new execution-planning bound; it is not a claim about the
registry schema.

## Public interface

    evaluate_candidate_install_plan(
        *,
        stored_binding,
        registry_schema_version,
        registry_entry,
        trust_profile_schema_version,
        trust_policy,
        trust_evidence,
        capability_policy,
        capability_declaration,
        requested_capabilities,
        trust_decision,
        capability_decision,
        recommendation_decision,
        installation_decision,
        operation,
        target_class,
        fresh_operator_authorization,
    ) -> dict

The interface does not accept a detached handoff result, a READY/CURRENT
boolean, a digest, an authorization boolean, source or destination roots, a
filesystem object, or a transaction identifier. The implementation
recomputes evaluate_execution_handoff(...) internally on every call.

Only an exact install operation for registry-skill-user-scope can proceed to
planning. A ready handoff is necessary but is not an execution token or
installation authority.

## V1 source and manifest contract

V1 is bundled-only:

- source.type must be bundled;
- source.repository and source.revision must both be None;
- git is reported as a deterministic rejected/unsupported source;
- no clone, fetch, network access, or arbitrary local source root is used.

The plan validates only the declared manifest. Each item has exactly path and
a lowercase 64-hex sha256. Paths are portable relative paths using /, NFC
input, bounded UTF-8 size/depth/segment size, no NUL, no dot segments, no
colon or backslash, no trailing dot/space, and no Windows device basename.
Exact duplicate paths are invalid. NFC/case-fold collisions are rejected.
No undeclared source file can be part of a future copy operation.

MAX_DECLARED_FILES is 64. Sixty-four declared files are within the planning
bound; sixty-five are rejected. No input is silently truncated, deduplicated,
or rewritten.

## Result and limitations

The result has a fixed, metadata-only shape:

    schema_version
    status
    assessment_scope
    operation
    target_class
    source_type
    file_count
    resource_limits
    execution_status
    reason_ids
    limitations
    truncated

Statuses are exactly planned, rejected, unknown, and invalid.
execution_status is always not-performed. planned means that the invocation
passed the live handoff and structural V1 checks; it does not mean that source
bytes, destination safety, disk space, permissions, or runtime capabilities
were verified.

Every result carries fixed limitations for:

- execution not performed and plan is not execution authority;
- filesystem not inspected;
- source bytes and byte sizes not verified;
- destination and disk space not resolved/verified;
- runtime capability enforcement not implemented;
- remote fetch disabled.

The planner deliberately has no file-size metadata. The future mutating layer
must enforce streamed single-file and aggregate byte limits before commit.

## Explicitly absent

This increment adds no filesystem adapter, source handles, symlink/reparse or
hardlink policy, destination validation, staging, lock, transaction,
replacement, rollback, recovery, state persistence, activation, post-install
hooks, runtime capability enforcement, remote fetch, CLI surface, or
installer integration. It also does not create a reusable authorization token.

## Future 5E-4B responsibility

Before mutation is allowed, the next increment must establish reusable
transactional filesystem primitives: safe source handles, explicit
symlink/reparse/hardlink policy, exact declared-byte copying, streamed byte
limits, stage hashing, same-filesystem staging, destination validation,
shared mutation locking, atomic replacement, and deterministic fault
injection/recovery semantics. Only then can a separate candidate executor be
considered.

The Phase 5A canonical evidence remains unchanged:

13354f946ccaf72793aee101073fcdead0ba38642cf1509a3232df3287dc4412

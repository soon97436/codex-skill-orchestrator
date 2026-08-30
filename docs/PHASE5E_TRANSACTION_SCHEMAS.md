# Phase 5E — Pure Transaction and Installed-State Schemas

Increment 4C1B defines durable-data contracts for a future candidate
transaction journal and installed-state record.  The modules are pure
validators and digest helpers.  They do not create, read, or write durable
state and do not grant authority to any caller.

## Scope and boundary

The canonical skills mutation/state namespace remains:

```text
<skills_root>/.cso-state
```

This increment does not create that namespace.  It does not implement journal
persistence, installed-state persistence, recovery, rollback, staging,
publication, authorization, target inspection, target mutation, or remote
fetch.  Windows candidate staging and mutation remain unsupported and fail
closed.  Candidate final-target mutation is **NONE**.

## Pure APIs

`skill_orchestrator.transaction_journal` exposes validation and digest helpers:

- `validate_transaction_id`
- `normalize_exact_manifest`
- `manifest_digest`
- `validate_journal_document`
- `journal_digest`
- `validate_phase_transition`
- `is_terminal_phase`

`skill_orchestrator.installed_state` exposes:

- `validate_installed_state_document`
- `installed_state_digest`

No API saves, loads, commits, installs, publishes, recovers, or mutates.

## Shared identity and manifest rules

Transaction IDs are exactly 32 lowercase hexadecimal characters.  They are
operational identifiers only; they are not authorization or reproducibility
authority.

Manifest records have exactly `path`, `sha256`, and `size`.  Paths are safe
POSIX relative paths, hashes are lowercase SHA-256 values, and sizes are
non-negative integers (`bool` is not accepted as an integer).  Manifests are
detached and sorted by path, reject duplicate paths, and allow at most 4096
records.  No source-copy byte limit is imposed here.

Manifest identity uses the domain-separated digest input
`cso-candidate-manifest-v1\0` followed by compact, sorted-key UTF-8 JSON of
the normalized manifest.  This is separate from the 4B staging digest and is
not an ownership authority.

Root identity is the path-free POSIX record:

```json
{"kind":"posix-dev-ino","device":0,"inode":1}
```

Windows durable root identity is not defined in V1.  Target keys and internal
stage/quarantine names are one safe ASCII segment; separators, traversal,
alternate-stream markers, trailing dot/space, and Windows device names are
rejected.

## Journal V1

Journal documents have one closed top-level schema containing the transaction
identity, candidate operation, phase, path-free skills-root identity, upstream
digests, the new manifest and digest, optional managed-current previous target,
optional stage/quarantine bindings, optional before/after installed-state
digests, cleanup status, and bounded stable reason IDs.

The only replaceable previous target classification is `managed-current`, with
its manifest and matching manifest digest.  Stage and quarantine bindings hold
only an internally derived relative segment and manifest digest; they are not
filesystem ownership authority.

Cleanup status is one of:

```text
none
cleanup-required
maintenance-required
recovery-required
```

Reason IDs are lowercase dotted ASCII identifiers (with optional hyphenated
segments), unique, sorted, and limited to 32.  Raw paths, exceptions,
commands, environment, credentials, usernames, hostnames, and authorization
material are not valid fields.

The phase machine is explicit:

```text
PREPARING -> PREPARED | ABORTED | RECOVERY_REQUIRED
PREPARED -> QUARANTINE_INTENT | PUBLISH_INTENT | ABORTED | RECOVERY_REQUIRED
QUARANTINE_INTENT -> QUARANTINED | ROLLING_BACK | RECOVERY_REQUIRED
QUARANTINED -> PUBLISH_INTENT | ROLLING_BACK | RECOVERY_REQUIRED
PUBLISH_INTENT -> PUBLISHED | ROLLING_BACK | RECOVERY_REQUIRED
PUBLISHED -> VERIFIED | ROLLING_BACK | RECOVERY_REQUIRED
VERIFIED -> STATE_COMMITTING | ROLLING_BACK | RECOVERY_REQUIRED
STATE_COMMITTING -> COMMITTED | ROLLING_BACK | RECOVERY_REQUIRED
ROLLING_BACK -> ROLLED_BACK | RECOVERY_REQUIRED
```

`COMMITTED`, `ROLLED_BACK`, `ABORTED`, and `RECOVERY_REQUIRED` are terminal;
no transition leaves them.  Phase-specific invariants reject impossible
claims, including a committed after-state in `PREPARING`, missing stage data
in `PREPARED`, missing managed previous-target data in quarantine phases,
missing after-state data in `STATE_COMMITTING` or `COMMITTED`, and recovery
without recovery cleanup plus a reason.

Journal identity uses
`cso-candidate-journal-v1\0` followed by normalized compact UTF-8 JSON.  It is
an integrity/reference digest, not authority.

## Installed state V1

Installed state records use the closed source vocabulary `bundled` and `git`,
the existing normalized skill-ID and semantic-version conventions, exact
lowercase SHA-256 upstream digests, the path-free root identity, the exact
`candidate-install` operation, and a validated transaction ID.

Declared and installed manifests must both be non-empty and must match exactly
in paths, hashes, and sizes.  Each manifest digest is recomputed and must
match its record.  `cso_version` is optional; when present it is a bounded safe
ASCII value.

Installed-state identity uses
`cso-installed-state-v1\0` followed by normalized compact UTF-8 JSON.  It is
not authority by itself, and fresh authorization is never persisted.

## Determinism and side effects

Validation returns detached normalized data, so later caller mutation cannot
change it.  Manifest, reason, mapping insertion, and equivalent input order do
not affect normalized output or digest.  The implementation has no current
time, randomness, locale, network, target inspection, or filesystem write
dependency.  In particular, pure calls do not create `.cso-state`,
`transactions/`, `journal.json`, or `installed-state.json`.

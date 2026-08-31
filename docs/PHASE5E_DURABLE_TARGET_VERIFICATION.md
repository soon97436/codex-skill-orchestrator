# Phase 5E — Durable Final-Target Verification

This descriptive working increment adds one POSIX-only, read-only primitive:

```text
verify_durable_target(
    skills_root,
    expected_root_identity,
    target_key,
    expected_manifest,
    *,
    limits=None,
)
```

It answers one bounded question: whether the direct
`<skills_root>/<target_key>` directory was observed during that call to exactly
match the existing V1 candidate manifest under the established Phase 5E
filesystem safety checks.

## Boundary

The verifier accepts only a skills root, an expected POSIX device/inode root
identity, one strict target key, an existing exact candidate manifest, and the
existing execution limits. It does not accept a target path, journal document,
recovery assessment, stage, stage lease, authorization, mutation-lock proof,
execution context, or installed-state record.

On supported POSIX hosts, root and target traversal is descriptor-relative and
no-follow. The root is opened component by component. The target is opened as
a direct directory leaf relative to the validated root. Every child directory
and file is opened relative to an already-open descriptor. No legacy
path-based manifest helper is used.

The exact-manifest contract is the existing candidate contract:

- the manifest is normalized by the V1 exact-manifest validator;
- the target and nested directories must be current-user-owned private `0700`
  directories on the skills-root filesystem;
- declared files must be current-user-owned, singly linked regular `0600`
  files on that filesystem;
- file digest and size must match; per-file and total limits apply;
- missing or extra regular files/directories are mismatches; and
- symlinks, FIFOs, sockets, devices, and other unsafe objects fail closed.

These owner/mode rules are not new final-target policy. They are the existing
candidate/stage security contract: stages are created as private `0700`
directories with `0600` regular files, and their existing observer verifies
the same properties. This verifier introduces no target mutation or new
publication semantics.

## Observation and non-authority

Results are immutable detached metadata with one of these bounded statuses:
`verified`, `missing`, `mismatch`, `unsafe`, `unstable`, or `unsupported`.

`verified` means only that the target was observed to exactly match during
this call. It is not ownership, admission, authorization, publication proof,
installed-state proof, recovery authority, a reservation, or permission to
mutate. It can become stale immediately after return. No descriptor, lease,
lock proof, authorization, or mutation method is returned.

The verifier checks root identity at the start and end of the call. It checks
the target directory identity before and after its complete traversal. It also
checks open file/directory metadata while traversing. Detected in-call target
or root replacement returns `unstable`; it does not retry for a favorable
result. This standalone primitive does not claim hostile-writer exclusion.

## Deliberate non-goals

The verifier acquires no mutation lock and performs no CSO explicit filesystem
writes. It does not create state, locks, stages, temporary files, journals, or
cleanup artifacts. It does not inspect `.cso-staging` or `.cso-state`.

There is no publication, rename, native syscall binding, quarantine, rollback,
stage lease consumption, authorization, execution context, journal phase
transition, installed-state persistence, recovery execution, engine
integration, or CLI integration.

A future publish flow must invoke any post-publication verification while it
holds its own continuously active execution authority. Publication architecture
remains deferred.

## Platform behavior

Windows is unsupported and fails closed before opening the skills root,
opening the target, traversing the target, or touching candidate state/staging
namespaces. There is no Windows path-based fallback.

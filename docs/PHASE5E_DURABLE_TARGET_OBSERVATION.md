# POSIX read-only durable final-target observation

This descriptive working increment adds one target-only observation primitive:

```text
observe_durable_target(skills_root, expected_root_identity, target_key)
```

It classifies the direct `<skills_root>/<target_key>` leaf as `absent`,
`present`, `unsafe`, `unstable`, or `unsupported`. The result is immutable
diagnostic metadata only. It is not admission, authorization, a reservation,
ownership, a managed-current classification, recovery authority, or permission
to publish or mutate.

## Boundary

The observer accepts only a skills root, expected POSIX device/inode root
identity, and the existing strict transaction-journal target key. It does not
accept or access a journal, recovery assessment, stage, stage lease, source,
execution context, mutation lock, authorization, or installed-state record.

On supported POSIX hosts it opens the root and direct leaf descriptor-relative
with no-follow semantics. It verifies the expected root identity, observes the
leaf twice to detect in-call absence/presence/identity/type drift, and safely
reopens the root to detect root replacement during the observation. Regular
files and directories are `present`; symlinks and special leaves are `unsafe`.

No CSO filesystem write occurs: the primitive does not create state, locks,
stages, files, temporary files, journals, or cleanup artifacts. It does not
acquire `MutationLockSet.for_skills()`.

## Non-authority and platform behavior

A stable `absent` result says only that the leaf was absent during that call.
It may become stale immediately after return and cannot be consumed as
publication authority. No descriptor, live handle, identity token, mutation
method, or authorization material is returned.

Windows is unsupported and fails closed before skills-root, target,
`.cso-state`, or `.cso-staging` access. There is no path-based fallback.

Final-target mutation remains **NONE**. Target admission, lock-held recheck,
fresh authorization, stage authority, journal/state integration, recovery, and
engine/CLI integration remain unimplemented.

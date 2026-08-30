# Phase 5E — Shared Mutation Domain

## Purpose and boundary

The 4C architecture review found that the legacy engine serialized mutations
only under `<install_root>/state/mutation.lock`, while the router target lived
under `<skills_root>`. That split lock domain was unsafe: two engine processes
could use different install roots while replacing the same router target.

This increment adopts the user-approved canonical skills mutation namespace:

```text
<skills_root>/.cso-state
```

Legacy engine mutations acquire both of these fixed-domain locks before
recovery, state work, snapshots, or target mutation:

```text
<install_root>/state/mutation.lock
<skills_root>/.cso-state/mutation.lock
```

A future candidate mutation executor will acquire the skills-domain lock
alone. This increment does not connect that foundation to candidate staging or
publication.

## Lock semantics

The lock set derives its resources from domain roots, deduplicates identical
resources, orders them with one deterministic total ordering, acquires them
fail-fast and nonblocking, and releases them in reverse order. If acquisition
fails part-way through, every lock already acquired is released.

The lock file's presence or content is not authority. Its diagnostic payload is
limited to the current process ID and a random token. A stale payload or stale
lock file does not represent a held OS lock; the OS lock is the authority.

On POSIX, the implementation validates the canonical root and opens the root,
state namespace, and lock file with descriptor/no-follow protections where
available. It holds those live descriptors for the lock lifetime and checks
ownership, type, permissions, link count, and descriptor/path identity. The
legacy install state directory remains compatible with existing non-writable
`0755` installations. New skills state is private `0700`, and the skills lock
file is `0600`. Suspicious existing namespaces are rejected rather than
silently repaired.

On Windows, the foundation preserves the standard-library one-byte `msvcrt`
nonblocking lock and stale-file behavior, with the same fixed domain paths and
reparse/symlink checks available to the platform. Windows support here is
cooperative and path-bound only; it is not a claim of POSIX-equivalent
descriptor identity or protection from hostile writers. Secure candidate
staging and candidate target mutation remain unsupported on Windows.

This lock foundation does not solve coordination with hostile non-CSO writers.
It also does not provide power-loss durability: the existing transaction
writer, replacement, journal, installed-state, and recovery designs remain
outside this increment.

## Compatibility and non-goals

- Dry-run and audit do not create `<skills_root>/.cso-state` merely by being
  evaluated.
- Legacy install, activate, rollback, transaction schema, target paths, and
  recovery behavior remain unchanged apart from the shared lock boundary.
- Remote fetch remains disabled.
- Journal and installed-state work are not implemented here.
- Candidate final-target mutation is **NONE**.
- This increment must not be described as making 4C target mutation ready.

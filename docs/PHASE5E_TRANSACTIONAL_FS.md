# Phase 5E transactional filesystem boundary

## Increment 4B review decision

Phase 5E Increment 4B is split into independently reviewable mutations.
Increment 4B1 adds only exact declared-file staging primitives. It creates a
disposable stage, streams the declared bytes into it, and independently
verifies the resulting stage. It does not select or replace a target and does
not write installed state.

Increment 4B2 is reserved for a separately reviewed target replacement,
locking, rollback, and recovery design. A separate future increment is also
required for a native Windows secure no-follow/reparse adapter.

## Execution limits

The version 1 execution limits are:

- maximum single file: 1,048,576 bytes;
- maximum candidate total: 8,388,608 bytes;
- streaming copy chunk: 65,536 bytes;
- maximum declared files: 64.

These are execution-security limits. They are not registry-schema, policy,
authorization, evidence, or lockfile fields. At this checkpoint, the bundled
registry payload has three declared files totaling 2,941 bytes; its largest
declared file is 1,746 bytes. Those measurements provide context only and are
not an authorization rule.

## Input and path contract

`stage_declared_candidate()` accepts a frozen `StageRequest` containing an
execution-plane source root, staging parent, portable candidate key, one to 64
declared path/hash pairs, and explicit execution limits. Declared paths use the
same bounded portable path rules as the Phase 5E candidate plan. Exact
duplicates and NFC/casefold collisions are invalid. SHA-256 values must be
lowercase 64-character hexadecimal strings.

The candidate key is used only to form an exclusive stage name. It is never
policy authority, evidence, or an installation identity.

## POSIX descriptor/no-follow model

Real POSIX staging is available only when the host exposes the required
descriptor-relative and no-follow primitives. The adapter:

- opens every source and staging ancestor relative to a held directory file
  descriptor;
- uses `O_NOFOLLOW`, `O_DIRECTORY`, and `O_CLOEXEC` where required;
- opens each declared leaf relative to its held parent descriptor;
- accepts regular source files only and rejects link counts above one;
- holds source and stage descriptors during copying and verification;
- compares source metadata before and after streaming;
- creates stage directories with mode `0700` and stage files with `0600`;
- never reopens an already validated path through an untrusted absolute path.

Source/staging overlap, root-like roots, symlink ancestry, symlink leaves,
hard-linked files, FIFOs, devices, sockets, and other special files fail
closed. `Path.resolve()` is not used as a security boundary.

## Windows limitation

Increment 4B1 does not implement native Windows no-follow/reparse-safe opens.
The real Windows adapter therefore returns deterministic `rejected` /
unsupported before it creates any stage directory or file. There is no
`ctypes`, `pywin32`, `CreateFileW`, or path-check-only fallback.

## Exact streaming copy and verification

Files are processed in deterministic portable-path order. The implementation
never recursively copies a source tree. For each declared file it securely
opens the exact source, exclusively creates the exact stage leaf, streams and
hashes bounded chunks, enforces per-file and aggregate byte limits, compares
the declared digest, and rechecks source-handle metadata.

A second descriptor-relative pass independently enumerates the stage. It
requires exact equality of actual and declared regular-file paths, hashes, and
sizes; only implied directories may exist. Extras, missing files, links,
special files, unexpected hardlinks, substitution, and resource-limit drift
prevent a `staged` result.

The content-only stage manifest digest is:

```text
sha256(
  b"cso-stage-manifest-v1\0" +
  canonical compact UTF-8 JSON of sorted path/sha256/size records
)
```

The digest is neither authorization nor an immutable evidence/binding digest.
Raw paths and raw file hashes are not exposed by the default result.

## Status, cleanup, and privacy

The closed status vocabulary is:

- `staged`: exact disposable stage independently verified;
- `rejected`: a valid request cannot be staged under the security/platform
  policy;
- `invalid`: malformed request, declaration, key, path, hash, or limits;
- `failed`: operational failure and owned-stage cleanup succeeded;
- `cleanup-required`: failure plus incomplete owned-stage cleanup.

Any rejection or failure after stage creation triggers cleanup through the
module-owned stage handle. Cleanup identity is checked against the held stage
descriptor. Cleanup failure is explicit and is never hidden.

`StageResult` contains only fixed status/reason/limitation identifiers, an
opaque stage token, counts, total bytes, and the manifest digest. It never
contains source or staging paths, declared relative paths, raw declared
hashes, the candidate key, task content, credentials, exception text,
commands, usernames, hostnames, environment data, or timestamps.

## Explicit non-goals and guarantees

`staged` does not mean installed, activated, authorized, or runtime-enforced.
Increment 4B1 adds no target path, replacement, rename, quarantine, deletion,
rollback, `MutationLock`, installed-state write, CLI or engine wiring,
installer integration, registry/schema/security/profile change, remote fetch,
subprocess, shell, dependency resolution, candidate import, hook, post-install
execution, or LLM operation.

The target remains untouched. Remote fetching remains disabled. Runtime
capability enforcement remains unimplemented. `activate` remains reserved.
The implementation makes no power-loss durability promise and adds no journal
or recovery protocol; its guarantee ends at a verified disposable stage plus
an explicit cleanup outcome.

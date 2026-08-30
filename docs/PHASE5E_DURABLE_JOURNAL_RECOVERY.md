# Phase 5E — Durable Journal and Recovery Foundation

Increment 4C2 persists a validated candidate transaction journal and provides a read-only recovery scan. It does not execute a candidate transaction.

## Persistence boundary

The only 4C2 write boundary is:

```text
<skills_root>/.cso-state/transactions/<transaction_id>/journal.json
```

`<transaction_id>` is the existing 32-lowercase-hex operational identifier. The journal body is the unchanged 4C1B V1 transaction-journal schema, serialized as canonical compact UTF-8 JSON. No persisted digest, signature, authorization token, source path, final-target path, exception, environment, credential, or runtime capability is added.

On supported POSIX platforms, writes acquire the existing skills-domain mutation lock. Directories are private, user-owned, same-filesystem regular directories. Files are private, user-owned, singly linked regular files. The writer uses descriptor-relative no-follow operations, exclusive temporary-file creation, file `fsync` before descriptor-relative atomic replacement, and a parent-directory `fsync` afterwards. If any required primitive or safety check is unavailable, persistence fails closed; it never falls back to path-based I/O.

The stored `skills_root_identity` must equal the live POSIX device/inode root identity on creation, update, and scan. An update must be a legal 4C1B phase transition. Transaction identity, root identity, admission inputs, manifest, and installed-state-before digest are immutable after creation; fields that become established later cannot be replaced.

## Recovery scan

`scan_durable_journals` is strictly read-only. It does not create `.cso-state`, acquire a mutation lock, write `RECOVERY_REQUIRED`, delete temporary files, repair permissions, rename, quarantine, or otherwise change filesystem state.

Every non-terminal journal is classified as `recovery-required`. A malformed or unexpected namespace entry, symlink/reparse point, invalid transaction directory name, unexpected transaction leaf, duplicate JSON key, invalid UTF-8/JSON/schema, unsafe file type or permissions, and skills-root identity mismatch is also classified as `recovery-required`. There is no clock-based expiry: a non-terminal journal is incomplete whenever it is scanned.

The scan reports terminal journals but does not clean them. It cannot decide that a journal authorizes recovery, target replacement, rollback, or a new transaction; those capabilities remain outside 4C2.

## Explicit non-goals

- Candidate final-target inspection or mutation is **NONE**.
- Candidate staging, stage lease persistence/reopen, quarantine, publication, rollback, post-verification, and installed-state persistence are absent.
- No engine, CLI, legacy transaction/recovery, remote fetch, authorization, activation, or runtime-capability integration is added.
- Windows persistence and recovery scan are unsupported and fail closed before `.cso-state`, `.cso-staging`, or a candidate target is created.

The journal is protected durable metadata, not authorization or integrity authority against a same-user or higher-privilege filesystem writer. A future executor must independently obtain fresh authorization, recompute admission, and safely validate all live filesystem state before any mutation.

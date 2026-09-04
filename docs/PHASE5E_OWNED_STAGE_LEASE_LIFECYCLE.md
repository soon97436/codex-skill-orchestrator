# Consumable / Tainted OwnedStageLease Lifecycle

This descriptive working increment extends the owned-stage lifecycle without
assigning an authoritative next numeric increment identifier.

## Baseline and authority

The baseline is main commit `e87e19c5c73191fb10010f92912b7f9286e8fe7c`, tree
`5650a447fcd97d24f0f0863185812501969a8eb5`.  The increment adds only
process-local capability revocation, unsafe cleanup/retry suppression, bounded
lifecycle metadata, and descriptor-only close.

It adds no final-target or generic path mutation authority, native adapter
invocation, publication authority, target authority, mutation-lock use, journal
write, installed-state write, recovery mutation, authorization or execution
context, engine or CLI reachability, package-root export, or Windows publication
fallback.

## Semantic and resource lifetime

`OwnedStageLease.state` has one authoritative bounded value:

```text
active
cleaned
cleanup-required
consumed
tainted
```

`active` is the implementation spelling of the architectural LIVE state.
There is no resurrection to `active`.  Semantic state is separate from
descriptor lifetime.  A lease owns its stage and staging-parent descriptors
while they remain non-negative; no public raw descriptor or path access is
added.

`close()` releases descriptors only.  It preserves stage name, stage ID,
expected device/inode and manifest metadata, digest, totals, and limits.  Each
closed descriptor is immediately recorded with the existing `-1` sentinel, so
later close calls cannot close a recycled descriptor number.

## Transitions

```text
active -- ordinary cleanup succeeds --> cleaned
active -- ordinary cleanup fails ----> cleanup-required
active -- future definite rename ----> consumed
active -- native outcome uncertain --> tainted
active -- source binding lost ------> tainted
consumed -- parent sync fails ------> tainted + post-rename-sync-failed
```

`consumed` means that a future native publication rename definitely departed
the original staging namespace and revoked publication capability.  A later
parent-sync failure transitions the lease to `tainted`; the known rename-success
fact belongs to future PublicationOutcome/journal evidence, not this lease.  A
future durable-publication verifier failure also leaves lease classification to
those future layers.

The bounded lifecycle reason identifiers are:

```text
native-outcome-indeterminate
source-binding-lost
post-rename-sync-failed
```

The first two are legal only from `active` and produce `tainted`.  The third is
legal only from `consumed` and also produces `tainted`.  `taint_reason` is set
only when `state` is `tainted`; every other state has no taint reason.  There
are no free-form reasons or PublicationOutcome diagnostics.

## Cleanup and close

Ordinary cleanup remains legal only from `active`.  It retains the existing
identity-safe descriptor-relative cleanup sequence and result values:
`cleaned` or `cleanup-required`.  `cleaned.cleanup()` remains idempotent;
`cleanup-required.cleanup()` remains a deterministic cleanup-required result
without retry.

`consumed.cleanup()` and `tainted.cleanup()` raise `RuntimeError` before any
adapter cleanup, revalidation, stat, recursive deletion, rmdir, or other
namespace operation.

`close()` is legal and idempotent from `cleaned`, `cleanup-required`,
`consumed`, and `tainted`.  It makes no namespace, target, journal, fsync, or
native-adapter operation.  `active.close()` raises `RuntimeError`: abandoning a
cleanup-capable stage must be explicit cleanup, not descriptor loss.

Partial cleanup failure is supported.  If stage cleanup already releases the
stage descriptor before a later failure, `cleanup-required` retains only the
remaining descriptor ownership.  A subsequent `close()` closes those remaining
descriptors once and performs no retry or namespace mutation.

## Deliberate separations

There is no context manager, finalizer, destructor, native adapter integration,
PublicationOutcome construction, journal integration, authorization/context
integration, recovery implementation, engine integration, or CLI integration.
Future coordination must explicitly consume or taint the lease and explicitly
close terminal leases in `try`/`finally` paths.

Pure lifecycle tests run on Ubuntu, macOS, and Windows.  That portability does
not imply Windows publication support: Windows publication remains unsupported
and fail-closed.

Hostile same-user source-leaf substitution remains unresolved.  This increment
only prevents unsafe cleanup/retry after lifecycle revocation; it does not solve
the source-name race.

## Deferred work

Deferred work includes constrained descriptor evidence borrowing, native
publication coordination, post-call identity proof, durability sequencing,
PublicationOutcome mapping, journal transitions, authorization, recovery,
installed-state persistence, engine integration, and CLI reachability.

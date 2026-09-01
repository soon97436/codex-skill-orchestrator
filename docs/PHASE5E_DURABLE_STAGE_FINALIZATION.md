# Durable Stage Finalization

This descriptive working increment strengthens the existing POSIX staging
write path before any publication work.  It does not define an authoritative
post-4C2 numeric increment identifier.

## Boundary

An exact candidate stage has already copied each declared file, synchronized
each staged file descriptor, and passed the existing exact-manifest
verification.  Before either staging API reports that stage as ready, it now
completes this repository-defined synchronization sequence:

```text
file bytes written
  -> file fsync (existing behavior, for every staged file)
  -> exact stage verification (existing behavior)
  -> nested stage directories, deepest first
  -> stage root directory
  -> staging parent directory
  -> skills root directory, only when this call created .cso-staging
  -> stage result / owned lease returned
```

Nested directories are reopened only descriptor-relatively from the live
stage descriptor with no-follow directory opens.  They are synchronized
child-before-parent so every parent whose namespace gained a child entry is
synchronized after that child.  The stage root is then synchronized for file
and nested-directory entries, and the staging parent is synchronized for the
new stage leaf.

`prepare_target_bound_stage()` records whether it created `.cso-staging`.
Only in that case does it synchronize the already-open skills-root descriptor
after the stage itself has finalized: the skills root gained the staging
namespace entry.  Reusing an existing `.cso-staging` does not add a
skills-root synchronization call.

## Lease and failure boundary

`OwnedStageLease` retains its existing lifecycle only: `active`, `cleaned`,
and `cleanup-required`.  No consume, publish, transfer, or taint state is
introduced.

An owned lease is returned to a target-bound caller only after every required
stage and, where applicable, skills-root synchronization succeeds.  If a
required synchronization fails, no lease is returned.  The existing
identity-safe stage cleanup path is attempted; a failed cleanup retains the
existing `cleanup-required` result.

## Deliberate limitations

This is pre-publication stage durability only.  It adds no final-target
inspection or mutation, target admission, rename, native no-replace binding,
post-rename parent synchronization, publication result, journal access,
authorization, execution context, installed-state persistence, recovery,
engine integration, or CLI integration.

The POSIX `fsync` calls establish only the repository-defined synchronization
sequence above.  They are not advertised as absolute hardware power-loss
guarantees, disk-cache flush guarantees, or `F_FULLFSYNC` semantics.

Windows remains unsupported and fail-closed before candidate staging
namespace creation; it receives no path-based durability fallback.

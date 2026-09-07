# Retained-Stage Identity Evidence Seam

This descriptive increment adds one package-private, read-only observation
for a consumed `OwnedStageLease`. It has no authoritative numeric increment
identifier.

## Contract

`_observe_consumed_stage_identity(lease)` returns immutable internal evidence:

```text
matched(device, inode)
unavailable(None, None)
```

`matched` is returned only for the exact `OwnedStageLease` type when it is in
the `consumed` state, retains a live stage descriptor, and `fstat()` confirms
that descriptor is a directory with the same device/inode identity recorded
when the lease was created. All other states and observation failures return
`unavailable` without raising or changing the lease.

The evidence contains no file descriptor, path, stage name, target name,
callback, close method, or transferable ownership. The lease remains the sole
owner of its descriptors. A consumed lease whose descriptors were later closed
remains semantically consumed; it merely returns unavailable evidence.

## Authority boundary

The sole new authority is a read-only `fstat()` of the lease-owned retained
stage descriptor. The seam does not duplicate or close a descriptor, mutate a
namespace, inspect a destination target, invoke native rename, synchronize a
directory, or use a mutation lock, journal, authorization, recovery,
installed-state, engine, or CLI layer. It is not exported from the package
root.

Windows publication remains unsupported and fail-closed. This internal result
does not introduce a Windows path fallback or publication route.

## Threat model and deferred work

This seam attests only that the retained lease-owned descriptor still names the
directory identity originally recorded by that lease. It does not prove target
identity, target manifest, publication durability, or that a source pathname
was not maliciously substituted before or during rename. It does not solve
hostile same-user source-leaf substitution.

Post-call descriptor-safe target identity proof remains deferred, as does the
runtime `PublicationOutcome` mapper and every publication coordinator concern.

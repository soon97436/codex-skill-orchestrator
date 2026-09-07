# Post-Call Descriptor-Safe Target Identity Proof

This descriptive increment adds one package-private, read-only evidence seam.
At one post-call observation interval it compares a final target leaf, observed
descriptor-relatively without following the final symlink, with the retained
object identity of a consumed `OwnedStageLease`.

It is informational and point-in-time only. It is not publication proof by
itself, durability proof, manifest proof, authorization, ownership, journal
state, recovery authority, or a continued namespace guarantee.

## Private contract

`_observe_post_call_target_identity(lease, destination_parent_fd,
expected_destination_parent_identity, target_leaf)` returns one immutable
`_PostCallTargetIdentityEvidence` with exactly one of these statuses:

- `matched`: every source and parent bracket check succeeded, and the no-follow
  target observation identified a directory with the same device/inode as the
  retained consumed stage.
- `mismatched`: the bracket remained trustworthy, but target absence, a
  symlink, a non-directory object, or different target device/inode supplied
  counterevidence.
- `unavailable`: the platform or input is unsupported, source evidence is
  unavailable or drifts, the parent descriptor or identity is invalid or
  drifts, or target observation cannot be completed safely.

The result exposes only `status`. It contains no device, inode, descriptor,
path, target name, errno, exception data, lease, callback, lock,
authorization, native result, journal data, or mutation method.

The expected destination-parent identity is a private two-integer tuple
`(device, inode)`, with a non-negative device and positive inode. No new shared
filesystem-identity abstraction or serialized schema is introduced. The
target leaf uses the existing strict portable target-key validator and is
never accepted as a path.

## Observation bracket

The helper performs this bounded sequence:

```text
destination-parent fstat and expected-identity check
retained consumed-stage evidence observation
target stat relative to the borrowed parent, follow_symlinks=False
retained consumed-stage evidence observation
destination-parent fstat and expected-identity check
classification
```

Source identity must match across both source observations. Parent identity
must match the caller-supplied expected identity across both parent
observations. A target mismatch is returned only after both post-checks remain
trustworthy; lost source or parent context overrides it with `unavailable`.
A trustworthy `FileNotFoundError` is target-absence counterevidence, while
other target observation failures are `unavailable`.

The helper composes with `_observe_consumed_stage_identity(lease)` and never
accesses or exports the retained raw stage descriptor. It does not change lease
state, taint reason, cleanup state, or descriptor fields.

## Borrowed destination-parent descriptor

`destination_parent_fd` is borrowed for the call. The helper does not open,
duplicate, close, wrap, transfer, retain, or return it. The caller must keep
both this descriptor and the consumed lease live, without concurrent close or
replacement, throughout the complete observation.

The parent descriptor must have been opened before publication with safe
directory/no-follow semantics, identity-bound, retained across publication,
and passed unchanged to this helper. The helper verifies only that the supplied
descriptor is currently live, identifies a directory, matches the expected
device/inode, and remains stable across its bracket.

Critically, the helper cannot independently prove that the supplied descriptor
is the exact same FD instance previously used by a native publication call.
That provenance is the responsibility of a future trusted coordinator.

A retained parent descriptor prevents descriptor-relative lookup from being
redirected when the parent path is renamed or replaced. This result does not
prove that an absolute path still resolves to that parent object. Existing
durable target verification retains responsibility for manifest and root-path
continuity checks.

## Authority and platform boundary

On macOS and Linux, positive proof uses `fstat()` plus descriptor-relative
`os.stat(..., dir_fd=destination_parent_fd, follow_symlinks=False)`. No target
FD is opened, and target contents are not traversed or hashed.

On Windows, the helper returns `unavailable` before lease, descriptor, or
filesystem access. Windows publication remains unsupported and fail-closed;
there is no path-based or fabricated positive-proof fallback. Portable result
contract tests still execute on Windows.

The production helper never invokes `_move_directory_leaf_no_replace`,
`renameat2`, `renameatx_np`, `os.rename`, `os.replace`, or any other mutation.
It performs no parent or stage synchronization and has no PublicationOutcome,
journal, installed-state, recovery, rollback, transaction-state, mutation-lock,
authorization, engine, or CLI integration.

## Hostile same-user limitation

This evidence partially mitigates hostile same-user manipulation by detecting
some post-call discrepancies. It does not prevent or fully solve hostile
same-user substitution.

For source-leaf substitution, it can show that the observed target differs
from the retained original stage, but cannot prove which source object the
native call resolved. For destination namespace replacement, it can detect an
absent, symlink, non-directory, or differently identified target at its own
observation instant. A hostile actor may change or restore namespace entries
around that instant, and the result may become stale immediately after return.

## Explicit exclusions

This increment adds no destination-parent ownership API, publication
coordinator, runtime PublicationOutcome mapper, native adapter change, lease
lifecycle change, durable target verifier change, manifest verification,
parent synchronization, lock integration, journal transition, installed-state
persistence, recovery, rollback, engine or CLI integration, Windows
publication, or package-root export.

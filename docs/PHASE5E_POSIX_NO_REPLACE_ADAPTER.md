# Private POSIX No-Replace Adapter

This descriptive working increment adds one package-private low-level native
filesystem primitive.  The repository does not define an authoritative
post-4C2 numeric increment identifier.

## Exact authority

The adapter introduces descriptor-scoped native leaf-rename authority only.
It accepts two borrowed parent directory file descriptors and one strict POSIX
pathname component per side.  It can move one source directory leaf to an
absent destination leaf without intentionally overwriting an existing
destination.

It is not CSO publication authority, final-target authorization, stage
ownership, target admission, mutation-lock authority, authorization authority,
journal authority, or recovery authority.  It is not exported from the package
root and has no production caller.

The package-private name is not a Python security boundary.  Its safety claim
is limited to cooperating CSO writers.  Hostile same-user source-leaf
substitution remains unresolved.

## Contract

`_move_directory_leaf_no_replace(source_parent_fd, source_leaf,
destination_parent_fd, destination_leaf)` borrows its descriptors: it never
closes, duplicates, serializes, retains, or changes their flags.  The caller
owns their lifetime.

Each leaf must be one ASCII POSIX component of 1 through 255 bytes.  Empty,
`.` , `..`, slash, NUL, and non-ASCII names are rejected.  Leading and embedded
dots are allowed because existing stage leaves are generated as
`.<candidate>.cso-stage-<id>`.

Before a native call, the adapter validates that both parent descriptors name
directories and observes the source leaf descriptor-relatively without
following symlinks.  A missing, symlink, regular-file, or other non-directory
source is rejected without a native rename attempt.  This check is
TOCTOU-sensitive and does not bind the source leaf name to its observed inode.
The destination is never pre-statted to establish absence: the native
no-replace operation is the destination-absence linearization point.

## Native backends

Darwin calls `renameatx_np` through `ctypes.CDLL(None, use_errno=True)` with
`RENAME_EXCL | RENAME_NOFOLLOW_ANY`.  Linux calls libc `renameat2` with
`RENAME_NOREPLACE`.  Missing native symbols return an unsupported result.  The
adapter has no raw-syscall fallback, path fallback, copy/delete emulation,
`/proc/self/fd` route, or Windows fallback.

Darwin capability probes, `getattrlist`, parent fsync, target verification,
lease changes, lock acquisition, authorization, journal transitions, installed
state, recovery, engine, and CLI integration are deliberately out of scope.

## Result layering

`NativeNoReplaceResult` is immutable bounded metadata:

- `platform`: `darwin`, `linux`, or `unsupported`;
- `status`: a native namespace result category;
- `attempted`: true only when the native rename syscall was invoked;
- `mutation_certainty`: `succeeded`, `no-mutation`, or `indeterminate`; and
- `reason_id`: a bounded deterministic identifier.

It contains no paths, descriptors, raw errno, operating-system messages,
exception representations, or capability objects.  Native success means only
that the namespace rename returned success.  It does not mean durable
publication, `PUBLISHED`, `VERIFIED`, or `COMMITTED`.

Linux network filesystem failures can be observationally ambiguous.  In
particular, an observed Linux `EEXIST` result is retained as
`destination-exists` while its generic `mutation_certainty` remains
`indeterminate`.  This adapter never imports or constructs `PublicationOutcome`;
a future trusted coordinator must map lower-level facts together with its own
durability, lease, authorization, journal, and verification evidence.

## Platform boundary

Windows returns `unsupported-platform` before descriptor validation, source
inspection, symbol resolution, or filesystem access.  Windows publication is
unsupported and fail-closed.

The adapter deliberately cannot prevent a hostile actor from replacing a
validated source directory leaf before native pathname resolution.  Darwin's
`RENAME_NOFOLLOW_ANY` prevents symlink traversal; it does not create a
rename-by-open-directory binding.  Future publication requires post-call
identity proof using a retained source FD and descriptor-relative destination
observation.

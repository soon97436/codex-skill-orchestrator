# Phase 5E target-bound staging

Phase 5E Increment 4B2 is split. This document covers only Increment 4B2A:
target-bound verified staging. It does not replace a target despite the
`transactional_replace` module name.

## Scope

4B2A validates an existing POSIX skills root, derives one strict target key,
rejects every existing target as unowned, creates or validates the private
`.cso-staging` namespace, and prepares exact declared files there. It performs
an initial and final absence check for the final target but never creates,
renames, writes, deletes, quarantines, chmods, or otherwise mutates it.

`StageResult` remains metadata only. Its opaque stage ID is not a path,
capability, or reopening mechanism. The additive owned-stage lease keeps the
live stage and staging-parent descriptor identities until explicit cleanup. A
lease is stateful, non-copyable, non-serializable, has a safe representation,
and is revalidated before `prepared` is returned.

The lease grants ownership of a disposable verified stage only. It grants no
target ownership, install authority, publication authority, capability grant,
runtime activation, or execution authority.

## Target and filesystem model

Target authority is a validated skills root plus one strict ASCII target key;
callers cannot provide a final target path or a staging parent. Root traversal
uses descriptor-relative, no-follow opens. The root must be an existing,
user-owned, non-group/world-writable directory that is neither filesystem root
nor the home directory. Symlink/reparse and special-file roots fail closed.

The reserved `.cso-staging` namespace is opened relative to the held skills
root descriptor and must remain on the same filesystem. The lease staging
parent identity is compared with that namespace identity. Source/staging
overlap is rejected.

4B2A has no installed-state authority. It must not call an existing target
managed-current or managed-modified. Every pre-existing target is rejected as
existing-unowned and remains untouched.

`prepared` means only that target-bound staging is ready at that instant. It
does not reserve the target or solve a later target-appearance race.

## Deliberate limitations

Real Windows target-bound staging is rejected before `.cso-staging` or a final
target is mutated. Remote fetch is disabled. Runtime capability enforcement,
activation, post-install execution, target replacement, shared `MutationLock`,
transaction journal, installed state, rollback, and crash/power-loss recovery
are not implemented.

Increment 4C must recompute the live admission plan, obtain the shared CSO
mutation lock, create a durable journal, recheck target state, mutate a target,
post-verify it, and then commit installed state. Existing unmanaged targets
must never be overwritten; future modified managed targets default to reject.

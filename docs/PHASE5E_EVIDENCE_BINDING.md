# Phase 5E Increment 2 — Immutable Evidence Binding

Phase 5E-2 adds a pure policy-plane seam for detecting evidence
substitution and drift before a future execution-plane integration. It binds
one already validated registry candidate, its trust/capability/admission
evidence, an exact operation, and the bounded target class
`registry-skill-user-scope`.

This is an integrity record, not an execution mechanism. The states are:

- `bound`: the supplied Phase 5E pipeline is admissible and all evidence is
  structurally valid and mutually consistent;
- `rejected`: a valid Phase 5E pipeline contains an explicit rejection;
- `unknown`: a valid Phase 5E pipeline lacks enough evidence to admit the
  candidate;
- `invalid`: an input, evidence contract, or cross-stage relationship is
  malformed or contradictory.

`bound` does not mean executed, installed, active, approved for runtime
capabilities, or fresh operator consent.

## Why this seam exists

The binding closes a time-of-check/time-of-use substitution gap. A caller
cannot validate candidate A and then present candidate B, a changed manifest,
or a changed policy under the same identifier while reusing the old evidence.
The complete normalized snapshots participate in a deterministic digest, so
any material change produces a different binding or a stale verification.

The module is deliberately independent of the current CSO management-plane
commands. `cso install`, `cso activate`, `apply_profile()`, and `plan_install()`
continue to manage the bundled CSO/router profiles. Phase 5E-2 does not gate,
replace, or modify them.

## Public API

```python
create_admission_binding(
    *,
    registry_schema_version,
    registry_entry,
    trust_profile_schema_version,
    trust_policy,
    trust_evidence,
    capability_policy,
    capability_declaration,
    requested_capabilities,
    trust_decision,
    capability_decision,
    recommendation_decision,
    installation_decision,
    target_class,
) -> dict

verify_admission_binding(binding, *, <the same current evidence>) -> dict
```

The facade calls the pure Phase 5E-1 admission pipeline on the supplied
normalized decisions. It never calls a registry loader, trust evaluator,
capability evaluator, recommendation evaluator, or installer. A pipeline
result of `invalid`, `rejected`, or `unknown` can never be upgraded to a
positive binding.

## Evidence and binding digests

The evidence digest covers the complete normalized evidence snapshot:

- registry schema version and full registry entry;
- trust profile schema version, resolved trust policy, and trust evidence;
- resolved capability policy, declaration, and requested capabilities;
- all four normalized Phase 5 decisions;
- the Phase 5E-1 pipeline result.

The registry entry includes source identity, source path identity, manifest
paths and SHA-256 declarations, license metadata, provenance metadata, and
optional capability declaration. Those values are digest input only; they are
not copied into the returned public binding beyond the stable subject
identity (skill ID, version, source type, and revision).

The binding digest is a separate, domain-separated SHA-256 over the evidence
digest, binding schema version, exact `install`/`activate` operation, and
`registry-skill-user-scope` target class. Therefore an install binding cannot
be reused as an activate binding. The digest is equality/integrity evidence;
it is not a token, credential, permit, bearer secret, capability grant, or
execution authorization. Knowledge of it grants nothing.

Canonical digest encoding is UTF-8 JSON with sorted object keys, compact
separators, `allow_nan=False`, and explicit domain prefixes. Set-like lists
(policy lists, manifests, and diagnostic IDs) are sorted after duplicate
rejection. Ordered pipeline stages retain their fixed order. The input
normalizer accepts only JSON-safe values and applies conservative depth,
container, string, and integer bounds.

The explicit input limits are `MAX_BINDING_DEPTH = 12`,
`MAX_BINDING_ITEMS = 2048` total values, `MAX_BINDING_LIST_ITEMS = 128` per
list or mapping, `MAX_BINDING_STRING_BYTES = 4096` UTF-8 bytes per string, and
`MAX_BINDING_INTEGER_BITS = 256`. Exceeding any limit is invalid; no input is
truncated or silently coerced.

## Verification semantics

`verify_admission_binding()` structurally validates the stored `bound` object,
recomputes the binding from current supplied evidence, and compares both
digests plus the stable subject, operation, and target class.

- `current`: the stored binding is valid, current evidence is still bound,
  and the exact identity matches;
- `stale`: the stored binding is valid but evidence changed, the operation or
  target changed, or current admission is no longer bound;
- `invalid`: the stored binding or current evidence is malformed or
  contradictory.

There is no refresh, replacement, timestamp, TTL, nonce, UUID, or clock-based
staleness. `stale` means evidence-based identity mismatch, never “too old”.
Stored `operator_authorization: granted` is evidence that was supplied to the
installation decision; it is not fresh operator consent. Every binding and
verification therefore carries the fixed operator-freshness limitation.

## Result and privacy boundary

A successful binding has `schema_version: 1`,
`assessment_scope: "phase5e-evidence-binding"`, stable subject identity,
operation, target class, two lowercase 64-hex digests,
`execution_status: "not-performed"`, fixed reason IDs, fixed limitations,
and `truncated: false`.

Verification returns only a bounded status/reason/limitation envelope. It does
not echo expected or actual digests, changed fields, paths, URLs, commands,
file names or hashes, publisher/maintainer data, trust evidence, policy
contents, task text, credentials, host/user/environment metadata, timestamps,
or exception strings.

All outcomes include these limitations:

- `phase5e.binding.limit.execution-not-performed`;
- `phase5e.binding.limit.operator-freshness-not-verified`;
- `phase5e.binding.limit.runtime-capability-enforcement-not-implemented`;
- `phase5e.binding.limit.not-an-execution-token`;
- `phase5e.binding.limit.remote-fetch-disabled`.

When no capability was requested, the result also includes
`phase5e.binding.limit.runtime-capability-not-requested`. That state is not a
runtime capability grant.

## Security and non-goals

`admission_binding.py` has no filesystem, environment, subprocess, shell,
network, Git/GitHub, engine, CLI, installer, registry-loader, or LLM
dependency. It does not install, activate, execute, mutate state, resolve
remote registries, or implement L2–L5 runtimes. Remote fetching remains
disabled and runtime capability enforcement remains unimplemented.

The module does not create `cso.lock`, read or write lockfiles, or turn
operator consent into a reproducibility field. Phase 6 may reuse stable
evidence concepts without treating this binding as a complete lockfile.

Future Phase 5E-3 work may define an execution boundary that obtains fresh
operator authorization and performs independent runtime checks. That future
boundary must continue to distinguish:

`trust != capability admission != recommendation != installation
authorization != runtime capability authorization`.

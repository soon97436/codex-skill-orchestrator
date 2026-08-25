# Phase 5C — Monotonic Trust Policy Profiles

Phase 5C adds named, deterministic trust-policy profiles on top of the
validated registry and the Phase 5A trust evaluator. Profiles are a
control-plane narrowing mechanism; they are not installation permission,
activation permission, or capability admission.

## Trust boundaries

`security/allowlist.json` remains the operational hard floor. A profile may
narrow its allowed source types, SPDX licenses, and provenance classes, but it
must never expand them or disable operational checksum and immutable-revision
requirements. A weakening or malformed profile fails closed with a typed
validation error; there is no silent intersection, coercion, or fallback.

The registry schema, registry data, checksum index, and operational security
files remain unchanged. In particular, the bundled-only operational policy
continues to deny remote sources and no profile can enable Git or network
fetching.

## Validated snapshot and pure policy resolution

Ordinary `validate_registry()` and `validate_registry_snapshot()` retain their
existing public behavior and do not load trust profiles. The separate
`validate_registry_trust_snapshot()` boundary reuses the same registry,
provenance, path, license, and checksum validation and then validates
`security/trust_profiles.json`.

The pure `registry_trust_policy` module only validates supplied mappings,
checks monotonic floors, resolves an exact profile ID, and compiles an
effective Phase 5A policy. It has no filesystem, environment, subprocess,
shell, network, randomness, or LLM behavior.

The initial document contains one default profile,
`first-party-bundled`, which allows bundled sources, first-party declared
provenance, and the operational approved SPDX set. A requested profile must
match exactly; unknown IDs fail without fallback.

## Provenance semantics

The adapter projects the validated registry declaration
`provenance.third_party` into a bounded class:

* `false` → `first-party`
* `true` → `third-party`

This is only a validated registry declaration, not cryptographic publisher
identity. Provenance completeness remains a separate evidence fact. A
third-party entry with complete metadata is not treated as incomplete; it is
rejected only when the selected profile disallows the `third-party` class, with
the stable reason `trust.provenance.class-disallowed`.

Legacy direct Phase 5A callers that omit profile-aware provenance fields retain
the exact prior decision dimensions, ordering, statuses, reasons, and
admission precedence. Profile-aware evaluator inputs must provide the entry
class and policy allowlist together.

## Adapter result

`evaluate_project_registry_trust(project_root, *, profile_id=None)` consumes one
validated trust snapshot, resolves the default or exact requested profile, and
evaluates normalized entries in ascending skill-ID order. The project result
adds only the selected `trust_profile_id` to the existing metadata-only shape.

Trust results do not expose paths, URLs, hashes, file contents, publisher text,
exception text, credentials, host or user metadata, timestamps, or environment
values. An admissible result is not authorization to install or activate a
skill.

## Explicit non-goals

Phase 5C does not add remote resolution, downloads, installer integration,
recommendation integration, CLI integration, capability admission, runtime
enforcement, or network policy changes. Capability admission remains a future
phase; reproducibility remains a separate roadmap concern.

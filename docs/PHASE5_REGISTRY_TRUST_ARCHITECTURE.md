# Phase 5A — Deterministic Registry Trust

## Purpose

Phase 5A adds a pure, deterministic trust-decision primitive for normalized
registry metadata. It is a planning/control-plane contract. It does not fetch,
install, activate, execute, or grant permissions to a skill.

The registry remains the sole trusted source of skill IDs, but registry
membership is not itself a trust decision.

## Public API

```python
evaluate_registry_trust(entry, *, policy, evidence) -> dict
```

The caller supplies already-normalized metadata, an explicit policy snapshot,
and deterministic evidence facts. The evaluator does not call the registry
validator and does not access repository files. Missing evidence is represented
as `None` and produces `unknown` rather than an implicit pass.

The evaluator accepts only the normalized metadata needed for this decision:

- entry identifier;
- source type and revision;
- SPDX license and redistribution flag;
- policy source/license allowlists and checksum/revision requirements;
- bounded boolean or `None` evidence facts.

Malformed caller input raises `TypeError` for a wrong top-level object and
`ValueError` for missing fields, unsupported normalized values, or invalid
policy/evidence types. Values are never silently coerced.

## Decision contract

Decision dimensions are always emitted in this order:

1. `registry`
2. `source_policy`
3. `source_identity`
4. `provenance`
5. `license`
6. `integrity`
7. `capability_policy`

Each decision has one of:

```text
pass | fail | unknown | not-applicable
```

The overall status is one of:

```text
admissible | rejected | unknown
```

Admission precedence is fail-closed:

```text
any fail       -> rejected
else unknown  -> unknown
else           -> admissible
```

`unknown` never falls back to `admissible`. `not-applicable` does not block
admission.

The result contains only stable fields:

```python
{
    "schema_version": 1,
    "status": "admissible" | "rejected" | "unknown",
    "skill_id": "...",
    "decisions": [...],
    "reasons": [...],
    "limitations": [...],
    "truncated": False,
}
```

It never echoes the entry, policy, evidence, payload content, paths, URLs,
secrets, host data, timestamps, or exception text.

## Dimension semantics

- `registry`: caller evidence that the entry passed the existing structural
  registry validation.
- `source_policy`: source type membership in the supplied allowlist. An
  explicitly disallowed source is `fail`, not `unknown`.
- `source_identity`: bundled sources are `not-applicable`; remote Git sources
  require immutable-revision evidence when policy requires it.
- `provenance`: caller-provided completeness evidence. Publisher and
  maintainer strings are not cryptographic identity proof.
- `license`: normalized SPDX membership and `redistribution == True`.
- `integrity`: checksum evidence when checksums are required; otherwise
  `not-applicable`.
- `capability_policy`: always `not-applicable` in Phase 5A. Capability
  declarations are not permissions and runtime enforcement is not implemented.

## Reason and limitation contract

Reason IDs are a closed vocabulary under the `trust.*` namespace. They are
flattened from decisions in the same dimension order, followed by exactly one
final admission reason. No reason ID contains a skill name, path, URL,
username, hostname, exception text, or raw value.

Limitations use fixed ordering. Every result includes
`trust.limit.capability-enforcement-not-implemented`. Git metadata additionally
includes `trust.limit.remote-fetch-disabled`.

## Security and privacy boundary

`registry_trust.py` has no filesystem, environment, subprocess, shell, network,
remote-registry, installer, skill-activation, LLM, or runtime-enforcement
dependency. It performs only local calculation over caller-supplied metadata.

Remote metadata representation is not remote fetch permission. A future Git
source may be described by the schema, but Phase 5A does not contact GitHub,
resolve refs, download artifacts, extract archives, or install code.

Trust/admissible is not install permission. Downstream install and activation
remain governed by the existing validated local installer contract.

## Compatibility and schema boundary

Phase 5A does not modify `registry/schema.json`, `registry/skills.json`,
`security/allowlist.json`, `security/checksums.json`, or any Phase 3, Phase 4,
or Phase 7 module. Registry schema v1 remains unchanged. The API is not exposed
through the CLI and is not integrated into recommendations, installation,
configuration, or release audit yet.

Phase 4 and Phase 7 semantics remain separate:

- Phase 4 validates task input, acceptance criteria, workflow selection, and
  completion evidence.
- Phase 7 selects a minimum assurance layer from caller-declared requirements.
- Phase 5A evaluates registry trust metadata only.

## Future Phase 5 boundaries

- **Phase 5B:** adapt existing registry validation, allowlist, and checksum
  results into trust evidence without changing install behavior.
- **Phase 5C:** make provenance, license, and source-policy profiles explicit;
  remote support remains disabled by default.
- **Phase 5D:** define capability-policy and recommendation/install admission
  boundaries while retaining declarative-only capability analysis.
- **Phase 5E:** perform macOS/Windows deterministic integration and release
  gating.

Reproducibility artifacts and locks remain Phase 6 work. Remote fetchers,
runtime sandboxing, LLM routing, and L2–L5 execution are outside this phase.

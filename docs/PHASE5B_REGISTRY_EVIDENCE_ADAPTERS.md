# Phase 5B — Registry Evidence Adapters

## Purpose

Phase 5B connects the existing validation plane to the pure Phase 5A trust
decision plane. It introduces a validated snapshot boundary so registry
metadata, the security allowlist, and checksum verification all come from one
successful local validation pass.

The adapter is a projection layer, not a second validator and not an
installation permission system.

## Validated Snapshot Adapter

The architecture is:

```text
registry/security files
        ↓
existing validation layer
        ↓
validated registry + validated allowlist snapshot
        ↓
Phase 5B adapter
        ↓
normalized Phase 5A entry/policy/evidence
        ↓
evaluate_registry_trust()
        ↓
deterministic trust result
```

The validation plane performs schema, path, provenance, license, checksum,
tree-manifest, and network-policy checks. The trust decision plane receives
only normalized values and evidence; it does not read files or catch
validation exceptions.

## APIs

The existing API remains compatible:

```python
validate_registry(project_root) -> dict
```

It still returns the validated registry mapping and propagates the existing
typed exceptions.

Phase 5B adds the validation-layer snapshot API:

```python
validate_registry_snapshot(project_root) -> dict
```

Its stable shape is:

```python
{
    "schema_version": 1,
    "registry": {"skill-id": <validated entry>},
    "policy": <validated allowlist snapshot>,
}
```

The registry and policy are loaded and validated in the same pass. Returned
data is isolated from the validator's working objects.

The adapter API is:

```python
evaluate_project_registry_trust(project_root) -> dict
```

It calls only `validate_registry_snapshot()` and
`evaluate_registry_trust()`. Validated skills are projected and evaluated in
ascending skill-ID order. The project result is:

```python
{
    "schema_version": 1,
    "skills": [<Phase 5A result>, ...],
    "truncated": False,
}
```

## Evidence mapping

Successful validation establishes the following bounded facts:

- `registry_valid = True`
- `provenance_complete = True`
- `integrity_verified = True`
- bundled source identity is `None` because it is not applicable;
- a validated Git source is immutable only when the validated policy requires
  immutable revisions.

The adapter projects only the Phase 5A entry fields (`id`, source type and
revision, SPDX license, and redistribution flag) and the four Phase 5A policy
fields. It does not silently filter, coerce, or infer policy values.

Capability admission remains `not-applicable`. Capability policy is deferred
to Phase 5D.

## Failure semantics

Malformed, unsafe, tampered, or unvalidated input fails before any trust result
is returned. `ValidationError`, `SecurityError`, and `IntegrityError` propagate
unchanged. The adapter never translates an exception into `unknown` and never
parses exception text.

This preserves mandatory SHA-256 verification, tree-manifest verification,
security checksum-index verification, and the existing distinction between
validation-time integrity enforcement and Phase 5A's `require_checksums`
decision dimension. Setting `require_checksums` to false in a normalized
Phase 5A policy does not disable the validation layer's operational checks.

## Privacy and output contract

Results are deterministic, metadata-only, and ordered by the authoritative
Phase 5A schema. They do not include source paths, repository URLs, raw file
contents, file hashes, license contents, exception text, secrets, usernames,
hostnames, timestamps, or environment values.

The adapter performs no filesystem reads of its own. All managed-file access
is confined to the existing validation snapshot API.

## Security and integration boundary

Phase 5B adds no network access, remote fetch, HTTP client, subprocess, shell
execution, environment inspection, installer behavior, skill activation,
recommendation gating, CLI surface, release-audit integration, LLM call,
capability enforcement, runtime sandbox, lockfile, or reproducibility logic.

Remote fetch remains disabled. A trust result of `admissible` is not install
permission, activation permission, or execution authorization.

The registry remains the trusted source of skill IDs. Phase 3, Phase 4, Phase
7, the registry/schema files, and security policy/data remain unchanged.

## Current bundled behavior

For the checked-in bundled `codex-skill-orchestrator` entry, the adapter
produces an admissible Phase 5A result with:

- registry: pass
- source policy: pass
- source identity: not-applicable
- provenance: pass
- license: pass
- integrity: pass
- capability policy: not-applicable

## Next boundary

Phase 5C may define explicit provenance, license, and source-policy profiles.
It must retain the validated snapshot boundary and keep remote support disabled
by default. Capability admission remains Phase 5D; reproducibility artifacts
remain Phase 6 work.

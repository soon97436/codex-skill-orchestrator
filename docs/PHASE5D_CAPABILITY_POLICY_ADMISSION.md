# Phase 5D — Capability Policy and Admission

## Increment 1 scope

Increment 1 adds a pure, deterministic capability-policy evaluator. It
accepts already-normalized capability declarations, an explicit policy
document, caller-declared requested capabilities, and a normalized Phase 5A
or Phase 5C trust status. It does not read files or perform any external
operation.

The evaluator returns a metadata-only decision. It does not grant operating
system permissions, install a skill, activate a skill, execute a process, or
enforce a runtime sandbox.

## Separate decisions

These are intentionally different decisions:

```text
trustworthiness
  != capability admission
  != recommendability
  != installation authorization
  != runtime capability authorization
```

The registry remains the sole trusted source of skill IDs. Capability
declarations are untrusted metadata until they pass the existing registry
validation and this policy evaluator.

## Capability policy profiles

`security/capability_profiles.json` is separate from
`security/trust_profiles.json`.

`security/allowlist.json` remains the operational source/network hard floor.
The capability document has its own non-selectable `operational_floor` and
named profiles. A profile may be equal to or more restrictive than that floor;
it may not widen filesystem scope, network mode, process mode, or command
allowlists.

Profile selection is exact. Unknown profiles, malformed profiles, and floor
mismatches fail with a typed validation error. There is no fallback, silent
intersection, coercion, or deduplication.

The initial capability vocabulary reuses the registry schema:

1. `filesystem-read`
2. `filesystem-write`
3. `network`
4. `process`

Filesystem scopes are `project`, `workspace`, and `unrestricted`. Network
modes are `none`, `localhost`, `restricted`, and `unrestricted`. Process modes
are `none`, `commands`, and `arbitrary`; command names remain bounded literal
values. Unknown values are invalid. The default profile allows project reads
only and denies network, writes, and process execution.

## Public interface

```python
evaluate_capability_policy(
    capability_declaration,
    *,
    policy,
    requested_capabilities,
    trust_status,
) -> dict
```

The interface is pure and deterministic. It has no filesystem, environment,
subprocess, shell, network, Git, GitHub, registry-loading, installer, or LLM
dependency. The evaluator does not interpret task text or command syntax.

## Result contract

```python
{
    "schema_version": 1,
    "status": "admissible" | "rejected" | "unknown" | "invalid" | "not-requested",
    "assessment_scope": "capability-policy-only",
    "trust_status": "admissible" | "rejected" | "unknown" | "not-evaluated",
    "profile_id": "...",
    "decisions": [
        {
            "capability": "filesystem-read",
            "status": "allowed" | "denied" | "unknown" | "not-requested",
            "reason_ids": [...]
        }
    ],
    "reasons": [...],
    "limitations": ["capability.limit.enforcement-not-implemented"],
    "truncated": False,
}
```

Decisions are always emitted in the four-family canonical order. `admissible`
requires trust `admissible` and independent approval of every requested
family. `not-requested` means that the request is empty; it is not a
capability grant. `unknown` never becomes `admissible`.

## Trust seam

Phase 5A/5C supplies only a normalized trust status. A rejected, unknown, or
not-evaluated trust result can never produce an `allowed` capability decision.
An admissible trust result is necessary but not sufficient: the requested
capabilities must still fit the declaration and selected profile.

The Phase 5A trust evaluator remains unchanged. Its capability-policy
dimension remains `not-applicable` because Increment 1 is a separate seam.

## Fail-closed rules

- Unknown or duplicate capabilities are invalid.
- Malformed declarations or requests are invalid.
- Missing declaration with a non-empty request is unknown.
- Unknown profile IDs and floor mismatches are typed failures.
- A request outside the selected profile is rejected.
- Trust rejection produces no positive capability admission.
- Missing policy never falls back to a permissive policy.

## Privacy and security

Results never contain task text, excerpts, command text, paths, URLs,
credentials, hashes, publisher text, hostnames, usernames, timestamps, or
environment values. Diagnostics use only fixed reason IDs and bounded
capability family names.

Increment 1 does not add recommendation filtering, installer enforcement, CLI
integration, remote fetching, dependency resolution, shell execution, runtime
sandboxing, or L2–L5 execution.

## Increment 2 — Recommendation admission seam

Increment 2 adds a pure seam for composing already-normalized upstream
decisions. It does not change the recommendation engine or install/runtime
paths. The intended decision pipeline is:

```text
candidate
  → registry membership
  → trust admission
  → capability admission
  → recommendation admission
```

The public interface is:

```python
evaluate_recommendation_admission(
    *,
    registry_membership: bool,
    trust_status: str,
    capability_decision: Mapping[str, Any],
) -> dict
```

It consumes only a boolean registry-membership fact, a normalized trust state,
and the content-free result of `evaluate_capability_policy`. It performs no
registry loading, policy loading, recommendation filtering, installation, or
activation.

The closed recommendation status vocabulary is:

```text
recommendable | rejected | unknown | invalid
```

Registry absence and explicit trust or capability rejection are `rejected`.
Unknown or not-yet-evaluated trust, and unknown capability evidence, remain
`unknown`. Malformed upstream evidence, unsupported input types, schema
mismatches, and trust-status mismatches are `invalid`. Only registry membership
plus admissible trust plus independently admissible capability evidence is
`recommendable`.

An empty capability request may still be `recommendable` when trust and
registry checks pass, but the result explicitly carries these limitations:

```text
recommendation.limit.capability-authorization-not-granted
recommendation.limit.installation-not-authorized
recommendation.limit.runtime-capability-not-authorized
```

Thus `recommendable` is not capability authorization, installation
authorization, or runtime authorization. No fallback, coercion, silent
intersection, or upgrade from unknown evidence is permitted. Per-capability
metadata remains in the fixed family order and contains only allowlisted
status/reason identifiers.

The seam is metadata-only and preserves the privacy boundary: it never emits
candidate identifiers, task text, commands, paths, URLs, secrets, tokens,
hashes, publisher text, usernames, hostnames, environment values, timestamps,
or exception strings. It has no filesystem, subprocess, shell, network,
environment, Git/GitHub, LLM, or installer dependency.

Increment 2 leaves `recommendations.py`, the capability-policy evaluator,
registry/trust evaluators, installer, CLI, and all Phase 3/4/7 contracts
unchanged. Runtime enforcement remains not implemented.

## Compatibility

The Phase 5A canonical probe, Phase 5C trust-profile semantics, registry
validation, declarative capability analysis, recommendation output, CLI,
installer, Phase 3/4/7 contracts, and canonical JSON writer remain unchanged.

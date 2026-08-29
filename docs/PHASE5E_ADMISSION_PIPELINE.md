# Phase 5E — Pure Deterministic Admission Pipeline

Phase 5E Increment 1 adds a policy/admission-plane facade over the
normalized decisions produced by the preceding Phase 5 seams. It validates
their shapes, cross-checks the facts they explicitly carry, and returns one
bounded metadata-only decision envelope.

## Planes and current installation boundary

CSO keeps three separate planes:

1. planning and recommendation,
2. policy and admission,
3. execution and mutation.

The current `cso install` command deploys the CSO application and its fixed
bundled router profile through `engine.apply_profile(include_app=True)`. The
current `cso activate` command switches that fixed router profile through
`engine.apply_profile(include_app=False)`. Neither command is an arbitrary
registry-candidate installer, so this increment deliberately does not wire
the facade into either command. It does not call `engine.py`, `cli.py`, an
installer, or a mutation path.

## Four-stage pipeline

The public API is:

```python
evaluate_admission_pipeline(
    *,
    trust_decision,
    capability_decision,
    recommendation_decision,
    installation_decision,
) -> dict
```

It consumes already-produced normalized mappings. It does not load files or
re-run any upstream evaluator. The immutable stage order is:

1. `registry-trust`
2. `capability-policy`
3. `recommendation-admission`
4. `installation-authorization`

The facade verifies the existing schema versions, scopes, closed status and
reason vocabularies, bounded arrays, canonical decision order, and
`truncated == False`. It then verifies the cross-stage facts:

- capability trust status equals registry-trust status;
- recommendation trust and capability statuses equal the preceding stages;
- installation recommendation and capability statuses equal the
  recommendation stage.

Any mismatch or status laundering is invalid. The facade never guesses,
repairs, silently intersects, or chooses one conflicting report.

## Result and status semantics

The normalized result has `schema_version: 1`,
`assessment_scope: "phase5e-admission-pipeline"`, the four `stages`,
`overall_status`, `execution_status: "not-performed"`, fixed `reason_ids`,
fixed `limitations`, and `truncated: false`.

The only top-level statuses are:

- `admissible`: all supplied reports are structurally valid and consistent,
  trust is `admissible`, capability policy is `admissible` or
  `not-requested`, recommendation is `recommendable`, and installation
  authorization is `authorized` with a granted operator assertion;
- `rejected`: a valid upstream stage explicitly rejects the candidate;
- `unknown`: a valid upstream stage lacks sufficient evidence;
- `invalid`: an input contract, cross-stage fact, or authorization invariant
  is malformed or contradictory.

The precedence is `invalid`, then `rejected`, then `unknown`, and only then
`admissible`. No downstream stage can upgrade a non-positive upstream state.

`capability_status == "not-requested"` may coexist with a recommendable
recommendation and authorized installation decision when all reports agree.
That state is not a capability grant, runtime authorization, or execution
claim; the result includes the explicit
`phase5e.limit.runtime-capability-not-requested` limitation.

## Trust, capability, recommendation, and installation boundaries

These concepts remain distinct:

`trustworthiness != capability admission != recommendability != installation
authorization != runtime capability authorization`.

Trust admission is necessary but not sufficient for capability admission.
Capability admission is not recommendation filtering. Recommendation is not
installation authorization. Installation authorization is only a bounded
decision and does not install, activate, or execute anything.

## Privacy and security

The envelope copies no upstream payloads. It emits only fixed stage names,
closed statuses, fixed reason IDs, fixed limitation IDs, the schema version,
`execution_status`, and `truncated`. It never emits skill IDs, profile IDs,
candidate IDs, task text, paths, commands, URLs, source or publisher data,
credentials, secrets, hashes, host/user/environment metadata, timestamps,
transaction IDs, or exception text.

`admission_pipeline.py` is a pure module. It has no filesystem, environment,
network, subprocess, shell, Git, GitHub, engine, CLI, installer, registry
loader, or LLM dependency. No evidence binding or operator freshness is
claimed yet.

## Fixed limitations

Every result records:

- `phase5e.limit.execution-not-performed`
- `phase5e.limit.evidence-binding-not-implemented`
- `phase5e.limit.operator-freshness-not-verified`
- `phase5e.limit.runtime-capability-enforcement-not-implemented`

The not-requested capability state additionally records
`phase5e.limit.runtime-capability-not-requested`.

## Future compatibility and non-goals

The facade is additive and leaves the Phase 3, Phase 4, Phase 5A–5D, and
Phase 7 contracts unchanged. In particular, the Phase 5A canonical output,
registry/security documents, recommendations, installer behavior, CLI
behavior, and existing JSON contracts are not modified.

Evidence binding is deferred to Phase 5E-2. Operator freshness is not
verified. Future Phase 6 reproducibility and lockfile/sync work can supply
additional evidence without changing this pure composition seam.

This increment does not implement:

- installation, activation, execution, or runtime capability enforcement;
- candidate/source/artifact identity binding or execution/approval tokens;
- repository policy loading or remote registry resolution;
- remote fetching, network package resolution, or dependency solving;
- L2–L5 runtimes, sandboxing, process isolation, or human approval
  orchestration;
- CLI, engine, installer, recommendation, registry, security, or schema
  integration;
- LLM routing or AI policy overrides.

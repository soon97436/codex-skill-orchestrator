# Phase 7A — Deterministic Assurance Triage Architecture

## Purpose

Phase 7A adds a small, deterministic structural assurance-triage primitive
above the Phase 4 foundations. It recommends one safety layer for a task
request:

| Layer | Meaning | Phase 7A behavior |
| --- | --- | --- |
| L1 Prompt | Direct handling of a structurally present task | Select only |
| L2 Context | Explicit external context or document requirement | Select only |
| L3 Harness | Explicit isolated validation requirement | Select only |
| L4 Loop | Explicit test/validation iteration requirement | Select only |
| L5 Graph | Explicit high-risk, cross-boundary, or approval route | Select only |

The module does not execute, schedule, graph, approve, install, or route a
skill. A selected layer is a recommendation for a later workflow; it is not a
claim that the task is semantically complete or safe to execute.

## Roadmap boundary

The phase number is intentionally separate from the existing platform
roadmap:

- Phase 5 = Registry & Trust
- Phase 6 = Reproducibility
- Phase 7 = Assurance Triage
- Phase 8 = Context Planner / L2
- Phase 9 = Harness / L3
- Phase 10 = Loop / L4
- Phase 11 = Graph / State / Approval / L5

This document and the core module implement only Phase 7A. Phase 7B and later
Phase 7 work are not implemented. Phases 8–11 are roadmap boundaries, not
runtime capabilities of this checkpoint.

## Public seam

`skill_orchestrator.task_triage.analyze_task_triage(request)` is a pure deep
module. The request is a JSON-like dictionary with only these fields:

```python
{
    "task_input": None | str | bytes,  # optional; validated by Phase 4A
    "requirements": {                  # required to select a layer
        "context": bool,
        "harness": bool,
        "loop": bool,
        "graph": bool,
        "human_approval": bool,
    },
}
```

The requirements are caller-declared structural facts. An explicit empty
mapping selects L1; omitting the mapping leaves complexity unknown and returns
`needs-input`. Unknown fields, unknown requirement names, non-boolean values,
malformed UTF-8, NULs, and oversized task input fail closed. No
natural-language keyword, grammar, locale, or prompt heuristic is inspected.

The assurance floor for future integrations is defined as:

```text
effective_level = max(
    deterministic_floor,
    repository_policy,
    requested_level,
    optional_ai_recommendation,
)
```

An optional AI recommendation may raise assurance, but must never lower the
deterministic floor. Phase 7A computes only the structural deterministic
selection; it does not implement `repository_policy`, AI routing, or any
runtime escalation mechanism.

## Result contract

The result is metadata-only and has stable fields:

```python
{
    "schema_version": 1,
    "status": "selected" | "needs-input" | "invalid",
    "assessment_scope": "deterministic-task-triage",
    "selected_layer": "L1" | "L2" | "L3" | "L4" | "L5" | None,
    "reasons": [...],
    "limitations": [...],
    "truncated": False,
}
```

`selected` means only that the input structure is valid and a layer can be
selected. `needs-input` is used for missing or empty task input. `invalid` is
used for unsupported structure or invalid task input. A result never asserts
semantic sufficiency, correctness, feasibility, approval, completion, or
execution readiness.

## Selection rules

Phase 7A uses a fixed precedence, evaluated independently of mapping order:

1. `graph` or `human_approval` → L5
2. `loop` → L4
3. `harness` → L3
4. `context` → L2
5. no requirement → L1

All true requirements remain visible as bounded structural reason records;
the highest layer is the sole selected layer. L5 reports that approval has not
been granted and graph orchestration is not implemented. No layer causes an
execution or an external side effect.

## Explainability and limitations

Reason IDs are centralized in `TRIAGE_REASON_IDS`:

- `triage.request.invalid`
- `triage.task.needs-input`
- `triage.task.invalid`
- `triage.task.structurally-ready`
- `triage.requirement.context`
- `triage.requirement.harness`
- `triage.requirement.loop`
- `triage.requirement.graph`
- `triage.requirement.human-approval`
- `triage.layer.selected`

Every result carries the fixed limitations
`triage.limit.semantic-intent-not-inferred`,
`triage.limit.execution-not-performed`, and
`triage.limit.requirements-caller-declared`. L5 adds
`triage.limit.human-approval-not-granted` and
`triage.limit.graph-orchestration-not-implemented`.

Evidence references contain only allowlisted states, requirement names, and
layer names. They never contain task text, excerpts, hashes, paths, secrets,
machine identity, environment values, or timestamps.

## Phase 4 responsibility boundaries

- Phase 4A remains the sole task-input structural adapter. Phase 7A consumes
  its metadata result and does not duplicate its UTF-8, BOM, whitespace, NUL,
  or size rules.
- Phase 4B remains the acceptance-criteria structural validator. Phase 7A
  does not infer, validate, or satisfy criteria.
- Phase 4C remains explicit workflow-profile selection. Layer selection is a
  separate safety recommendation and does not select profiles or skill IDs.
- Phase 4D remains evidence-coverage evaluation. Phase 7A does not execute or
  verify work and does not claim completion.
- Phase 4E remains the cross-platform release gate. Phase 7A does not add a
  CLI or release gate integration.
- The registry remains the sole trusted skill-ID source. This module emits no
  skill identifier and never resolves a registry entry.

## Security and portability

The core module has no filesystem, environment, subprocess, shell, network,
LLM, repository-discovery, or remote-registry dependency. Task text is inert
data. It uses fixed ordering and Phase 4A's explicit Unicode behavior, so
line endings, locale, platform, and input mapping order cannot change the
decision. Canonical JSON remains an existing caller concern; no CLI surface
is introduced in Phase 7A.

## Deferred work

L3 harness execution, L4 validation loops, L5 graph/state orchestration,
human approval UX, task-to-repository context binding, workflow integration,
repository-policy evaluation, AI routing, and CLI exposure require separate
phases and contracts. They must not be implemented by extending this deep
module opportunistically.

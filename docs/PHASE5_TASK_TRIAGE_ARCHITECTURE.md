# Phase 5A Task Triage Architecture

## Purpose

Phase 5A adds a small, deterministic structural triage primitive above the
Phase 4 foundations. It recommends one safety layer for a task request:

| Layer | Meaning | Phase 5A behavior |
| --- | --- | --- |
| L1 Prompt | Direct handling of a structurally present task | Select only |
| L2 Context | Explicit external context or document requirement | Select only |
| L3 Harness | Explicit isolated validation requirement | Select only |
| L4 Loop | Explicit test/validation iteration requirement | Select only |
| L5 Graph | Explicit high-risk, cross-boundary, or approval route | Select only |

The module does not execute, schedule, graph, approve, install, or route a
skill. A selected layer is a recommendation for a later workflow; it is not a
claim that the task is semantically complete or safe to execute.

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

Phase 5A uses a fixed precedence, evaluated independently of mapping order:

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

- Phase 4A remains the sole task-input structural adapter. Phase 5A consumes
  its metadata result and does not duplicate its UTF-8, BOM, whitespace, NUL,
  or size rules.
- Phase 4B remains the acceptance-criteria structural validator. Phase 5A
  does not infer, validate, or satisfy criteria.
- Phase 4C remains explicit workflow-profile selection. Layer selection is a
  separate safety recommendation and does not select profiles or skill IDs.
- Phase 4D remains evidence-coverage evaluation. Phase 5A does not execute or
  verify work and does not claim completion.
- Phase 4E remains the cross-platform release gate. Phase 5A does not add a
  CLI or release gate integration.
- The registry remains the sole trusted skill-ID source. This module emits no
  skill identifier and never resolves a registry entry.

## Security and portability

The core module has no filesystem, environment, subprocess, shell, network,
LLM, repository-discovery, or remote-registry dependency. Task text is inert
data. It uses fixed ordering and Phase 4A's explicit Unicode behavior, so
line endings, locale, platform, and input mapping order cannot change the
decision. Canonical JSON remains an existing caller concern; no CLI surface
is introduced in Phase 5A.

## Deferred work

L3 harness execution, L4 validation loops, L5 graph/state orchestration,
human approval UX, task-to-repository context binding, workflow integration,
and CLI exposure require separate phases and contracts. They must not be
implemented by extending this deep module opportunistically.

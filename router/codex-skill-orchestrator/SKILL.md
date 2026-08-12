---
name: codex-skill-orchestrator
description: Select a lightweight work profile and route a task to only the relevant installed skills. Use when the user invokes $codex-skill-orchestrator, asks to switch Universal, Economy, Deep Reasoning, project-size, Research, Security, or Custom profiles, or wants to reduce unnecessary skill loading.
---

# Codex Skill Orchestrator

Route with the active profile without loading every available skill.

## Workflow

1. Read `references/active-profile.json`. If it is absent, use the Universal policy summarized in `references/profiles.md`.
2. Match the task against the profile's routes. Prefer explicit user intent over keyword matches.
3. Select no more than `policy.max_active_routes` routes, ordered by priority and relevance.
4. Treat each route's `capability_hints` as suggestions. Use only matching skills that the current runtime actually exposes.
5. Invoke or read only the selected skills. Do not inspect unrelated skill bodies.
6. If no route matches, handle the task normally with the host's built-in capabilities.

## Guardrails

- Never install, download, enable, or trust a skill merely because a profile mentions a capability.
- Never claim a hinted skill is installed without checking the runtime-provided skill list.
- Do not change a profile implicitly. Tell the user the exact `activate` command and wait for authorization when a persistent change is requested.
- Respect the active profile's reasoning hint as a preference, not permission to override host policy.
- Keep secrets, credentials, personal paths, and private project data out of routing output.

Read `references/profiles.md` only when explaining profiles or when the generated active profile file is missing or invalid.

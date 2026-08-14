# Project Handoff

> Record only version-safe project state. Keep credentials, secret values, private paths, chat history, runtime, cache, and private backups machine-local.

## Project

- Name: Codex Skill Orchestrator
- Repository root: `<PROJECT_ROOT>`

## Purpose

- Provide reviewed skill definitions and routing profiles through a cross-platform installer while keeping installed Codex Skills and runtime state machine-local.

## Current Status

- Phase: v0.1.0 release preparation
- Status: Phase 3A–3H are complete. PR #1 merged Phase 3 to `main`, and post-merge validation passed on macOS after the cross-platform release candidate passed macOS and Windows gates.
- Release state: Release documentation is in preparation. The `v0.1.0` tag and GitHub Release do not yet exist, and Phase 4 has not started.

## Repository

- Remote: `https://github.com/soon97436/codex-skill-orchestrator.git`
- Source of truth: GitHub repository
- Main: Phase 3 Main LKG `9eb31ebca0aece5150367ec47c37287925fab2f9`

## Current Branch

- `release/v0.1.0`

## Phase 3 Main LKG

- Commit: `9eb31ebca0aece5150367ec47c37287925fab2f9`
- Tree: `82092a5c69d985fafbfd7248a7763f04ebfb236d`
- Scope: Phase 3A–3H integrated through PR #1; 63 tracked files and 146 tests passed on merged `main`.

## Completed Work

- Phase 1 provides bundled first-party routing, profiles, registry validation, checksums, dry-run, audit, activation, and rollback.
- Windows and POSIX entry points use the same Python runtime and standard-library implementation.
- Canonical LF policy and release-integrity validation pass macOS working-tree and fresh-checkout validation.
- Phase 3A provides bounded, metadata-only context discovery with fail-closed link and reparse handling.
- Phase 3B models root, path-scoped, and unknown scope states and reports deterministic structural overlap and conflict evidence.
- Phase 3C adds registry-bounded scoped recommendations with structured reasons and explicit completeness.
- Phase 3D adds deterministic recommendation explanations with registry-bounded evidence references and explicit limitations.
- Phase 3E adds declarative capability analysis without runtime enforcement.
- Phase 3F closes the integrated Phase 3 contract across recommendation, explanation, capability, privacy, trust, and determinism invariants.
- Phase 3G passed the complete macOS and Windows release gates after stale HANDOFF state was corrected and both platforms revalidated the final candidate.
- Phase 3H merged Phase 3 to `main` through PR #1 with a normal merge commit and completed post-merge validation.

## Pending Work

- Complete the v0.1.0 release-documentation PR.
- Revalidate `main` after that PR is merged.
- Tag the verified release commit as `v0.1.0` and create the GitHub Release in a separate checkpoint.
- Consider Phase 4 only after the release is complete.

## Architecture Decisions

- GitHub stores reviewed source, profiles, registry, checksums, public configuration, and documentation.
- Installers materialize the selected router beneath machine-local `<CODEX_HOME>/skills`.
- Checksummed text payloads use Git-canonical LF bytes on every platform.
- Registry, security index, and payload checksums fail closed when any value differs.
- Background synchronization is fetch-first and never publishes local work.
- Scope overlap is metadata, not proof of semantic contradiction.
- Phase 3B conflicts are limited to normalized-path collisions and duplicate source registrations.
- Recommendation candidates are deterministic ID mappings filtered through the validated registry; registry expansion remains a separate security decision.
- Unknown or conflicted context does not contribute trusted scoped recommendation evidence.
- Recommendation explanations and capability findings remain deterministic, metadata-only, and registry-bounded.
- Capability declarations are audit metadata only; enforcement status remains `not-implemented`.
- Recommendation completeness, explanation completeness, and capability-analysis completeness remain distinct.

## Required Tools

| Tool | Constraint | Verification command |
| --- | --- | --- |
| Git | Current supported version | `git --version` |
| Python | 3.9 or newer | `python --version` |
| PowerShell | 5.1+ on Windows | `$PSVersionTable.PSVersion` |
| POSIX shell | macOS/Linux | `command -v sh` |

## Environment Variable Names

Only names are versioned; values remain local.

| Name | Purpose | Required |
| --- | --- | --- |
| `CODEX_HOME` | Optional Codex home override | No |
| `CSO_HOME` | Optional orchestrator state-root override | No |
| `LOCALAPPDATA` | Default Windows state-root base | Windows default |
| `XDG_STATE_HOME` | Optional POSIX state-root base | No |
| `PYTHONDONTWRITEBYTECODE` | Prevent source-checkout bytecode state | No |

## Windows Notes

- Use `<PROJECT_ROOT>`, `<USER_HOME>`, and `<CODEX_HOME>` placeholders in versioned documentation.
- Run `scripts/smoke.ps1`; it shares Python discovery with the installer.
- Confirm checksummed payloads remain LF even when `core.autocrlf=true`.

## macOS Notes

- Use `<PROJECT_ROOT>`, `<USER_HOME>`, and `<CODEX_HOME>` placeholders in versioned documentation.
- Run `scripts/smoke.sh` with an isolated temporary install and skills root.
- Do not copy installed Windows Skills, junctions, runtime state, or credentials.

## Validation Commands

```text
python -m unittest discover -s tests -v
python scripts/release_audit.py
git diff --check
git check-attr -a -- router/codex-skill-orchestrator/SKILL.md router/codex-skill-orchestrator/agents/openai.yaml router/codex-skill-orchestrator/references/profiles.md
sh scripts/smoke.sh
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/smoke.ps1
```

## Tests

- macOS: Phase 3 Main LKG passed 146 unit tests, POSIX smoke, release audit, Python 3.9 grammar, diff checks, canonical JSON, privacy, and integrity gates.
- Windows: The Phase 3 release candidate with tree `82092a5c69d985fafbfd7248a7763f04ebfb236d` passed the Windows unit, smoke, release-audit, portability, canonical JSON, privacy, and integrity gates before merge.

## Known Issues

- Linux has not yet passed a formal release gate.
- Capability analysis remains declarative-only; runtime sandbox enforcement is not implemented.

## Security Notes

- Secret values, credentials, tokens, cookies, private keys, chat history, runtime, cache, private config, and private backups remain machine-local.
- Release audit must fail on profile, registry, manifest, payload, unsafe-path, or secret findings.
- Credentials copied or committed: `NO`

## Next Recommended Action

1. Complete and merge the v0.1.0 release-documentation PR.
2. Validate the merged release-preparation `main`.
3. Tag the verified release commit as `v0.1.0` and create the GitHub Release.
4. Consider Phase 4 only after the release is complete.

## Last Handoff

- Date: 2026-08-14
- From platform: macOS
- To platform: v0.1.0 release-documentation review
- Prepared by: Codex
- Phase 3 Main LKG commit: `9eb31ebca0aece5150367ec47c37287925fab2f9`
- Phase 3 Main LKG tree: `82092a5c69d985fafbfd7248a7763f04ebfb236d`
- Durable state: Phase 3 is merged and validated; release documentation is in preparation.
- Sync: `main` was `0 behind / 0 ahead` before the release branch was created.
- Worktree: Release-documentation changes are expected only on `release/v0.1.0`.

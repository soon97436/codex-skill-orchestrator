# Project Handoff

> Record only version-safe project state. Keep credentials, secret values, private paths, chat history, runtime, cache, and private backups machine-local.

## Project

- Name: Codex Skill Orchestrator
- Repository root: `<PROJECT_ROOT>`

## Purpose

- Provide reviewed skill definitions and routing profiles through a cross-platform installer while keeping installed Codex Skills and runtime state machine-local.

## Current Status

- Phase: Phase 3G — final cross-platform release gate remediation
- Status: Phase 3A–3F implementation and integration are complete, and the Phase 3F checkpoint passed macOS and Windows validation. Phase 3G found this stale handoff as a documentation defect; the current change is the minimal remediation candidate.
- Blocker: The documentation-only remediation checkpoint must pass the final Phase 3G gate on macOS and Windows. Phase 3H merge is not authorized.

## Repository

- Remote: `https://github.com/soon97436/codex-skill-orchestrator.git`
- Source of truth: GitHub repository
- Main: `8442408744915dc16806be4bf1c9d5c22eecf7ff`

## Current Branch

- `phase3/context-skill-intelligence`

## Last Known Good Commit

- Commit: `cbdc20ccbbb040b093633db0330b015936c6ab64`
- Tree: `a5206a2c6581f14da314d75350ae5c57bd1bf8b7`
- Scope: Phase 3F cross-platform checkpoint; 63 tracked files and 146 tests passed on macOS and Windows.
- The documentation-remediation commit that follows this LKG still requires Phase 3G validation on both platforms.

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

## Pending Work

- Validate this documentation-only remediation and create one documentation checkpoint.
- Rerun the complete Phase 3G final release gate on macOS and Windows from that same commit.
- Proceed to Phase 3H only after both platforms pass; main remains unchanged until then.

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

- macOS: Phase 3F commit `cbdc20ccbbb040b093633db0330b015936c6ab64` passed 146 unit tests, POSIX smoke, release audit, Python 3.9 grammar, diff checks, canonical JSON, privacy, and integrity gates.
- Windows: The same Phase 3F commit and tree passed the Windows unit, smoke, release-audit, portability, canonical JSON, privacy, and integrity gates.

## Known Issues

- Phase 3G remains blocked only by validation of the documentation-remediation checkpoint. The Phase 3F implementation checkpoint is cross-platform verified.

## Security Notes

- Secret values, credentials, tokens, cookies, private keys, chat history, runtime, cache, private config, and private backups remain machine-local.
- Release audit must fail on profile, registry, manifest, payload, unsafe-path, or secret findings.
- Credentials copied or committed: `NO`

## Next Recommended Action

1. Validate the documentation-only remediation.
2. Create one documentation checkpoint.
3. Push it non-force to `phase3/context-skill-intelligence`.
4. Rerun the Phase 3G final release gate on macOS and Windows.
5. Proceed to Phase 3H only if both platforms pass.

## Last Handoff

- Date: 2026-08-13
- From platform: macOS
- To platform: macOS and Windows Phase 3G validation
- Prepared by: Codex
- Verified Phase 3F commit: `cbdc20ccbbb040b093633db0330b015936c6ab64`
- Verified Phase 3F tree: `a5206a2c6581f14da314d75350ae5c57bd1bf8b7`
- Documentation remediation: `Pending checkpoint creation and Phase 3G validation`
- Sync: `0 behind / 0 ahead at the Phase 3F LKG`
- Worktree: `Documentation-only remediation candidate`

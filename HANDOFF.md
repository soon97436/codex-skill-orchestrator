# Project Handoff

> Record only version-safe project state. Keep credentials, secret values, private paths, chat history, runtime, cache, and private backups machine-local.

## Project

- Name: Codex Skill Orchestrator
- Repository root: `<PROJECT_ROOT>`

## Purpose

- Provide reviewed skill definitions and routing profiles through a cross-platform installer while keeping installed Codex Skills and runtime state machine-local.

## Current Status

- Phase: Phase 3B — deterministic context scope conflicts
- Status: Checkpoint B passed macOS validation and is ready for a checkpoint commit
- Blocker: The new Checkpoint B commit requires Windows validation after the macOS checkpoint is pushed.

## Repository

- Remote: `https://github.com/soon97436/codex-skill-orchestrator.git`
- Source of truth: GitHub repository

## Current Branch

- `phase3/context-skill-intelligence`

## Last Known Good Commit

- `8bd07be73515c34846e3b5f683eec09a4a7e4b2b` is the current Mac/Windows verified Phase 3A checkpoint.

## Completed Work

- Phase 1 provides bundled first-party routing, profiles, registry validation, checksums, dry-run, audit, activation, and rollback.
- Windows and POSIX entry points use the same Python runtime and standard-library implementation.
- Canonical LF policy and release-integrity validation pass macOS working-tree and fresh-checkout validation.
- Phase 3A provides bounded, metadata-only context discovery with fail-closed link and reparse handling.
- Phase 3B models root, path-scoped, and unknown scope states and reports deterministic structural overlap and conflict evidence.

## Pending Work

- Validate the Phase 3B checkpoint on Windows without changing the source checkout.
- Promote the new commit to Last Known Good only after both platforms pass the same Git commit.

## Architecture Decisions

- GitHub stores reviewed source, profiles, registry, checksums, public configuration, and documentation.
- Installers materialize the selected router beneath machine-local `<CODEX_HOME>/skills`.
- Checksummed text payloads use Git-canonical LF bytes on every platform.
- Registry, security index, and payload checksums fail closed when any value differs.
- Background synchronization is fetch-first and never publishes local work.
- Scope overlap is metadata, not proof of semantic contradiction.
- Phase 3B conflicts are limited to normalized-path collisions and duplicate source registrations.

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

- macOS: Phase 3B passed 91 unit tests, POSIX smoke, release audit, Python 3.9 grammar, diff checks, and integrity checks.
- Windows: Phase 3A commit `8bd07be73515c34846e3b5f683eec09a4a7e4b2b` passed; Phase 3B is pending.

## Known Issues

- Phase 3B cross-device readiness remains pending until Windows validates the new checkpoint revision.

## Security Notes

- Secret values, credentials, tokens, cookies, private keys, chat history, runtime, cache, private config, and private backups remain machine-local.
- Release audit must fail on profile, registry, manifest, payload, unsafe-path, or secret findings.
- Credentials copied or committed: `NO`

## Next Recommended Action

1. Validate the exact Phase 3B checkpoint commit on Windows after it is pushed.

## Last Handoff

- Date: 2026-08-13
- From platform: macOS
- To platform: Windows
- Prepared by: Codex
- Local SHA: `8bd07be73515c34846e3b5f683eec09a4a7e4b2b`
- Remote SHA: `8bd07be73515c34846e3b5f683eec09a4a7e4b2b`
- Sync: `0 behind / 0 ahead`
- Worktree: `CLEAN at the last cross-platform checkpoint`

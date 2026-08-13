# Project Handoff

> Record only version-safe project state. Keep credentials, secret values, private paths, chat history, runtime, cache, and private backups machine-local.

## Project

- Name: Codex Skill Orchestrator
- Repository root: `<PROJECT_ROOT>`

## Purpose

- Provide reviewed skill definitions and routing profiles through a cross-platform installer while keeping installed Codex Skills and runtime state machine-local.

## Current Status

- Phase: Cross-device adoption
- Status: Cross-device adoption under validation
- Blocker: Windows unit, smoke, release-audit, checksum, and path validation are still required.

## Repository

- Remote: `https://github.com/soon97436/codex-skill-orchestrator.git`
- Source of truth: GitHub repository

## Current Branch

- `main`

## Last Known Good Commit

- Not established yet.
- Commit `fab537209b1d9b21ced05e9456070baa94e56813` predates the cross-platform checksum fix and must not be treated as known-good.

## Completed Work

- Phase 1 provides bundled first-party routing, profiles, registry validation, checksums, dry-run, audit, activation, and rollback.
- Windows and POSIX entry points use the same Python runtime and standard-library implementation.
- Canonical LF policy and release-integrity validation pass macOS working-tree and fresh-checkout validation.

## Pending Work

- Run the listed Windows validation commands without changing the source checkout.
- Establish a Last Known Good Commit only after both platforms pass from the same Git commit.

## Architecture Decisions

- GitHub stores reviewed source, profiles, registry, checksums, public configuration, and documentation.
- Installers materialize the selected router beneath machine-local `<CODEX_HOME>/skills`.
- Checksummed text payloads use Git-canonical LF bytes on every platform.
- Registry, security index, and payload checksums fail closed when any value differs.
- Background synchronization is fetch-first and never publishes local work.

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

- macOS: 36 unit tests, POSIX smoke, release audit, checksum integrity, and fresh checkout passed on 2026-08-13.
- Windows: not run for the adoption diff.

## Known Issues

- Cross-device readiness remains blocked until Windows validates the same source revision.

## Security Notes

- Secret values, credentials, tokens, cookies, private keys, chat history, runtime, cache, private config, and private backups remain machine-local.
- Release audit must fail on profile, registry, manifest, payload, unsafe-path, or secret findings.
- Credentials copied or committed: `NO`

## Next Recommended Action

1. Validate the identical diff on Windows before committing or pushing.

## Last Handoff

- Date: 2026-08-13
- From platform: macOS
- To platform: Windows
- Prepared by: Codex
- Local SHA: `fab537209b1d9b21ced05e9456070baa94e56813`
- Remote SHA: `fab537209b1d9b21ced05e9456070baa94e56813`
- Sync: `0 behind / 0 ahead`
- Worktree: `DIRTY — cross-device adoption changes are intentionally uncommitted`

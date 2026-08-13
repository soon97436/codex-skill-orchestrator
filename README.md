# Codex Skill Orchestrator

Codex Skill Orchestrator is a small, cross-platform profile manager for Codex and compatible AI-agent skills. Install one lightweight router, then switch profiles instead of copying every workflow into one oversized `SKILL.md` or manually toggling skills one by one.

Phase 1 is intentionally local-only and fail-closed:

- installs the first-party router bundled in this repository;
- never downloads or executes third-party code;
- uses only the Python standard library;
- supports deterministic plans, zero-write dry runs, integrity audits, and conflict-safe rollback;
- records source, version, license, and SHA-256 metadata for every bundled file;
- keeps capability hints separate from installed third-party skills.

## Profiles

| Profile | Purpose |
|---|---|
| Universal | Balanced routing for everyday development work. |
| Economy / Save Usage | Minimize active routes and prefer explicit skill invocation. |
| Deep Reasoning | Allow a wider set of analysis and verification routes. |
| Small Project | Keep routing focused for compact repositories. |
| Large Project | Add architecture, dependency, and staged-review routes. |
| Research | Prioritize primary sources, synthesis, and citation checks. |
| Security | Prioritize threat modeling, dependency audit, and remediation verification. |
| Custom | Extend Universal with a safe user-defined routing slot. Phase 1 provides the slot but no profile editor. |

Profiles contain routing policy and generic capability hints. A hint never installs code and never grants a tool permission. The router may select only skills already exposed by the host runtime.

## Requirements

- Python 3.9 or newer
- Windows PowerShell 5.1+ or PowerShell 7 for the Windows installer
- A POSIX-compatible `sh` for macOS/Linux

No package manager, administrator privileges, API key, or network connection is required.

## Quick start

Clone the repository, inspect the plan, then install.

### Windows PowerShell

```powershell
Set-Location codex-skill-orchestrator
.\installer\install.ps1 -Action Install -Profile Universal -DryRun
.\installer\install.ps1 -Action Install -Profile Universal
```

### macOS/Linux

```sh
cd codex-skill-orchestrator
./installer/install.sh install --profile universal --dry-run
./installer/install.sh install --profile universal
```

The default managed state directory is `%LOCALAPPDATA%\codex-skill-orchestrator` on Windows and `${XDG_STATE_HOME:-$HOME/.local/state}/codex-skill-orchestrator` on macOS/Linux. The router skill is installed under `${CODEX_HOME:-$HOME/.codex}/skills`.

The installer does not modify `PATH`, shell profiles, PowerShell execution policy, global `AGENTS.md`, or existing unrelated skills.

## Cross-device workflow

Use [HANDOFF.md](HANDOFF.md) for current project state and [docs/CROSS_DEVICE_WORKFLOW.md](docs/CROSS_DEVICE_WORKFLOW.md) for the Windows/macOS fetch-first workflow and shared-versus-local data boundary. GitHub carries reviewed source, profiles, registry, checksums, public configuration, and documentation; installed Skills, credentials, runtime, cache, private configuration, chat history, and private backups remain machine-local.

The implementation security model is documented in [SECURITY.md](SECURITY.md) and [security/policy.md](security/policy.md).

## Switch profiles

From the source checkout:

```powershell
.\installer\install.ps1 -Action Activate -Profile Economy
```

```sh
./installer/install.sh activate --profile economy
```

The installed launchers are also available without changing `PATH`:

```powershell
& (Join-Path $env:LOCALAPPDATA 'codex-skill-orchestrator\app\bin\cso.ps1') activate --profile research
```

```sh
"${XDG_STATE_HOME:-$HOME/.local/state}/codex-skill-orchestrator/app/bin/cso" activate --profile research
```

## Commands

```text
profiles                         List validated profiles
plan      --profile PROFILE      Print the deterministic installation plan
install   --profile PROFILE      Install the app and selected router profile
activate  --profile PROFILE      Switch the installed router profile
route     --profile PROFILE      Preview deterministic task routing
audit                            Verify project metadata and installed checksums
rollback                         Restore the previous managed transaction
```

Mutating commands accept `--dry-run`. `install`, `activate`, `audit`, and `rollback` accept `--install-root` and `--skills-dir` for isolated testing or non-default layouts. Run `python -m skill_orchestrator --help` for the complete interface.

## Safety model

1. Validate every profile, registry entry, license identifier, allowlist rule, version, and checksum before writing.
2. Reject network sources in Phase 1. The registry schema reserves immutable revision and artifact checksum fields for a future fetcher.
3. Reject source/destination overlap, path traversal, symlinks, and Windows reparse points in managed payloads.
4. Stage complete components and verify their SHA-256 manifests before atomic replacement.
5. Serialize mutations with an operating-system file lock and journal only root-relative paths.
6. Back up overwritten components before activation; interrupted installs and rollbacks resume from durable transaction state.
7. Refuse rollback if the installed files changed after the recorded transaction.
8. Never remove files outside the transaction manifest.

See [SECURITY.md](SECURITY.md) for vulnerability reporting and [THIRD_PARTY.md](THIRD_PARTY.md) for dependency and license policy.

## Repository layout

```text
installer/             PowerShell and POSIX entry points plus installed launchers
router/                The lightweight first-party Codex skill
profiles/              Versioned routing profiles and schema
registry/              Skill provenance registry and schema
security/              Allowlist policy and checked-in checksums
skill_orchestrator/    Python standard-library engine and CLI
scripts/               Smoke and release-audit helpers
tests/                 Unit and integration tests
```

## Development

```sh
python -m unittest discover -s tests -v
python -m skill_orchestrator audit --json
```

On Windows, run `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/smoke.ps1`. On macOS/Linux, run `sh scripts/smoke.sh`.

## Phase 1 boundaries

- No remote registry fetcher or archive extractor is included.
- No third-party skill code is bundled.
- Custom is a checked-in example slot in Phase 1; users edit a reviewed checkout rather than using a profile-editor command.
- Profile capability hints are advisory; the host runtime remains responsible for skill discovery and permission enforcement.
- Installation is user-scoped and does not modify host-wide Codex or Claude configuration.

## License

Released under the [MIT License](LICENSE).

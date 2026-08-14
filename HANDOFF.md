# Project Handoff

> Record only version-safe project state. Keep credentials, secret values, private paths, chat history, runtime, cache, and private backups machine-local.

## Project

- Name: Codex Skill Orchestrator
- Repository root: `<PROJECT_ROOT>`

## Purpose

- Provide reviewed skill definitions and routing profiles through a cross-platform installer while keeping installed Codex Skills and runtime state machine-local.

## Validated Integration State

- Phase 3A–3H were integrated through PR #1 and validated at commit `9eb31ebca0aece5150367ec47c37287925fab2f9` with tree `82092a5c69d985fafbfd7248a7763f04ebfb236d`.
- The v0.1.0 release documentation was integrated through PR #2 at commit `a19c2c724914fa9b838000b119a5822933c80034` with tree `f136b69bbe64be67fc4fe70eee12667823d9317d`.
- Git tags and GitHub Releases are authoritative for published release state. This handoff records validated integration history and the release procedure; it does not mirror a live branch, tag, or release state.

## Repository

- Remote: `https://github.com/soon97436/codex-skill-orchestrator.git`
- Source of truth: GitHub repository
- Published release authority: Git tags and GitHub Releases
- Handoff authority: validated integration history and reusable release procedure

## Immutable Integration History

- Phase 3 Main LKG before the release-documentation merge:
  - Commit: `9eb31ebca0aece5150367ec47c37287925fab2f9`
  - Tree: `82092a5c69d985fafbfd7248a7763f04ebfb236d`
  - Integration: PR #1 merged Phase 3A–3H; 63 tracked files and 146 tests passed on merged `main`.
- v0.1.0 release-documentation merge result:
  - Commit: `a19c2c724914fa9b838000b119a5822933c80034`
  - Tree: `f136b69bbe64be67fc4fe70eee12667823d9317d`
  - Integration: PR #2 merged the reviewed v0.1.0 release documentation.

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
- PR #2 merged the reviewed v0.1.0 release documentation to `main` with a normal merge commit.

## Release Procedure

1. Validate the exact candidate commit and tree on `main`.
2. Confirm that release documentation is internally consistent with that candidate.
3. Run the complete cross-platform and release gate.
4. Create a release tag only for the verified commit.
5. Verify that the tag resolves to the intended commit and tree.
6. Create the GitHub Release from that verified tag.

After publication, use the Git tag and GitHub Release metadata as the authority for published state. Start future development from the latest validated `main` rather than from a release branch or a recorded worktree state.

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

- The recorded v0.1.0 validation evidence does not include a formal Linux release gate.
- In the recorded v0.1.0 architecture, capability analysis is declarative-only; runtime sandbox enforcement is not implemented.

## Security Notes

- Secret values, credentials, tokens, cookies, private keys, chat history, runtime, cache, private config, and private backups remain machine-local.
- Release audit must fail on profile, registry, manifest, payload, unsafe-path, or secret findings.
- Credentials copied or committed: `NO`

## Last Handoff

- Date: 2026-08-14
- From platform: macOS
- Prepared by: Codex
- Phase 3 Main LKG commit: `9eb31ebca0aece5150367ec47c37287925fab2f9`
- Phase 3 Main LKG tree: `82092a5c69d985fafbfd7248a7763f04ebfb236d`
- Release-documentation merge commit: `a19c2c724914fa9b838000b119a5822933c80034`
- Release-documentation merge tree: `f136b69bbe64be67fc4fe70eee12667823d9317d`
- Durable state: Phase 3 and the v0.1.0 release documentation were integrated through reviewed merge commits and are ready for exact-commit release validation.
- Publication state: Determine it from Git tags and GitHub Releases, not from this document.
- Continuation rule: Start future work from the latest validated `main` and apply the release procedure above when publishing a release.

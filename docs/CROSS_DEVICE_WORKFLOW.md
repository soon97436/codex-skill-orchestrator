# Cross-Device Workflow

## Source and installation flow

```text
GitHub repository
  → shared router / profiles / registry / checksums
  → Windows or POSIX installer
  → machine-local <CODEX_HOME>/skills
```

GitHub is the source of truth for reviewed, versioned content. Each machine performs its own dry-run, install, audit, and profile activation. Installed Skills are outputs, not synchronization sources.

## Data boundary

Shared:

- skill source and public metadata
- profiles and routing policy
- registry, provenance, licenses, and immutable revisions
- checksums, allowlists, tests, public configuration, and documentation

Machine-local:

- credentials, API keys, tokens, cookies, and private keys
- runtime, cache, logs, staging, and temporary downloads
- private configuration and trusted-project paths
- Codex or Claude authentication, chat history, sessions, and browser state
- private backups and installed Skills beneath `<CODEX_HOME>`

Versioned paths use `<PROJECT_ROOT>`, `<USER_HOME>`, and `<CODEX_HOME>` rather than private absolute paths.

## Start work: fetch-first

From `<PROJECT_ROOT>`:

```text
git status --short --branch
git fetch origin
git branch --show-current
git rev-list --left-right --count origin/<CURRENT_BRANCH>...<CURRENT_BRANCH>
```

- Clean and synchronized: continue.
- Clean and only remote-ahead: `git pull --ff-only origin <CURRENT_BRANCH>`, then verify again.
- Dirty, local-ahead, diverged, detached, conflicted, or failed fetch: stop for human review.

Background synchronization may fetch, inspect status, and perform the clean remote-ahead fast-forward above. It must not add, commit, push, force-push, stash, reset, clean, merge, rebase, switch branches, run installers, or modify handoff state.

## Validate and install

1. Read `README.md`, `HANDOFF.md`, and security guidance.
2. Run unit tests, the platform smoke test, release audit, and checksum validation.
3. Review the installer dry-run using machine-local `<CODEX_HOME>` and state roots.
4. Install only after every validation passes.
5. Audit the machine-local result; do not commit installed output or local state.

OS schedulers and the soon-development-platform synchronization scripts are outside this repository and are not copied here.

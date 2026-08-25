# CI Automation

## Purpose

Codex Skill Orchestrator uses a small GitHub-hosted validation workflow to
run the existing Python 3.9, test, release-audit, and native smoke gates on
Ubuntu, macOS, and Windows. Pull requests targeting `main` and pushes to
`main` use the same read-only validation contract; `workflow_dispatch` is
available for an explicit manual run.

The workflow is infrastructure only. It does not change CSO runtime behavior,
install skills, or grant the registry remote-fetch capability.

## Architecture

```text
PR / main push
      |
      v
GitHub Actions (read-only)
      |
      +--> Ubuntu / Python 3.9
      +--> macOS / Python 3.9
      +--> Windows / Python 3.9
                    |
                    +--> tracked-file Python 3.9 grammar
                    +--> Phase 5A focused tests
                    +--> full unittest suite
                    +--> release audit
                    +--> native smoke
```

One matrix job runs on GitHub-hosted `*-latest` runners with Python 3.9 and
a twenty-minute job limit. The workflow does not use self-hosted runners,
dependency installation, caches, artifact uploads, or external service calls.
Ubuntu and macOS run `scripts/smoke.sh`; Windows runs the native
`scripts/smoke.ps1` through PowerShell. Windows validation therefore does not
use WSL or Git Bash as its authority.

The workflow compiles every tracked UTF-8 Python file with the active Python
3.9 interpreter. It reports the runner OS and Python version without dumping
the environment or the full GitHub context.

## Supply chain and permissions

Only GitHub-maintained actions are used, and both executable references are
pinned to immutable commit SHAs:

- `actions/checkout` v4: `11d5960a326750d5838078e36cf38b85af677262`
- `actions/setup-python` v5: `a26af69be951a213d495a4c3e4e4022e16d87065`

The workflow has only `contents: read` permission. It does not use
`pull_request_target`, write permissions, explicit workflow tokens, or
secrets. No repository settings, branch protection, or required-status-check
configuration is changed by this bootstrap. Merge remains a separate human
approval gate.

Official checkout and Python setup traffic is CI infrastructure traffic. It
does not enable CSO registry remote fetching or any other CSO runtime network
behavior. The workflow does not install third-party project dependencies.

## Boundaries and future work

GitHub-hosted Windows validation replaces ordinary physical-Windows
portability validation for this repository. A physical Windows machine is
still useful for hardware- or local-environment-specific checks, but it is
not required for ordinary CI validation.

The durable Phase 5A Canonical Probe v1R remains separate evidence and is not
claimed as an in-repository CI canonical gate. Phase 5E may add a stricter
deterministic cross-platform canonical evidence contract.

The `ubuntu-latest`, `macos-latest`, and `windows-latest` images are moving
hosted environments. They are portability signals, not immutable canonical
machines. The CI workflow does not make a release, deploy anything, or enable
remote registry resolution.

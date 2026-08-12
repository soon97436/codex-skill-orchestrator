# Contributing

Thank you for helping keep skill orchestration small, portable, and auditable.

## Ground rules

- Keep the router concise. Put profile-specific details in profile files or one-level references.
- Use Python's standard library for Phase 1 runtime code.
- Do not commit secrets, personal data, absolute private paths, runtime state, downloaded archives, or generated backups.
- Do not add third-party skill code without provenance, an immutable version pin, a compatible SPDX license, and verified checksums.
- Preserve zero-write behavior for every dry run.
- Make rollback touch only files owned by its transaction manifest.

## Development workflow

1. Create a focused branch.
2. Run `python -m unittest discover -s tests -v`.
3. Run `python -m skill_orchestrator audit --json`.
4. Run the platform smoke script when changing an installer.
5. Run `python scripts/release_audit.py` before opening a pull request.

Changes to schemas, security policy, installers, or transaction handling should include positive and fail-closed tests. Pull requests should explain compatibility and rollback impact.

By contributing, you agree that your contribution is licensed under this repository's MIT License.

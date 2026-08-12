# Security Policy

## Supported versions

Security fixes are provided for the latest released minor version. Phase 1 is pre-1.0 and may change interfaces while preserving safe rollback behavior.

## Report a vulnerability

Please use the repository's private GitHub Security Advisory workflow. Do not open a public issue containing an exploit, credential, private path, or personal data.

Include the affected version or commit, operating system and Python version, a minimal reproduction using placeholder data, expected and observed behavior, impact, and any known workaround.

Do not include real secrets. Maintainers will acknowledge a valid report, coordinate remediation, and publish credit if the reporter requests it.

## Security boundaries

- Phase 1 performs no network downloads.
- Installers run at user scope and do not request elevation.
- Dry runs must produce no persistent writes.
- Registry metadata is fail-closed: unknown licenses, missing hashes, mutable revisions, and non-allowlisted sources are rejected.
- Rollback refuses to overwrite files that no longer match the transaction manifest.
- Audit is read-only and never repairs findings automatically.

See `security/policy.md` for the implementation-level supply-chain policy.

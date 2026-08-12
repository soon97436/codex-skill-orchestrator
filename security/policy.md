# Supply-chain policy

Phase 1 accepts only checked-in, first-party `bundled` sources. Network schemes, redirects, archives, subprocess installers, and post-install hooks are unsupported and denied.

Validation order:

1. Parse strict JSON and reject unknown schema fields.
2. Validate conservative identifiers and relative POSIX paths.
3. Validate version, license, provenance, and allowlist policy.
4. Reject symlinks, hard-link-like aliases, Windows reparse points, and source/destination overlap.
5. Verify checked-in SHA-256 values before staging.
6. Stage a complete component beside its destination and verify the staged manifest.
7. Back up an existing managed component.
8. Replace atomically and commit the root-relative transaction journal.

Rollback verifies the current installed manifest before changing anything. A mismatch is a conflict, not permission to overwrite user changes.

Future network support must additionally enforce per-hop HTTPS allowlists, immutable commit pins, download and extraction limits, archive-member containment, and pre-extraction artifact verification.

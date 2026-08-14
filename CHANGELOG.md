# Changelog

All notable changes to this project will be documented in this file.

## [0.1.0] - 2026-08-14

### Added

- Deterministic repository analysis.
- Local project initialization and health checks.
- Context discovery for supported agent instruction formats.
- Structural context scope analysis.
- Deterministic conflict detection.
- Registry-bounded scoped skill recommendations.
- Recommendation explainability.
- Declarative capability analysis.
- Cross-platform macOS and Windows validation.

### Security

- Fail-closed handling for unsafe context paths.
- Normalized path-collision detection.
- Windows Junction and reparse-point protection.
- Registry trust boundary for skills.
- Deterministic capability declaration validation.
- No runtime enforcement claims.

### Compatibility

- Python 3.9-compatible syntax.
- Deterministic UTF-8 and LF machine JSON.
- macOS validated.
- Windows validated.

### Known limitations

- Linux has not yet passed a formal release gate.
- No runtime sandbox enforcement.
- No remote skill registry or downloads.
- No `cso.lock` or `cso sync`.
- No LLM-based routing.
- Capability analysis is declarative-only.

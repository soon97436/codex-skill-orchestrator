# Third-party provenance and licensing

Phase 1 bundles no third-party skills, libraries, binaries, or copied documentation. Runtime code uses only the Python standard library.

Profile `capability_hints` are generic routing labels. They do not identify a dependency, fetch software, or imply redistribution rights.

A future third-party registry entry must include a canonical HTTPS source URL and allowlisted host, a human-readable version plus immutable 40-character commit revision, SHA-256 for the exact reviewed artifact and every installed file, SPDX license identifier and canonical license URL, publisher and maintainer provenance, explicit redistribution status, and an allowlisted installation subdirectory and file manifest.

Unknown, custom, source-available, copyleft, or missing licenses require explicit maintainer review and remain denied by default. Metadata alone never grants permission to redistribute third-party code.

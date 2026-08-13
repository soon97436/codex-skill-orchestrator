# CSO Quickstart

CSO analyzes local project metadata, recommends a compatible profile, and can write one declarative project configuration. It works offline and does not execute project scripts.

Commands below assume `cso` is an installed launcher. From a source checkout, substitute `./installer/install.sh` on macOS/Linux or `.\installer\cso.ps1` on Windows.

## Analyze a project

Run from the project or pass its root explicitly:

```sh
cso analyze
cso analyze --project-root <PROJECT_ROOT>
cso analyze --json
```

Analysis is read-only. It reports detected technologies, bounded file count, project-size heuristic, known agent-context evidence, profile recommendation, and skills that exist in the validated registry. Context evidence contains only repository-relative paths, kind, conservative scope, and an explicit `root`, `path-scoped`, or `unknown` scope state. CSO reads bounded bytes only to validate size and UTF-8 encoding; it never emits or interprets instruction content. A truncation warning means the result is incomplete.

Context discovery recognizes `AGENTS.md`, `CLAUDE.md`, `.cursorrules`, direct `.cursor/rules/*.md` files, and `.github/copilot-instructions.md`. It emits at most 256 evidence records, scans at most 50,000 filesystem entries, and skips context files larger than 256,000 bytes. Cursor rule scope remains `unknown` when the path alone does not define it.

The JSON document includes deterministic `scope_overlaps`, `conflicts`, and `conflict_analysis_complete` fields. Scope overlap records describe only same-scope or ancestor/descendant path relationships. Conflict records are limited to normalized-path collisions and duplicate source registrations, use stable IDs, and contain no instruction contents or machine identity. When discovery is incomplete, conflict analysis is also explicitly incomplete.

Completion criterion: the command exits `0`, reports that no project files were modified, and does not create `.cso/`.

## Initialize CSO

Review the analysis and accept the prompt:

```sh
cso init
```

For non-interactive environments:

```sh
cso init --yes
```

Initialization writes only `.cso/config.json`. If it already exists, CSO refuses to replace it unless you explicitly run:

```sh
cso init --yes --force
```

`--force` replaces only `config.json`; other files beneath `.cso/` remain untouched. Users may choose whether to version-control this declarative configuration. CSO does not alter `.gitignore`.

Completion criterion: `.cso/config.json` is UTF-8 canonical JSON ending in LF and contains no timestamp, hostname, username, secret, or absolute project path.

## Understand the generated config

```json
{
  "analysis": {
    "detected": [
      "python"
    ]
  },
  "profile": "small-project",
  "skills": [],
  "version": 1
}
```

`profile` is selected conservatively from project size. `skills` contains only IDs present in `registry/skills.json`; it may be empty when no registered skill matches.

## Run doctor

```sh
cso doctor
```

Doctor checks the Python runtime, registry and schemas, checksum manifest, canonical payload integrity, optional project configuration, and platform. Missing `.cso/config.json` is healthy; a present malformed or unsafe config fails with a concrete reason.

Completion criterion: every required check reports `PASS` and the command exits `0`.

## Offline and local-first behavior

`analyze`, `init`, and `doctor` require no API key or network connection. They do not download skills, inspect credentials, read `.env` content, send telemetry, or execute third-party commands.

## Not included in the deterministic checkpoint

This checkpoint does not provide remote skill installation, `cso sync`, lockfiles, natural-language contradiction detection, permissions manifests, semantic context scoring, LLM recommendations, Codex API integration, GitHub bots, runtime sandboxing, or token accounting.

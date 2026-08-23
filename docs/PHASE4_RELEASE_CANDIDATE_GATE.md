# Phase 4 Release-Candidate Gate

## Role

Phase 4E validates the integrated Phase 4A–4D contracts. It adds release
validation, not a production orchestration interface. The integrated runtime
interface remains `evaluate_completion_gate(request)`.

The gate proves only that one exact commit and tree passed the documented,
deterministic checks. A PASS is not proof of correctness for every task,
semantic acceptance-criteria satisfaction, approval, merge readiness,
publication, deployment, or release.

Skill identifiers remain bounded by the reviewed Phase 3 skill registry.
Workflow profile identifiers remain bounded by the reviewed local static Phase
4C workflow catalog. The gate does not resolve identifiers remotely or invent
identifiers from untrusted input.

## Developer command

Start from a clean worktree whose `HEAD` is the candidate commit:

```sh
python3 scripts/phase4_rc_gate.py \
  --candidate <FULL_40_HEX_COMMIT_SHA> \
  --expected-tree <FULL_40_HEX_TREE_SHA> \
  --json
```

Both identities must be exact lowercase 40-hex values. A branch, tag, or
abbreviated SHA is not an RC identity.

Exit codes are fixed:

- `0`: every required RC gate passed.
- `2`: command usage or candidate/tree identity error.
- `3`: a candidate validation gate failed.
- `4`: an unexpected harness failure occurred.

## Output contract

With `--json`, stdout contains canonical JSON only. It is UTF-8 without a BOM,
uses LF bytes, has exactly one final LF, and orders gate records by the fixed
gate tuple. It contains the schema version, gate name, candidate SHA, tree SHA,
overall status, and logical gate statuses.

Human execution diagnostics go to stderr. They may identify the platform class,
gate status, test counts, or skip counts. Canonical stdout and stderr do not
contain a hostname, username, absolute repository path, temporary path,
timestamp, duration, executable path, secret, or fixture content.

The fixed gate order is:

1. `phase4.rc.identity`
2. `phase4.rc.fresh-checkout`
3. `phase4.rc.eol-integrity`
4. `phase4.rc.python39-grammar`
5. `phase4.rc.phase4-integration`
6. `phase4.rc.phase4d`
7. `phase4.rc.phase4c`
8. `phase4.rc.phase4b`
9. `phase4.rc.phase4a`
10. `phase4.rc.phase3`
11. `phase4.rc.full-unit`
12. `phase4.rc.smoke`
13. `phase4.rc.canonical-json`
14. `phase4.rc.privacy-security`
15. `phase4.rc.release-audit`
16. `phase4.rc.deterministic-repeat`
17. `phase4.rc.final-identity`

A failed gate is `fail`; later gates that were not executed are `not-run`.
The process never returns success after a required failure.

## Local fresh checkouts

The harness determines the source root with Git and requires its worktree to be
clean. Source `HEAD`, the candidate object type, and the candidate tree must
match the supplied identities before validation begins.

It creates two independent temporary clones from the local repository with
`git clone --no-local --no-checkout`. It does not use a GitHub URL, network API,
download, remote registry, LLM, or credential. Temporary clones are outside the
source worktree and are removed when validation ends.

The primary clone sets deterministic local checkout behavior before a detached
checkout of the exact candidate. Substantive tests, smoke, and audit gates run
there. A second independent clone sets `core.autocrlf=true` and stress-tests
repository attributes against the same detached candidate.

## EOL and attributes

Git metadata is authoritative. The harness inventories tracked paths, reads
`text` and `eol` attributes, and inspects canonical blobs without decoding
binary or NUL-containing data as text.

- Canonical normalized text blobs use repository LF form.
- Files explicitly marked `eol=lf` must remain LF in both checkouts.
- Files explicitly marked `eol=crlf` use CRLF working-tree bytes when they have
  line endings; expected CRLF is not corruption.
- Files governed only by `text=auto` are not falsely required to have LF
  working-tree bytes on Windows.
- The gate does not impose a new repository-wide trailing-newline rule.
- Canonical CLI JSON retains its separate exact-one-final-LF contract.

The harness does not modify `.gitattributes` or repair a failed checkout.

## Native and cross-platform gates

The POSIX path runs the existing `scripts/smoke.sh` with `/bin/sh`. Windows runs
the existing `scripts/smoke.ps1` with `pwsh` when available, otherwise supported
Windows PowerShell, using `-NoProfile` and `-NonInteractive`. The harness does
not translate, duplicate, or edit the smoke scripts.

The new Phase 4 integration test file permits zero platform-specific skips.
Existing explicit historical host-capability skips elsewhere may remain, but a
new integration skip fails the RC gate.

macOS and Windows feature validation must use the same feature commit and tree.
Feature validation establishes PR safety only. A normal merge creates a new
main commit, so final Phase 4 closure requires both platforms to run fresh-clone
RC validation against the same merged-main commit and tree.

## Failure policy

On failure, stop. Do not repair files, normalize line endings, change attributes,
amend history, substitute a different candidate, or convert an incomplete state
into a trusted completion claim. Stderr identifies the candidate, tree, platform
class, gate, expected state, and observed state without exposing private paths
or untrusted content.

## Validation sequence

For a feature pull request:

1. Run the full Mac feature validation.
2. Commit the exact three-file Phase 4E changes.
3. Run the RC harness twice on the committed feature SHA/tree and compare stdout
   bytes.
4. Push the feature branch normally.
5. Create a draft PR that uses `Refs #8`, not an automatic-closing keyword.
6. Independently validate the same feature SHA/tree on Windows.
7. Merge only after both feature-platform gates pass and separate authorization
   is granted.

For final merged-main RC closure:

1. Record the merged-main commit and tree.
2. Run the Mac harness twice against that exact identity.
3. Run the Windows harness twice against that same identity.
4. Confirm canonical stdout bytes and logical gate statuses match.
5. Close Issue #8 only after separate authorization and both merged-main gates
   pass.

Issue #8 remains open through feature implementation, PR merge, and any
single-platform merged-main validation. This procedure creates no tag and no
release.

#!/usr/bin/env python3
"""Validate one exact Phase 4 release-candidate commit and tree."""

from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Dict, List, Mapping, Optional, Sequence, Tuple


EXIT_PASS = 0
EXIT_USAGE = 2
EXIT_VALIDATION = 3
EXIT_INTERNAL = 4
_SHA_RE = re.compile(r"^[0-9a-f]{40}$", flags=re.ASCII)


RC_GATE_IDS = (
    "phase4.rc.identity",
    "phase4.rc.fresh-checkout",
    "phase4.rc.eol-integrity",
    "phase4.rc.python39-grammar",
    "phase4.rc.phase4-integration",
    "phase4.rc.phase4d",
    "phase4.rc.phase4c",
    "phase4.rc.phase4b",
    "phase4.rc.phase4a",
    "phase4.rc.phase3",
    "phase4.rc.full-unit",
    "phase4.rc.smoke",
    "phase4.rc.canonical-json",
    "phase4.rc.privacy-security",
    "phase4.rc.release-audit",
    "phase4.rc.deterministic-repeat",
    "phase4.rc.final-identity",
)
_ALLOWED_GATE_STATUSES = frozenset({"pass", "fail", "not-run"})


class GateFailure(Exception):
    def __init__(self, gate_id: str, expected: str, observed: str) -> None:
        super().__init__(gate_id)
        self.gate_id = gate_id
        self.expected = expected
        self.observed = observed


def is_full_sha(value: object) -> bool:
    return type(value) is str and _SHA_RE.fullmatch(value) is not None


def build_payload(
    candidate_sha: str,
    tree_sha: str,
    statuses: Mapping[str, str],
) -> Dict[str, object]:
    if set(statuses) != set(RC_GATE_IDS):
        raise ValueError("gate status set does not match the fixed gate contract")
    if any(status not in _ALLOWED_GATE_STATUSES for status in statuses.values()):
        raise ValueError("invalid gate status")
    gates = [
        {"gate_id": gate_id, "status": statuses[gate_id]}
        for gate_id in RC_GATE_IDS
    ]
    return {
        "schema_version": 1,
        "gate": "phase4-release-candidate",
        "candidate_sha": candidate_sha,
        "tree_sha": tree_sha,
        "status": (
            "pass"
            if all(entry["status"] == "pass" for entry in gates)
            else "fail"
        ),
        "gates": gates,
    }


def canonical_json_bytes(payload: Mapping[str, object]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _platform_class() -> str:
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "posix"


def _child_environment(repository_root: Path) -> Dict[str, str]:
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONHASHSEED"] = "0"
    environment["PYTHONPATH"] = str(repository_root)
    return environment


def _run(
    arguments: Sequence[str],
    *,
    cwd: Path,
    environment: Optional[Mapping[str, str]] = None,
    input_bytes: Optional[bytes] = None,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(arguments),
        cwd=str(cwd),
        env=None if environment is None else dict(environment),
        input=input_bytes,
        check=False,
        capture_output=True,
        text=False,
    )


def _git(
    repository: Path,
    arguments: Sequence[str],
    *,
    input_bytes: Optional[bytes] = None,
) -> subprocess.CompletedProcess:
    return _run(
        ["git", *arguments],
        cwd=repository,
        input_bytes=input_bytes,
    )


def _decoded_stdout(result: subprocess.CompletedProcess) -> str:
    return result.stdout.decode("utf-8", errors="strict").strip()


def _require_command(
    result: subprocess.CompletedProcess,
    gate_id: str,
    operation: str,
) -> subprocess.CompletedProcess:
    if result.returncode != 0:
        raise GateFailure(gate_id, operation + " succeeds", "command-failed")
    return result


def _head_sha(repository: Path, gate_id: str) -> str:
    result = _require_command(
        _git(repository, ["rev-parse", "HEAD"]),
        gate_id,
        "HEAD identity lookup",
    )
    return _decoded_stdout(result)


def _tree_sha(repository: Path, revision: str, gate_id: str) -> str:
    result = _require_command(
        _git(repository, ["show", "-s", "--format=%T", revision]),
        gate_id,
        "tree identity lookup",
    )
    return _decoded_stdout(result)


def _is_clean(repository: Path, gate_id: str) -> bool:
    result = _require_command(
        _git(repository, ["status", "--porcelain=v1", "-z"]),
        gate_id,
        "worktree status lookup",
    )
    return result.stdout == b""


def _repository_root() -> Path:
    result = _run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=Path.cwd(),
    )
    if result.returncode != 0:
        raise GateFailure(
            "phase4.rc.identity",
            "source is a Git worktree",
            "not-a-git-worktree",
        )
    return Path(_decoded_stdout(result))


def _verify_source_identity(
    repository: Path,
    candidate_sha: str,
    expected_tree: str,
) -> None:
    gate_id = "phase4.rc.identity"
    if not _is_clean(repository, gate_id):
        raise GateFailure(gate_id, "source worktree clean", "source-dirty")
    if _head_sha(repository, gate_id) != candidate_sha:
        raise GateFailure(gate_id, candidate_sha, "source-head-mismatch")
    object_type = _require_command(
        _git(repository, ["cat-file", "-t", candidate_sha]),
        gate_id,
        "candidate object lookup",
    )
    if _decoded_stdout(object_type) != "commit":
        raise GateFailure(gate_id, "candidate object is commit", "not-a-commit")
    if _tree_sha(repository, candidate_sha, gate_id) != expected_tree:
        raise GateFailure(gate_id, expected_tree, "candidate-tree-mismatch")


def _clone_checkout(
    source: Path,
    destination: Path,
    candidate_sha: str,
    expected_tree: str,
    *,
    autocrlf: bool,
    gate_id: str,
) -> None:
    clone = _run(
        [
            "git",
            "clone",
            "--no-local",
            "--no-checkout",
            str(source),
            str(destination),
        ],
        cwd=destination.parent,
    )
    _require_command(clone, gate_id, "local fresh clone")
    _require_command(
        _git(
            destination,
            ["config", "core.autocrlf", "true" if autocrlf else "false"],
        ),
        gate_id,
        "local checkout configuration",
    )
    if not autocrlf:
        _require_command(
            _git(destination, ["config", "core.eol", "lf"]),
            gate_id,
            "primary checkout EOL configuration",
        )
    _require_command(
        _git(destination, ["checkout", "--detach", candidate_sha]),
        gate_id,
        "detached candidate checkout",
    )
    if _head_sha(destination, gate_id) != candidate_sha:
        raise GateFailure(gate_id, candidate_sha, "checkout-head-mismatch")
    if _tree_sha(destination, "HEAD", gate_id) != expected_tree:
        raise GateFailure(gate_id, expected_tree, "checkout-tree-mismatch")
    if not _is_clean(destination, gate_id):
        raise GateFailure(gate_id, "fresh checkout clean", "checkout-dirty")


def _tracked_paths(repository: Path, gate_id: str) -> List[str]:
    result = _require_command(
        _git(repository, ["ls-files", "-z"]),
        gate_id,
        "tracked path inventory",
    )
    return [
        item.decode("utf-8", errors="strict")
        for item in result.stdout.split(b"\x00")
        if item
    ]


def _attributes(
    repository: Path,
    paths: Sequence[str],
    gate_id: str,
) -> Dict[str, Dict[str, str]]:
    encoded_paths = b"\x00".join(path.encode("utf-8") for path in paths) + b"\x00"
    result = _require_command(
        _git(
            repository,
            ["check-attr", "-z", "--stdin", "text", "eol"],
            input_bytes=encoded_paths,
        ),
        gate_id,
        "Git attribute inspection",
    )
    fields = [
        item.decode("utf-8", errors="strict")
        for item in result.stdout.split(b"\x00")
        if item
    ]
    if len(fields) % 3:
        raise GateFailure(gate_id, "complete Git attribute records", "malformed-attributes")
    attributes: Dict[str, Dict[str, str]] = {path: {} for path in paths}
    for index in range(0, len(fields), 3):
        path, name, value = fields[index : index + 3]
        if path not in attributes or name not in {"text", "eol"}:
            raise GateFailure(gate_id, "bounded Git attributes", "unexpected-attribute")
        attributes[path][name] = value
    return attributes


def _blob_bytes(repository: Path, revision: str, path: str, gate_id: str) -> bytes:
    result = _require_command(
        _git(repository, ["show", revision + ":" + path]),
        gate_id,
        "canonical blob lookup",
    )
    return result.stdout


def _verify_eol_integrity(
    primary: Path,
    stress: Path,
    candidate_sha: str,
    expected_tree: str,
) -> int:
    gate_id = "phase4.rc.eol-integrity"
    if _head_sha(stress, gate_id) != candidate_sha:
        raise GateFailure(gate_id, candidate_sha, "stress-head-mismatch")
    if _tree_sha(stress, "HEAD", gate_id) != expected_tree:
        raise GateFailure(gate_id, expected_tree, "stress-tree-mismatch")
    if not _is_clean(stress, gate_id):
        raise GateFailure(gate_id, "stress checkout clean", "stress-checkout-dirty")
    paths = _tracked_paths(primary, gate_id)
    attributes = _attributes(primary, paths, gate_id)
    for path in paths:
        primary_path = primary.joinpath(*path.split("/"))
        stress_path = stress.joinpath(*path.split("/"))
        if primary_path.is_symlink() or stress_path.is_symlink():
            continue
        blob = _blob_bytes(primary, candidate_sha, path, gate_id)
        if b"\x00" in blob:
            continue
        try:
            blob.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            continue
        text_attribute = attributes[path].get("text", "unspecified")
        eol_attribute = attributes[path].get("eol", "unspecified")
        if text_attribute != "unspecified" or eol_attribute != "unspecified":
            if b"\r" in blob:
                raise GateFailure(
                    gate_id,
                    "normalized text blobs use canonical LF",
                    "noncanonical-blob-eol",
                )
        if eol_attribute == "lf":
            for checkout_path in (primary_path, stress_path):
                working = checkout_path.read_bytes()
                if b"\r" in working:
                    raise GateFailure(
                        gate_id,
                        "explicit eol=lf working bytes",
                        "explicit-lf-conversion-failed",
                    )
        elif eol_attribute == "crlf" and b"\n" in blob:
            for checkout_path in (primary_path, stress_path):
                working = checkout_path.read_bytes()
                if b"\r\n" not in working or b"\n" in working.replace(b"\r\n", b""):
                    raise GateFailure(
                        gate_id,
                        "explicit eol=crlf working bytes",
                        "explicit-crlf-conversion-failed",
                    )
    return len(paths)


def _python39_grammar(repository: Path) -> int:
    files = sorted(
        path
        for path in repository.rglob("*.py")
        if ".git" not in path.parts
    )
    for path in files:
        ast.parse(
            path.read_text(encoding="utf-8"),
            filename=path.relative_to(repository).as_posix(),
            feature_version=(3, 9),
        )
    return len(files)


def _unittest_summary(output: bytes) -> Dict[str, int]:
    text = output.decode("utf-8", errors="replace")
    match = re.search(r"Ran (\d+) tests? in ", text)
    if match is None:
        raise ValueError("unittest summary missing")
    total = int(match.group(1))
    values = {"failed": 0, "errors": 0, "skipped": 0}
    names = {"failures": "failed", "errors": "errors", "skipped": "skipped"}
    for source_name, target_name in names.items():
        count = re.search(source_name + r"=(\d+)", text)
        if count is not None:
            values[target_name] = int(count.group(1))
    values["total"] = total
    values["passed"] = total - values["failed"] - values["errors"] - values["skipped"]
    return values


def _run_tests(
    repository: Path,
    gate_id: str,
    arguments: Sequence[str],
    *,
    zero_skips: bool = False,
    required_test_names: Sequence[str] = (),
) -> Dict[str, int]:
    result = _run(
        [sys.executable, *arguments],
        cwd=repository,
        environment=_child_environment(repository),
    )
    combined_output = result.stdout + result.stderr
    try:
        summary = _unittest_summary(combined_output)
    except ValueError:
        raise GateFailure(gate_id, "unittest summary", "summary-missing")
    if result.returncode != 0 or summary["failed"] or summary["errors"]:
        raise GateFailure(gate_id, "all tests pass", "test-failure")
    if zero_skips and summary["skipped"]:
        raise GateFailure(gate_id, "zero integration skips", "integration-skip")
    decoded_output = combined_output.decode("utf-8", errors="replace")
    if any(test_name not in decoded_output for test_name in required_test_names):
        raise GateFailure(
            gate_id,
            "required privacy/security tests executed",
            "required-test-missing",
        )
    return summary


def _run_smoke(repository: Path) -> None:
    gate_id = "phase4.rc.smoke"
    environment = _child_environment(repository)
    if os.name == "nt":
        executable = shutil.which("pwsh") or shutil.which("powershell")
        if executable is None:
            raise GateFailure(gate_id, "PowerShell available", "powershell-missing")
        arguments = [
            executable,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            "scripts/smoke.ps1",
        ]
    else:
        arguments = ["/bin/sh", "scripts/smoke.sh"]
    result = _run(arguments, cwd=repository, environment=environment)
    if result.returncode != 0:
        raise GateFailure(gate_id, "native smoke passes", "smoke-failed")


def _run_release_audit(repository: Path) -> int:
    gate_id = "phase4.rc.release-audit"
    result = _run(
        [sys.executable, "scripts/release_audit.py"],
        cwd=repository,
        environment=_child_environment(repository),
    )
    if result.returncode != 0:
        raise GateFailure(gate_id, "release audit clean", "release-audit-failed")
    match = re.search(rb"Tracked files: (\d+)", result.stdout)
    if match is None:
        raise GateFailure(gate_id, "tracked file count reported", "audit-count-missing")
    return int(match.group(1))


def _verify_final_identity(
    source: Path,
    primary: Path,
    stress: Path,
    candidate_sha: str,
    expected_tree: str,
) -> None:
    gate_id = "phase4.rc.final-identity"
    for label, repository in (
        ("source", source),
        ("primary", primary),
        ("stress", stress),
    ):
        if _head_sha(repository, gate_id) != candidate_sha:
            raise GateFailure(gate_id, candidate_sha, label + "-head-mismatch")
        if _tree_sha(repository, "HEAD", gate_id) != expected_tree:
            raise GateFailure(gate_id, expected_tree, label + "-tree-mismatch")
        if not _is_clean(repository, gate_id):
            raise GateFailure(gate_id, label + " worktree clean", label + "-dirty")


def _emit_payload(payload: Mapping[str, object]) -> None:
    sys.stdout.buffer.write(canonical_json_bytes(payload))
    sys.stdout.buffer.flush()


def _stderr_report(
    candidate_sha: str,
    tree_sha: str,
    gate_id: str,
    status: str,
    detail: str = "",
) -> None:
    suffix = " " + detail if detail else ""
    sys.stderr.write(
        "candidate="
        + candidate_sha
        + " tree="
        + tree_sha
        + " platform="
        + _platform_class()
        + " gate="
        + gate_id
        + " status="
        + status
        + suffix
        + "\n"
    )


def _fail(
    candidate_sha: str,
    tree_sha: str,
    statuses: Dict[str, str],
    failure: GateFailure,
    exit_code: int,
) -> int:
    statuses[failure.gate_id] = "fail"
    _stderr_report(
        candidate_sha,
        tree_sha,
        failure.gate_id,
        "fail",
        "expected=" + failure.expected + " observed=" + failure.observed,
    )
    _emit_payload(build_payload(candidate_sha, tree_sha, statuses))
    return exit_code


def _mark_pass(
    statuses: Dict[str, str],
    candidate_sha: str,
    tree_sha: str,
    gate_id: str,
    detail: str = "",
) -> None:
    statuses[gate_id] = "pass"
    _stderr_report(candidate_sha, tree_sha, gate_id, "pass", detail)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--expected-tree", required=True)
    parser.add_argument("--json", action="store_true", required=True)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parser().parse_args(argv)
    candidate_sha = args.candidate
    expected_tree = args.expected_tree
    if not is_full_sha(candidate_sha) or not is_full_sha(expected_tree):
        sys.stderr.write("candidate and expected tree must be lowercase 40-hex identities\n")
        return EXIT_USAGE
    statuses = {gate_id: "not-run" for gate_id in RC_GATE_IDS}
    try:
        source = _repository_root()
        _verify_source_identity(source, candidate_sha, expected_tree)
        _mark_pass(statuses, candidate_sha, expected_tree, "phase4.rc.identity")
    except GateFailure as failure:
        return _fail(
            candidate_sha,
            expected_tree,
            statuses,
            failure,
            EXIT_USAGE,
        )
    try:
        with tempfile.TemporaryDirectory(prefix="cso-phase4-rc-") as temporary:
            temporary_root = Path(temporary)
            primary = temporary_root / "primary"
            stress = temporary_root / "autocrlf"
            _clone_checkout(
                source,
                primary,
                candidate_sha,
                expected_tree,
                autocrlf=False,
                gate_id="phase4.rc.fresh-checkout",
            )
            _mark_pass(
                statuses,
                candidate_sha,
                expected_tree,
                "phase4.rc.fresh-checkout",
            )
            _clone_checkout(
                source,
                stress,
                candidate_sha,
                expected_tree,
                autocrlf=True,
                gate_id="phase4.rc.eol-integrity",
            )
            try:
                tracked_count = _verify_eol_integrity(
                    primary,
                    stress,
                    candidate_sha,
                    expected_tree,
                )
            except GateFailure:
                raise
            except (OSError, UnicodeError):
                raise GateFailure(
                    "phase4.rc.eol-integrity",
                    "tracked attributes and bytes are valid",
                    "eol-inspection-failed",
                )
            _mark_pass(
                statuses,
                candidate_sha,
                expected_tree,
                "phase4.rc.eol-integrity",
                "tracked=" + str(tracked_count),
            )
            try:
                python_count = _python39_grammar(primary)
            except (OSError, SyntaxError, UnicodeError):
                raise GateFailure(
                    "phase4.rc.python39-grammar",
                    "all repository Python parses as Python 3.9",
                    "grammar-validation-failed",
                )
            _mark_pass(
                statuses,
                candidate_sha,
                expected_tree,
                "phase4.rc.python39-grammar",
                "files=" + str(python_count),
            )
            test_gates: Tuple[Tuple[str, Sequence[str], bool], ...] = (
                (
                    "phase4.rc.phase4-integration",
                    ("tests/test_phase4_integration.py", "-v"),
                    True,
                ),
                ("phase4.rc.phase4d", ("tests/test_completion_gate.py", "-v"), False),
                ("phase4.rc.phase4c", ("tests/test_workflow_selection.py", "-v"), False),
                ("phase4.rc.phase4b", ("tests/test_acceptance_criteria.py", "-v"), False),
                ("phase4.rc.phase4a", ("tests/test_task_readiness.py", "-v"), False),
                ("phase4.rc.phase3", ("tests/test_phase3_integration.py", "-v"), False),
                (
                    "phase4.rc.full-unit",
                    ("-m", "unittest", "discover", "-s", "tests", "-v"),
                    False,
                ),
            )
            for gate_id, arguments, zero_skips in test_gates:
                summary = _run_tests(
                    primary,
                    gate_id,
                    arguments,
                    zero_skips=zero_skips,
                )
                _mark_pass(
                    statuses,
                    candidate_sha,
                    expected_tree,
                    gate_id,
                    "tests="
                    + str(summary["total"])
                    + " passed="
                    + str(summary["passed"])
                    + " skipped="
                    + str(summary["skipped"]),
                )
            _run_smoke(primary)
            _mark_pass(
                statuses,
                candidate_sha,
                expected_tree,
                "phase4.rc.smoke",
            )
            canonical_summary = _run_tests(
                primary,
                "phase4.rc.canonical-json",
                ("tests/test_cli_phase2.py", "-v"),
            )
            _mark_pass(
                statuses,
                candidate_sha,
                expected_tree,
                "phase4.rc.canonical-json",
                "tests=" + str(canonical_summary["total"]),
            )
            privacy_summary = _run_tests(
                primary,
                "phase4.rc.privacy-security",
                ("tests/test_phase4_integration.py", "-v"),
                zero_skips=True,
                required_test_names=(
                    "test_integrated_privacy_does_not_echo_untrusted_content",
                    "test_integrated_product_modules_have_no_external_effect_dependencies",
                ),
            )
            _mark_pass(
                statuses,
                candidate_sha,
                expected_tree,
                "phase4.rc.privacy-security",
                "tests=" + str(privacy_summary["total"]),
            )
            audit_count = _run_release_audit(primary)
            _mark_pass(
                statuses,
                candidate_sha,
                expected_tree,
                "phase4.rc.release-audit",
                "tracked=" + str(audit_count),
            )
            _verify_final_identity(
                source,
                primary,
                stress,
                candidate_sha,
                expected_tree,
            )
            statuses["phase4.rc.deterministic-repeat"] = "pass"
            statuses["phase4.rc.final-identity"] = "pass"
            first = canonical_json_bytes(
                build_payload(candidate_sha, expected_tree, statuses)
            )
            second = canonical_json_bytes(
                build_payload(candidate_sha, expected_tree, dict(statuses))
            )
            if first != second:
                statuses["phase4.rc.deterministic-repeat"] = "fail"
                statuses["phase4.rc.final-identity"] = "not-run"
                raise GateFailure(
                    "phase4.rc.deterministic-repeat",
                    "byte-identical canonical rebuild",
                    "byte-mismatch",
                )
            _stderr_report(
                candidate_sha,
                expected_tree,
                "phase4.rc.deterministic-repeat",
                "pass",
            )
            _stderr_report(
                candidate_sha,
                expected_tree,
                "phase4.rc.final-identity",
                "pass",
            )
            _emit_payload(build_payload(candidate_sha, expected_tree, statuses))
            return EXIT_PASS
    except GateFailure as failure:
        return _fail(
            candidate_sha,
            expected_tree,
            statuses,
            failure,
            EXIT_VALIDATION,
        )
    except Exception as exc:
        gate_id = next(
            (
                candidate
                for candidate in RC_GATE_IDS
                if statuses[candidate] == "not-run"
            ),
            "phase4.rc.final-identity",
        )
        statuses[gate_id] = "fail"
        _stderr_report(
            candidate_sha,
            expected_tree,
            gate_id,
            "fail",
            "expected=harness-completes observed=internal-error-"
            + type(exc).__name__,
        )
        _emit_payload(build_payload(candidate_sha, expected_tree, statuses))
        return EXIT_INTERNAL


if __name__ == "__main__":
    raise SystemExit(main())

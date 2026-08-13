"""Bounded, local-only project analysis."""

from __future__ import annotations

import heapq
import json
import os
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Set, Tuple

from .errors import SecurityError, ValidationError
from .validation import is_reparse_point


MAX_ENTRIES = 50_000
MAX_METADATA_BYTES = 1_000_000
SMALL_PROJECT_THRESHOLD = 100
LARGE_PROJECT_THRESHOLD = 1_000

EXCLUDED_DIRECTORIES = {
    ".cso",
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "target",
    "vendor",
    ".cache",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}

PYTHON_MARKERS = {"pyproject.toml", "requirements.txt", "setup.py", "setup.cfg"}
DOCKER_MARKERS = {"Dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}


def _classify_size(files_analyzed: int) -> str:
    if files_analyzed < SMALL_PROJECT_THRESHOLD:
        return "small"
    if files_analyzed >= LARGE_PROJECT_THRESHOLD:
        return "large"
    return "medium"


def _record(collection: MutableMapping[str, Set[str]], name: str, evidence: str) -> None:
    collection.setdefault(name, set()).add(evidence)


def _stable_findings(collection: Mapping[str, Set[str]], key: str) -> List[Dict[str, Any]]:
    return [
        {key: name, "evidence": sorted(evidence)}
        for name, evidence in sorted(collection.items())
    ]


def _safe_package_metadata(path: Path, relative: str, warnings: List[str]) -> Mapping[str, Any]:
    try:
        if path.stat().st_size > MAX_METADATA_BYTES:
            warnings.append(f"Skipped oversized metadata: {relative}")
            return {}
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        warnings.append(f"Skipped malformed metadata: {relative}")
        return {}
    if not isinstance(document, dict):
        warnings.append(f"Skipped malformed metadata: {relative}")
        return {}
    result: Dict[str, Any] = {}
    for key in ("dependencies", "devDependencies"):
        value = document.get(key)
        result[key] = value if isinstance(value, dict) else {}
    scripts = document.get("scripts")
    result["scripts"] = sorted(scripts) if isinstance(scripts, dict) else []
    return result


def _walk_files(
    root: Path,
    max_entries: int,
    warnings: List[str],
) -> Tuple[List[Tuple[Path, str]], List[str], bool]:
    files: List[Tuple[Path, str]] = []
    directory_paths: List[str] = []
    pending = [root]
    visited = 0
    truncated = False
    while pending:
        current = pending.pop()
        remaining = max_entries - visited
        if remaining <= 0:
            truncated = True
            break
        try:
            with os.scandir(current) as iterator:
                entries = heapq.nsmallest(
                    remaining + 1,
                    (entry for entry in iterator if entry.name.casefold() not in EXCLUDED_DIRECTORIES),
                    key=lambda entry: (entry.name.casefold(), entry.name),
                )
        except OSError:
            relative = current.relative_to(root).as_posix() or "."
            warnings.append(f"Skipped unreadable directory: {relative}")
            continue
        directories: List[Path] = []
        if len(entries) > remaining:
            entries = entries[:remaining]
            truncated = True
        for entry in entries:
            visited += 1
            path = Path(entry.path)
            relative = path.relative_to(root).as_posix()
            try:
                if entry.is_symlink() or is_reparse_point(path):
                    warnings.append(f"Skipped link or reparse point: {relative}")
                elif entry.is_dir(follow_symlinks=False):
                    directories.append(path)
                    directory_paths.append(relative)
                elif entry.is_file(follow_symlinks=False):
                    files.append((path, relative))
            except OSError:
                warnings.append(f"Skipped unreadable entry: {relative}")
        if truncated:
            break
        pending.extend(reversed(directories))
    files.sort(key=lambda item: item[1])
    return files, sorted(directory_paths), truncated


def analyze_project(root: Path, *, max_entries: int = MAX_ENTRIES) -> Dict[str, Any]:
    """Return a stable analysis document without modifying *root*."""

    if max_entries < 1:
        raise ValidationError("analysis max_entries must be positive")
    lexical_root = root.expanduser().absolute()
    if not lexical_root.is_dir() or is_reparse_point(lexical_root):
        raise SecurityError("project root must be a regular directory")
    root = lexical_root.resolve(strict=True)

    warnings: List[str] = []
    files, directories, truncated = _walk_files(root, max_entries, warnings)
    detected: Dict[str, Set[str]] = {}
    tests: Dict[str, Set[str]] = {}

    for path, relative in files:
        name = path.name
        lower_name = name.casefold()
        if name in PYTHON_MARKERS:
            _record(detected, "python", relative)
        if name == "package.json":
            _record(detected, "nodejs", relative)
            metadata = _safe_package_metadata(path, relative, warnings)
            dependencies = set(metadata.get("dependencies", {})) | set(metadata.get("devDependencies", {}))
            if "react" in dependencies:
                _record(detected, "react", relative)
            if "typescript" in dependencies:
                _record(detected, "typescript", relative)
            for framework in ("jest", "vitest", "playwright"):
                package_name = "@playwright/test" if framework == "playwright" else framework
                if framework in dependencies or package_name in dependencies:
                    _record(tests, framework, relative)
        if name == "tsconfig.json":
            _record(detected, "typescript", relative)
        if name in DOCKER_MARKERS:
            _record(detected, "docker", relative)
        if relative.startswith(".github/workflows/"):
            _record(detected, "github-actions", relative)
        if name == "Cargo.toml":
            _record(detected, "rust", relative)
            _record(tests, "cargo-test", relative)
        if name == "go.mod":
            _record(detected, "go", relative)
            _record(tests, "go-test", relative)
        if name == "pom.xml":
            _record(detected, "java-maven", relative)
        if name in {"build.gradle", "build.gradle.kts"}:
            _record(detected, "java-gradle", relative)
        if lower_name.endswith((".sln", ".csproj")):
            _record(detected, "dotnet", relative)
        if lower_name.endswith(".tf"):
            _record(detected, "terraform", relative)
        if name in {"pytest.ini", "conftest.py"} or (
            relative.startswith("tests/") and lower_name.startswith("test_") and lower_name.endswith(".py")
        ):
            _record(tests, "pytest", relative)
        if lower_name.endswith(".py") and (
            lower_name.startswith("test") or "/test" in relative.casefold()
        ):
            _record(tests, "unittest", relative)

    if ".github/workflows" in directories and "github-actions" not in detected:
        _record(detected, "github-actions", ".github/workflows/")

    return {
        "schema_version": 1,
        "truncated": truncated,
        "detected": _stable_findings(detected, "technology"),
        "tests": _stable_findings(tests, "framework"),
        "project": {
            "files_analyzed": len(files),
            "size": "unknown" if truncated else _classify_size(len(files)),
            "truncated": truncated,
        },
        "warnings": sorted(set(warnings)),
    }

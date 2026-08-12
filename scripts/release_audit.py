#!/usr/bin/env python3
"""Read-only public-release audit for secrets, private paths, and license metadata."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


ROOT = Path(__file__).resolve().parents[1]
MAX_TEXT_BYTES = 2 * 1024 * 1024
SKIP_DIRECTORIES = {".git", "__pycache__", ".venv", "venv", "dist", "build"}
REQUIRED_FILES = {
    "README.md",
    "LICENSE",
    ".gitignore",
    "SECURITY.md",
    "CONTRIBUTING.md",
}
SUSPICIOUS_NAMES = re.compile(
    r"(?i)(^|/)(?:\.env(?:\..*)?|\.npmrc|\.pypirc|\.netrc|\.git-credentials|"
    r"credentials?(?:\.[^/]+)?|cookies?(?:\.[^/]+)?|id_(?:rsa|dsa|ecdsa|ed25519)(?:\.pub)?|"
    r"[^/]+\.(?:pem|p12|pfx|key))$"
)
CONTENT_PATTERNS: Sequence[Tuple[str, re.Pattern[str]]] = (
    ("private-key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    (
        "known-token",
        re.compile(
            r"(?:sk-[A-Za-z0-9_-]{20,}|github_pat_[A-Za-z0-9_]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|"
            r"xox[baprs]-[A-Za-z0-9-]{20,}|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{30,}|"
            r"ya29\.[0-9A-Za-z_-]{20,})"
        ),
    ),
    (
        "credential-assignment",
        re.compile(
            r"(?i)(?:api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret|password|passwd|"
            r"credential(?:s)?|cookie(?:s)?|session[_-]?token)\s*[=:]\s*['\"]?[^\s'\";,]{8,}"
        ),
    ),
    (
        "windows-private-path",
        re.compile(r"(?i)[A-Z]:\\" + "Users" + r"\\(?!you(?:\\|$)|example(?:\\|$)|username(?:\\|$))[^\\\s'\"]+"),
    ),
    (
        "mac-private-path",
        re.compile("/" + "Users" + r"/(?!you(?:/|$)|example(?:/|$)|username(?:/|$))[^/\s'\"]+"),
    ),
    (
        "linux-private-path",
        re.compile("/" + "home" + r"/(?!you(?:/|$)|example(?:/|$)|username(?:/|$))[^/\s'\"]+"),
    ),
    ("email-address", re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")),
)


def run_git(arguments: Sequence[str], *, text: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(ROOT), *arguments],
        check=False,
        capture_output=True,
        text=text,
    )


def is_git_repository() -> bool:
    result = run_git(["rev-parse", "--is-inside-work-tree"])
    return result.returncode == 0 and result.stdout.strip() == "true"


def working_files() -> Iterable[Path]:
    for current, dirs, files in os.walk(ROOT, topdown=True, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name not in SKIP_DIRECTORIES)
        current_path = Path(current)
        for name in sorted(files):
            yield current_path / name


def decode_text(data: bytes) -> Optional[str]:
    if len(data) > MAX_TEXT_BYTES or b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan_content(text: str, location: str, scope: str) -> List[Dict[str, object]]:
    findings: List[Dict[str, object]] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        for category, pattern in CONTENT_PATTERNS:
            if pattern.search(line):
                findings.append(
                    {
                        "scope": scope,
                        "file": location,
                        "line": line_number,
                        "category": category,
                    }
                )
    return findings


def scan_working_tree() -> List[Dict[str, object]]:
    findings: List[Dict[str, object]] = []
    for path in working_files():
        relative = path.relative_to(ROOT).as_posix()
        if SUSPICIOUS_NAMES.search(relative):
            findings.append({"scope": "working-tree", "file": relative, "line": None, "category": "suspicious-filename"})
        text = decode_text(path.read_bytes())
        if text is not None:
            findings.extend(scan_content(text, relative, "working-tree"))
    return findings


def tracked_files() -> List[str]:
    if not is_git_repository():
        return []
    result = run_git(["ls-files", "-z"], text=False)
    if result.returncode != 0:
        return []
    return [item.decode("utf-8") for item in result.stdout.split(b"\x00") if item]


def scan_history() -> List[Dict[str, object]]:
    if not is_git_repository():
        return [{"scope": "git", "file": ".git", "line": None, "category": "not-a-git-repository"}]
    commits_result = run_git(["rev-list", "--all"])
    if commits_result.returncode != 0:
        return [{"scope": "git", "file": ".git", "line": None, "category": "history-unreadable"}]
    findings: List[Dict[str, object]] = []
    for commit in [line for line in commits_result.stdout.splitlines() if line]:
        files_result = run_git(["ls-tree", "-r", "--name-only", "-z", commit], text=False)
        if files_result.returncode != 0:
            findings.append({"scope": commit[:12], "file": ".git", "line": None, "category": "tree-unreadable"})
            continue
        for raw_name in files_result.stdout.split(b"\x00"):
            if not raw_name:
                continue
            name = raw_name.decode("utf-8")
            if SUSPICIOUS_NAMES.search(name):
                findings.append({"scope": commit[:12], "file": name, "line": None, "category": "suspicious-filename"})
            blob = run_git(["show", f"{commit}:{name}"], text=False)
            if blob.returncode != 0:
                continue
            text = decode_text(blob.stdout)
            if text is not None:
                findings.extend(scan_content(text, name, commit[:12]))
    return findings


def license_findings() -> List[Dict[str, object]]:
    findings: List[Dict[str, object]] = []
    registry_path = ROOT / "registry" / "skills.json"
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return [{"scope": "working-tree", "file": "registry/skills.json", "line": None, "category": "invalid-registry"}]
    for entry in registry.get("skills", []):
        license_info = entry.get("license", {})
        provenance = entry.get("provenance", {})
        if not license_info.get("spdx") or not license_info.get("redistribution"):
            findings.append({"scope": "working-tree", "file": "registry/skills.json", "line": None, "category": "license-not-approved"})
        if provenance.get("third_party"):
            source = entry.get("source", {})
            if not source.get("repository") or not re.fullmatch(r"[0-9a-f]{40}", source.get("revision", "")):
                findings.append({"scope": "working-tree", "file": "registry/skills.json", "line": None, "category": "third-party-provenance-incomplete"})
    return findings


def audit() -> Dict[str, object]:
    findings = scan_working_tree()
    findings.extend(scan_history())
    findings.extend(license_findings())
    for filename in sorted(REQUIRED_FILES):
        if not (ROOT / filename).is_file():
            findings.append({"scope": "working-tree", "file": filename, "line": None, "category": "required-file-missing"})
    suspicious_tracked = [name for name in tracked_files() if SUSPICIOUS_NAMES.search(name)]
    unique = []
    seen = set()
    for finding in findings:
        key = (finding["scope"], finding["file"], finding["line"], finding["category"])
        if key not in seen:
            unique.append(finding)
            seen.add(key)
    return {
        "schema_version": 1,
        "status": "clean" if not unique else "findings",
        "git_repository": is_git_repository(),
        "tracked_files": len(tracked_files()),
        "suspicious_tracked_files": suspicious_tracked,
        "findings": unique,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    result = audit()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Release audit: {result['status']}")
        print(f"Tracked files: {result['tracked_files']}")
        for finding in result["findings"]:
            suffix = f":{finding['line']}" if finding["line"] is not None else ""
            print(f"- {finding['category']}: {finding['file']}{suffix} [{finding['scope']}]")
    return 0 if result["status"] == "clean" else 4


if __name__ == "__main__":
    raise SystemExit(main())

"""Deterministic evidence for known agent context files."""

from __future__ import annotations

import os
import stat
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .validation import is_reparse_point


MAX_CONTEXT_FILES = 256
MAX_CONTEXT_FILE_BYTES = 256_000
MAX_CONTEXT_SCAN_ENTRIES = 50_000


def normalize_scope_identity(scope: str) -> Tuple[str, ...]:
    if scope == ".":
        return ()
    return tuple(unicodedata.normalize("NFC", part).casefold() for part in scope.split("/"))


def scope_contains(parent: str, child: str) -> bool:
    if parent == "unknown" or child == "unknown":
        return False
    parent_identity = normalize_scope_identity(parent)
    child_identity = normalize_scope_identity(child)
    return child_identity[: len(parent_identity)] == parent_identity


def _scope_overlaps(evidence: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    known = [item for item in evidence if item["scope_state"] != "unknown"]
    known.sort(
        key=lambda item: (
            len(normalize_scope_identity(item["scope"])),
            normalize_scope_identity(item["scope"]),
            unicodedata.normalize("NFC", item["path"]).casefold(),
            item["path"],
        )
    )
    overlaps: List[Dict[str, Any]] = []
    for index, ancestor in enumerate(known):
        ancestor_parts = normalize_scope_identity(ancestor["scope"])
        for descendant in known[index + 1 :]:
            descendant_parts = normalize_scope_identity(descendant["scope"])
            if not scope_contains(ancestor["scope"], descendant["scope"]):
                continue
            relationship = "same-scope" if descendant_parts == ancestor_parts else "ancestor-descendant"
            overlaps.append(
                {
                    "type": "scope-overlap",
                    "paths": [ancestor["path"], descendant["path"]],
                    "scopes": [ancestor["scope"], descendant["scope"]],
                    "relationship": relationship,
                }
            )
    return overlaps


def _conflict_sort_key(conflict: Dict[str, Any]) -> Tuple[Any, ...]:
    severity_order = {"error": 0, "warning": 1, "info": 2}
    return (
        severity_order.get(conflict["severity"], 99),
        conflict["type"],
        normalize_scope_identity(conflict["scope"]) if conflict["scope"] != "unknown" else ("~unknown",),
        tuple(unicodedata.normalize("NFC", path).casefold() for path in conflict["paths"]),
        tuple(conflict["paths"]),
    )


def _collision_conflicts(
    logical_contexts: Mapping[str, Sequence[Tuple[str, Tuple[str, str, str]]]],
) -> List[Dict[str, Any]]:
    conflicts: List[Dict[str, Any]] = []
    for logical_key in sorted(logical_contexts):
        registrations = logical_contexts[logical_key]
        if len(registrations) < 2:
            continue
        paths = sorted(
            {relative for relative, _classification in registrations},
            key=lambda relative: (unicodedata.normalize("NFC", relative).casefold(), relative),
        )
        classification = min(
            registrations,
            key=lambda item: (unicodedata.normalize("NFC", item[0]).casefold(), item[0]),
        )[1]
        conflict_type = "normalized-path-collision" if len(paths) > 1 else "duplicate-source-registration"
        reason = (
            "multiple context paths share one NFC-casefold identity"
            if len(paths) > 1
            else "one context source is registered more than once"
        )
        conflicts.append(
            {
                "id": f"context.{conflict_type}",
                "type": conflict_type,
                "severity": "warning",
                "paths": paths,
                "scope": classification[1],
                "reason": reason,
            }
        )
    return sorted(conflicts, key=_conflict_sort_key)


def _context_document(
    evidence: Sequence[Dict[str, Any]],
    conflicts: Sequence[Dict[str, Any]],
    incomplete: bool,
) -> Dict[str, Any]:
    return {
        "evidence": list(evidence),
        "scope_overlaps": _scope_overlaps(evidence),
        "conflicts": list(conflicts),
        "conflict_analysis_complete": not incomplete,
        "truncated": incomplete,
    }


def _context_kind_and_scope(relative: str) -> Optional[Tuple[str, str, str]]:
    path = Path(relative)
    parts = tuple(part.casefold() for part in path.parts)
    if path.name.casefold() in {"agents.md", "claude.md"}:
        parent = path.parent.as_posix()
        if parent == ".":
            return "agent-instructions", ".", "root"
        return "agent-instructions", parent, "path-scoped"
    if parts == (".cursorrules",):
        return "cursor-rules", ".", "root"
    if len(parts) == 3 and parts[:2] == (".cursor", "rules") and parts[2].endswith(".md"):
        return "cursor-rule", "unknown", "unknown"
    if parts == (".github", "copilot-instructions.md"):
        return "copilot-instructions", ".", "root"
    return None


def discover_context(
    files: Sequence[Tuple[Path, str]],
    warnings: List[str],
    *,
    unsafe_paths: Sequence[str] = (),
    traversal_truncated: bool = False,
    max_files: int = MAX_CONTEXT_FILES,
    max_bytes: int = MAX_CONTEXT_FILE_BYTES,
) -> Dict[str, Any]:
    """Validate bounded files and emit metadata-only context evidence."""

    evidence: List[Dict[str, Any]] = []
    incomplete = bool(unsafe_paths) or traversal_truncated
    count_truncated = False
    for unsafe_path in sorted(set(unsafe_paths)):
        warnings.append(f"context-unsafe-path: {unsafe_path}")
    if traversal_truncated:
        warnings.append("Context discovery incomplete because project traversal was truncated")
        return _context_document([], [], True)
    candidates: List[Tuple[Path, str, Tuple[str, str, str]]] = []
    logical_contexts: Dict[str, List[Tuple[str, Tuple[str, str, str]]]] = defaultdict(list)
    for path, relative in files:
        classification = _context_kind_and_scope(relative)
        if classification is None:
            continue
        candidates.append((path, relative, classification))
        logical_key = unicodedata.normalize("NFC", relative).casefold()
        logical_contexts[logical_key].append((relative, classification))
    collisions = {
        key
        for key, registrations in logical_contexts.items()
        if len({relative for relative, _classification in registrations}) > 1
    }
    duplicates = {
        key
        for key, registrations in logical_contexts.items()
        if len(registrations) > 1
        and len({relative for relative, _classification in registrations}) == 1
    }
    for key in sorted(collisions):
        paths = sorted({relative for relative, _classification in logical_contexts[key]})
        warnings.append("context-ambiguous-path: " + " | ".join(paths))
    for key in sorted(duplicates):
        warnings.append(f"context-duplicate-registration: {logical_contexts[key][0][0]}")
    if collisions:
        incomplete = True
    conflicts = _collision_conflicts(logical_contexts)

    processed_registrations = set()
    for path, relative, classification in sorted(candidates, key=lambda item: (item[1].casefold(), item[1])):
        logical_key = unicodedata.normalize("NFC", relative).casefold()
        if logical_key in collisions:
            continue
        registration_key = (logical_key, relative)
        if registration_key in processed_registrations:
            continue
        processed_registrations.add(registration_key)
        if len(evidence) >= max_files:
            incomplete = True
            count_truncated = True
            break
        try:
            metadata = os.stat(path, follow_symlinks=False)
        except OSError:
            warnings.append(f"context-unreadable: {relative}")
            incomplete = True
            continue
        if not stat.S_ISREG(metadata.st_mode) or is_reparse_point(path):
            warnings.append(f"context-unsafe-file: {relative}")
            incomplete = True
            continue
        size_bytes = metadata.st_size
        if size_bytes > max_bytes:
            warnings.append(f"context-oversized: {relative}")
            incomplete = True
            continue
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except OSError:
            warnings.append(f"context-unreadable: {relative}")
            incomplete = True
            continue
        try:
            opened = os.fstat(descriptor)
            same_file = (
                metadata.st_ino != 0
                and opened.st_ino != 0
                and metadata.st_dev == opened.st_dev
                and metadata.st_ino == opened.st_ino
            )
            if not same_file or not stat.S_ISREG(opened.st_mode) or is_reparse_point(path):
                warnings.append(f"context-unsafe-file: {relative}")
                incomplete = True
                continue
            chunks: List[bytes] = []
            remaining = max_bytes + 1
            while remaining:
                chunk = os.read(descriptor, min(65_536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            content = b"".join(chunks)
        except OSError:
            warnings.append(f"context-unreadable: {relative}")
            incomplete = True
            continue
        finally:
            os.close(descriptor)
        if len(content) > max_bytes:
            warnings.append(f"context-oversized: {relative}")
            incomplete = True
            continue
        try:
            content.decode("utf-8-sig")
        except UnicodeDecodeError:
            warnings.append(f"context-invalid-utf8: {relative}")
            incomplete = True
            continue
        kind, scope, scope_state = classification
        evidence.append(
            {
                "path": relative,
                "kind": kind,
                "scope": scope,
                "scope_state": scope_state,
            }
        )
    if count_truncated:
        warnings.append(f"Context evidence truncated at {max_files} files")
    return _context_document(evidence, conflicts, incomplete)

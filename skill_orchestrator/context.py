"""Deterministic evidence for known agent context files."""

from __future__ import annotations

import os
import stat
import unicodedata
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .validation import is_reparse_point


MAX_CONTEXT_FILES = 256
MAX_CONTEXT_FILE_BYTES = 256_000
MAX_CONTEXT_SCAN_ENTRIES = 50_000


def _context_kind_and_scope(relative: str) -> Optional[Tuple[str, str]]:
    path = Path(relative)
    parts = tuple(part.casefold() for part in path.parts)
    if path.name.casefold() in {"agents.md", "claude.md"}:
        parent = path.parent.as_posix()
        return "agent-instructions", "." if parent == "." else parent
    if parts == (".cursorrules",):
        return "cursor-rules", "."
    if len(parts) == 3 and parts[:2] == (".cursor", "rules") and parts[2].endswith(".md"):
        return "cursor-rule", "unknown"
    if parts == (".github", "copilot-instructions.md"):
        return "copilot-instructions", "."
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
        return {"evidence": [], "truncated": True}
    candidates: List[Tuple[Path, str, Tuple[str, str]]] = []
    logical_paths: Dict[str, List[str]] = defaultdict(list)
    for path, relative in files:
        classification = _context_kind_and_scope(relative)
        if classification is None:
            continue
        candidates.append((path, relative, classification))
        logical_key = unicodedata.normalize("NFC", relative).casefold()
        logical_paths[logical_key].append(relative)
    ambiguous = {key for key, paths in logical_paths.items() if len(paths) > 1}
    for key in sorted(ambiguous):
        warnings.append("context-ambiguous-path: " + " | ".join(sorted(logical_paths[key])))
    if ambiguous:
        incomplete = True

    for path, relative, classification in sorted(candidates, key=lambda item: (item[1].casefold(), item[1])):
        logical_key = unicodedata.normalize("NFC", relative).casefold()
        if logical_key in ambiguous:
            continue
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
        kind, scope = classification
        evidence.append(
            {
                "path": relative,
                "kind": kind,
                "scope": scope,
            }
        )
    if count_truncated:
        warnings.append(f"Context evidence truncated at {max_files} files")
    return {"evidence": evidence, "truncated": incomplete}

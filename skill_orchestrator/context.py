"""Deterministic evidence for known agent context files."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .validation import is_reparse_point


MAX_CONTEXT_FILES = 256
MAX_CONTEXT_BYTES = 256_000


def _context_kind_and_scope(relative: str) -> Optional[Tuple[str, str]]:
    path = Path(relative)
    parts = tuple(part.casefold() for part in path.parts)
    if path.name.casefold() in {"agents.md", "claude.md"}:
        parent = path.parent.as_posix()
        return "agent-instructions", "." if parent == "." else parent
    if parts == (".cursorrules",):
        return "cursor-rules", "."
    if len(parts) > 2 and parts[:2] == (".cursor", "rules"):
        return "cursor-rule", "unknown"
    if parts == (".github", "copilot-instructions.md"):
        return "copilot-instructions", "."
    return None


def discover_context(
    files: Sequence[Tuple[Path, str]],
    warnings: List[str],
    *,
    max_files: int = MAX_CONTEXT_FILES,
    max_bytes: int = MAX_CONTEXT_BYTES,
) -> Dict[str, Any]:
    """Classify bounded, prevalidated regular files without reading content."""

    evidence: List[Dict[str, Any]] = []
    truncated = False
    for path, relative in files:
        classification = _context_kind_and_scope(relative)
        if classification is None:
            continue
        if len(evidence) >= max_files:
            truncated = True
            break
        try:
            metadata = os.stat(path, follow_symlinks=False)
        except OSError:
            warnings.append(f"Skipped unreadable context file: {relative}")
            continue
        if not stat.S_ISREG(metadata.st_mode) or is_reparse_point(path):
            warnings.append(f"Skipped link or reparse point: {relative}")
            continue
        size_bytes = metadata.st_size
        if size_bytes > max_bytes:
            warnings.append(f"Skipped oversized context file: {relative}")
            continue
        kind, scope = classification
        evidence.append(
            {
                "path": relative,
                "kind": kind,
                "scope": scope,
                "size_bytes": size_bytes,
            }
        )
    if truncated:
        warnings.append(f"Context evidence truncated at {max_files} files")
    return {"evidence": evidence, "truncated": truncated}

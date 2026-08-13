"""Project-local CSO configuration contract and safe persistence."""

from __future__ import annotations

import os
import secrets
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence

from .errors import OperationError, SecurityError, ValidationError
from .validation import IDENTIFIER_RE, canonical_json, is_reparse_point, load_json


CONFIG_VERSION = 1
CONFIG_DIRECTORY = ".cso"
CONFIG_FILENAME = "config.json"
CONFIG_KEYS = {"version", "profile", "skills", "analysis"}
ANALYSIS_KEYS = {"detected"}


def build_config(
    analysis: Mapping[str, Any],
    *,
    profile: str,
    recommendations: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    technologies = sorted({item["technology"] for item in analysis.get("detected", [])})
    skills = sorted({item["skill"] for item in recommendations})
    return {
        "version": CONFIG_VERSION,
        "profile": profile,
        "skills": skills,
        "analysis": {"detected": technologies},
    }


def _safe_config_path(project_root: Path, *, create_directory: bool) -> Path:
    lexical_root = project_root.expanduser().absolute()
    if is_reparse_point(lexical_root) or not lexical_root.is_dir():
        raise SecurityError("project root must be a regular directory")
    root = lexical_root.resolve(strict=True)
    config_directory = root / CONFIG_DIRECTORY
    if config_directory.exists() or is_reparse_point(config_directory):
        if is_reparse_point(config_directory) or not config_directory.is_dir():
            raise SecurityError(".cso must be a regular directory inside the project root")
    elif create_directory:
        config_directory.mkdir(mode=0o700)
    config_path = config_directory / CONFIG_FILENAME
    if config_path.exists() or is_reparse_point(config_path):
        if is_reparse_point(config_path) or not config_path.is_file():
            raise SecurityError(".cso/config.json must be a regular file")
    return config_path


def validate_config_document(
    document: Mapping[str, Any],
    *,
    profiles: Iterable[str] = (),
    registry_skills: Iterable[str] = (),
) -> None:
    if not isinstance(document, dict) or set(document) != CONFIG_KEYS:
        raise ValidationError("CSO configuration must contain only version, profile, skills, and analysis")
    if document["version"] != CONFIG_VERSION or isinstance(document["version"], bool):
        raise ValidationError("CSO configuration has unsupported version")
    profile = document["profile"]
    if not isinstance(profile, str) or not IDENTIFIER_RE.fullmatch(profile):
        raise ValidationError("CSO configuration profile must be a non-empty string")
    available_profiles = set(profiles)
    if available_profiles and profile not in available_profiles:
        raise ValidationError(f'profile: unknown profile "{profile}"')
    skills = document["skills"]
    if not isinstance(skills, list) or any(not isinstance(item, str) or not item for item in skills):
        raise ValidationError("CSO configuration skills must be a string array")
    if any(not IDENTIFIER_RE.fullmatch(item) for item in skills):
        raise ValidationError("CSO configuration skills contains an invalid identifier")
    if len(set(skills)) != len(skills):
        raise ValidationError("CSO configuration skills contains duplicates")
    available_skills = set(registry_skills)
    unknown = sorted(set(skills) - available_skills) if available_skills else []
    if unknown:
        raise ValidationError("skills: unknown registry skill " + ", ".join(unknown))
    analysis = document["analysis"]
    if not isinstance(analysis, dict) or set(analysis) != ANALYSIS_KEYS:
        raise ValidationError("CSO configuration analysis must contain only detected")
    detected = analysis["detected"]
    if not isinstance(detected, list) or any(not isinstance(item, str) or not item for item in detected):
        raise ValidationError("CSO configuration analysis.detected must be a string array")
    if any(not IDENTIFIER_RE.fullmatch(item) for item in detected):
        raise ValidationError("CSO configuration analysis.detected contains an invalid identifier")
    if len(set(detected)) != len(detected):
        raise ValidationError("CSO configuration analysis.detected contains duplicates")


def load_config(project_root: Path) -> Optional[Mapping[str, Any]]:
    path = _safe_config_path(project_root, create_directory=False)
    if not path.exists():
        return None
    return load_json(path)


def write_config(project_root: Path, document: Mapping[str, Any], *, force: bool = False) -> Path:
    validate_config_document(document)
    path = _safe_config_path(project_root, create_directory=True)
    if path.exists() and not force:
        raise OperationError(".cso/config.json already exists; run cso init --force to replace it")
    temporary = path.with_name(f".{CONFIG_FILENAME}.tmp-{secrets.token_hex(4)}")
    try:
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(canonical_json(document))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
            if os.name != "nt":
                directory_fd = os.open(str(path.parent), os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
        except OSError as exc:
            raise OperationError(f"cannot write .cso/config.json atomically: {exc}") from exc
    finally:
        if temporary.exists():
            temporary.unlink()
    return path

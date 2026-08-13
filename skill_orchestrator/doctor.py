"""Read-only structured health checks for CSO and a project."""

from __future__ import annotations

import platform
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List

from .config import load_config, validate_config_document
from .errors import OrchestratorError, ValidationError
from .validation import load_json, load_profiles, validate_registry


CHECKSUMMED_PAYLOADS = (
    "router/codex-skill-orchestrator/SKILL.md",
    "router/codex-skill-orchestrator/agents/openai.yaml",
    "router/codex-skill-orchestrator/references/profiles.md",
)


def _schema_check(path: Path, label: str) -> str:
    document = load_json(path)
    if not isinstance(document, dict) or document.get("type") != "object" or not document.get("$schema"):
        raise ValidationError(f"{label} is not a supported JSON schema")
    return f"{label} is available and valid JSON."


def _python_runtime_check() -> str:
    if sys.version_info < (3, 9):
        raise ValidationError("Python 3.9 or newer is required")
    return f"Python {sys.version_info.major}.{sys.version_info.minor} is supported."


def _registry_check(source_root: Path) -> str:
    validate_registry(source_root)
    return "Registry is valid."


def _profile_schema_check(source_root: Path) -> str:
    _schema_check(source_root / "profiles" / "schema.json", "Profile schema")
    profiles = load_profiles(source_root)
    return f"Profile schema and {len(profiles)} profiles are valid."


def _checksum_check(source_root: Path) -> str:
    document = load_json(source_root / "security" / "checksums.json")
    if not isinstance(document, dict) or document.get("algorithm") != "sha256":
        raise ValidationError("checksum manifest must use sha256")
    return "Checksum manifest uses SHA-256."


def _payload_check(source_root: Path) -> str:
    validate_registry(source_root)
    try:
        attributes = (source_root / ".gitattributes").read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValidationError("cannot read canonical EOL policy") from exc
    rules = set(attributes)
    missing = [path for path in CHECKSUMMED_PAYLOADS if f"{path} text eol=lf" not in rules]
    if missing:
        raise ValidationError("canonical LF policy missing for " + ", ".join(missing))
    return "Checksummed payloads and canonical LF policy are valid."


def _configuration_check(source_root: Path, project_root: Path) -> str:
    document = load_config(project_root)
    if document is None:
        return "No project configuration found (optional)."
    profiles = load_profiles(source_root)
    registry = validate_registry(source_root)
    validate_config_document(document, profiles=profiles, registry_skills=registry)
    return ".cso/config.json is valid."


def _capture(name: str, operation: Callable[[], str]) -> Dict[str, str]:
    try:
        message = operation()
    except (OrchestratorError, OSError, ValueError) as exc:
        return {"name": name, "status": "FAIL", "message": str(exc)}
    return {"name": name, "status": "PASS", "message": message}


def run_doctor(source_root: Path, project_root: Path) -> Dict[str, Any]:
    """Run deterministic, read-only health checks."""

    checks: List[Dict[str, str]] = []
    checks.append(_capture("python_runtime", _python_runtime_check))
    checks.append(_capture("registry", lambda: _registry_check(source_root)))
    checks.append(
        _capture("registry_schema", lambda: _schema_check(source_root / "registry" / "schema.json", "Registry schema"))
    )
    checks.append(_capture("profile_schema", lambda: _profile_schema_check(source_root)))
    checks.append(
        _capture(
            "config_schema",
            lambda: _schema_check(source_root / "schemas" / "cso-config.schema.json", "Configuration schema"),
        )
    )
    checks.append(_capture("checksums", lambda: _checksum_check(source_root)))
    checks.append(_capture("canonical_payload_integrity", lambda: _payload_check(source_root)))
    checks.append(_capture("configuration", lambda: _configuration_check(source_root, project_root)))
    checks.append(
        {
            "name": "platform",
            "status": "PASS",
            "message": f"Platform: {platform.system() or sys.platform}.",
        }
    )
    return {
        "schema_version": 1,
        "status": "healthy" if all(check["status"] == "PASS" for check in checks) else "unhealthy",
        "checks": checks,
    }

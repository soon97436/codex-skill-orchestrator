"""Strict schema, provenance, path, and checksum validation."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Tuple
from urllib.parse import urlparse

from .errors import IntegrityError, SecurityError, ValidationError


IDENTIFIER_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_PROFILES = {
    "universal",
    "economy",
    "deep-reasoning",
    "small-project",
    "large-project",
    "research",
    "security",
    "custom",
}

PROFILE_KEYS = {
    "schema_version",
    "id",
    "name",
    "aliases",
    "description",
    "extends",
    "policy",
    "routes",
}
POLICY_KEYS = {
    "mode",
    "reasoning_hint",
    "max_active_routes",
    "prefer_explicit_invocation",
}
ROUTE_KEYS = {"intent", "keywords", "capability_hints", "guidance", "priority"}
REGISTRY_ENTRY_KEYS = {
    "id",
    "name",
    "description",
    "version",
    "source",
    "license",
    "provenance",
    "files",
}
OPTIONAL_REGISTRY_ENTRY_KEYS = {"capabilities"}
CAPABILITY_SCOPES = {"project", "workspace", "unrestricted"}
NETWORK_MODES = {"none", "localhost", "restricted", "unrestricted", "unknown"}
PROCESS_MODES = {"none", "commands", "arbitrary", "unknown"}
MAX_CAPABILITY_SCOPES = 64
MAX_CAPABILITY_COMMANDS = 64
COMMAND_LITERAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")


def _reject_duplicate_keys(pairs: Iterable[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path) -> Any:
    try:
        raw = path.read_text(encoding="utf-8")
        return json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except UnicodeDecodeError as exc:
        raise ValidationError(f"invalid UTF-8: {path.name}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON in {path.name}: {exc.msg}") from exc
    except OSError as exc:
        raise ValidationError(f"cannot read required file: {path.name}") from exc


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_reparse_point(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def validate_relative_path(value: str, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\x00" in value or "\\" in value:
        raise SecurityError(f"{label} must be a non-empty POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise SecurityError(f"{label} contains an unsafe path segment")
    if any(":" in part for part in path.parts):
        raise SecurityError(f"{label} contains a drive or alternate-stream marker")
    return path


def safe_join(root: Path, relative: str, label: str = "path") -> Path:
    posix = validate_relative_path(relative, label)
    root_resolved = root.resolve(strict=False)
    candidate = root_resolved.joinpath(*posix.parts).resolve(strict=False)
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise SecurityError(f"{label} escapes its managed root") from exc
    return candidate


def tree_manifest(root: Path) -> Dict[str, str]:
    if not root.exists():
        return {}
    if not root.is_dir() or is_reparse_point(root):
        raise SecurityError("managed component is not a safe directory")
    manifest: Dict[str, str] = {}
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in list(dirs):
            child = current_path / name
            if is_reparse_point(child):
                raise SecurityError(f"reparse point rejected: {child.relative_to(root).as_posix()}")
        for name in files:
            child = current_path / name
            if is_reparse_point(child) or not child.is_file():
                raise SecurityError(f"non-regular file rejected: {child.relative_to(root).as_posix()}")
            relative = child.relative_to(root).as_posix()
            validate_relative_path(relative, "manifest path")
            manifest[relative] = sha256_file(child)
    return dict(sorted(manifest.items()))


def _expect_exact_keys(document: Mapping[str, Any], expected: set, label: str) -> None:
    if not isinstance(document, dict):
        raise ValidationError(f"{label} must be an object")
    missing = expected - set(document)
    extra = set(document) - expected
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if extra:
            details.append("unknown " + ", ".join(sorted(extra)))
        raise ValidationError(f"{label}: {'; '.join(details)}")


def _expect_required_optional_keys(
    document: Mapping[str, Any],
    required: set,
    optional: set,
    label: str,
) -> None:
    if not isinstance(document, dict):
        raise ValidationError(f"{label} must be an object")
    missing = required - set(document)
    extra = set(document) - required - optional
    if missing or extra:
        details = []
        if missing:
            details.append("missing " + ", ".join(sorted(missing)))
        if extra:
            details.append("unknown " + ", ".join(sorted(extra)))
        raise ValidationError(f"{label}: {'; '.join(details)}")


def _expect_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{label} must be a non-empty string")
    return value


def _expect_string_list(value: Any, label: str, allow_empty: bool = True) -> List[str]:
    if not isinstance(value, list) or (not allow_empty and not value):
        raise ValidationError(f"{label} must be a string array")
    if any(not isinstance(item, str) or not item for item in value):
        raise ValidationError(f"{label} must contain non-empty strings")
    if len(set(value)) != len(value):
        raise ValidationError(f"{label} contains duplicates")
    return list(value)


def normalize_profile_name(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")


def validate_profile_document(document: Mapping[str, Any], filename: str) -> None:
    _expect_exact_keys(document, PROFILE_KEYS, f"profile {filename}")
    if document["schema_version"] != 1:
        raise ValidationError(f"profile {filename} has unsupported schema_version")
    profile_id = _expect_string(document["id"], f"profile {filename} id")
    if not IDENTIFIER_RE.fullmatch(profile_id):
        raise ValidationError(f"profile {filename} has invalid id")
    _expect_string(document["name"], f"profile {profile_id} name")
    _expect_string(document["description"], f"profile {profile_id} description")
    aliases = _expect_string_list(document["aliases"], f"profile {profile_id} aliases")
    parents = _expect_string_list(document["extends"], f"profile {profile_id} extends")
    for alias in aliases + parents:
        if not IDENTIFIER_RE.fullmatch(alias):
            raise ValidationError(f"profile {profile_id} contains an invalid identifier")

    policy = document["policy"]
    _expect_exact_keys(policy, POLICY_KEYS, f"profile {profile_id} policy")
    if policy["mode"] not in {"balanced", "economy", "deep", "focused", "scaled", "research", "security", "custom"}:
        raise ValidationError(f"profile {profile_id} has invalid mode")
    if policy["reasoning_hint"] not in {"low", "medium", "high"}:
        raise ValidationError(f"profile {profile_id} has invalid reasoning_hint")
    if not isinstance(policy["max_active_routes"], int) or isinstance(policy["max_active_routes"], bool) or not 1 <= policy["max_active_routes"] <= 8:
        raise ValidationError(f"profile {profile_id} has invalid max_active_routes")
    if not isinstance(policy["prefer_explicit_invocation"], bool):
        raise ValidationError(f"profile {profile_id} has invalid invocation preference")

    routes = document["routes"]
    if not isinstance(routes, list):
        raise ValidationError(f"profile {profile_id} routes must be an array")
    seen_intents = set()
    for index, route in enumerate(routes):
        _expect_exact_keys(route, ROUTE_KEYS, f"profile {profile_id} route {index}")
        intent = _expect_string(route["intent"], f"profile {profile_id} route intent")
        if not IDENTIFIER_RE.fullmatch(intent) or intent in seen_intents:
            raise ValidationError(f"profile {profile_id} has invalid or duplicate route intent")
        seen_intents.add(intent)
        keywords = _expect_string_list(route["keywords"], f"route {intent} keywords", allow_empty=False)
        if any(keyword != keyword.lower() for keyword in keywords):
            raise ValidationError(f"route {intent} keywords must be lowercase")
        hints = _expect_string_list(route["capability_hints"], f"route {intent} capability_hints")
        if any(not IDENTIFIER_RE.fullmatch(hint) for hint in hints):
            raise ValidationError(f"route {intent} contains an invalid capability hint")
        _expect_string(route["guidance"], f"route {intent} guidance")
        if not isinstance(route["priority"], int) or isinstance(route["priority"], bool) or not 0 <= route["priority"] <= 100:
            raise ValidationError(f"route {intent} has invalid priority")


def load_profiles(project_root: Path) -> Dict[str, Dict[str, Any]]:
    profiles_dir = project_root / "profiles"
    profiles: Dict[str, Dict[str, Any]] = {}
    for path in sorted(profiles_dir.glob("*.json")):
        if path.name == "schema.json":
            continue
        document = load_json(path)
        validate_profile_document(document, path.name)
        profile_id = document["id"]
        if profile_id in profiles:
            raise ValidationError(f"duplicate profile id: {profile_id}")
        if path.stem != profile_id:
            raise ValidationError(f"profile filename must match id: {path.name}")
        profiles[profile_id] = document

    missing = REQUIRED_PROFILES - set(profiles)
    if missing:
        raise ValidationError("missing required profiles: " + ", ".join(sorted(missing)))

    aliases: Dict[str, str] = {}
    for profile_id, profile in profiles.items():
        values = [profile_id, profile["name"], *profile["aliases"]]
        for value in values:
            normalized = normalize_profile_name(value)
            owner = aliases.get(normalized)
            if owner is not None and owner != profile_id:
                raise ValidationError(f"profile alias collision: {normalized}")
            aliases[normalized] = profile_id
        for parent in profile["extends"]:
            if parent not in profiles:
                raise ValidationError(f"profile {profile_id} extends unknown profile {parent}")

    for profile_id in profiles:
        _resolve_profile(profile_id, profiles, [], {})
    return profiles


def _resolve_profile(
    profile_id: str,
    profiles: Mapping[str, Dict[str, Any]],
    stack: List[str],
    cache: MutableMapping[str, Dict[str, Any]],
) -> Dict[str, Any]:
    if profile_id in cache:
        return copy.deepcopy(cache[profile_id])
    if profile_id in stack:
        raise ValidationError("profile inheritance cycle: " + " -> ".join(stack + [profile_id]))
    stack.append(profile_id)
    current = profiles[profile_id]
    merged_routes: Dict[str, Dict[str, Any]] = {}
    merged_policy: Dict[str, Any] = {}
    lineage: List[str] = []
    for parent_id in current["extends"]:
        parent = _resolve_profile(parent_id, profiles, stack, cache)
        merged_policy.update(parent["policy"])
        for route in parent["routes"]:
            merged_routes[route["intent"]] = copy.deepcopy(route)
        lineage.extend(parent.get("resolved_from", [parent_id]))
    merged_policy.update(copy.deepcopy(current["policy"]))
    for route in current["routes"]:
        merged_routes[route["intent"]] = copy.deepcopy(route)
    stack.pop()
    resolved = copy.deepcopy(current)
    resolved["policy"] = merged_policy
    resolved["routes"] = sorted(merged_routes.values(), key=lambda item: (-item["priority"], item["intent"]))
    resolved["resolved_from"] = list(dict.fromkeys(lineage + [profile_id]))
    cache[profile_id] = resolved
    return copy.deepcopy(resolved)


def resolve_profile(project_root: Path, requested: str) -> Dict[str, Any]:
    profiles = load_profiles(project_root)
    lookup: Dict[str, str] = {}
    for profile_id, profile in profiles.items():
        for value in [profile_id, profile["name"], *profile["aliases"]]:
            lookup[normalize_profile_name(value)] = profile_id
    normalized = normalize_profile_name(requested)
    if normalized not in lookup:
        raise ValidationError(f"unknown profile: {requested}")
    return _resolve_profile(lookup[normalized], profiles, [], {})


def _validate_allowlist(document: Mapping[str, Any]) -> None:
    expected = {
        "schema_version",
        "allowed_source_types",
        "allowed_remote_hosts",
        "allowed_spdx_licenses",
        "require_checksums",
        "require_immutable_revision_for_remote",
        "deny_network_in_phase_1",
    }
    _expect_exact_keys(document, expected, "allowlist")
    if document["schema_version"] != 1:
        raise ValidationError("allowlist has unsupported schema_version")
    for key in ("allowed_source_types", "allowed_remote_hosts", "allowed_spdx_licenses"):
        _expect_string_list(document[key], f"allowlist {key}")
    for key in ("require_checksums", "require_immutable_revision_for_remote", "deny_network_in_phase_1"):
        if not isinstance(document[key], bool):
            raise ValidationError(f"allowlist {key} must be boolean")


def _validate_capabilities(document: Mapping[str, Any], skill_id: str) -> None:
    label = f"registry {skill_id} capabilities"
    _expect_exact_keys(
        document,
        {"schema_version", "filesystem", "network", "process"},
        label,
    )
    if document["schema_version"] != 1:
        raise ValidationError(f"{label} has unsupported schema_version")

    filesystem = document["filesystem"]
    _expect_exact_keys(filesystem, {"read", "write"}, f"{label} filesystem")
    for access in ("read", "write"):
        scopes = _expect_string_list(
            filesystem[access],
            f"{label} filesystem {access}",
        )
        if len(scopes) > MAX_CAPABILITY_SCOPES or any(
            scope not in CAPABILITY_SCOPES for scope in scopes
        ):
            raise ValidationError(f"{label} filesystem {access} has invalid scope")

    network = document["network"]
    _expect_exact_keys(network, {"mode"}, f"{label} network")
    if network["mode"] not in NETWORK_MODES:
        raise ValidationError(f"{label} network has invalid mode")

    process = document["process"]
    _expect_exact_keys(process, {"mode", "commands"}, f"{label} process")
    if process["mode"] not in PROCESS_MODES:
        raise ValidationError(f"{label} process has invalid mode")
    commands = _expect_string_list(process["commands"], f"{label} process commands")
    if len(commands) > MAX_CAPABILITY_COMMANDS or any(
        not COMMAND_LITERAL_RE.fullmatch(command) for command in commands
    ):
        raise ValidationError(f"{label} process has invalid command literal")
    if (process["mode"] == "commands") != bool(commands):
        raise ValidationError(f"{label} process mode and commands are inconsistent")


def validate_registry_snapshot(project_root: Path) -> Dict[str, Any]:
    registry = load_json(project_root / "registry" / "skills.json")
    _expect_exact_keys(registry, {"schema_version", "skills"}, "registry")
    if registry["schema_version"] != 1 or not isinstance(registry["skills"], list):
        raise ValidationError("registry has unsupported schema or invalid skills")
    allowlist = load_json(project_root / "security" / "allowlist.json")
    _validate_allowlist(allowlist)
    checksum_index = load_json(project_root / "security" / "checksums.json")
    _expect_exact_keys(checksum_index, {"schema_version", "algorithm", "bundles"}, "checksum index")
    if checksum_index["schema_version"] != 1 or checksum_index["algorithm"] != "sha256" or not isinstance(checksum_index["bundles"], dict):
        raise ValidationError("checksum index has unsupported format")

    seen_ids = set()
    seen_names = set()
    validated: Dict[str, Dict[str, Any]] = {}
    for entry in registry["skills"]:
        _expect_required_optional_keys(
            entry,
            REGISTRY_ENTRY_KEYS,
            OPTIONAL_REGISTRY_ENTRY_KEYS,
            "registry entry",
        )
        skill_id = _expect_string(entry["id"], "registry skill id")
        name = _expect_string(entry["name"], f"registry {skill_id} name")
        if not IDENTIFIER_RE.fullmatch(skill_id) or skill_id in seen_ids or name.casefold() in seen_names:
            raise ValidationError(f"invalid or duplicate registry skill: {skill_id}")
        seen_ids.add(skill_id)
        seen_names.add(name.casefold())
        _expect_string(entry["description"], f"registry {skill_id} description")
        if not isinstance(entry["version"], str) or not SEMVER_RE.fullmatch(entry["version"]):
            raise ValidationError(f"registry {skill_id} has invalid version")
        if "capabilities" in entry:
            _validate_capabilities(entry["capabilities"], skill_id)

        source = entry["source"]
        _expect_exact_keys(source, {"type", "path", "repository", "revision"}, f"registry {skill_id} source")
        source_type = source["type"]
        if source_type not in allowlist["allowed_source_types"]:
            raise SecurityError(f"registry {skill_id} source type is not allowlisted")
        source_relative = validate_relative_path(source["path"], f"registry {skill_id} source path")
        source_dir = safe_join(project_root, source_relative.as_posix(), f"registry {skill_id} source path")

        provenance = entry["provenance"]
        _expect_exact_keys(provenance, {"publisher", "maintainer", "third_party"}, f"registry {skill_id} provenance")
        _expect_string(provenance["publisher"], f"registry {skill_id} publisher")
        _expect_string(provenance["maintainer"], f"registry {skill_id} maintainer")
        if not isinstance(provenance["third_party"], bool):
            raise ValidationError(f"registry {skill_id} third_party must be boolean")

        if source_type == "bundled":
            if source["repository"] is not None or source["revision"] is not None or provenance["third_party"]:
                raise SecurityError(f"bundled registry skill {skill_id} has inconsistent provenance")
        else:
            if allowlist["deny_network_in_phase_1"]:
                raise SecurityError(f"network source rejected in Phase 1: {skill_id}")
            repository = _expect_string(source["repository"], f"registry {skill_id} repository")
            parsed = urlparse(repository)
            if parsed.scheme != "https" or parsed.hostname not in allowlist["allowed_remote_hosts"]:
                raise SecurityError(f"registry {skill_id} repository is not allowlisted")
            revision = _expect_string(source["revision"], f"registry {skill_id} revision")
            if allowlist["require_immutable_revision_for_remote"] and not COMMIT_RE.fullmatch(revision):
                raise SecurityError(f"registry {skill_id} revision is not immutable")

        license_info = entry["license"]
        _expect_exact_keys(license_info, {"spdx", "license_file", "source_url", "redistribution"}, f"registry {skill_id} license")
        spdx = _expect_string(license_info["spdx"], f"registry {skill_id} SPDX license")
        if spdx not in allowlist["allowed_spdx_licenses"] or license_info["redistribution"] is not True:
            raise SecurityError(f"registry {skill_id} license is not approved")
        license_path = safe_join(project_root, license_info["license_file"], f"registry {skill_id} license file")
        if not license_path.is_file() or is_reparse_point(license_path):
            raise SecurityError(f"registry {skill_id} license file is missing or unsafe")
        if source_type == "bundled" and license_info["source_url"] is not None:
            raise ValidationError(f"bundled registry skill {skill_id} must use the repository license")

        files = entry["files"]
        if not isinstance(files, list) or not files:
            raise ValidationError(f"registry {skill_id} files must be a non-empty array")
        file_manifest: Dict[str, str] = {}
        for file_entry in files:
            _expect_exact_keys(file_entry, {"path", "sha256"}, f"registry {skill_id} file")
            relative = validate_relative_path(file_entry["path"], f"registry {skill_id} file path").as_posix()
            digest = file_entry["sha256"]
            if relative in file_manifest or not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
                raise ValidationError(f"registry {skill_id} has invalid file manifest")
            source_file = safe_join(source_dir, relative, f"registry {skill_id} file path")
            if not source_file.is_file() or is_reparse_point(source_file):
                raise SecurityError(f"registry {skill_id} file is missing or unsafe: {relative}")
            actual = sha256_file(source_file)
            if actual != digest:
                raise IntegrityError(f"registry checksum mismatch: {skill_id}/{relative}")
            file_manifest[relative] = digest

        actual_manifest = tree_manifest(source_dir)
        if actual_manifest != dict(sorted(file_manifest.items())):
            raise IntegrityError(f"registry file list is incomplete for {skill_id}")
        bundle_key = f"{skill_id}@{entry['version']}"
        indexed = checksum_index["bundles"].get(bundle_key)
        if indexed != dict(sorted(file_manifest.items())):
            raise IntegrityError(f"checksum index mismatch for {bundle_key}")
        validated[skill_id] = copy.deepcopy(entry)

    return {
        "schema_version": 1,
        "registry": copy.deepcopy(validated),
        "policy": copy.deepcopy(allowlist),
    }


def validate_registry(project_root: Path) -> Dict[str, Any]:
    """Validate the registry and return its existing public mapping."""

    return validate_registry_snapshot(project_root)["registry"]


def validate_project(project_root: Path) -> Dict[str, Any]:
    profiles = load_profiles(project_root)
    registry = validate_registry(project_root)
    return {"profiles": profiles, "registry": registry}

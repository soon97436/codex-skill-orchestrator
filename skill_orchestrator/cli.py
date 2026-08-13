"""Command-line interface for the Phase 1 orchestrator."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from . import __version__
from .config import build_config, write_config
from .doctor import run_doctor
from .engine import (
    apply_profile,
    audit,
    default_install_root,
    default_skills_dir,
    plan_install,
    profile_catalog,
    project_root,
    rollback,
    route_task,
)
from .errors import OrchestratorError
from .recommendations import analyze_and_recommend


def _add_roots(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--install-root", type=Path, default=default_install_root(), help="Managed app and state root")
    parser.add_argument("--skills-dir", type=Path, default=default_skills_dir(), help="Host skill directory")


def _add_json(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="Emit stable JSON output")


def _add_project_root(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Project to inspect (default: current directory)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cso",
        description="Install one lightweight skill router and switch audited work profiles.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze = subparsers.add_parser("analyze", help="Analyze a project without modifying it")
    _add_project_root(analyze)
    _add_json(analyze)

    init = subparsers.add_parser("init", help="Create a reviewed project-local CSO configuration")
    _add_project_root(init)
    init.add_argument("--yes", action="store_true", help="Accept recommendations without prompting")
    init.add_argument("--force", action="store_true", help="Replace an existing safe .cso/config.json")

    doctor = subparsers.add_parser("doctor", help="Run read-only CSO and environment health checks")
    _add_project_root(doctor)

    profiles = subparsers.add_parser("profiles", help="List validated profiles")
    _add_json(profiles)

    plan = subparsers.add_parser("plan", help="Print a deterministic installation plan")
    plan.add_argument("--profile", default="universal")
    _add_roots(plan)
    _add_json(plan)

    install = subparsers.add_parser("install", help="Install the app and router profile")
    install.add_argument("--profile", default="universal")
    install.add_argument("--dry-run", action="store_true")
    _add_roots(install)
    _add_json(install)

    activate = subparsers.add_parser("activate", help="Switch the installed router profile")
    activate.add_argument("--profile", required=True)
    activate.add_argument("--dry-run", action="store_true")
    _add_roots(activate)
    _add_json(activate)

    audit_parser = subparsers.add_parser("audit", help="Read-only integrity and policy audit")
    _add_roots(audit_parser)
    _add_json(audit_parser)

    rollback_parser = subparsers.add_parser("rollback", help="Restore the previous managed transaction")
    rollback_parser.add_argument("--dry-run", action="store_true")
    _add_roots(rollback_parser)
    _add_json(rollback_parser)

    route = subparsers.add_parser("route", help="Preview deterministic task routing")
    route.add_argument("--profile", default="universal")
    route.add_argument("--task", required=True)
    _add_json(route)
    return parser


def _human_output(document: Any) -> str:
    if isinstance(document, list):
        return "\n".join(f"{item['id']}: {item['name']} — {item['description']}" for item in document)
    if isinstance(document, dict) and document.get("command") in {"install", "activate", "rollback"}:
        lines = [f"{document['command'].upper()}: {'DRY RUN' if document.get('dry_run') else 'OK'}"]
        if document.get("profile"):
            lines.append(f"Profile: {document['profile']}")
        for action in document.get("actions", []):
            target = action.get("target", action.get("profile", ""))
            lines.append(f"- {action['action'].upper()} {target}".rstrip())
        if "changed" in document:
            lines.append(f"Changed: {'yes' if document['changed'] else 'no'}")
        return "\n".join(lines)
    if isinstance(document, dict) and "selected_routes" in document:
        lines = [f"Profile: {document['profile']}"]
        if not document["selected_routes"]:
            lines.append("- No profile route matched; use host built-ins.")
        for route in document["selected_routes"]:
            hints = ", ".join(route["capability_hints"]) or "host-builtins"
            lines.append(f"- {route['intent']}: {hints}")
        return "\n".join(lines)
    if isinstance(document, dict) and "status" in document:
        lines = [f"Audit: {document['status']}", f"Installation: {document['installation']}"]
        for finding in document.get("findings", []):
            lines.append(f"- {finding['code']}: {finding['message']}")
        return "\n".join(lines)
    return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)


def _human_analysis(document: Mapping[str, Any]) -> str:
    lines = ["CSO Project Analysis", "", "Detected:"]
    if document["detected"]:
        for finding in document["detected"]:
            lines.append(f"  {finding['technology']:<20} yes")
    else:
        lines.append("  No supported technology markers detected.")
    project = document["project"]
    lines.extend(
        [
            "",
            "Project:",
            f"  Files analyzed: {project['files_analyzed']}",
            f"  Project size: {project['size']}",
        ]
    )
    if project["truncated"]:
        lines.append("  Warning: analysis was truncated at the configured traversal limit.")
    lines.extend(["", "Context evidence:"])
    context = document["context"]
    if context["evidence"]:
        for evidence in context["evidence"]:
            lines.append(
                f"  {evidence['path']} ({evidence['kind']}; scope: {evidence['scope']})"
            )
    else:
        lines.append("  No known agent context files discovered.")
    if context["truncated"]:
        lines.append("  Warning: context discovery was truncated.")
    lines.extend(["", "Recommended profile:", f"  {document['recommended_profile']}"])
    lines.extend(["", "Recommended skills:"])
    if document["recommended_skills"]:
        for recommendation in document["recommended_skills"]:
            lines.append(f"  {recommendation['skill']} (score: {recommendation['score']})")
            for reason in recommendation["reasons"]:
                lines.append(f"    reason: {reason}")
    else:
        lines.append("  No matching skills are present in the validated registry.")
    for warning in document.get("warnings", []):
        lines.append(f"Warning: {warning}")
    lines.extend(
        [
            "",
            "Security:",
            "  No third-party code downloaded.",
            "  No project files modified.",
        ]
    )
    return "\n".join(lines)


def _human_doctor(document: Mapping[str, Any]) -> str:
    lines = ["CSO Doctor", ""]
    for check in document["checks"]:
        label = check["name"].replace("_", " ").title()
        lines.append(f"{label:<28} {check['status']}")
        lines.append(f"  {check['message']}")
        if check["name"] == "configuration" and check["status"] == "FAIL":
            lines.append("  Run: cso init --force")
    lines.extend(["", "Environment healthy." if document["status"] == "healthy" else "Environment needs attention."])
    return "\n".join(lines)


def _emit(document: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(_human_output(document))


def _stdin_is_interactive(stream: Any) -> bool:
    if not stream.isatty():
        return False
    try:
        descriptor = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return True
    try:
        os.get_terminal_size(descriptor)
    except (OSError, ValueError):
        return False
    return True


def run(args: argparse.Namespace) -> int:
    as_json = bool(getattr(args, "json", False))
    if args.command == "analyze":
        result = analyze_and_recommend(args.project_root, project_root())
        if as_json:
            _emit(result, True)
        else:
            print(_human_analysis(result))
        return 0
    if args.command == "init":
        result = analyze_and_recommend(args.project_root, project_root())
        print(_human_analysis(result))
        if not args.yes:
            if not _stdin_is_interactive(sys.stdin):
                print("Initialization requires an interactive terminal. Run: cso init --yes", file=sys.stderr)
                return 1
            answer = input("Use this configuration? [Y/n] ").strip().casefold()
            if answer not in {"", "y", "yes"}:
                print("Initialization cancelled. No files were modified.")
                return 0
        document = build_config(
            result,
            profile=result["recommended_profile"],
            recommendations=result["recommended_skills"],
        )
        write_config(args.project_root, document, force=args.force)
        print("Configuration written: .cso/config.json")
        return 0
    if args.command == "doctor":
        result = run_doctor(project_root(), args.project_root)
        print(_human_doctor(result))
        return 0 if result["status"] == "healthy" else 1
    if args.command == "profiles":
        result: Any = profile_catalog()
    elif args.command == "plan":
        result = plan_install(args.profile, args.install_root, args.skills_dir)
    elif args.command == "install":
        result = apply_profile(
            args.profile,
            args.install_root,
            args.skills_dir,
            include_app=True,
            dry_run=args.dry_run,
        )
    elif args.command == "activate":
        result = apply_profile(
            args.profile,
            args.install_root,
            args.skills_dir,
            include_app=False,
            dry_run=args.dry_run,
        )
    elif args.command == "audit":
        result = audit(args.install_root, args.skills_dir)
        _emit(result, as_json)
        return 0 if result["status"] == "clean" else 4
    elif args.command == "rollback":
        result = rollback(args.install_root, args.skills_dir, dry_run=args.dry_run)
    elif args.command == "route":
        result = route_task(args.task, args.profile)
    else:
        raise AssertionError(f"unhandled command: {args.command}")
    _emit(result, as_json)
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        return run(args)
    except OrchestratorError as exc:
        exit_code = 1 if args.command in {"analyze", "init", "doctor"} else exc.exit_code
        print(f"ERROR[{exit_code}]: {exc}", file=sys.stderr)
        return exit_code
    except KeyboardInterrupt:
        print("ERROR[130]: interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

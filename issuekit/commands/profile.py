"""Implementation of the project profile command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from issuekit.commands._common import run_command
from issuekit.config import load_config
from issuekit.project_profile import ProjectProfile, load_project_profile
from issuekit.proposals_api import api_client
from issuekit.workflow import WorkflowError


def register(subparsers: argparse._SubParsersAction) -> None:
    profile_parser = subparsers.add_parser(
        "profile",
        help="Show the local project profile and, when available, stored remote ones.",
    )
    profile_parser.add_argument(
        "--project",
        help="Fetch the stored profile for this project instead of the local one.",
    )
    profile_parser.add_argument(
        "--all",
        action="store_true",
        help="List every stored remote project profile (the PM router's input).",
    )
    profile_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    profile_parser.set_defaults(func=run)


def run(args) -> int:
    cwd = Path.cwd()
    config = load_config(cwd)

    def action() -> int:
        if args.all:
            return _run_all(config, json_out=args.json)
        if args.project:
            return _run_remote(config, args.project, json_out=args.json)
        return _run_local(config, cwd, json_out=args.json)

    return run_command(
        action,
        errors=(FileNotFoundError, RuntimeError, ValueError, WorkflowError),
    )


def _run_all(config, *, json_out: bool) -> int:
    with api_client(config) as client:
        profiles = client.list_project_profiles()
    if json_out:
        print(json.dumps(profiles, indent=2))
        return 0
    if not profiles:
        print("No stored project profiles.")
        return 0
    for profile in profiles:
        tags = ", ".join(profile.get("tags") or []) or "-"
        print(f"{profile.get('project', '?')}\t{tags}\t{profile.get('summary', '')}")
    return 0


def _run_remote(config, project: str, *, json_out: bool) -> int:
    with api_client(config) as client:
        profile = client.get_project_profile(project)
    if json_out:
        print(json.dumps(profile, indent=2))
        return 0
    _print_remote(profile)
    return 0


def _run_local(config, cwd: Path, *, json_out: bool) -> int:
    local = load_project_profile(config, cwd)
    remote = _try_fetch_remote(config)
    if json_out:
        print(
            json.dumps(
                {
                    "local": local.to_payload() if local is not None else None,
                    "remote": remote,
                },
                indent=2,
            )
        )
        return 0
    if local is None:
        print(f"No local project profile ({config.profile_file} not found).")
    else:
        _print_local(config, local)
    if remote is not None:
        print("")
        print("Stored remote profile:")
        _print_remote(remote)
    return 0


def _try_fetch_remote(config) -> dict | None:
    if not config.api_url:
        return None
    try:
        with api_client(config) as client:
            return client.get_project_profile()
    except WorkflowError:
        # A backend without mine-py#172 (404/405) or no stored profile yet: the
        # local profile is still shown; the remote is simply unavailable.
        return None


def _print_local(config, profile: ProjectProfile) -> None:
    print(f"Local project profile ({config.profile_file}):")
    print(f"  summary: {profile.summary or '(none)'}")
    print(f"  tags: {', '.join(profile.tags) or '(none)'}")
    print(f"  source_commit: {profile.source_commit or '(uncommitted)'}")
    print(f"  source_committed_at: {profile.source_committed_at or '(unknown)'}")
    print(f"  profile_md: {len(profile.profile_md)} chars")


def _print_remote(profile: dict) -> None:
    print(f"  project: {profile.get('project', '?')}")
    print(f"  summary: {profile.get('summary') or '(none)'}")
    print(f"  tags: {', '.join(profile.get('tags') or []) or '(none)'}")
    print(f"  source_commit: {profile.get('source_commit') or '(unknown)'}")

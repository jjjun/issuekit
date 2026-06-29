"""Implementation of API login/logout commands."""

from __future__ import annotations

from datetime import datetime, timezone
import getpass
import os
from pathlib import Path
import sys

from issuekit.client import IssuekitClient
from issuekit.config import load_config
from issuekit.workflow import WorkflowError


def run_login(args) -> int:
    try:
        config = load_config(Path.cwd())
        username = args.user or os.getenv("ISSUEKIT_API_USER")
        password = os.getenv("ISSUEKIT_API_PASSWORD")
        if not username and sys.stdin.isatty():
            username = input("Issuekit API username: ").strip()
        if not username:
            print("Error: API username is required; pass --user or set ISSUEKIT_API_USER.", file=sys.stderr)
            return 1
        if not password and sys.stdin.isatty():
            password = getpass.getpass("Issuekit API password: ")
        if not password:
            print(
                "Error: API password is required; set ISSUEKIT_API_PASSWORD or run from a TTY.",
                file=sys.stderr,
            )
            return 1
        if not config.api_url:
            print("Error: API URL is required; set api_url or ISSUEKIT_API_URL.", file=sys.stderr)
            return 1
        with IssuekitClient(
            config.api_url,
            project=config.project,
            timeout=config.api_timeout,
            username=username,
            password=password,
            use_env_token=False,
        ) as client:
            client.login(force=True)
            expiry = _format_expiry(client.token_expiry)
    except (WorkflowError, ValueError) as exc:
        print(f"Error: login failed: {exc}", file=sys.stderr)
        return 1

    print(f"Logged in to {config.api_url.rstrip('/')} as {username}; token expires {expiry}.")
    return 0


def run_logout(_args) -> int:
    try:
        config = load_config(Path.cwd())
        if not config.api_url:
            print("Error: API URL is required; set api_url or ISSUEKIT_API_URL.", file=sys.stderr)
            return 1
        with IssuekitClient(
            config.api_url,
            project=config.project,
            timeout=config.api_timeout,
            use_env_token=False,
        ) as client:
            client.logout()
    except (WorkflowError, ValueError) as exc:
        print(f"Error: logout failed: {exc}", file=sys.stderr)
        return 1

    print(f"Logged out of {config.api_url.rstrip('/')}.")
    return 0


def _format_expiry(expiry: float | None) -> str:
    if expiry is None:
        return "at an unknown time"
    return datetime.fromtimestamp(expiry, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

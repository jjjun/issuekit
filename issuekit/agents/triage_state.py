"""State persistence helpers for triage-author proposal decisions."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

from issuekit.agentrun.run_dir import prepare_run_dir
from issuekit.file_permissions import write_owner_only_text
from issuekit.timestamps import utc_now_iso

STATE_FILENAME = "triage-author-state.json"


def now() -> str:
    return utc_now_iso()


def state_path(cwd: Path) -> Path:
    return cwd / ".agent-runs" / STATE_FILENAME


def load_state(cwd: Path) -> dict[str, dict[str, str]]:
    prepare_run_dir(cwd)
    path = state_path(cwd)
    if path.is_symlink():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    state: dict[str, dict[str, str]] = {}
    for key, value in raw.items():
        if not isinstance(value, dict):
            continue
        fingerprint = value.get("fingerprint")
        body_sha = value.get("body_sha")
        if not isinstance(fingerprint, str) and not isinstance(body_sha, str):
            continue
        entry = {"replied_at": str(value.get("replied_at", ""))}
        if isinstance(value.get("suppressed_at"), str):
            entry["suppressed_at"] = value["suppressed_at"]
        if isinstance(fingerprint, str):
            entry["fingerprint"] = fingerprint
        if isinstance(body_sha, str):
            entry["body_sha"] = body_sha
        state[str(key)] = entry
    return state


def save_state(cwd: Path, state: Mapping[str, Mapping[str, str]]) -> None:
    path = state_path(cwd)
    prepare_run_dir(cwd)
    serialized = json.dumps(dict(state), indent=2, sort_keys=True)
    try:
        if not path.is_symlink() and path.read_text(encoding="utf-8") == serialized:
            return
    except OSError:
        pass
    write_owner_only_text(path, serialized)

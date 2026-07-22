"""State persistence helpers for triage-author proposal decisions."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
from pathlib import Path


STATE_FILENAME = "triage-author-state.json"


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def state_path(cwd: Path) -> Path:
    return cwd / ".agent-runs" / STATE_FILENAME


def load_state(cwd: Path) -> dict[str, dict[str, str]]:
    path = state_path(cwd)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    state: dict[str, dict[str, str]] = {}
    for key, value in raw.items():
        if isinstance(value, dict) and isinstance(value.get("body_sha"), str):
            state[str(key)] = {
                "body_sha": value["body_sha"],
                "replied_at": str(value.get("replied_at", "")),
            }
    return state


def save_state(cwd: Path, state: Mapping[str, Mapping[str, str]]) -> None:
    path = state_path(cwd)
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        json.dumps(dict(state), indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )

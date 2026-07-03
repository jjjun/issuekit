"""State persistence helpers for the PM request command."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any


STATE_FILENAME = "pm-requests.json"


def _state_path(cwd: Path) -> Path:
    return cwd / ".agent-runs" / STATE_FILENAME


def _load_state(cwd: Path) -> dict[str, dict[str, Any]]:
    path = _state_path(cwd)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    state: dict[str, dict[str, Any]] = {}
    for key, value in raw.items():
        if not str(key).isdigit() or not isinstance(value, dict):
            continue
        state[str(int(key))] = dict(value)
    return state


def _save_state(cwd: Path, state: dict[str, dict[str, Any]]) -> None:
    path = _state_path(cwd)
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        json.dumps(state, indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()

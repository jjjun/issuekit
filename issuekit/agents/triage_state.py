"""State persistence helpers for triage-author proposal decisions."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile


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
        if not isinstance(value, dict):
            continue
        fingerprint = value.get("fingerprint")
        body_sha = value.get("body_sha")
        if not isinstance(fingerprint, str) and not isinstance(body_sha, str):
            continue
        entry = {"replied_at": str(value.get("replied_at", ""))}
        if isinstance(fingerprint, str):
            entry["fingerprint"] = fingerprint
        if isinstance(body_sha, str):
            entry["body_sha"] = body_sha
        state[str(key)] = entry
    return state


def save_state(cwd: Path, state: Mapping[str, Mapping[str, str]]) -> None:
    path = state_path(cwd)
    path.parent.mkdir(exist_ok=True)
    serialized = json.dumps(dict(state), indent=2, sort_keys=True)
    try:
        if path.read_text(encoding="utf-8") == serialized:
            return
    except OSError:
        pass
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(serialized)
            temporary_path = Path(temporary.name)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass

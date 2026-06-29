"""Minimal .env loading for repo-local issuekit configuration."""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(cwd: Path | str = ".") -> None:
    """Load environment variables from ``<cwd>/.env`` without overriding env."""
    dotenv_path = Path(cwd) / ".env"
    try:
        lines = dotenv_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return

    for line in lines:
        parsed = _parse_dotenv_line(line)
        if parsed is None:
            continue
        key, value = parsed
        os.environ.setdefault(key, value)


def _parse_dotenv_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].strip()
        if "=" not in stripped:
            return None

    key, value = stripped.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        return None
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return key, value

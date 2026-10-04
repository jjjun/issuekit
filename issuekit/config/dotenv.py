"""Minimal .env loading for repo-local issuekit configuration."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ISSUEKIT_PREFIX = "ISSUEKIT_"
_ALLOWED_DOTENV_KEYS = {
    "ISSUEKIT_API_URL",
    "ISSUEKIT_API_USER",
    "ISSUEKIT_API_PASSWORD",
    "ISSUEKIT_API_TOKEN",
    "ISSUEKIT_PROJECT",
    "ISSUEKIT_API_TIMEOUT",
}
_SENSITIVE_DOTENV_KEYS = {
    "ISSUEKIT_API_URL",
    "ISSUEKIT_API_USER",
    "ISSUEKIT_API_PASSWORD",
    "ISSUEKIT_API_TOKEN",
}
_LOADED_DOTENV_VALUES: dict[str, str] = {}
_LOADED_DOTENV_PATHS: dict[str, Path] = {}
_DOTENV_FILE_SIGNATURES: dict[Path, tuple[int, int] | None] = {}
_IGNORED_DOTENV_NOTICES: set[tuple[Path, str]] = set()


def load_dotenv(cwd: Path | str = ".") -> None:
    """Load environment variables from ``<cwd>/.env`` without overriding env."""
    dotenv_path = Path(cwd) / ".env"
    resolved_path = dotenv_path.resolve()
    try:
        stat = dotenv_path.stat()
        signature = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        signature = None
    previous_signature = _DOTENV_FILE_SIGNATURES.get(resolved_path)
    if resolved_path in _DOTENV_FILE_SIGNATURES and previous_signature != signature:
        for key, source_path in tuple(_LOADED_DOTENV_PATHS.items()):
            if source_path != resolved_path:
                continue
            if os.environ.get(key) == _LOADED_DOTENV_VALUES.get(key):
                os.environ.pop(key, None)
            _LOADED_DOTENV_PATHS.pop(key, None)
            _LOADED_DOTENV_VALUES.pop(key, None)
    _DOTENV_FILE_SIGNATURES[resolved_path] = signature
    try:
        lines = dotenv_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return

    for line in lines:
        parsed = _parse_dotenv_line(line)
        if parsed is None:
            continue
        key, value = parsed
        if not key.startswith(_ISSUEKIT_PREFIX):
            continue
        if key not in _ALLOWED_DOTENV_KEYS:
            notice_key = (dotenv_path.resolve(), key)
            if notice_key not in _IGNORED_DOTENV_NOTICES:
                _IGNORED_DOTENV_NOTICES.add(notice_key)
                print(
                    f"Notice: ignored {key} from repo-local dotenv file {dotenv_path}; "
                    "set it in the process environment instead.",
                    file=sys.stderr,
                )
            continue
        if os.environ.get(key, ""):
            continue
        os.environ[key] = value
        _LOADED_DOTENV_VALUES[key] = value
        _LOADED_DOTENV_PATHS[key] = resolved_path
        if key in _SENSITIVE_DOTENV_KEYS:
            print(
                f"Notice: loaded {key} from repo-local dotenv file {dotenv_path}.",
                file=sys.stderr,
            )


def is_loaded_from_dotenv(key: str) -> bool:
    """Return whether the current value was injected from a repo-local dotenv file."""
    return key in _LOADED_DOTENV_VALUES and os.environ.get(key) == _LOADED_DOTENV_VALUES[key]


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

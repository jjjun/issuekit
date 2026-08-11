"""Token cache persistence for the issuekit API client."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from issuekit.file_permissions import chmod_600, ensure_owner_only_directory, open_owner_only
from issuekit.workflow import WorkflowError

from .security import is_expired, jwt_expiry

_TOKEN_CACHE_ENV = "ISSUEKIT_TOKEN_CACHE"
_WARNED_LOOSE_TOKEN_CACHE_PATHS: set[Path] = set()


def _token_cache_path() -> Path:
    override = os.getenv(_TOKEN_CACHE_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".issuekit" / "token.json"


def read_cached_token(api_url: str) -> dict[str, Any] | None:
    entry = _read_token_cache().get(api_url)
    if not isinstance(entry, dict):
        return None
    token = entry.get("token")
    expires_at = entry.get("expires_at")
    if not isinstance(token, str) or not token:
        return None
    if expires_at is not None and not isinstance(expires_at, (int, float)):
        return None
    expiry = float(expires_at) if isinstance(expires_at, (int, float)) else jwt_expiry(token)
    if is_expired(expiry):
        return None
    return {"token": token, "expires_at": expiry}


def cached_token_miss_message(api_url: str) -> str | None:
    cached_urls = sorted(
        url
        for url in _read_token_cache()
        if isinstance(url, str) and url != api_url
    )
    if not cached_urls:
        return None
    return (
        f"no cached token for {api_url} (cached: {', '.join(cached_urls)}); "
        "re-run `issuekit login` with ISSUEKIT_API_URL set to the URL this client uses"
    )


def write_cached_token(api_url: str, token: str, expires_at: float | None) -> None:
    cache = _read_token_cache()
    cache[api_url] = {"token": token, "expires_at": expires_at}
    _write_token_cache(cache)


def delete_cached_token(api_url: str) -> None:
    path = _token_cache_path()
    cache = _read_token_cache()
    if api_url not in cache:
        return
    del cache[api_url]
    if cache:
        _write_token_cache(cache)
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise WorkflowError(f"Failed to remove API token cache: {exc}", code="token_cache_error") from exc


def _read_token_cache() -> dict[str, Any]:
    path = _token_cache_path()
    _warn_if_token_cache_is_loose(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError:
        return {}
    try:
        payload = json.loads(raw)
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _write_token_cache(cache: Mapping[str, Any]) -> None:
    path = _token_cache_path()
    try:
        ensure_owner_only_directory(path.parent)
    except OSError as exc:
        raise WorkflowError(f"Failed to create API token cache directory: {exc}", code="token_cache_error") from exc

    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = json.dumps(dict(cache), sort_keys=True, separators=(",", ":")) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    try:
        fd = open_owner_only(
            temp_path,
            flags,
            warn=_warn_token_cache_permissions,
            windows_acl=True,
        )
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
        os.replace(temp_path, path)
        chmod_600(path, warn=_warn_token_cache_permissions, windows_acl=True)
    except OSError as exc:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise WorkflowError(f"Failed to write API token cache: {exc}", code="token_cache_error") from exc


def _warn_if_token_cache_is_loose(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        mode = path.stat().st_mode
    except OSError:
        return
    if mode & 0o044 == 0:
        return
    resolved = path.resolve()
    if resolved in _WARNED_LOOSE_TOKEN_CACHE_PATHS:
        return
    _WARNED_LOOSE_TOKEN_CACHE_PATHS.add(resolved)
    _warn_token_cache_permissions(
        path,
        "cache file is group/other-readable; run `chmod 600` on the file",
    )


def _warn_token_cache_permissions(path: Path, reason: str) -> None:
    print(
        f"Warning: could not restrict API token cache permissions for {path}: {reason}",
        file=sys.stderr,
    )

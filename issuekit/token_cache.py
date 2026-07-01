"""Token cache persistence for the issuekit API client."""

from __future__ import annotations

from collections.abc import Mapping
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any

from issuekit.client_security import _is_expired, _jwt_expiry
from issuekit.workflow import WorkflowError


_TOKEN_CACHE_ENV = "ISSUEKIT_TOKEN_CACHE"


def _token_cache_path() -> Path:
    override = os.getenv(_TOKEN_CACHE_ENV)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".issuekit" / "token.json"


def _read_cached_token(api_url: str) -> dict[str, Any] | None:
    entry = _read_token_cache().get(api_url)
    if not isinstance(entry, dict):
        return None
    token = entry.get("token")
    expires_at = entry.get("expires_at")
    if not isinstance(token, str) or not token:
        return None
    if expires_at is not None and not isinstance(expires_at, (int, float)):
        return None
    expiry = float(expires_at) if isinstance(expires_at, (int, float)) else _jwt_expiry(token)
    if _is_expired(expiry):
        return None
    return {"token": token, "expires_at": expiry}


def _write_cached_token(api_url: str, token: str, expires_at: float | None) -> None:
    cache = _read_token_cache()
    cache[api_url] = {"token": token, "expires_at": expires_at}
    _write_token_cache(cache)


def _delete_cached_token(api_url: str) -> None:
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
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise WorkflowError(f"Failed to create API token cache directory: {exc}", code="token_cache_error") from exc

    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    data = json.dumps(dict(cache), sort_keys=True, separators=(",", ":")) + "\n"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_BINARY"):
        flags |= os.O_BINARY
    try:
        fd = os.open(temp_path, flags, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(data)
        _chmod_600(temp_path)
        os.replace(temp_path, path)
        _chmod_600(path)
    except OSError as exc:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise WorkflowError(f"Failed to write API token cache: {exc}", code="token_cache_error") from exc


def _chmod_600(path: Path) -> None:
    if os.name == "nt":
        _restrict_windows_acl(path)
        return
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _restrict_windows_acl(path: Path) -> None:
    user = os.getenv("USERNAME")
    if not user:
        _warn_token_cache_permissions(path, "current Windows user could not be determined")
        return
    try:
        result = subprocess.run(
            [
                "icacls",
                str(path),
                "/inheritance:r",
                "/grant:r",
                f"{user}:F",
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        _warn_token_cache_permissions(path, str(exc))
        return
    if result.returncode != 0:
        reason = result.stderr.strip() or f"icacls exited with {result.returncode}"
        _warn_token_cache_permissions(path, reason)


def _warn_token_cache_permissions(path: Path, reason: str) -> None:
    print(
        f"Warning: could not restrict API token cache permissions for {path}: {reason}",
        file=sys.stderr,
    )

"""Security-related helpers for the issuekit API client."""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import sys
import time
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlparse

_ALLOW_INSECURE_ENV = "ISSUEKIT_ALLOW_INSECURE"
_WARNED_INSECURE_API_URLS: set[str] = set()


def is_expired(expiry: float | None) -> bool:
    return expiry is not None and expiry <= time.time() + 30


def warn_insecure_api_url(api_url: str) -> None:
    if _env_flag_enabled(_ALLOW_INSECURE_ENV):
        return
    if not _is_insecure_remote_url(api_url):
        return
    if api_url in _WARNED_INSECURE_API_URLS:
        return
    _WARNED_INSECURE_API_URLS.add(api_url)
    print(
        "Warning: ISSUEKIT API URL uses non-HTTPS transport; service-account "
        "credentials and bearer tokens will be sent in cleartext. Use HTTPS or "
        f"set {_ALLOW_INSECURE_ENV}=1 to suppress this warning for a trusted endpoint.",
        file=sys.stderr,
    )


def _is_insecure_remote_url(api_url: str) -> bool:
    parsed = urlparse(api_url)
    if parsed.scheme.lower() != "http":
        return False
    host = (parsed.hostname or "").lower()
    if host == "localhost":
        return False
    try:
        return not ipaddress.ip_address(host).is_loopback
    except ValueError:
        return True


def _env_flag_enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def response_expiry(payload: Mapping[str, Any]) -> float | None:
    for key in ("expires_at", "expires"):
        raw = payload.get(key)
        if isinstance(raw, (int, float)):
            return float(raw)
    raw_expires_in = payload.get("expires_in")
    if isinstance(raw_expires_in, (int, float)):
        return time.time() + float(raw_expires_in)
    return None


def jwt_expiry(token: str | None) -> float | None:
    if not token:
        return None
    parts = token.split(".")
    if len(parts) < 2:
        return None
    try:
        payload = json.loads(_b64url_decode(parts[1]).decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    exp = payload.get("exp")
    return float(exp) if isinstance(exp, (int, float)) else None


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))

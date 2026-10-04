"""Shared URL helpers."""

from __future__ import annotations

from urllib.parse import urlparse


def api_url_origin(api_url: str) -> str | None:
    """Return the API URL origin without userinfo, path, query, or fragment."""
    try:
        parsed = urlparse(api_url)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if not parsed.scheme or not hostname:
        return None
    host = f"[{hostname}]" if ":" in hostname else hostname
    netloc = host if port is None else f"{host}:{port}"
    return f"{parsed.scheme}://{netloc}"

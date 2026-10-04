"""Issue lifecycle session token helpers."""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import Mapping

ISSUEKIT_SESSION_ENV = "ISSUEKIT_SESSION"
SESSION_TOKEN_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,199}$")


def is_valid_session_token(value: str) -> bool:
    return bool(SESSION_TOKEN_PATTERN.fullmatch(value))


def validate_session_token(value: str, *, label: str = "session") -> str:
    token = value.strip()
    if not token:
        raise ValueError(f"{label} must not be empty.")
    if not is_valid_session_token(token):
        raise ValueError(
            f"Invalid {label} token: {value}. Expected pattern "
            "^[a-z0-9][a-z0-9_-]{0,199}$."
        )
    return token


def current_session_token(environ: Mapping[str, str] | None = None) -> str | None:
    source = os.environ if environ is None else environ
    raw = source.get(ISSUEKIT_SESSION_ENV)
    if raw is None or not raw.strip():
        return None
    return validate_session_token(raw, label=ISSUEKIT_SESSION_ENV)


def resolved_or_new_session_token(
    prefix: str,
    environ: Mapping[str, str] | None = None,
) -> str:
    return current_session_token(environ) or new_session_token(prefix)


def new_session_token(prefix: str) -> str:
    valid_prefix = validate_session_token(prefix, label="session prefix")
    return f"{valid_prefix}-{uuid.uuid4().hex}"

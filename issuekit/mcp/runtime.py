"""Process and configuration runtime for the MCP server."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from mcp.server.fastmcp import Context

from issuekit import __version__
from issuekit.api.token_cache import read_cached_token
from issuekit.config import (
    IssuekitConfig,
    has_config_candidate,
    load_config,
    resolve_machine_config_path,
    resolve_repository_root,
)
from issuekit.config.local import LocalConfigError, load_toml, read_local_config
from issuekit.errors import WorkflowError
from issuekit.gitutil import git_root
from issuekit.session import new_session_token
from issuekit.store import get_store
from issuekit.urls import api_url_origin

MCP_SESSION = new_session_token("mcp")
_CODEX_ENV_VARS = (
    "ISSUEKIT_API_URL",
    "ISSUEKIT_API_TIMEOUT",
    "ISSUEKIT_PROJECT",
    "ISSUEKIT_WORKSPACE",
    "ISSUEKIT_CONFIG",
    "ISSUEKIT_TOKEN_CACHE",
    "ISSUEKIT_ALLOW_INSECURE",
    "ISSUEKIT_ENFORCE_AUTHOR_HANDOFF",
    "XDG_CONFIG_HOME",
)
_HEALTH_ENV_KEYS = (*_CODEX_ENV_VARS, "ISSUEKIT_API_TOKEN")


class McpRuntime:
    def __init__(self, root: Path, *, allow_overrides: bool = False) -> None:
        self.root = root
        self.allow_overrides = allow_overrides

    async def config_root(self, ctx: Context | None = None) -> Path:
        return await _resolve_config_root(self.root, ctx)

    @asynccontextmanager
    async def api_config(
        self,
        ctx: Context | None = None,
    ) -> AsyncIterator[tuple[IssuekitConfig, Path]]:
        yield await _load_api_config(self.root, ctx)

    @asynccontextmanager
    async def api_store(
        self,
        ctx: Context | None = None,
    ) -> AsyncIterator[tuple[IssuekitConfig, Path, Any]]:
        config, config_root = await _load_api_config(self.root, ctx)
        with get_store(config) as store:
            yield config, config_root, store


async def _health_status(root: Path, ctx: Context | None = None) -> dict[str, Any]:
    config_root = await _resolve_config_root(root, ctx)
    machine_path = resolve_machine_config_path()
    payload: dict[str, Any] = {
        "ok": True,
        "version": __version__,
        "cwd": str(config_root.resolve()),
        "project": None,
        "api_url_configured": False,
        "api_url_source": "none",
        "api_url_trusted_by": "none",
        "api_url_origin": None,
        "repo_config_source": "none",
        "machine_config_path": None if machine_path is None else str(machine_path),
        "machine_config_status": _machine_config_status(machine_path),
        "env_present": {key: key in os.environ for key in _HEALTH_ENV_KEYS},
        "token_cached": False,
        "token_expires_at": None,
        "worker_present": False,
        "worker": None,
        "author_guard_active": False,
        "author_guards": [],
        "errors": [],
    }
    try:
        local_config = read_local_config(config_root)
    except LocalConfigError as exc:
        payload["ok"] = False
        payload["errors"].append(f"local_config: {exc}")
        local_config = None
    if local_config is not None:
        if local_config.author_guards:
            payload["author_guard_active"] = True
            payload["author_guards"] = [dict(guard) for guard in local_config.author_guards]

    try:
        config = load_config(config_root)
    except Exception as exc:
        payload["ok"] = False
        payload["errors"].append(f"config: {type(exc).__name__}: {exc}")
        return payload

    payload["project"] = config.project
    payload["api_url_configured"] = bool(config.api_url)
    payload["api_url_source"] = config.api_url_source
    payload["api_url_trusted_by"] = config.api_url_trusted_by
    payload["api_url_origin"] = api_url_origin(config.api_url)
    payload["repo_config_source"] = config.repo_config_source
    cached_token = read_cached_token(config.api_url.rstrip("/")) if config.api_url else None
    payload["token_cached"] = cached_token is not None
    payload["token_expires_at"] = None if cached_token is None else cached_token["expires_at"]
    payload["worker"] = config.worker_key()
    payload["worker_present"] = config.worker is not None
    return payload


async def _load_api_config(
    root: Path,
    ctx: Context | None = None,
) -> tuple[IssuekitConfig, Path]:
    config_root = await _resolve_config_root(root, ctx)
    config = load_config(config_root)
    if not config.api_url:
        raise WorkflowError(
            _missing_api_url_message(config_root), code="missing_api_url"
        )
    return config, config_root


async def _resolve_config_root(root: Path, ctx: Context | None = None) -> Path:
    root = root.resolve()
    local_root = _configured_root(root)
    if local_root is not None:
        return local_root

    for client_root in await _client_roots(ctx):
        configured = _configured_root(client_root)
        if configured is not None:
            return configured

    return root


def _configured_root(root: Path) -> Path | None:
    root = root.resolve()
    config_root = resolve_repository_root(root)
    if has_config_candidate(config_root):
        return config_root
    repository_root = git_root(root)
    # A machine API config is sufficient for an existing repository root, but
    # does not make an arbitrary MCP process directory a project context.
    if repository_root is not None and (
        has_config_candidate(repository_root)
        or bool(os.getenv("ISSUEKIT_API_URL", "").strip())
        or _machine_config_has_api_url()
    ):
        return repository_root
    return None


def _machine_config_has_api_url() -> bool:
    machine_path = resolve_machine_config_path()
    if machine_path is None:
        return False
    try:
        if not machine_path.is_file():
            return False
        data = load_toml(machine_path)
    except (LocalConfigError, OSError):
        return False
    return bool(str(data.get("api_url", "")).strip())


def _machine_config_status(machine_path: Path | None) -> str:
    if machine_path is None:
        return "missing"
    try:
        if not machine_path.is_file():
            return "missing"
        machine_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return f"unreadable: {type(exc).__name__}"
    return "readable"


async def _client_roots(ctx: Context | None) -> tuple[Path, ...]:
    if ctx is None:
        return ()
    try:
        request_context = ctx.request_context
    except ValueError:
        return ()
    try:
        result = await request_context.session.list_roots()
    except Exception:
        return ()
    paths: list[Path] = []
    for root in result.roots:
        path = _path_from_file_uri(str(root.uri))
        if path is not None:
            paths.append(path)
    return tuple(paths)


def _path_from_file_uri(uri: str) -> Path | None:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    path = unquote(parsed.path)
    if os.name == "nt" and len(path) >= 3 and path[0] == "/" and path[2] == ":":
        path = path[1:]
    if parsed.netloc:
        path = f"//{parsed.netloc}{path}"
    return Path(path)


def _missing_api_url_message(root: Path) -> str:
    machine_path = resolve_machine_config_path()
    repo_config_source, repo_api_url_status = _repo_config_api_url_status(root)
    machine_status = _machine_config_status(machine_path)
    if machine_path is None:
        machine_config = "disabled (ISSUEKIT_CONFIG is empty)"
    elif machine_status == "missing":
        machine_config = f"{machine_path} (missing)"
    elif machine_status != "readable":
        machine_config = f"{machine_path} ({machine_status})"
    else:
        machine_api_url_status = _machine_config_api_url_status(
            machine_path, machine_status
        )
        if machine_api_url_status.startswith("unreadable:"):
            machine_config = f"{machine_path} ({machine_api_url_status})"
        else:
            machine_config = (
                f"{machine_path} (readable; api_url: {machine_api_url_status})"
            )

    api_url_env_value = os.environ.get("ISSUEKIT_API_URL")
    if api_url_env_value is None:
        api_url_env_status = "not set"
    elif not api_url_env_value.strip():
        api_url_env_status = (
            "set but empty (an empty value overrides api_url from config; unset it)"
        )
    else:
        api_url_env_status = "set"
    missing_machine_config_hint = (
        " If the CLI finds a machine config, this process has a different "
        "ISSUEKIT_CONFIG, HOME, or XDG_CONFIG_HOME."
        if machine_path is not None and machine_status == "missing"
        else ""
    )
    codex_env_vars = ", ".join(f'"{name}"' for name in _CODEX_ENV_VARS)
    return (
        "API store requires api_url. MCP resolved the repository root to "
        f"{root.resolve()}. ISSUEKIT_API_URL is set in this process: "
        f"{api_url_env_status}. Repository config source: {repo_config_source} "
        f"(api_url: {repo_api_url_status}). Machine config: {machine_config}."
        f"{missing_machine_config_hint} The CLI usually "
        "gets api_url from ISSUEKIT_API_URL in the user's shell, while MCP clients "
        "may start servers with a filtered environment. In Codex, add "
        f"env_vars = [{codex_env_vars}] to "
        "[mcp_servers.issuekit], or set api_url in the machine config. To continue "
        "reviewing before that is resolved, `issuekit show <id>` and "
        "`issuekit next-review` are read-only CLI equivalents of the get_issue "
        "and next_review tools."
    )


def _repo_config_api_url_status(root: Path) -> tuple[str, str]:
    pyproject_path = root / "pyproject.toml"
    if pyproject_path.exists():
        try:
            data = load_toml(pyproject_path)
        except (LocalConfigError, OSError) as exc:
            return "pyproject [tool.issuekit]", f"unreadable: {type(exc).__name__}"
        tool_config = data.get("tool")
        if isinstance(tool_config, dict):
            issuekit_config = tool_config.get("issuekit")
            if isinstance(issuekit_config, dict):
                return (
                    "pyproject [tool.issuekit]",
                    "present" if "api_url" in issuekit_config else "absent",
                )

    issuekit_path = root / "issuekit.toml"
    if issuekit_path.exists():
        try:
            data = load_toml(issuekit_path)
        except (LocalConfigError, OSError) as exc:
            return "issuekit.toml", f"unreadable: {type(exc).__name__}"
        return "issuekit.toml", "present" if "api_url" in data else "absent"
    return "none", "absent"


def _machine_config_api_url_status(
    machine_path: Path | None, machine_status: str
) -> str:
    if machine_path is None:
        return "not configured"
    if machine_status != "readable":
        return machine_status
    try:
        data = load_toml(machine_path)
    except (LocalConfigError, OSError) as exc:
        return f"unreadable: {type(exc).__name__}"
    return "present" if "api_url" in data else "absent"

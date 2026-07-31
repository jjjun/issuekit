"""Configuration for a headless agent runtime."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentRunConfig:
    """Per-agent headless run settings."""

    binary: str
    adapter: str | None = None
    runtime: str = "exec"
    app_server_argv: tuple[str, ...] = ("app-server",)
    lease_ttl_seconds: int = 60
    known_paths: tuple[str, ...] = ()
    headless_argv: tuple[str, ...] = ()
    resumable: bool = False
    session_flag: str | None = None
    approval_flag: str | None = None
    approval_value: str | None = None
    output_format_flag: str | None = None
    output_format: str | None = None
    model_flag: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None
    effort_argv: tuple[str, ...] = ()
    prompt_suffix: str | None = None
    model_prompts: tuple[tuple[str, str], ...] = ()
    speed: bool = False
    speed_argv: tuple[str, ...] = ()

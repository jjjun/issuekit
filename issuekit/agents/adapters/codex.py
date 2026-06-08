"""Codex headless adapter."""

from __future__ import annotations

from issuekit.agents.runner import ConfigAgentAdapter
from issuekit.config import IssuekitConfig


class CodexAdapter(ConfigAgentAdapter):
    """Adapter for the OpenAI Codex CLI non-interactive mode.

    Verified contract (codex-cli 0.119.0 on Windows and Ubuntu):
    - Non-interactive mode is ``codex exec "<prompt>"``.
    - ``--full-auto`` enables automatic execution with sandbox ``workspace-write``.
      It is a value-less flag (approval_value is None).
    - The runner passes ``stdin=subprocess.DEVNULL``; this is safe because
      ``codex exec`` reads the prompt from its argv when provided.
    - Stdout carries the final response; stderr carries session metadata,
      reasoning logs, and errors.
    - Exit code is 0 on success and non-zero on failure (e.g. 1 for API errors).
    """

    def __init__(
        self,
        config: IssuekitConfig | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__("codex", config=config, model=model)

"""Codex headless adapter."""

from __future__ import annotations

from issuekit.agents.runner import ConfigAgentAdapter
from issuekit.config import IssuekitConfig


class CodexAdapter(ConfigAgentAdapter):
    """Adapter for the OpenAI Codex CLI non-interactive mode.

    Contract (to be verified on Windows and Ubuntu):
    - Non-interactive mode is ``codex exec "<prompt>"``.
    - ``--approval-mode`` selects the auto-approval level (e.g. ``auto-edit``).
    - Stdout carries the final response; stderr carries logs / reasoning.
    - Stdin must be empty/closed or the process can hang.
    """

    def __init__(
        self,
        config: IssuekitConfig | None = None,
        model: str | None = None,
    ) -> None:
        super().__init__("codex", config=config, model=model)

    def parse_output(self, stdout: str, stderr: str) -> dict[str, str]:
        return {
            "stdout": stdout,
            "stderr": stderr,
        }

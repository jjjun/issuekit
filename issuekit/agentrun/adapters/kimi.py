"""Kimi headless adapter."""

from __future__ import annotations

from issuekit.agentrun.adapter import ConfigAgentAdapter
from issuekit.agentrun.config import AgentRunConfig


class KimiAdapter(ConfigAgentAdapter):
    """Adapter for the kimi-code CLI headless contract.

    Verified contract against kimi-code v0.11.0:
    - Headless mode is ``kimi -p "<prompt>" --output-format text``.
    - ``-p`` auto-executes tools and REJECTS ``--auto`` / ``-y``.
    - Reasoning narration goes to stderr; final answer to stdout.
    - Stdin must be empty/closed or the process can hang.
    """

    def __init__(
        self,
        agent_name: str = "kimi",
        *,
        run_config: AgentRunConfig,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        super().__init__(
            agent_name,
            run_config=run_config,
            model=model,
            reasoning_effort=reasoning_effort,
        )

    def parse_output(self, stdout: str, stderr: str) -> dict[str, str]:
        result = super().parse_output(stdout, stderr)
        marker = "To resume this session:"
        for line in reversed(stderr.splitlines()):
            if line.startswith(marker):
                command = line.removeprefix(marker).strip()
                executable, separator, session_id = command.rpartition(" -r ")
                if (
                    executable
                    and separator
                    and session_id
                    and not any(char.isspace() for char in session_id)
                ):
                    result["resume_session_id"] = session_id
                break
        return result

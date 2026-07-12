"""Kimi headless adapter."""

from __future__ import annotations

from issuekit.agents.runner import ConfigAgentAdapter
from issuekit.config import IssuekitConfig


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
        config: IssuekitConfig | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> None:
        super().__init__(
            agent_name,
            config=config,
            model=model,
            reasoning_effort=reasoning_effort,
        )

    def parse_output(self, stdout: str, stderr: str) -> dict[str, str]:
        result: dict[str, str] = {
            "stdout": stdout,
            "stderr": stderr,
        }
        for line in reversed(stderr.splitlines()):
            if line.startswith("To resume this session:"):
                parts = line.split()
                if parts:
                    result["resume_session_id"] = parts[-1]
                break
        return result

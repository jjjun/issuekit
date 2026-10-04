"""Kimi headless adapter."""

from __future__ import annotations

from issuekit.agentrun.adapter import ConfigAgentAdapter


class KimiAdapter(ConfigAgentAdapter):
    """Adapter for the kimi-code CLI headless contract.

    Verified contract against kimi-code v0.11.0:
    - Headless mode is ``kimi -p "<prompt>" --output-format text``.
    - ``-p`` auto-executes tools and REJECTS ``--auto`` / ``-y``.
    - Reasoning narration goes to stderr; final answer to stdout.
    - Stdin must be empty/closed or the process can hang.
    """

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

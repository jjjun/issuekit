"""Kimi headless adapter."""

from __future__ import annotations

import shutil
from pathlib import Path

from issuekit.agents.runner import AgentAdapter


class KimiAdapter(AgentAdapter):
    """Adapter for the kimi-code CLI headless contract.

    Verified contract against kimi-code v0.11.0:
    - Headless mode is ``kimi -p "<prompt>" --output-format text``.
    - ``-p`` auto-executes tools and REJECTS ``--auto`` / ``-y``.
    - Reasoning narration goes to stderr; final answer to stdout.
    - Stdin must be empty/closed or the process can hang.
    """

    def __init__(self, model: str | None = None) -> None:
        self.model = model

    def resolve_binary(self) -> Path:
        found = shutil.which("kimi")
        if found:
            return Path(found)
        home = Path.home()
        candidates = [
            home / ".kimi-code" / "bin" / "kimi",
            home / ".kimi-code" / "bin" / "kimi.exe",
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        raise RuntimeError(
            "kimi executable not found. Tried PATH and known per-OS locations."
        )

    def build_argv(self, prompt: str, plan_path: Path) -> list[str]:
        argv = ["-p", prompt, "--output-format", "text"]
        if self.model:
            argv.extend(["-m", self.model])
        return argv

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

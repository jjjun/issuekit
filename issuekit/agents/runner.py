"""Agent headless runner core."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from issuekit.config import AgentRunConfig, IssuekitConfig


class AgentAdapter(ABC):
    """Pluggable adapter for a headless coding agent."""

    @abstractmethod
    def resolve_binary(self) -> Path:
        """Return the path to the agent executable."""

    @abstractmethod
    def build_argv(self, prompt: str, plan_path: Path) -> list[str]:
        """Build the command-line argv for the agent."""

    @abstractmethod
    def parse_output(self, stdout: str, stderr: str) -> dict[str, str]:
        """Parse stdout/stderr into a structured dict."""


class ConfigAgentAdapter(AgentAdapter):
    """Adapter driven by declarative AgentRunConfig."""

    def __init__(
        self,
        agent_name: str,
        *,
        config: IssuekitConfig | None = None,
        model: str | None = None,
    ) -> None:
        self.agent_name = agent_name
        self.config = config or IssuekitConfig()
        agents_dict = dict(self.config.agents)
        if agent_name not in agents_dict:
            raise ValueError(f"Unknown agent: {agent_name}")
        self.run_config = agents_dict[agent_name]
        self.model = model

    def resolve_binary(self) -> Path:
        found = shutil.which(self.run_config.binary)
        if found:
            return Path(found)
        for p in self.run_config.known_paths:
            expanded = Path(p).expanduser()
            if expanded.exists():
                return expanded
        raise RuntimeError(
            f"{self.run_config.binary} executable not found. "
            "Tried PATH and known per-OS locations."
        )

    def build_argv(self, prompt: str, plan_path: Path) -> list[str]:
        argv = list(self.run_config.headless_argv)
        argv.append(prompt)
        if self.run_config.approval_flag:
            argv.append(self.run_config.approval_flag)
            if self.run_config.approval_value:
                argv.append(self.run_config.approval_value)
        if self.run_config.output_format_flag and self.run_config.output_format:
            argv.extend(
                [self.run_config.output_format_flag, self.run_config.output_format]
            )
        if self.model and self.run_config.model_flag:
            argv.extend([self.run_config.model_flag, self.model])
        return argv

    @abstractmethod
    def parse_output(self, stdout: str, stderr: str) -> dict[str, str]:
        """Parse stdout/stderr into a structured dict."""


def resolve_adapter(
    agent_name: str,
    config: IssuekitConfig | None = None,
    model: str | None = None,
) -> AgentAdapter:
    """Resolve an AgentAdapter by registered agent name."""
    if agent_name == "kimi":
        from issuekit.agents.adapters.kimi import KimiAdapter

        return KimiAdapter(config=config, model=model)
    if agent_name == "codex":
        from issuekit.agents.adapters.codex import CodexAdapter

        return CodexAdapter(config=config, model=model)
    raise ValueError(f"Unknown agent: {agent_name}")


@dataclass(frozen=True)
class AgentResult:
    """Result of a headless agent run."""

    exit_code: int
    stdout_path: Path
    stderr_path: Path
    elapsed_sec: float
    timed_out: bool
    parsed: dict[str, str] | None = None
    status_short: str | None = None


class AgentRunner:
    """Drive a coding agent synchronously against a target repo."""

    def run(
        self,
        adapter: AgentAdapter,
        plan_path: Path,
        repo: Path,
        timeout: float = 600.0,
    ) -> AgentResult:
        plan_path = plan_path.resolve()
        repo = repo.resolve()
        if not plan_path.exists():
            raise FileNotFoundError(f"Plan file not found: {plan_path}")
        if not repo.exists():
            raise FileNotFoundError(f"Repo directory not found: {repo}")

        binary = adapter.resolve_binary()
        prompt = (
            f"Read the plan file at: {plan_path} . Implement it fully by editing "
            "files directly in this repository. Do NOT run git commit or git push - "
            "leave all changes unstaged for review. If the plan is ambiguous, make "
            "the most reasonable choice and note it at the end."
        )
        argv = [str(binary)] + adapter.build_argv(prompt, plan_path)

        run_dir = repo / ".agent-runs"
        run_dir.mkdir(exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        stdout_path = run_dir / f"{stamp}.out.log"
        stderr_path = run_dir / f"{stamp}.err.log"

        start = time.monotonic()
        with open(stdout_path, "w", encoding="utf-8") as out_f, open(
            stderr_path, "w", encoding="utf-8"
        ) as err_f:
            kwargs: dict[str, Any] = {
                "stdin": subprocess.DEVNULL,
                "stdout": out_f,
                "stderr": err_f,
                "cwd": str(repo),
            }
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True

            proc = subprocess.Popen(argv, **kwargs)
            try:
                exit_code = proc.wait(timeout=timeout)
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
                self._kill_process_group(proc)
                exit_code = proc.returncode if proc.returncode is not None else -1

        elapsed = time.monotonic() - start

        stdout_text = stdout_path.read_text(encoding="utf-8")
        stderr_text = stderr_path.read_text(encoding="utf-8")
        parsed = adapter.parse_output(stdout_text, stderr_text)

        status_short = self._git_status_short(repo)

        return AgentResult(
            exit_code=exit_code,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
            elapsed_sec=elapsed,
            timed_out=timed_out,
            parsed=parsed,
            status_short=status_short,
        )

    def _kill_process_group(self, proc: subprocess.Popen) -> None:
        if os.name == "nt":
            try:
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        else:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGTERM)
                proc.wait(timeout=5)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    pgid = os.getpgid(proc.pid)
                    os.killpg(pgid, signal.SIGKILL)
                    proc.wait()
                except ProcessLookupError:
                    proc.kill()
                    proc.wait()

    def _git_status_short(self, repo: Path) -> str | None:
        try:
            result = subprocess.run(
                ["git", "--no-pager", "status", "--short"],
                cwd=str(repo),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except (OSError, subprocess.SubprocessError):
            return None

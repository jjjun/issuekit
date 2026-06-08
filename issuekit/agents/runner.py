"""Agent headless runner core."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from issuekit.agents.status import RunStatus, repo_relative, status_path, write_status
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

    def parse_output(self, stdout: str, stderr: str) -> dict[str, str]:
        """Parse stdout/stderr into a structured dict."""
        return {
            "stdout": stdout,
            "stderr": stderr,
        }


def resolve_adapter(
    agent_name: str,
    config: IssuekitConfig | None = None,
    model: str | None = None,
) -> AgentAdapter:
    """Resolve an AgentAdapter by registered agent name."""
    config = config or IssuekitConfig()
    if agent_name == "kimi":
        from issuekit.agents.adapters.kimi import KimiAdapter

        return KimiAdapter(config=config, model=model)
    if agent_name == "codex":
        from issuekit.agents.adapters.codex import CodexAdapter

        return CodexAdapter(config=config, model=model)
    if agent_name in dict(config.agents):
        return ConfigAgentAdapter(agent_name, config=config, model=model)
    raise ValueError(f"Unknown agent: {agent_name}")


@dataclass(frozen=True)
class AgentResult:
    """Result of a headless agent run."""

    exit_code: int
    stdout_path: Path
    agent_log_path: Path
    elapsed_sec: float
    timed_out: bool
    parsed: dict[str, str] | None = None
    status_short: str | None = None
    status_path: Path | None = None


class AgentRunner:
    """Drive a coding agent synchronously against a target repo."""

    def run(
        self,
        adapter: AgentAdapter,
        plan_path: Path,
        repo: Path,
        timeout: float = 600.0,
        agent_name: str | None = None,
        issue_id: int | None = None,
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
        run_id, reservation_path = self._reserve_run_id(run_dir)
        stdout_path = run_dir / f"{run_id}.out.log"
        agent_log_path = run_dir / f"{run_id}.agent.log"
        run_status_path = status_path(run_dir, run_id)
        started_at = datetime.now().replace(microsecond=0).isoformat()
        run_status = RunStatus(
            run_id=run_id,
            agent=agent_name or "unknown",
            issue=issue_id,
            status="running",
            pid=None,
            started_at=started_at,
            ended_at=None,
            elapsed_sec=None,
            exit_code=None,
            plan=repo_relative(plan_path, repo),
            stdout_log=repo_relative(stdout_path, repo),
            agent_log=repo_relative(agent_log_path, repo),
        )
        write_status(run_status_path, run_status)
        self._release_run_id_reservation(reservation_path)

        start = time.monotonic()
        with open(stdout_path, "w", encoding="utf-8") as out_f, open(
            agent_log_path, "w", encoding="utf-8"
        ) as log_f:
            kwargs: dict[str, Any] = {
                "stdin": subprocess.DEVNULL,
                "stdout": out_f,
                "stderr": log_f,
                "cwd": str(repo),
            }
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True

            proc = subprocess.Popen(argv, **kwargs)
            run_status = replace(run_status, pid=proc.pid)
            write_status(run_status_path, run_status)
            try:
                exit_code = proc.wait(timeout=timeout)
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
                self._kill_process_group(proc)
                exit_code = proc.returncode if proc.returncode is not None else -1

        elapsed = time.monotonic() - start
        terminal_status = self._terminal_status(exit_code, timed_out)
        write_status(
            run_status_path,
            replace(
                run_status,
                status=terminal_status,
                ended_at=datetime.now().replace(microsecond=0).isoformat(),
                elapsed_sec=elapsed,
                exit_code=exit_code,
            ),
        )

        stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
        agent_log_text = agent_log_path.read_text(encoding="utf-8", errors="replace")
        parsed = adapter.parse_output(stdout_text, agent_log_text)

        status_short = self._git_status_short(repo)

        return AgentResult(
            exit_code=exit_code,
            stdout_path=stdout_path,
            agent_log_path=agent_log_path,
            elapsed_sec=elapsed,
            timed_out=timed_out,
            parsed=parsed,
            status_short=status_short,
            status_path=run_status_path,
        )

    def _reserve_run_id(self, run_dir: Path) -> tuple[str, Path]:
        base = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_id = base
        counter = 2
        while True:
            reservation_path = run_dir / f"{run_id}.lock"
            if (
                (run_dir / f"{run_id}.out.log").exists()
                or (run_dir / f"{run_id}.agent.log").exists()
                or (run_dir / f"{run_id}.err.log").exists()
                or status_path(run_dir, run_id).exists()
                or reservation_path.exists()
            ):
                run_id = f"{base}-{counter:02d}"
                counter += 1
                continue
            try:
                fd = os.open(
                    reservation_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                )
            except FileExistsError:
                run_id = f"{base}-{counter:02d}"
                counter += 1
                continue
            os.close(fd)
            return run_id, reservation_path

    def _release_run_id_reservation(self, reservation_path: Path) -> None:
        try:
            reservation_path.unlink()
        except FileNotFoundError:
            pass

    def _terminal_status(self, exit_code: int, timed_out: bool):
        if timed_out:
            return "timed_out"
        if exit_code == 0:
            return "completed"
        return "failed"

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

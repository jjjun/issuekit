"""Agent headless runner core."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from issuekit.agents.status import (
    HEARTBEAT_INTERVAL_SEC,
    RunStatus,
    read_status,
    repo_relative,
    status_path,
    write_status,
)
from issuekit.config import AgentRunConfig, IssuekitConfig
from issuekit.core import last_nonempty_line
from issuekit.gitutil import changed_file_count, git_status_short


class AgentAdapter(ABC):
    """Pluggable adapter for a headless coding agent."""

    @abstractmethod
    def resolve_binary(self) -> Path:
        """Return the path to the agent executable."""

    @abstractmethod
    def build_argv(
        self,
        prompt: str,
        plan_path: Path,
        session_id: str | None = None,
    ) -> list[str]:
        """Build the command-line argv for the agent."""

    @abstractmethod
    def parse_output(self, stdout: str, stderr: str) -> dict[str, str]:
        """Parse stdout/stderr into a structured dict."""

    def supports_session_resume(self) -> bool:
        """Return True when the adapter can resume a caller-provided session."""
        return False


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

    def build_argv(
        self,
        prompt: str,
        plan_path: Path,
        session_id: str | None = None,
    ) -> list[str]:
        resolved_model = self.model or self.run_config.model
        prompt = self._append_prompt_suffixes(prompt, resolved_model)
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
        if resolved_model and self.run_config.model_flag:
            argv.extend([self.run_config.model_flag, resolved_model])
        if (
            session_id
            and self.run_config.resumable
            and self.run_config.session_flag
        ):
            argv.extend([self.run_config.session_flag, session_id])
        return argv

    def parse_output(self, stdout: str, stderr: str) -> dict[str, str]:
        """Parse stdout/stderr into a structured dict."""
        return {
            "stdout": stdout,
            "stderr": stderr,
        }

    def supports_session_resume(self) -> bool:
        """Return True when the declarative config has a session flag."""
        return bool(self.run_config.resumable and self.run_config.session_flag)

    def _append_prompt_suffixes(self, prompt: str, resolved_model: str | None) -> str:
        parts = [prompt]
        if self.run_config.prompt_suffix:
            parts.append(self.run_config.prompt_suffix)
        if resolved_model:
            model_prompt = dict(self.run_config.model_prompts).get(resolved_model)
            if model_prompt:
                parts.append(model_prompt)
        return "\n\n".join(parts)


def resolve_adapter(
    agent_name: str,
    config: IssuekitConfig | None = None,
    model: str | None = None,
) -> AgentAdapter:
    """Resolve an AgentAdapter by registered agent name."""
    config = config or IssuekitConfig()
    run_config = dict(config.agents).get(agent_name)
    if run_config is None:
        raise ValueError(f"Unknown agent: {agent_name}")
    if run_config.adapter:
        from issuekit.agents.adapters.registry import resolve_custom_adapter

        adapter_class = resolve_custom_adapter(run_config.adapter)
        if adapter_class is None:
            raise ValueError(
                f"Unknown adapter '{run_config.adapter}' for agent: {agent_name}"
            )
        return adapter_class(agent_name, config=config, model=model)
    return ConfigAgentAdapter(agent_name, config=config, model=model)


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



class _RunWatcher:
    """Background watcher that updates status JSON and optionally emits a heartbeat."""

    def __init__(
        self,
        *,
        run_status_path: Path,
        run_status: RunStatus,
        repo: Path,
        agent_log_path: Path,
        enable_heartbeat: bool,
        start_time: float,
    ) -> None:
        self.run_status_path = run_status_path
        self.run_status = run_status
        self.repo = repo
        self.agent_log_path = agent_log_path
        self.enable_heartbeat = enable_heartbeat
        self.start_time = start_time
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001 - a tick must never kill the writer
                sys.stderr.write(f"\nstatus writer tick failed (continuing): {exc}\n")
                sys.stderr.flush()
            self._stop_event.wait(timeout=HEARTBEAT_INTERVAL_SEC)

    def _tick(self) -> None:
        last_line = self._read_last_log_line(self.agent_log_path)
        now = datetime.now().replace(microsecond=0).isoformat()
        changed = self._changed_file_count(self.repo)

        self.run_status = replace(
            self.run_status,
            last_log_line=last_line,
            last_log_at=now if last_line else self.run_status.last_log_at,
            heartbeat_at=now,
        )
        write_status(self.run_status_path, self.run_status)

        if self.enable_heartbeat:
            elapsed = time.monotonic() - self.start_time
            minutes, seconds = divmod(int(elapsed), 60)
            line_text = last_line or "-"
            if len(line_text) > 50:
                line_text = line_text[:47] + "..."
            msg = (
                f"[{minutes:02d}:{seconds:02d}] running run={self.run_status.run_id} "
                f"changed={changed} last: {line_text}"
            )
            max_width = 100
            if len(msg) > max_width:
                msg = msg[: max_width - 3] + "..."
            sys.stderr.write(f"\r{msg}")
            sys.stderr.flush()

    @staticmethod
    def _read_last_log_line(path: Path) -> str | None:
        try:
            data = path.read_bytes()
        except OSError:
            return None
        if not data:
            return None
        return last_nonempty_line(data.decode("utf-8", errors="replace"))

    @staticmethod
    def _changed_file_count(repo: Path) -> int:
        return changed_file_count(repo)


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
        follow: bool = False,
        prompt_suffix: str | None = None,
        prompt_override: str | None = None,
        abort_event: threading.Event | None = None,
        session_id: str | None = None,
        issuekit_session: str | None = None,
    ) -> AgentResult:
        plan_path = plan_path.resolve()
        repo = repo.resolve()
        if not plan_path.exists():
            raise FileNotFoundError(f"Plan file not found: {plan_path}")
        if not repo.exists():
            raise FileNotFoundError(f"Repo directory not found: {repo}")

        binary = adapter.resolve_binary()
        prompt = prompt_override or (
            f"Read the plan file at: {plan_path} . Implement it fully by editing "
            "files directly in this repository. Do NOT run git commit or git push - "
            "leave all changes unstaged for review. Edit only code, tests, and "
            "supporting project files needed for the implementation. Write "
            "maintainable, idiomatic code that matches surrounding imports, naming, "
            "and comment density; use normal imports and real identifiers when they "
            "work. Do not split or obfuscate string literals, import paths, or "
            "identifiers, and do not use importlib/getattr/setattr/globals() "
            "indirection unless dynamic loading is truly required. Issuekit owns the "
            "API-backed issue lifecycle, including claim, submit, review, approval, "
            "and completion state; do not run issuekit claim, submit-review, "
            "request-changes, approve, or complete, and do not mutate tracker state "
            "or issue lifecycle metadata directly. If the plan is ambiguous, make "
            "the most reasonable choice and note it at the end."
        )
        if prompt_suffix:
            prompt = f"{prompt}\n\n{prompt_suffix}"
        argv = [str(binary)] + adapter.build_argv(
            prompt,
            plan_path,
            session_id=session_id,
        )

        run_dir = repo / ".agent-runs"
        run_dir_existed = run_dir.exists()
        run_dir.mkdir(exist_ok=True)
        if not run_dir_existed:
            print(
                ".agent-runs/ is gitignored run-log storage and is not normally committed.",
                file=sys.stderr,
            )
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

        enable_heartbeat = sys.stderr.isatty() or follow
        start = time.monotonic()
        watcher: _RunWatcher | None = None
        with open(stdout_path, "w", encoding="utf-8") as out_f, open(
            agent_log_path, "w", encoding="utf-8"
        ) as log_f:
            kwargs: dict[str, Any] = {
                "stdin": subprocess.DEVNULL,
                "stdout": out_f,
                "stderr": log_f,
                "cwd": str(repo),
            }
            if issuekit_session is not None:
                env = os.environ.copy()
                env["ISSUEKIT_SESSION"] = issuekit_session
                kwargs["env"] = env
            if os.name == "nt":
                kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                kwargs["start_new_session"] = True

            proc = subprocess.Popen(argv, **kwargs)
            run_status = replace(run_status, pid=proc.pid)
            write_status(run_status_path, run_status)

            watcher = _RunWatcher(
                run_status_path=run_status_path,
                run_status=run_status,
                repo=repo,
                agent_log_path=agent_log_path,
                enable_heartbeat=enable_heartbeat,
                start_time=start,
            )
            watcher.start()

            try:
                exit_code, timed_out = self._wait_for_process(
                    proc,
                    timeout=timeout,
                    abort_event=abort_event,
                )
            except subprocess.TimeoutExpired:
                timed_out = True
                self._kill_process_group(proc)
                exit_code = proc.returncode if proc.returncode is not None else -1
            finally:
                if watcher is not None:
                    watcher.stop()
                    if enable_heartbeat:
                        sys.stderr.write("\n")
                        sys.stderr.flush()

        elapsed = time.monotonic() - start
        terminal_status = self._terminal_status(exit_code, timed_out)

        # Preserve fields the watcher may have written.
        try:
            current_status = read_status(run_status_path)
        except (OSError, ValueError):
            current_status = run_status

        write_status(
            run_status_path,
            replace(
                run_status,
                status=terminal_status,
                ended_at=datetime.now().replace(microsecond=0).isoformat(),
                elapsed_sec=elapsed,
                exit_code=exit_code,
                last_log_line=current_status.last_log_line,
                last_log_at=current_status.last_log_at,
                heartbeat_at=current_status.heartbeat_at,
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

    def _wait_for_process(
        self,
        proc: subprocess.Popen,
        *,
        timeout: float,
        abort_event: threading.Event | None,
    ) -> tuple[int, bool]:
        if abort_event is None:
            return proc.wait(timeout=timeout), False

        deadline = time.monotonic() + timeout
        while True:
            if abort_event.is_set():
                self._kill_process_group(proc)
                exit_code = proc.returncode if proc.returncode is not None else -1
                return exit_code, True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._kill_process_group(proc)
                exit_code = proc.returncode if proc.returncode is not None else -1
                return exit_code, True
            try:
                return proc.wait(timeout=min(0.25, remaining)), False
            except subprocess.TimeoutExpired:
                continue

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
        return git_status_short(repo)

"""Agent headless runner core."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from issuekit.agentrun._coerce import last_nonempty_line
from issuekit.agentrun.adapter import AgentAdapter
from issuekit.agentrun.git import changed_file_count, git_status_short
from issuekit.agentrun.status import (
    HEARTBEAT_INTERVAL_SEC,
    RunStatus,
    read_status,
    repo_relative,
    status_path,
    write_status,
)
from issuekit.file_permissions import ensure_owner_only_directory, open_owner_only


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
    report_path: Path | None = None


@dataclass(frozen=True)
class AgentPrompt:
    """Prompt content, its on-disk location, and the launch instruction."""

    path: Path
    body: str
    pointer: str


def implementation_report_instruction(destination: str) -> str:
    """Return the instruction for writing an implementer report."""

    return (
        "Write your closing implementation and verification report to "
        f"{destination}, including answers to any reporting requests in the plan."
    )


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
        self._thread.join()

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

        self.run_status = replace(
            self.run_status,
            last_log_line=last_line,
            last_log_at=now if last_line else self.run_status.last_log_at,
            heartbeat_at=now,
        )
        write_status(self.run_status_path, self.run_status)

        if self.enable_heartbeat:
            changed = changed_file_count(self.repo)
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


class AgentRunner:
    """Drive a coding agent synchronously against a target repo."""

    def run(
        self,
        adapter: AgentAdapter,
        prompt: AgentPrompt,
        repo: Path,
        timeout: float = 600.0,
        agent_name: str | None = None,
        issue_id: int | None = None,
        follow: bool = False,
        prompt_suffix: str | None = None,
        run_dir: Path | None = None,
        abort_event: threading.Event | None = None,
        session_id: str | None = None,
        resume_session: bool = False,
        issuekit_session: str | None = None,
        implementer_report: bool = False,
    ) -> AgentResult:
        plan_path = prompt.path.resolve()
        repo = repo.resolve()
        if not repo.exists():
            raise FileNotFoundError(f"Repo directory not found: {repo}")

        binary = adapter.resolve_binary()
        if prompt_suffix:
            prompt_text = f"{prompt.pointer}\n\n{prompt_suffix}"
        else:
            prompt_text = prompt.pointer
        argv = [str(binary)] + adapter.build_argv(
            prompt_text,
            plan_path,
            session_id=session_id,
            resume=resume_session,
        )

        run_dir = (run_dir or repo / ".agent-runs").resolve()
        run_dir_existed = run_dir.exists()
        ensure_owner_only_directory(run_dir)
        if not run_dir_existed:
            print(
                ".agent-runs/ is gitignored run-log storage and is not normally committed.",
                file=sys.stderr,
            )
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        plan_path.write_text(prompt.body, encoding="utf-8", newline="\n")
        run_id, reservation_path = self._reserve_run_id(run_dir)
        stdout_path = run_dir / f"{run_id}.out.log"
        agent_log_path = run_dir / f"{run_id}.agent.log"
        report_path = run_dir / f"{run_id}.report.md" if implementer_report else None
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
        with os.fdopen(
            open_owner_only(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC),
            "w",
            encoding="utf-8",
        ) as out_f, os.fdopen(
            open_owner_only(agent_log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC),
            "w",
            encoding="utf-8",
        ) as log_f:
            kwargs: dict[str, Any] = {
                "stdin": subprocess.DEVNULL,
                "stdout": out_f,
                "stderr": log_f,
                "cwd": str(repo),
            }
            if issuekit_session is not None or report_path is not None:
                env = os.environ.copy()
                if issuekit_session is not None:
                    env["ISSUEKIT_SESSION"] = issuekit_session
                if report_path is not None:
                    env["ISSUEKIT_IMPLEMENTER_REPORT_FILE"] = str(report_path)
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
            finally:
                watcher.stop()
                if enable_heartbeat:
                    sys.stderr.write("\n")
                    sys.stderr.flush()

        elapsed = time.monotonic() - start
        terminal_status = self._terminal_status(exit_code, timed_out)

        try:
            stdout_text = stdout_path.read_text(encoding="utf-8", errors="replace")
            agent_log_text = agent_log_path.read_text(encoding="utf-8", errors="replace")
            parsed = adapter.parse_output(stdout_text, agent_log_text)
        except Exception:  # noqa: BLE001 - parsing must never block the terminal status write
            parsed = None

        # Preserve fields the watcher may have written.
        try:
            current_status = read_status(run_status_path)
        except (OSError, ValueError):
            current_status = run_status

        write_status(
            run_status_path,
            replace(
                current_status,
                status=terminal_status,
                ended_at=datetime.now().replace(microsecond=0).isoformat(),
                elapsed_sec=elapsed,
                exit_code=exit_code,
                failure_reason=(parsed or {}).get("failure_reason"),
                terminal_reason=(parsed or {}).get("terminal_reason"),
            ),
        )

        status_short = git_status_short(repo)

        return AgentResult(
            exit_code=exit_code,
            stdout_path=stdout_path,
            agent_log_path=agent_log_path,
            elapsed_sec=elapsed,
            timed_out=timed_out,
            parsed=parsed,
            status_short=status_short,
            status_path=run_status_path,
            report_path=report_path,
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
                fd = open_owner_only(
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
            try:
                return proc.wait(timeout=timeout), False
            except subprocess.TimeoutExpired:
                return self._terminate_process(proc)

        deadline = time.monotonic() + timeout
        while True:
            if abort_event.is_set():
                return self._terminate_process(proc)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return self._terminate_process(proc)
            try:
                return proc.wait(timeout=min(0.25, remaining)), False
            except subprocess.TimeoutExpired:
                continue

    def _terminate_process(self, proc: subprocess.Popen) -> tuple[int, bool]:
        self._kill_process_group(proc)
        exit_code = proc.returncode if proc.returncode is not None else -1
        return exit_code, True

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

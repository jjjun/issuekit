"""Agent headless runner core."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from issuekit.agentrun.adapter import AgentAdapter, ConfigAgentAdapter
from issuekit.agentrun.parsed import parsed_int, parsed_is_error, parsed_usage
from issuekit.agentrun.run_dir import prepare_run_dir
from issuekit.agentrun.status import (
    RunStatus,
    read_status,
    repo_relative,
    status_path,
    write_status,
)
from issuekit.agentrun.watcher import _RunWatcher
from issuekit.file_permissions import open_owner_only_new, write_owner_only_text
from issuekit.gitutil import git_status_short

MAX_PROMPT_CHARS = 24_000


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
        f"{destination}, including answers to any reporting requests in the "
        "plan. The report is mandatory; a run that ends without one is treated "
        "as a failed run, not a completed one. This run is a single turn: "
        "background tasks cannot wake you, no completion notification will "
        "ever arrive, and ending the turn ends the run, whatever is still "
        "running in the background. Await every verification command "
        "you start and record its actual exit result before writing the report; "
        "starting a command in the background and ending the turn is a failed "
        "run. Include a section listing every acceptance criterion you could "
        "not verify in this environment and why (for example: no browser "
        "available). Leaving such a criterion unmentioned is worse than "
        "reporting it unverified."
    )


@dataclass(frozen=True)
class _RunFiles:
    run_id: str
    stdout_path: Path
    agent_log_path: Path
    report_path: Path | None
    status_path: Path
    status: RunStatus


@dataclass(frozen=True)
class _RunContext:
    repo: Path
    binary: Path
    argv: list[str]
    files: _RunFiles


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
        drop_env: Sequence[str] = (),
    ) -> AgentResult:
        context = self._prepare_run(
            adapter=adapter,
            prompt=prompt,
            repo=repo,
            agent_name=agent_name,
            issue_id=issue_id,
            prompt_suffix=prompt_suffix,
            run_dir=run_dir,
            session_id=session_id,
            resume_session=resume_session,
            implementer_report=implementer_report,
        )
        enable_heartbeat = sys.stderr.isatty() or follow
        start = time.monotonic()
        exit_code, timed_out, run_error, run_status = self._launch_and_supervise(
            context=context,
            start_time=start,
            timeout=timeout,
            abort_event=abort_event,
            enable_heartbeat=enable_heartbeat,
            issuekit_session=issuekit_session,
            drop_env=drop_env,
        )

        elapsed, parsed = self._finalize_status(
            adapter=adapter,
            run_files=context.files,
            run_status=run_status,
            exit_code=exit_code,
            timed_out=timed_out,
            start_time=start,
        )

        return self._build_result(
            context=context,
            exit_code=exit_code,
            timed_out=timed_out,
            run_error=run_error,
            elapsed=elapsed,
            parsed=parsed,
        )

    def _build_result(
        self,
        *,
        context: _RunContext,
        exit_code: int,
        timed_out: bool,
        run_error: BaseException | None,
        elapsed: float,
        parsed: dict[str, str] | None,
    ) -> AgentResult:
        status_short = git_status_short(context.repo)
        if run_error is not None:
            raise run_error
        return AgentResult(
            exit_code=exit_code,
            stdout_path=context.files.stdout_path,
            agent_log_path=context.files.agent_log_path,
            elapsed_sec=elapsed,
            timed_out=timed_out,
            parsed=parsed,
            status_short=status_short,
            status_path=context.files.status_path,
            report_path=context.files.report_path,
        )

    def _prepare_run(
        self,
        *,
        adapter: AgentAdapter,
        prompt: AgentPrompt,
        repo: Path,
        agent_name: str | None,
        issue_id: int | None,
        prompt_suffix: str | None,
        run_dir: Path | None,
        session_id: str | None,
        resume_session: bool,
        implementer_report: bool,
    ) -> _RunContext:
        plan_path = prompt.path.absolute()
        repo = repo.resolve()
        if not repo.exists():
            raise FileNotFoundError(f"Repo directory not found: {repo}")
        prompt_text = self._compose_prompt(adapter, prompt, prompt_suffix)
        binary = adapter.resolve_binary()
        argv = [str(binary)] + adapter.build_argv(
            prompt_text,
            plan_path,
            session_id=session_id,
            resume=resume_session,
        )
        files = self._prepare_run_files(
            repo=repo,
            plan_path=plan_path,
            prompt=prompt,
            agent_name=agent_name,
            issue_id=issue_id,
            implementer_report=implementer_report,
            run_dir=run_dir,
        )
        return _RunContext(repo=repo, binary=binary, argv=argv, files=files)

    def _launch_and_supervise(
        self,
        *,
        context: _RunContext,
        start_time: float,
        timeout: float,
        abort_event: threading.Event | None,
        enable_heartbeat: bool,
        issuekit_session: str | None,
        drop_env: Sequence[str],
    ) -> tuple[int, bool, BaseException | None, RunStatus]:
        files = context.files
        with (
            os.fdopen(open_owner_only_new(files.stdout_path), "w", encoding="utf-8") as out_f,
            os.fdopen(open_owner_only_new(files.agent_log_path), "w", encoding="utf-8") as log_f,
        ):
            kwargs = self._popen_kwargs(
                repo=context.repo,
                stdout_file=out_f,
                agent_log_file=log_f,
                issuekit_session=issuekit_session,
                report_path=files.report_path,
                drop_env=drop_env,
            )
            proc, run_status = self._launch(
                binary=context.binary,
                argv=context.argv,
                kwargs=kwargs,
                run_files=files,
                start_time=start_time,
            )
            result = self._supervise(
                proc=proc,
                run_status=run_status,
                run_files=files,
                repo=context.repo,
                start_time=start_time,
                timeout=timeout,
                abort_event=abort_event,
                enable_heartbeat=enable_heartbeat,
            )
            return (*result, run_status)

    def _compose_prompt(
        self,
        adapter: AgentAdapter,
        prompt: AgentPrompt,
        prompt_suffix: str | None,
    ) -> str:
        prompt_text = (
            f"{prompt.pointer}\n\n{prompt_suffix}" if prompt_suffix else prompt.pointer
        )
        composed_prompt = adapter.compose_prompt(prompt_text)
        if len(composed_prompt) > MAX_PROMPT_CHARS:
            raise ValueError(
                f"Composed prompt is {len(composed_prompt)} characters; "
                f"limit is {MAX_PROMPT_CHARS}."
            )
        return prompt_text

    def _prepare_run_files(
        self,
        *,
        repo: Path,
        plan_path: Path,
        prompt: AgentPrompt,
        agent_name: str | None,
        issue_id: int | None,
        implementer_report: bool,
        run_dir: Path | None,
    ) -> _RunFiles:
        run_dir_existed = (run_dir or repo / ".agent-runs").exists()
        run_dir = prepare_run_dir(repo, run_dir)
        if not run_dir_existed:
            print(
                ".agent-runs/ is gitignored run-log storage and is not normally committed.",
                file=sys.stderr,
            )
        plan_path.parent.mkdir(parents=True, exist_ok=True)
        write_owner_only_text(plan_path, prompt.body)
        run_id, reservation_path = self._reserve_run_id(run_dir)
        stdout_path = run_dir / f"{run_id}.out.log"
        agent_log_path = run_dir / f"{run_id}.agent.log"
        report_path = run_dir / f"{run_id}.report.md" if implementer_report else None
        run_status_path = status_path(run_dir, run_id)
        run_status = RunStatus(
            run_id=run_id,
            agent=agent_name or "unknown",
            issue=issue_id,
            status="running",
            pid=None,
            started_at=datetime.now().replace(microsecond=0).isoformat(),
            ended_at=None,
            elapsed_sec=None,
            exit_code=None,
            plan=repo_relative(plan_path, repo),
            stdout_log=repo_relative(stdout_path, repo),
            agent_log=repo_relative(agent_log_path, repo),
        )
        write_status(run_status_path, run_status)
        self._release_run_id_reservation(reservation_path)
        return _RunFiles(
            run_id=run_id,
            stdout_path=stdout_path,
            agent_log_path=agent_log_path,
            report_path=report_path,
            status_path=run_status_path,
            status=run_status,
        )

    def _popen_kwargs(
        self,
        *,
        repo: Path,
        stdout_file: Any,
        agent_log_file: Any,
        issuekit_session: str | None,
        report_path: Path | None,
        drop_env: Sequence[str],
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "stdin": subprocess.DEVNULL,
            "stdout": stdout_file,
            "stderr": agent_log_file,
            "cwd": str(repo),
        }
        env = os.environ.copy()
        for key in drop_env:
            env.pop(key, None)
        if issuekit_session is not None:
            env["ISSUEKIT_SESSION"] = issuekit_session
        if report_path is not None:
            env["ISSUEKIT_IMPLEMENTER_REPORT_FILE"] = str(report_path)
        kwargs["env"] = env
        if os.name == "nt":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        return kwargs

    def _launch(
        self,
        *,
        binary: Path,
        argv: list[str],
        kwargs: dict[str, Any],
        run_files: _RunFiles,
        start_time: float,
    ) -> tuple[subprocess.Popen, RunStatus]:
        try:
            proc = subprocess.Popen(argv, **kwargs)
        except OSError as exc:
            write_status(
                run_files.status_path,
                replace(
                    run_files.status,
                    status="failed",
                    ended_at=datetime.now().replace(microsecond=0).isoformat(),
                    elapsed_sec=time.monotonic() - start_time,
                    exit_code=1,
                    failure_reason=str(exc),
                ),
            )
            raise RuntimeError(f"Could not launch {binary}: {exc}") from exc
        run_status = replace(run_files.status, pid=proc.pid)
        write_status(run_files.status_path, run_status)
        return proc, run_status

    def _supervise(
        self,
        *,
        proc: subprocess.Popen,
        run_status: RunStatus,
        run_files: _RunFiles,
        repo: Path,
        start_time: float,
        timeout: float,
        abort_event: threading.Event | None,
        enable_heartbeat: bool,
    ) -> tuple[int, bool, BaseException | None]:
        watcher = _RunWatcher(
            run_status_path=run_files.status_path,
            run_status=run_status,
            repo=repo,
            agent_log_path=run_files.agent_log_path,
            stdout_log_path=run_files.stdout_path,
            enable_heartbeat=enable_heartbeat,
            start_time=start_time,
        )
        watcher.start()
        run_error: BaseException | None = None
        try:
            exit_code, timed_out = self._wait_for_process(
                proc,
                timeout=timeout,
                abort_event=abort_event,
            )
        except BaseException as exc:
            self._kill_process_group(proc)
            exit_code = 130
            timed_out = False
            run_error = exc
        finally:
            watcher.stop()
            if enable_heartbeat:
                sys.stderr.write("\n")
                sys.stderr.flush()
        return exit_code, timed_out, run_error

    def _finalize_status(
        self,
        *,
        adapter: AgentAdapter,
        run_files: _RunFiles,
        run_status: RunStatus,
        exit_code: int,
        timed_out: bool,
        start_time: float,
    ) -> tuple[float, dict[str, str] | None]:
        elapsed = time.monotonic() - start_time
        terminal_status = self._terminal_status(exit_code, timed_out)
        stdout_text = ""
        try:
            stdout_text = run_files.stdout_path.read_text(
                encoding="utf-8", errors="replace"
            )
            agent_log_text = run_files.agent_log_path.read_text(
                encoding="utf-8", errors="replace"
            )
            parsed = adapter.parse_output(stdout_text, agent_log_text)
        except Exception:  # noqa: BLE001 - parsing must never block the terminal status write
            parsed = None
        _warn_if_fast_mode_disabled(adapter, parsed)

        # Preserve fields the watcher may have written.
        try:
            current_status = read_status(run_files.status_path)
        except (OSError, ValueError):
            current_status = run_status

        write_status(
            run_files.status_path,
            replace(
                current_status,
                status=terminal_status,
                ended_at=datetime.now().replace(microsecond=0).isoformat(),
                elapsed_sec=elapsed,
                exit_code=exit_code,
                failure_reason=(parsed or {}).get("failure_reason"),
                terminal_reason=(parsed or {}).get("terminal_reason"),
                session_id=(parsed or {}).get("session_id"),
                usage=parsed_usage(parsed),
                final_message=_parsed_final_message(parsed, stdout_text),
                is_error=parsed_is_error(parsed),
                permission_denials=parsed_int(parsed, "permission_denials"),
                permission_denied_tools=(parsed or {}).get("permission_denied_tools"),
                api_error_status=(parsed or {}).get("api_error_status"),
                fast_mode_state=(parsed or {}).get("fast_mode_state"),
                fast_mode_disabled_reason=(parsed or {}).get(
                    "fast_mode_disabled_reason"
                ),
            ),
        )
        return elapsed, parsed

    def _reserve_run_id(self, run_dir: Path) -> tuple[str, Path]:
        base = datetime.now().strftime("%Y%m%d-%H%M%S")
        run_id = base
        counter = 2
        while True:
            reservation_path = run_dir / f"{run_id}.lock"
            if (
                status_path(run_dir, run_id).exists()
                or reservation_path.exists()
            ):
                run_id = f"{base}-{counter:02d}"
                counter += 1
                continue
            try:
                fd = open_owner_only_new(reservation_path)
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
                pass
            try:
                result = subprocess.run(
                    ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=5,
                )
                if result.returncode != 0 and proc.poll() is None:
                    proc.kill()
            except (OSError, subprocess.SubprocessError):
                if proc.poll() is None:
                    proc.kill()
            proc.wait()
        else:
            pgid = proc.pid
            try:
                os.killpg(pgid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()


def _warn_if_fast_mode_disabled(
    adapter: AgentAdapter,
    parsed: dict[str, str] | None,
) -> None:
    if (
        not isinstance(adapter, ConfigAgentAdapter)
        or adapter.run_config.speed is not True
    ):
        return
    fields = parsed or {}
    state = fields.get("fast_mode_state")
    disabled_reason = fields.get("fast_mode_disabled_reason")
    if state and state.casefold() == "on":
        return
    if not state and not disabled_reason:
        return
    reason = disabled_reason or state
    print(
        f"WARNING: fast mode was requested but is not active: {reason}",
        file=sys.stderr,
    )


def _parsed_final_message(
    parsed: dict[str, str] | None,
    raw_stdout: str,
) -> str | None:
    if parsed is None:
        return None
    message = parsed.get("stdout")
    if not message:
        return None
    has_result_metadata = any(
        key in parsed
        for key in (
            "session_id",
            "is_error",
            "terminal_reason",
            "cost_usd",
            "num_turns",
            "permission_denials",
            "permission_denied_tools",
            "api_error_status",
            "fast_mode_state",
            "fast_mode_disabled_reason",
        )
    ) or any(key.startswith("usage_") for key in parsed)
    return message if has_result_metadata or message != raw_stdout else None

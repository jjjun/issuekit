"""Issue-owned bridge between mine-py commands and local Codex App Server."""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from issuekit.agentrun.adapter import AgentAdapter
from issuekit.agentrun.app_server import (
    AppServerError,
    AppServerTransport,
    CommandJournal,
    normalize_notification,
)
from issuekit.agentrun.config import AgentRunConfig
from issuekit.agentrun.parsed import encode_usage, int_counts
from issuekit.agentrun.run_dir import prepare_run_dir
from issuekit.agentrun.runner import (
    AgentPrompt,
    AgentResult,
    implementation_report_instruction,
)
from issuekit.api import IssuekitClient
from issuekit.api.factory import client_for, require_api_url
from issuekit.config import IssuekitConfig
from issuekit.core import Issue
from issuekit.errors import WorkflowError
from issuekit.file_permissions import open_owner_only_new, write_owner_only_text

LEASE_STOP_CODES = frozenset(
    {
        "claim_lost",
        "lease_expired",
        "lease_token_invalid",
        "stale_generation",
    }
)
STDERR_TAIL_CHARS = 4096
T = TypeVar("T")


def _usage_total(event: Mapping[str, Any]) -> dict[str, int]:
    """Return the cumulative thread token counts carried by a normalized event."""
    payload = event.get("payload")
    usage = payload.get("usage") if isinstance(payload, Mapping) else None
    total = usage.get("total") if isinstance(usage, Mapping) else None
    return int_counts(total)


def _retry_once(call: Callable[[], T]) -> T:
    try:
        return call()
    except WorkflowError as exc:
        if exc.code != "request_failed":
            raise
    return call()


@dataclass(frozen=True)
class AttemptContext:
    session_id: str
    generation: int
    worker_id: str
    lease_token: str
    headers: dict[str, str]


@dataclass
class _AttemptState:
    native_session_id: str | None = None
    session_id: str | None = None
    timed_out: bool = False
    exit_code: int = 1
    event_number: int = 0
    failure_reason: str | None = None
    terminal_event: list[str] = field(default_factory=list)
    usage_total: dict[str, int] = field(default_factory=dict)
    pending_events: list[dict[str, Any]] = field(default_factory=list)
    current_command_id: str | None = None
    turn_finished: threading.Event = field(default_factory=threading.Event)
    turn_id: str | None = None
    heartbeat: threading.Thread | None = None
    stop_heartbeat: threading.Event = field(default_factory=threading.Event)
    heartbeat_error: list[BaseException] = field(default_factory=list)


@dataclass(frozen=True)
class _ValidatedRequest:
    worker_id: str
    run_config: AgentRunConfig
    binary: Path
    ttl: int
    model: str | None
    reasoning_effort: str | None


@dataclass(frozen=True)
class _RunFiles:
    stdout_path: Path
    agent_log_path: Path
    report_path: Path
    journal: CommandJournal
    started: float


class AppServerAttemptRunner:
    """Run one implementation attempt through the provider command stream."""

    def __init__(
        self,
        config: IssuekitConfig,
        issue: Issue,
        *,
        recovery: bool = False,
        transport_factory: Callable[..., AppServerTransport] = AppServerTransport,
    ) -> None:
        self.config = config
        self.issue = issue
        self.recovery = recovery
        self.transport_factory = transport_factory

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
        **_: Any,
    ) -> AgentResult:
        issue_id, request = self._validate_request(adapter, agent_name, issue_id)
        files = self._prepare_run_files(prompt, repo, run_dir, issue_id)
        pointer = self._compose_pointer(adapter, prompt, prompt_suffix, files.report_path)
        state = _AttemptState()
        with client_for(self.config, keepalive=True) as client:
            parent, resume = self._recovery_ancestry(client, issue_id, repo)
            context = self._create_and_acquire(
                client,
                issue_id,
                agent_name,
                request.worker_id,
                request.ttl,
                parent_session_id=parent,
                resume_from_session_id=resume,
            )
            self._start_heartbeat(client, issue_id, context, request.ttl, state)
            transport: AppServerTransport | None = None
            try:
                with os.fdopen(
                    open_owner_only_new(files.agent_log_path),
                    "w",
                    encoding="utf-8",
                    newline="\n",
                ) as log:
                    transport = self.transport_factory(
                        request.binary,
                        request.run_config.app_server_argv,
                        cwd=repo,
                        stderr=log,
                        notification=lambda message: self._on_notification(
                            message, context, state
                        ),
                    )
                    transport.initialize()
                    self._start_native_session(
                        client, issue_id, context, transport, repo,
                        request, resume, state,
                    )
                    self._start_turn(
                        client, issue_id, context, transport, files.journal,
                        pointer, state,
                    )
                    self._await_turn(
                        client, issue_id, context, transport, files, timeout,
                        abort_event, state,
                    )
                    turn_succeeded = self._finish_turn_command(
                        client, issue_id, context, state
                    )
                    if turn_succeeded:
                        self._close_session_command(
                            client, issue_id, context, transport, files.journal
                        )
                    state.exit_code = 124 if state.timed_out else 0 if turn_succeeded else 1
            except WorkflowError as exc:
                if exc.code not in LEASE_STOP_CODES:
                    self._best_effort_failure(client, issue_id, context, exc.code)
                raise
            except BaseException:
                self._best_effort_failure(client, issue_id, context, "runtime_failed")
                raise
            finally:
                self._teardown(client, issue_id, context, transport, state)

        return self._write_result(files, state)

    def _validate_request(
        self, adapter: AgentAdapter, agent_name: str | None, issue_id: int | None
    ) -> tuple[int, _ValidatedRequest]:
        if issue_id is None:
            raise ValueError("App Server attempt requires an issue id.")
        if agent_name != "codex":
            raise ValueError("codex_app_server runtime is Codex-only.")
        require_api_url(
            self.config,
            "Codex App Server runtime",
            error=ValueError,
        )
        worker_id = self.config.worker_key()
        if worker_id is None:
            raise ValueError("codex_app_server runtime requires a registered worker.")
        run_config = getattr(adapter, "run_config", None)
        if run_config is None:
            raise ValueError("codex_app_server runtime requires declarative Codex config.")
        binary = adapter.resolve_binary()
        model, reasoning_effort = adapter.effective_runtime()
        return issue_id, _ValidatedRequest(
            worker_id=worker_id,
            run_config=run_config,
            binary=binary,
            ttl=run_config.lease_ttl_seconds,
            model=model,
            reasoning_effort=reasoning_effort,
        )

    def _prepare_run_files(
        self,
        prompt: AgentPrompt,
        repo: Path,
        run_dir: Path | None,
        issue_id: int,
    ) -> _RunFiles:
        run_dir = prepare_run_dir(repo, run_dir)
        prompt.path.parent.mkdir(parents=True, exist_ok=True)
        write_owner_only_text(prompt.path, prompt.body)
        run_id = f"app-server-{issue_id}-{uuid.uuid4().hex[:8]}"
        return _RunFiles(
            stdout_path=run_dir / f"{run_id}.out.log",
            agent_log_path=run_dir / f"{run_id}.agent.log",
            report_path=run_dir / f"{run_id}.report.md",
            journal=CommandJournal(run_dir / f"{run_id}.commands.jsonl"),
            started=time.monotonic(),
        )

    def _compose_pointer(
        self,
        adapter: AgentAdapter,
        prompt: AgentPrompt,
        prompt_suffix: str | None,
        report_path: Path,
    ) -> str:
        pointer = prompt.pointer.replace(
            implementation_report_instruction(
                "the path in $ISSUEKIT_IMPLEMENTER_REPORT_FILE"
            ),
            implementation_report_instruction(str(report_path)),
        )
        if prompt_suffix:
            pointer = f"{pointer}\n\n{prompt_suffix}"
        pointer = adapter.compose_prompt(pointer)
        if self.recovery:
            pointer = (
                "This is a recovery attempt. Inspect the current worktree before "
                "making changes and do not assume a previous turn was delivered.\n\n"
                f"{pointer}"
            )
        return pointer

    def _start_heartbeat(
        self,
        client: IssuekitClient,
        issue_id: int,
        context: AttemptContext,
        ttl: int,
        state: _AttemptState,
    ) -> None:
        state.session_id = context.session_id
        state.heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            args=(
                client,
                issue_id,
                context,
                ttl,
                state.stop_heartbeat,
                state.heartbeat_error,
            ),
            daemon=True,
        )
        state.heartbeat.start()

    def _on_notification(
        self,
        message: dict[str, Any],
        context: AttemptContext,
        state: _AttemptState,
    ) -> None:
        state.event_number += 1
        event = normalize_notification(
            message,
            event_key=f"{context.session_id}:{state.event_number}",
            command_id=state.current_command_id,
        )
        if event is not None:
            if event["event_type"] == "turn_failed":
                payload = event.get("payload")
                message_text = (
                    payload.get("message") if isinstance(payload, Mapping) else None
                )
                if isinstance(message_text, str):
                    state.failure_reason = message_text
            total = _usage_total(event)
            if total:
                state.usage_total.clear()
                state.usage_total.update(total)
            state.pending_events.append(event)
            if event["event_type"] in {
                "turn_completed",
                "turn_failed",
                "turn_interrupted",
            }:
                state.terminal_event.append(event["event_type"])
                state.turn_finished.set()

    def _start_native_session(
        self,
        client: IssuekitClient,
        issue_id: int,
        context: AttemptContext,
        transport: AppServerTransport,
        repo: Path,
        request: _ValidatedRequest,
        resume: str | None,
        state: _AttemptState,
    ) -> None:
        self._append_event(
            client,
            issue_id,
            context,
            {
                "event_key": f"{context.session_id}:runtime-started",
                "event_type": "runtime_started",
                "payload": {"transport": "stdio", "runtime": "codex_app_server"},
            },
        )
        if resume:
            native_session_id = transport.resume_thread(
                self._native_id(client, issue_id, resume), cwd=repo
            )
        else:
            native_session_id = transport.start_thread(
                cwd=repo,
                model=request.model,
                reasoning_effort=request.reasoning_effort,
            )
        state.native_session_id = native_session_id
        client.attach_native_agent_session(
            issue_id,
            context.session_id,
            native_session_id,
            headers=context.headers,
            resume_from_session_id=resume,
        )
        self._append_event(
            client,
            issue_id,
            context,
            {
                "event_key": f"{context.session_id}:native-session",
                "event_type": "native_session_attached",
                "payload": {"native_session_id": native_session_id},
            },
        )

    def _start_turn(
        self,
        client: IssuekitClient,
        issue_id: int,
        context: AttemptContext,
        transport: AppServerTransport,
        journal: CommandJournal,
        pointer: str,
        state: _AttemptState,
    ) -> None:
        command = self._create_command(
            client,
            issue_id,
            context.session_id,
            {
                "idempotency_key": str(uuid.uuid4()),
                "kind": "turn_start",
                "expected_turn_id": None,
                "payload": {"prompt": pointer},
            },
        )
        claimed = client.claim_agent_command(
            issue_id, context.session_id, headers=context.headers
        ).get("command")
        if not isinstance(claimed, dict) or claimed.get("id") != command.get("id"):
            raise AppServerError("Provider did not return the initial command.")
        journal.record(claimed)
        state.current_command_id = str(claimed["id"])
        if state.native_session_id is None:
            raise AppServerError("Native session was not started.")
        state.turn_id = transport.start_turn(state.native_session_id, pointer)
        client.acknowledge_agent_command(
            issue_id,
            context.session_id,
            state.current_command_id,
            {"state": "accepted", "result": {"turn_id": state.turn_id}},
            headers=context.headers,
        )

    def _await_turn(
        self,
        client: IssuekitClient,
        issue_id: int,
        context: AttemptContext,
        transport: AppServerTransport,
        files: _RunFiles,
        timeout: float,
        abort_event: threading.Event | None,
        state: _AttemptState,
    ) -> None:
        if state.native_session_id is None or state.turn_id is None:
            raise AppServerError("Turn was not started.")
        deadline = time.monotonic() + timeout
        while not state.turn_finished.is_set():
            process_exit = transport.process.poll()
            if process_exit is not None:
                detail = _stderr_tail(files.agent_log_path)
                message = f"Codex App Server exited with status {process_exit}."
                if detail:
                    message = f"{message} Stderr tail: {detail}"
                raise AppServerError(message)
            if abort_event is not None and abort_event.is_set():
                transport.interrupt_turn(state.native_session_id, state.turn_id)
            if state.heartbeat_error:
                raise state.heartbeat_error[0]
            self._flush_events(client, issue_id, context, state.pending_events)
            self._execute_pending_command(
                client,
                issue_id,
                context,
                transport,
                files.journal,
                state.native_session_id,
                state.turn_id,
            )
            if time.monotonic() >= deadline:
                state.timed_out = True
                transport.interrupt_turn(state.native_session_id, state.turn_id)
                break
            state.turn_finished.wait(1.0)
        self._flush_events(client, issue_id, context, state.pending_events)

    def _finish_turn_command(
        self,
        client: IssuekitClient,
        issue_id: int,
        context: AttemptContext,
        state: _AttemptState,
    ) -> bool:
        if state.current_command_id is None or state.turn_id is None:
            raise AppServerError("Turn command was not started.")
        turn_succeeded = (
            not state.timed_out
            and bool(state.terminal_event)
            and state.terminal_event[-1] == "turn_completed"
        )
        request = (
            {"state": "succeeded", "result": {"turn_id": state.turn_id}}
            if turn_succeeded
            else {
                "state": "failed",
                "error_code": (
                    "timeout"
                    if state.timed_out
                    else state.terminal_event[-1]
                    if state.terminal_event
                    else "turn_failed"
                ),
                "result": {"turn_id": state.turn_id},
            }
        )
        client.acknowledge_agent_command(
            issue_id,
            context.session_id,
            state.current_command_id,
            request,
            headers=context.headers,
        )
        return turn_succeeded

    def _close_session_command(
        self,
        client: IssuekitClient,
        issue_id: int,
        context: AttemptContext,
        transport: AppServerTransport,
        journal: CommandJournal,
    ) -> None:
        close_command = self._create_command(
            client,
            issue_id,
            context.session_id,
            {
                "idempotency_key": str(uuid.uuid4()),
                "kind": "session_close",
                "expected_turn_id": None,
                "payload": {},
            },
        )
        claimed = client.claim_agent_command(
            issue_id, context.session_id, headers=context.headers
        ).get("command")
        if not isinstance(claimed, dict) or claimed.get("id") != close_command.get("id"):
            raise AppServerError("Provider did not return the session-close command.")
        journal.record(claimed)
        close_id = str(claimed["id"])
        transport.close()
        client.acknowledge_agent_command(
            issue_id,
            context.session_id,
            close_id,
            {"state": "accepted"},
            headers=context.headers,
        )
        client.acknowledge_agent_command(
            issue_id,
            context.session_id,
            close_id,
            {"state": "succeeded"},
            headers=context.headers,
        )

    def _teardown(
        self,
        client: IssuekitClient,
        issue_id: int,
        context: AttemptContext,
        transport: AppServerTransport | None,
        state: _AttemptState,
    ) -> None:
        if transport is not None:
            process_exit = transport.close()
            try:
                self._append_event(
                    client,
                    issue_id,
                    context,
                    {
                        "event_key": f"{context.session_id}:runtime-stopped",
                        "event_type": "runtime_stopped",
                        "payload": (
                            {"exit_code": process_exit, "usage": dict(state.usage_total)}
                            if state.usage_total
                            else {"exit_code": process_exit}
                        ),
                    },
                )
                client.seal_agent_session(
                    issue_id,
                    context.session_id,
                    "implementation_turn_finished",
                    headers=context.headers,
                )
                close_request = (
                    {
                        "outcome": "closed",
                        "reason": "implementation_turn_finished",
                    }
                    if state.exit_code == 0
                    else {
                        "outcome": "failed",
                        "reason": "implementation_turn_failed",
                        "error_code": (
                            "timeout"
                            if state.timed_out
                            else state.terminal_event[-1]
                            if state.terminal_event
                            else "turn_failed"
                        ),
                    }
                )
                client.close_agent_session(
                    issue_id,
                    context.session_id,
                    close_request,
                    headers=context.headers,
                )
            except WorkflowError:
                pass
        state.stop_heartbeat.set()
        if state.heartbeat is not None:
            state.heartbeat.join(timeout=2)
        try:
            client.release_agent_session_lease(
                issue_id, context.session_id, headers=context.headers
            )
        except WorkflowError:
            pass

    def _write_result(self, files: _RunFiles, state: _AttemptState) -> AgentResult:
        with os.fdopen(
            open_owner_only_new(files.stdout_path),
            "w",
            encoding="utf-8",
            newline="\n",
        ) as handle:
            handle.write(
                json.dumps(
                    {
                        "runtime": "codex_app_server",
                        "session_id": state.session_id,
                        "native_session_id": state.native_session_id,
                        "exit_code": state.exit_code,
                        "usage": dict(state.usage_total),
                    },
                    ensure_ascii=True,
                )
                + "\n"
            )
        parsed = {
            "runtime": "codex_app_server",
            "agent_session_id": state.session_id or "",
            "native_session_id": state.native_session_id or "",
            **encode_usage(state.usage_total),
        }
        if state.failure_reason is not None:
            parsed["failure_reason"] = state.failure_reason
        if state.terminal_event and state.terminal_event[-1] == "turn_failed":
            parsed["is_error"] = "true"
        return AgentResult(
            exit_code=state.exit_code,
            stdout_path=files.stdout_path,
            agent_log_path=files.agent_log_path,
            elapsed_sec=time.monotonic() - files.started,
            timed_out=state.timed_out,
            parsed=parsed,
            status_short=None,
            report_path=files.report_path,
        )

    def _create_and_acquire(
        self,
        client: IssuekitClient,
        issue_id: int,
        agent: str,
        worker_id: str,
        ttl: int,
        *,
        parent_session_id: str | None,
        resume_from_session_id: str | None,
    ) -> AttemptContext:
        request = {
            "idempotency_key": str(uuid.uuid4()),
            "role": "implementer",
            "agent": agent,
            "runtime": "codex_app_server",
            "worker_id": worker_id,
        }
        if parent_session_id:
            request["parent_session_id"] = parent_session_id
        if resume_from_session_id:
            request["resume_from_session_id"] = resume_from_session_id
        session = _retry_once(lambda: client.create_agent_session(issue_id, request))
        session_id = session.get("id")
        if not isinstance(session_id, str):
            raise WorkflowError(
                "Agent session response omitted id.", code="invalid_response"
            )
        lease_token = secrets.token_urlsafe(32)
        lease_request = {
            "worker_id": worker_id,
            "acquire_key": str(uuid.uuid4()),
            "lease_token": lease_token,
            "ttl_seconds": ttl,
        }
        lease = _retry_once(
            lambda: client.acquire_agent_session_lease(
                issue_id, session_id, lease_request
            )
        )
        generation = lease.get("generation")
        if not isinstance(generation, int):
            raise WorkflowError(
                "Agent lease response omitted generation.", code="invalid_response"
            )
        return AttemptContext(
            session_id=session_id,
            generation=generation,
            worker_id=worker_id,
            lease_token=lease_token,
            headers={
                "X-Issue-Agent-Worker": worker_id,
                "X-Issue-Agent-Lease": lease_token,
                "X-Issue-Agent-Generation": str(generation),
            },
        )

    def _heartbeat_loop(
        self,
        client: IssuekitClient,
        issue_id: int,
        context: AttemptContext,
        ttl: int,
        stop: threading.Event,
        errors: list[BaseException],
    ) -> None:
        interval = max(1.0, ttl * 0.4)
        last_success = time.monotonic()
        last_error: BaseException | None = None
        while not stop.wait(interval):
            if (
                last_error is not None
                and time.monotonic() - last_success >= ttl * 0.5
            ):
                errors.append(last_error)
                stop.set()
                return
            try:
                client.heartbeat_agent_session_lease(
                    issue_id,
                    context.session_id,
                    headers=context.headers,
                    ttl_seconds=ttl,
                )
                last_success = time.monotonic()
                last_error = None
                interval = max(1.0, ttl * 0.4)
            except WorkflowError as exc:
                if exc.code == "request_failed":
                    age = time.monotonic() - last_success
                    if age < ttl * 0.5:
                        last_error = exc
                        interval = min(1.0, ttl * 0.5 - age)
                        continue
                errors.append(exc)
                stop.set()
                return
            except BaseException as exc:
                errors.append(exc)
                stop.set()
                return

    def _execute_pending_command(
        self,
        client: IssuekitClient,
        issue_id: int,
        context: AttemptContext,
        transport: AppServerTransport,
        journal: CommandJournal,
        native_session_id: str,
        turn_id: str,
    ) -> None:
        claimed = client.claim_agent_command(
            issue_id, context.session_id, headers=context.headers
        ).get("command")
        if not isinstance(claimed, dict):
            return
        command_id = str(claimed.get("id"))
        if command_id in journal.command_ids():
            raise WorkflowError(
                "A journaled command was redelivered; delivery is ambiguous.",
                code="delivery_unknown",
            )
        journal.record(claimed)
        kind = claimed.get("kind")
        payload = claimed.get("payload")
        payload = payload if isinstance(payload, dict) else {}
        expected_turn_id = claimed.get("expected_turn_id")
        try:
            if kind == "turn_steer":
                prompt = payload.get("prompt", payload.get("text", ""))
                transport.steer_turn(
                    native_session_id, str(expected_turn_id), str(prompt)
                )
            elif kind == "turn_interrupt":
                transport.interrupt_turn(native_session_id, str(expected_turn_id))
            elif kind == "session_close":
                transport.close()
            else:
                raise AppServerError(f"Unsupported provider command kind: {kind}")
            client.acknowledge_agent_command(
                issue_id,
                context.session_id,
                command_id,
                {"state": "accepted"},
                headers=context.headers,
            )
            client.acknowledge_agent_command(
                issue_id,
                context.session_id,
                command_id,
                {"state": "succeeded", "result": {"turn_id": turn_id}},
                headers=context.headers,
            )
        except BaseException as exc:
            client.acknowledge_agent_command(
                issue_id,
                context.session_id,
                command_id,
                {"state": "failed", "error_code": "local_side_effect_failed"},
                headers=context.headers,
            )
            raise exc

    def _append_event(
        self,
        client: IssuekitClient,
        issue_id: int,
        context: AttemptContext,
        event: dict[str, Any],
    ) -> None:
        client.append_agent_events(
            issue_id, context.session_id, [event], headers=context.headers
        )

    def _create_command(
        self,
        client: IssuekitClient,
        issue_id: int,
        session_id: str,
        request: dict[str, Any],
    ) -> dict[str, Any]:
        return _retry_once(
            lambda: client.create_agent_command(issue_id, session_id, request)
        )

    def _flush_events(
        self,
        client: IssuekitClient,
        issue_id: int,
        context: AttemptContext,
        pending_events: list[dict[str, Any]],
    ) -> None:
        while pending_events:
            batch = pending_events[:100]
            _retry_once(
                lambda batch=batch: client.append_agent_events(
                    issue_id, context.session_id, batch, headers=context.headers
                )
            )
            del pending_events[: len(batch)]

    def _recovery_ancestry(
        self, client: IssuekitClient, issue_id: int, repo: Path
    ) -> tuple[str | None, str | None]:
        page = client.list_agent_sessions(issue_id, limit=100)
        items = page.get("items")
        if not isinstance(items, list):
            return None, None
        previous = next(
            (
                item
                for item in reversed(items)
                if isinstance(item, dict) and item.get("state") in {"sealed", "failed"}
            ),
            None,
        )
        if not isinstance(previous, dict) or not isinstance(previous.get("id"), str):
            return None, None
        parent = previous["id"]
        ambiguous_commands = client.list_agent_commands(
            issue_id, parent, state="delivery_unknown", limit=1
        ).get("items")
        has_ambiguous_delivery = (
            isinstance(ambiguous_commands, list) and bool(ambiguous_commands)
        )
        affinity_matches = (
            self.recovery
            and previous.get("machine_id")
            == (self.config.worker.machine_id if self.config.worker else None)
            and previous.get("repo_key")
            == (self.config.worker.repo_id if self.config.worker else None)
            and previous.get("checkout_path") == str(repo.resolve())
            and isinstance(previous.get("native_session_id"), str)
            and not has_ambiguous_delivery
            and previous.get("active_turn_id") is None
        )
        return parent, parent if affinity_matches else None

    def _native_id(
        self, client: IssuekitClient, issue_id: int, session_id: str
    ) -> str:
        session = client.get_agent_session(issue_id, session_id)
        native_id = session.get("native_session_id")
        if not isinstance(native_id, str):
            raise WorkflowError(
                "Recovery session has no native session id.", code="invalid_response"
            )
        return native_id

    def _best_effort_failure(
        self,
        client: IssuekitClient,
        issue_id: int,
        context: AttemptContext,
        error_code: str,
    ) -> None:
        try:
            client.close_agent_session(
                issue_id,
                context.session_id,
                {
                    "outcome": "failed",
                    "reason": "local_runtime_failure",
                    "error_code": error_code,
                },
                headers=context.headers,
            )
        except WorkflowError:
            pass


def _stderr_tail(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, os.SEEK_END)
            size = stream.tell()
            stream.seek(max(0, size - STDERR_TAIL_CHARS))
            return (
                stream.read(STDERR_TAIL_CHARS)
                .decode("utf-8", errors="replace")
                .strip()
            )
    except OSError:
        return ""

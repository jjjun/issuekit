"""Issue-owned bridge between mine-py commands and local Codex App Server."""

from __future__ import annotations

import json
import secrets
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from issuekit.agentrun.adapter import AgentAdapter
from issuekit.agentrun.app_server import (
    AppServerError,
    AppServerTransport,
    CommandJournal,
    normalize_notification,
)
from issuekit.agentrun.runner import (
    AgentPrompt,
    AgentResult,
    implementation_report_instruction,
)
from issuekit.api import IssuekitClient
from issuekit.api.features import is_feature_unavailable
from issuekit.config import IssuekitConfig
from issuekit.core import Issue
from issuekit.workflow import WorkflowError

LEASE_STOP_CODES = frozenset(
    {
        "claim_lost",
        "lease_expired",
        "lease_token_invalid",
        "stale_generation",
    }
)


def _usage_total(event: Mapping[str, Any]) -> dict[str, int]:
    """Return the cumulative thread token counts carried by a normalized event."""
    payload = event.get("payload")
    usage = payload.get("usage") if isinstance(payload, Mapping) else None
    total = usage.get("total") if isinstance(usage, Mapping) else None
    if not isinstance(total, Mapping):
        return {}
    return {
        str(name): count
        for name, count in total.items()
        if isinstance(count, int) and not isinstance(count, bool)
    }


@dataclass(frozen=True)
class AttemptContext:
    session_id: str
    generation: int
    worker_id: str
    lease_token: str
    headers: dict[str, str]


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
        del follow
        if issue_id is None:
            raise ValueError("App Server attempt requires an issue id.")
        if agent_name != "codex":
            raise ValueError("codex_app_server runtime is Codex-only.")
        if not self.config.api_url:
            raise ValueError("codex_app_server runtime requires api_url.")
        worker_id = self.config.worker_key()
        if worker_id is None:
            raise ValueError("codex_app_server runtime requires a registered worker.")

        run_dir = (run_dir or repo / ".agent-runs").resolve()
        run_dir.mkdir(parents=True, exist_ok=True)
        prompt.path.parent.mkdir(parents=True, exist_ok=True)
        prompt.path.write_text(prompt.body, encoding="utf-8", newline="\n")
        run_id = f"app-server-{issue_id}-{uuid.uuid4().hex[:8]}"
        stdout_path = run_dir / f"{run_id}.out.log"
        agent_log_path = run_dir / f"{run_id}.agent.log"
        report_path = run_dir / f"{run_id}.report.md"
        journal = CommandJournal(run_dir / f"{run_id}.commands.jsonl")
        started = time.monotonic()
        timed_out = False
        exit_code = 1
        native_session_id: str | None = None
        session_id: str | None = None

        run_config = getattr(adapter, "run_config", None)
        if run_config is None:
            raise ValueError("codex_app_server runtime requires declarative Codex config.")
        binary = adapter.resolve_binary()
        ttl = run_config.lease_ttl_seconds
        model, reasoning_effort = adapter.effective_runtime()
        pointer = prompt.pointer
        pointer = pointer.replace(
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

        with IssuekitClient(
            self.config.api_url,
            project=self.config.project,
            timeout=self.config.api_timeout,
        ) as client:
            parent, resume = self._recovery_ancestry(client, issue_id, repo)
            context = self._create_and_acquire(
                client,
                issue_id,
                agent_name,
                worker_id,
                ttl,
                parent_session_id=parent,
                resume_from_session_id=resume,
            )
            session_id = context.session_id
            stop_heartbeat = threading.Event()
            heartbeat_error: list[BaseException] = []
            heartbeat = threading.Thread(
                target=self._heartbeat_loop,
                args=(client, issue_id, context, ttl, stop_heartbeat, heartbeat_error),
                daemon=True,
            )
            heartbeat.start()
            pending_events: list[dict[str, Any]] = []
            usage_total: dict[str, int] = {}
            event_number = 0
            current_command_id: str | None = None
            turn_finished = threading.Event()
            terminal_event: list[str] = []

            def on_notification(message: dict[str, Any]) -> None:
                nonlocal event_number
                event_number += 1
                event = normalize_notification(
                    message,
                    event_key=f"{context.session_id}:{event_number}",
                    command_id=current_command_id,
                )
                if event is not None:
                    total = _usage_total(event)
                    if total:
                        usage_total.clear()
                        usage_total.update(total)
                    pending_events.append(event)
                    if event["event_type"] in {
                        "turn_completed",
                        "turn_failed",
                        "turn_interrupted",
                    }:
                        terminal_event.append(event["event_type"])
                        turn_finished.set()

            transport: AppServerTransport | None = None
            try:
                with agent_log_path.open("w", encoding="utf-8", newline="\n") as log:
                    transport = self.transport_factory(
                        binary,
                        run_config.app_server_argv,
                        cwd=repo,
                        stderr=log,
                        notification=on_notification,
                    )
                    transport.initialize()
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
                            model=model,
                            reasoning_effort=reasoning_effort,
                        )
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
                    current_command_id = str(claimed["id"])
                    turn_id = transport.start_turn(native_session_id, pointer)
                    client.acknowledge_agent_command(
                        issue_id,
                        context.session_id,
                        current_command_id,
                        {"state": "accepted", "result": {"turn_id": turn_id}},
                        headers=context.headers,
                    )
                    deadline = time.monotonic() + timeout
                    while not turn_finished.is_set():
                        if abort_event is not None and abort_event.is_set():
                            transport.interrupt_turn(native_session_id, turn_id)
                        if heartbeat_error:
                            raise heartbeat_error[0]
                        self._flush_events(
                            client, issue_id, context, pending_events
                        )
                        self._execute_pending_command(
                            client,
                            issue_id,
                            context,
                            transport,
                            journal,
                            native_session_id,
                            turn_id,
                        )
                        if time.monotonic() >= deadline:
                            timed_out = True
                            transport.interrupt_turn(native_session_id, turn_id)
                            break
                        turn_finished.wait(0.1)
                    self._flush_events(client, issue_id, context, pending_events)
                    client.acknowledge_agent_command(
                        issue_id,
                        context.session_id,
                        current_command_id,
                        (
                            {
                                "state": "failed",
                                "error_code": (
                                    "timeout"
                                    if timed_out
                                    else terminal_event[-1]
                                    if terminal_event
                                    else "turn_failed"
                                ),
                                "result": {"turn_id": turn_id},
                            }
                            if timed_out
                            or not terminal_event
                            or terminal_event[-1] != "turn_completed"
                            else {
                                "state": "succeeded",
                                "result": {"turn_id": turn_id},
                            }
                        ),
                        headers=context.headers,
                    )
                    turn_succeeded = (
                        not timed_out
                        and bool(terminal_event)
                        and terminal_event[-1] == "turn_completed"
                    )
                    if turn_succeeded:
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
                        claimed_close = client.claim_agent_command(
                            issue_id, context.session_id, headers=context.headers
                        ).get("command")
                        if (
                            not isinstance(claimed_close, dict)
                            or claimed_close.get("id") != close_command.get("id")
                        ):
                            raise AppServerError(
                                "Provider did not return the session-close command."
                            )
                        journal.record(claimed_close)
                        close_id = str(claimed_close["id"])
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
                    exit_code = 124 if timed_out else 0 if turn_succeeded else 1
            except WorkflowError as exc:
                if exc.code not in LEASE_STOP_CODES:
                    self._best_effort_failure(client, issue_id, context, exc.code)
                raise
            except BaseException:
                self._best_effort_failure(client, issue_id, context, "runtime_failed")
                raise
            finally:
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
                                    {"exit_code": process_exit, "usage": dict(usage_total)}
                                    if usage_total
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
                            if exit_code == 0
                            else {
                                "outcome": "failed",
                                "reason": "implementation_turn_failed",
                                "error_code": (
                                    "timeout"
                                    if timed_out
                                    else terminal_event[-1]
                                    if terminal_event
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
                stop_heartbeat.set()
                heartbeat.join(timeout=2)
                try:
                    client.release_agent_session_lease(
                        issue_id, context.session_id, headers=context.headers
                    )
                except WorkflowError:
                    pass

        stdout_path.write_text(
            json.dumps(
                {
                    "runtime": "codex_app_server",
                    "session_id": session_id,
                    "native_session_id": native_session_id,
                    "exit_code": exit_code,
                    "usage": dict(usage_total),
                },
                ensure_ascii=True,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return AgentResult(
            exit_code=exit_code,
            stdout_path=stdout_path,
            agent_log_path=agent_log_path,
            elapsed_sec=time.monotonic() - started,
            timed_out=timed_out,
            parsed={
                "runtime": "codex_app_server",
                "agent_session_id": session_id or "",
                "native_session_id": native_session_id or "",
                **{f"usage_{name}": str(count) for name, count in usage_total.items()},
            },
            status_short=None,
            report_path=report_path,
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
        try:
            try:
                session = client.create_agent_session(issue_id, request)
            except WorkflowError as exc:
                if exc.code != "request_failed":
                    raise
                session = client.create_agent_session(issue_id, request)
        except WorkflowError as exc:
            if not is_feature_unavailable(exc):
                raise
            raise WorkflowError(
                "The provider does not support issue agent sessions; "
                "use runtime='exec' or upgrade the provider.",
                code="unsupported_runtime",
            ) from exc
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
        try:
            lease = client.acquire_agent_session_lease(
                issue_id, session_id, lease_request
            )
        except WorkflowError as exc:
            if exc.code != "request_failed":
                raise
            lease = client.acquire_agent_session_lease(
                issue_id, session_id, lease_request
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
        while not stop.wait(max(1.0, ttl * 0.4)):
            try:
                client.heartbeat_agent_session_lease(
                    issue_id,
                    context.session_id,
                    headers=context.headers,
                    ttl_seconds=ttl,
                )
            except BaseException as exc:
                errors.append(exc)
                stop.set()

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
        try:
            return client.create_agent_command(issue_id, session_id, request)
        except WorkflowError as exc:
            if exc.code != "request_failed":
                raise
            return client.create_agent_command(issue_id, session_id, request)

    def _flush_events(
        self,
        client: IssuekitClient,
        issue_id: int,
        context: AttemptContext,
        pending_events: list[dict[str, Any]],
    ) -> None:
        while pending_events:
            batch = pending_events[:100]
            try:
                client.append_agent_events(
                    issue_id, context.session_id, batch, headers=context.headers
                )
            except WorkflowError as exc:
                if exc.code != "request_failed":
                    raise
                client.append_agent_events(
                    issue_id, context.session_id, batch, headers=context.headers
                )
            del pending_events[: len(batch)]

    def _recovery_ancestry(
        self, client: IssuekitClient, issue_id: int, repo: Path
    ) -> tuple[str | None, str | None]:
        try:
            page = client.list_agent_sessions(issue_id, limit=100)
        except WorkflowError as exc:
            if not is_feature_unavailable(exc):
                raise
            raise WorkflowError(
                "The provider does not support issue agent sessions; "
                "use runtime='exec' or upgrade the provider.",
                code="unsupported_runtime",
            ) from exc
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

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from textwrap import dedent

import pytest

from issuekit.agentrun.config import AgentRunConfig
from issuekit.agentrun.runner import AgentPrompt
from issuekit.agents import app_server_runtime
from issuekit.agents.app_server_runtime import AppServerAttemptRunner
from issuekit.agents.run_claimed import implementation_prompt
from issuekit.api.client import BURST_HTTP_LIMITS
from issuekit.config import IssuekitConfig, WorkerIdentity
from issuekit.core import Issue
from issuekit.errors import WorkflowError


class FakeAdapter:
    run_config = AgentRunConfig(
        binary="codex",
        runtime="codex_app_server",
        app_server_argv=("app-server",),
    )

    def resolve_binary(self) -> Path:
        return Path("codex")

    def effective_runtime(self) -> tuple[None, None]:
        return None, None

    def compose_prompt(self, prompt: str) -> str:
        return f"{prompt}\n\nMake minimal, additive diffs."


class FakeAgentSessionClient:
    instances: list[FakeAgentSessionClient] = []
    create_error: WorkflowError | None = None
    list_error: WorkflowError | None = None

    def __init__(self, *args, **kwargs) -> None:
        self.init_kwargs = kwargs
        self.commands: list[dict[str, object]] = []
        self.events: list[dict[str, object]] = []
        self.acknowledgements: list[tuple[str, dict[str, object]]] = []
        self.closed_sessions: list[dict[str, object]] = []
        self.__class__.instances.append(self)

    def __enter__(self) -> FakeAgentSessionClient:
        return self

    def __exit__(self, *args) -> None:
        return None

    def list_agent_sessions(self, *args, **kwargs) -> dict[str, object]:
        if self.list_error is not None:
            raise self.list_error
        return {"items": []}

    def create_agent_session(
        self, issue_id: int, request: dict[str, object]
    ) -> dict[str, object]:
        if self.create_error is not None:
            raise self.create_error
        return {"id": "session-1"}

    def acquire_agent_session_lease(
        self, issue_id: int, session_id: str, request: dict[str, object]
    ) -> dict[str, object]:
        return {"generation": 1}

    def append_agent_events(
        self,
        issue_id: int,
        session_id: str,
        events: list[dict[str, object]],
        *,
        headers: dict[str, str],
    ) -> dict[str, object]:
        self.events.extend(events)
        return {"items": []}

    def attach_native_agent_session(self, *args, **kwargs) -> dict[str, object]:
        return {"id": "session-1"}

    def create_agent_command(
        self, issue_id: int, session_id: str, request: dict[str, object]
    ) -> dict[str, object]:
        command = {
            "id": f"command-{len(self.commands) + 1}",
            "sequence": len(self.commands) + 1,
            **request,
        }
        self.commands.append(command)
        return command

    def claim_agent_command(
        self, issue_id: int, session_id: str, *, headers: dict[str, str]
    ) -> dict[str, object]:
        command = next(
            (item for item in self.commands if not item.get("claimed")),
            None,
        )
        if command is not None:
            command["claimed"] = True
        return {"command": command}

    def acknowledge_agent_command(
        self,
        issue_id: int,
        session_id: str,
        command_id: str,
        request: dict[str, object],
        *,
        headers: dict[str, str],
    ) -> dict[str, object]:
        self.acknowledgements.append((command_id, request))
        return {"id": command_id, **request}

    def seal_agent_session(self, *args, **kwargs) -> dict[str, object]:
        return {"id": "session-1", "state": "sealed"}

    def close_agent_session(
        self,
        issue_id: int,
        session_id: str,
        request: dict[str, object],
        *,
        headers: dict[str, str],
    ) -> dict[str, object]:
        self.closed_sessions.append(request)
        return {"id": session_id, **request}

    def release_agent_session_lease(self, *args, **kwargs) -> None:
        return None


class FakeTransport:
    def __init__(
        self,
        *args,
        notification,
        complete_on_start: bool,
        **kwargs,
    ) -> None:
        self.notification = notification
        self.complete_on_start = complete_on_start
        self.interruptions: list[tuple[str, str]] = []
        self.prompts: list[str] = []
        self.process = FakeProcess()

    def initialize(self) -> None:
        return None

    def start_thread(self, **kwargs) -> str:
        return "thread-1"

    def start_turn(self, native_session_id: str, prompt: str) -> str:
        self.prompts.append(prompt)
        if self.complete_on_start:
            self.notification(
                {
                    "method": "thread/tokenUsage/updated",
                    "params": {
                        "threadId": native_session_id,
                        "turnId": "turn-1",
                        "tokenUsage": {
                            "last": {"inputTokens": 10, "totalTokens": 14},
                            "total": {"inputTokens": 30, "totalTokens": 42},
                        },
                    },
                }
            )
            self.notification(
                {
                    "method": "turn/completed",
                    "params": {"turn": {"id": "turn-1", "status": "completed"}},
                }
            )
        return "turn-1"

    def interrupt_turn(self, native_session_id: str, turn_id: str) -> None:
        self.interruptions.append((native_session_id, turn_id))
        self.notification(
            {
                "method": "turn/completed",
                "params": {"turn": {"id": turn_id, "status": "interrupted"}},
            }
        )

    def close(self) -> int:
        return 0


class FakeProcess:
    returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode


def make_issue() -> Issue:
    return Issue(
        id=322,
        ref="issuekit#322",
        title="App Server runtime",
        issue_status="active",
        created="2026-07-31",
        completed="",
        priority="medium",
        assignee="codex",
        stage="implementing",
        implementer="codex",
        author="claude",
        body="# Issue #322: App Server runtime\n",
        metadata={},
    )


def make_config(*, api_url: str = "https://mine.example", worker: bool = True) -> IssuekitConfig:
    return IssuekitConfig(
        api_url=api_url,
        worker=WorkerIdentity("machine", "repo", "checkout") if worker else None,
    )


def make_prompt(tmp_path: Path) -> AgentPrompt:
    return AgentPrompt(tmp_path / "plan.md", "plan", "Implement the plan.")


@pytest.mark.parametrize(
    ("issue_id", "agent_name", "config", "message"),
    [
        (None, "codex", make_config(), "issue id"),
        (322, "claude", make_config(), "Codex-only"),
        (322, "codex", make_config(api_url=""), "requires api_url"),
        (322, "codex", make_config(worker=False), "registered worker"),
    ],
)
def test_app_server_runner_rejects_invalid_context(
    tmp_path: Path,
    issue_id: int | None,
    agent_name: str,
    config: IssuekitConfig,
    message: str,
) -> None:
    runner = AppServerAttemptRunner(config, make_issue())

    with pytest.raises(ValueError, match=message):
        runner.run(
            FakeAdapter(),
            make_prompt(tmp_path),
            tmp_path,
            issue_id=issue_id,
            agent_name=agent_name,
        )


def test_app_server_runner_returns_result_for_successful_attempt(
    fake_api,
    tmp_path: Path, monkeypatch
) -> None:
    FakeAgentSessionClient.instances.clear()
    FakeAgentSessionClient.create_error = None
    transports: list[FakeTransport] = []

    def transport_factory(*args, **kwargs) -> FakeTransport:
        transport = FakeTransport(*args, complete_on_start=True, **kwargs)
        transports.append(transport)
        return transport

    fake_api.install_factory(FakeAgentSessionClient)
    runner = AppServerAttemptRunner(
        make_config(), make_issue(), transport_factory=transport_factory
    )

    result = runner.run(
        FakeAdapter(),
        make_prompt(tmp_path),
        tmp_path,
        issue_id=322,
        agent_name="codex",
        run_dir=tmp_path / "runs",
    )

    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.stdout_path.exists()
    assert result.agent_log_path.exists()
    assert result.report_path == tmp_path / "runs" / result.report_path.name
    assert result.parsed == {
        "runtime": "codex_app_server",
        "agent_session_id": "session-1",
        "native_session_id": "thread-1",
        "usage_input_tokens": "30",
        "usage_total_tokens": "42",
    }
    assert len(transports) == 1
    assert transports[0].prompts == [
        "Implement the plan.\n\nMake minimal, additive diffs."
    ]
    assert json.loads(result.stdout_path.read_text(encoding="utf-8"))["usage"] == {
        "input_tokens": 30,
        "total_tokens": 42,
    }
    client = FakeAgentSessionClient.instances[-1]
    assert client.init_kwargs["http_limits"] is BURST_HTTP_LIMITS
    stopped = [
        event for event in client.events if event["event_type"] == "runtime_stopped"
    ]
    assert stopped[-1]["payload"] == {
        "exit_code": 0,
        "usage": {"input_tokens": 30, "total_tokens": 42},
    }
    assert client.closed_sessions == [
        {
            "outcome": "closed",
            "reason": "implementation_turn_finished",
        }
    ]


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink behavior is required")
def test_app_server_runner_replaces_prompt_symlink_without_writing_through_it(
    fake_api,
    tmp_path: Path, monkeypatch
) -> None:
    FakeAgentSessionClient.instances.clear()
    FakeAgentSessionClient.create_error = None
    fake_api.install_factory(FakeAgentSessionClient)
    run_dir = tmp_path / "runs"
    run_dir.mkdir()
    prompt_path = run_dir / "issue-322.md"
    target = tmp_path / "outside.md"
    target.write_text("leave this prompt alone\n", encoding="utf-8")
    prompt_path.symlink_to(target)
    prompt = AgentPrompt(prompt_path, "safe prompt", "Implement the plan.")

    result = AppServerAttemptRunner(
        make_config(),
        make_issue(),
        transport_factory=lambda *args, **kwargs: FakeTransport(
            *args, complete_on_start=True, **kwargs
        ),
    ).run(
        FakeAdapter(),
        prompt,
        tmp_path,
        issue_id=322,
        agent_name="codex",
        run_dir=run_dir,
    )

    assert result.exit_code == 0
    assert target.read_text(encoding="utf-8") == "leave this prompt alone\n"
    assert not prompt_path.is_symlink()
    assert prompt_path.read_text(encoding="utf-8") == "safe prompt"


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink behavior is required")
def test_app_server_runner_refuses_a_symlinked_run_directory_before_writing(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (repo / ".agent-runs").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RuntimeError, match=r"Refusing \.agent-runs: it is a symlink"):
        AppServerAttemptRunner(make_config(), make_issue()).run(
            FakeAdapter(),
            AgentPrompt(
                repo / ".agent-runs" / "issue-322.md", "safe prompt", "pointer"
            ),
            repo,
            issue_id=322,
            agent_name="codex",
        )

    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits are not used on Windows")
def test_app_server_runner_creates_owner_only_artifacts(
    fake_api,
    tmp_path: Path, monkeypatch
) -> None:
    FakeAgentSessionClient.instances.clear()
    FakeAgentSessionClient.create_error = None
    fake_api.install_factory(FakeAgentSessionClient)
    run_dir = tmp_path / "runs"
    result = AppServerAttemptRunner(
        make_config(), make_issue(), transport_factory=lambda *args, **kwargs: FakeTransport(
            *args, complete_on_start=True, **kwargs
        )
    ).run(
        FakeAdapter(),
        make_prompt(tmp_path),
        tmp_path,
        issue_id=322,
        agent_name="codex",
        run_dir=run_dir,
    )

    assert run_dir.stat().st_mode & 0o777 == 0o700
    assert result.stdout_path.stat().st_mode & 0o777 == 0o600
    assert result.agent_log_path.stat().st_mode & 0o777 == 0o600
    assert next(run_dir.glob("*.commands.jsonl")).stat().st_mode & 0o777 == 0o600


def test_app_server_pointer_contains_one_concrete_report_instruction(
    fake_api,
    tmp_path: Path, monkeypatch
) -> None:
    FakeAgentSessionClient.instances.clear()
    FakeAgentSessionClient.create_error = None
    transports: list[FakeTransport] = []

    def transport_factory(*args, **kwargs) -> FakeTransport:
        transport = FakeTransport(*args, complete_on_start=True, **kwargs)
        transports.append(transport)
        return transport

    fake_api.install_factory(FakeAgentSessionClient)
    runner = AppServerAttemptRunner(
        make_config(), make_issue(), transport_factory=transport_factory
    )
    plan_path = tmp_path / "plan.md"

    result = runner.run(
        FakeAdapter(),
        AgentPrompt(plan_path, "plan", implementation_prompt(plan_path)),
        tmp_path,
        issue_id=322,
        agent_name="codex",
        run_dir=tmp_path / "runs",
    )

    pointer = transports[0].prompts[0]
    assert pointer.count("closing implementation and verification report") == 1
    assert "$ISSUEKIT_IMPLEMENTER_REPORT_FILE" not in pointer
    assert str(result.report_path) in pointer


@pytest.mark.parametrize("failure_point", ["session_list", "session_create"])
def test_app_server_runner_propagates_missing_session_error(
    fake_api,
    tmp_path: Path, monkeypatch, failure_point: str
) -> None:
    FakeAgentSessionClient.instances.clear()
    original_error = WorkflowError("Not found.", code="not_found")
    attribute = "list_error" if failure_point == "session_list" else "create_error"
    monkeypatch.setattr(FakeAgentSessionClient, attribute, original_error)
    fake_api.install_factory(FakeAgentSessionClient)
    runner = AppServerAttemptRunner(make_config(), make_issue())

    with pytest.raises(WorkflowError, match="Not found.") as exc_info:
        runner.run(
            FakeAdapter(),
            make_prompt(tmp_path),
            tmp_path,
            issue_id=322,
            agent_name="codex",
        )

    assert exc_info.value is original_error


def test_app_server_runner_interrupts_turn_when_aborted(
    fake_api,
    tmp_path: Path, monkeypatch
) -> None:
    FakeAgentSessionClient.instances.clear()
    FakeAgentSessionClient.create_error = None
    transports: list[FakeTransport] = []

    def transport_factory(*args, **kwargs) -> FakeTransport:
        transport = FakeTransport(*args, complete_on_start=False, **kwargs)
        transports.append(transport)
        return transport

    fake_api.install_factory(FakeAgentSessionClient)
    runner = AppServerAttemptRunner(
        make_config(), make_issue(), transport_factory=transport_factory
    )
    abort_event = threading.Event()
    abort_event.set()

    result = runner.run(
        FakeAdapter(),
        make_prompt(tmp_path),
        tmp_path,
        issue_id=322,
        agent_name="codex",
        abort_event=abort_event,
    )

    assert result.exit_code == 1
    assert transports[0].interruptions == [("thread-1", "turn-1")]
    assert FakeAgentSessionClient.instances[-1].closed_sessions == [
        {
            "outcome": "failed",
            "reason": "implementation_turn_failed",
            "error_code": "turn_interrupted",
        }
    ]


def test_app_server_runner_includes_failed_turn_message_in_result(
    fake_api,
    tmp_path: Path, monkeypatch
) -> None:
    FakeAgentSessionClient.instances.clear()
    FakeAgentSessionClient.create_error = None

    class FailedTransport(FakeTransport):
        def start_turn(self, native_session_id: str, prompt: str) -> str:
            turn_id = super().start_turn(native_session_id, prompt)
            self.notification(
                {
                    "method": "turn/completed",
                    "params": {
                        "turn": {
                            "id": turn_id,
                            "status": "failed",
                            "error": {"message": "model request failed"},
                        }
                    },
                }
            )
            return turn_id

    fake_api.install_factory(FakeAgentSessionClient)
    runner = AppServerAttemptRunner(
        make_config(),
        make_issue(),
        transport_factory=lambda *args, **kwargs: FailedTransport(
            *args, complete_on_start=False, **kwargs
        ),
    )

    result = runner.run(
        FakeAdapter(),
        make_prompt(tmp_path),
        tmp_path,
        issue_id=322,
        agent_name="codex",
    )

    assert result.exit_code == 1
    assert result.parsed is not None
    assert result.parsed["failure_reason"] == "model request failed"
    assert result.parsed["is_error"] == "true"


def test_app_server_runner_fails_fast_when_server_exits_mid_turn(
    fake_api,
    tmp_path: Path, monkeypatch
) -> None:
    FakeAgentSessionClient.instances.clear()
    FakeAgentSessionClient.create_error = None
    server = tmp_path / "crashing_app_server.py"
    server.write_text(
        dedent(
            """\
            import json, sys
            for line in sys.stdin:
                message = json.loads(line)
                if 'id' not in message:
                    continue
                method = message.get('method')
                if method == 'initialize':
                    result = {}
                elif method == 'thread/start':
                    result = {'thread': {'id': 'thread-1'}}
                elif method == 'turn/start':
                    result = {'turn': {'id': 'turn-1'}}
                else:
                    result = {}
                print(json.dumps({'id': message['id'], 'result': result}), flush=True)
                if method == 'turn/start':
                    print('fake server exploded', file=sys.stderr, flush=True)
                    sys.exit(7)
            """
        ),
        encoding="utf-8",
        newline="\n",
    )

    fake_api.install_factory(FakeAgentSessionClient)
    adapter = FakeAdapter()
    adapter.run_config = AgentRunConfig(
        binary=sys.executable,
        runtime="codex_app_server",
        app_server_argv=(str(server),),
    )
    adapter.resolve_binary = lambda: Path(sys.executable)
    runner = AppServerAttemptRunner(make_config(), make_issue())
    started = time.monotonic()

    with pytest.raises(app_server_runtime.AppServerError) as exc_info:
        runner.run(
            adapter,
            make_prompt(tmp_path),
            tmp_path,
            timeout=10,
            issue_id=322,
            agent_name="codex",
        )

    assert time.monotonic() - started < 2
    assert "status 7" in str(exc_info.value)
    assert "fake server exploded" in str(exc_info.value)


def test_app_server_runner_retries_one_transient_heartbeat_failure() -> None:
    class RetryStop:
        waits = 0

        def wait(self, timeout: float) -> bool:
            self.waits += 1
            return self.waits == 3

        def set(self) -> None:
            raise AssertionError("heartbeat should continue after one transient error")

    class TransientHeartbeatClient:
        calls = 0

        def heartbeat_agent_session_lease(self, *args, **kwargs) -> None:
            self.calls += 1
            if self.calls == 1:
                raise WorkflowError("temporary network failure", code="request_failed")

    client = TransientHeartbeatClient()
    errors: list[BaseException] = []
    context = app_server_runtime.AttemptContext(
        session_id="session-1",
        generation=1,
        worker_id="worker-1",
        lease_token="lease-1",
        headers={},
    )

    AppServerAttemptRunner._heartbeat_loop(
        AppServerAttemptRunner(make_config(), make_issue()),
        client,
        322,
        context,
        15,
        RetryStop(),
        errors,
    )

    assert client.calls == 2
    assert errors == []

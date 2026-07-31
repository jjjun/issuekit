from __future__ import annotations

import threading
from pathlib import Path

import pytest

from issuekit.agentrun.config import AgentRunConfig
from issuekit.agentrun.runner import AgentPrompt
from issuekit.agents import app_server_runtime
from issuekit.agents.app_server_runtime import AppServerAttemptRunner
from issuekit.config import IssuekitConfig, WorkerIdentity
from issuekit.core import Issue
from issuekit.workflow import WorkflowError


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


class FakeAgentSessionClient:
    instances: list[FakeAgentSessionClient] = []
    create_error: WorkflowError | None = None

    def __init__(self, *args, **kwargs) -> None:
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

    def initialize(self) -> None:
        return None

    def start_thread(self, **kwargs) -> str:
        return "thread-1"

    def start_turn(self, native_session_id: str, prompt: str) -> str:
        if self.complete_on_start:
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
    tmp_path: Path, monkeypatch
) -> None:
    FakeAgentSessionClient.instances.clear()
    FakeAgentSessionClient.create_error = None
    transports: list[FakeTransport] = []

    def transport_factory(*args, **kwargs) -> FakeTransport:
        transport = FakeTransport(*args, complete_on_start=True, **kwargs)
        transports.append(transport)
        return transport

    monkeypatch.setattr(app_server_runtime, "IssuekitClient", FakeAgentSessionClient)
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
    }
    assert len(transports) == 1
    assert FakeAgentSessionClient.instances[-1].closed_sessions == [
        {
            "outcome": "closed",
            "reason": "implementation_turn_finished",
        }
    ]


def test_app_server_runner_translates_missing_provider_feature(
    tmp_path: Path, monkeypatch
) -> None:
    FakeAgentSessionClient.instances.clear()
    FakeAgentSessionClient.create_error = WorkflowError(
        "Not found.", code="not_found"
    )
    monkeypatch.setattr(app_server_runtime, "IssuekitClient", FakeAgentSessionClient)
    runner = AppServerAttemptRunner(make_config(), make_issue())

    with pytest.raises(WorkflowError, match="does not support") as exc_info:
        runner.run(
            FakeAdapter(),
            make_prompt(tmp_path),
            tmp_path,
            issue_id=322,
            agent_name="codex",
        )

    assert exc_info.value.code == "unsupported_runtime"


def test_app_server_runner_interrupts_turn_when_aborted(
    tmp_path: Path, monkeypatch
) -> None:
    FakeAgentSessionClient.instances.clear()
    FakeAgentSessionClient.create_error = None
    transports: list[FakeTransport] = []

    def transport_factory(*args, **kwargs) -> FakeTransport:
        transport = FakeTransport(*args, complete_on_start=False, **kwargs)
        transports.append(transport)
        return transport

    monkeypatch.setattr(app_server_runtime, "IssuekitClient", FakeAgentSessionClient)
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

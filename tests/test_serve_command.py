import errno
import io
import os
import signal
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

import issuekit.agentrun.run_dir as run_dir_module
from issuekit import cli
from issuekit.agentrun import AgentPrompt
from issuekit.commands import serve, serve_loop
from issuekit.config import TriagePolicy
from issuekit.errors import WorkflowError
from issuekit.testing import FakeIssuekitClient
from tests.issue_helpers import api_issue


@dataclass(frozen=True)
class FakeResult:
    exit_code: int = 0
    stdout_path: Path = Path("out.log")
    agent_log_path: Path = Path("agent.log")
    elapsed_sec: float = 1.25
    timed_out: bool = False
    parsed: dict[str, str] | None = None
    status_short: str | None = " M tracked.py"
    status_path: Path | None = Path("status.json")
    report_path: Path | None = None


class FakeRunner:
    calls: list[
        tuple[AgentPrompt, Path, float, str | None, int | None, str | None]
    ] = []
    models: list[str | None] = []
    reasoning_efforts: list[str | None] = []
    resolved_models: list[str | None] = []

    def run(
        self,
        adapter,
        prompt: AgentPrompt,
        repo: Path,
        timeout: float,
        agent_name: str | None = None,
        issue_id: int | None = None,
        follow: bool = False,
        **kwargs,
    ) -> FakeResult:
        self.models.append(adapter.model)
        self.reasoning_efforts.append(adapter.reasoning_effort)
        argv = adapter.build_argv("prompt", Path("plan.md"))
        self.resolved_models.append(
            argv[argv.index("--model") + 1] if "--model" in argv else None
        )
        self.calls.append(
            (
                prompt,
                repo,
                timeout,
                agent_name,
                issue_id,
                kwargs.get("prompt_suffix"),
            )
        )
        return FakeResult(parsed={"resume_session_id": "abc123"})


class ReviewApprovingRunner:
    calls: list[int | None] = []
    resolved_models: list[str | None] = []

    def run(
        self,
        adapter,
        prompt: AgentPrompt,
        repo: Path,
        timeout: float,
        agent_name: str | None = None,
        issue_id: int | None = None,
        follow: bool = False,
        **kwargs,
    ) -> FakeResult:
        self.calls.append(issue_id)
        argv = adapter.build_argv("prompt", Path("plan.md"))
        self.resolved_models.append(
            argv[argv.index("--model") + 1] if "--model" in argv else None
        )
        return FakeResult(
            parsed={
                "stdout": (
                    "```review\n"
                    '{"verdict":"approve","verification":"uv run pytest","notes":""}\n'
                    "```"
                )
            },
            status_short="",
        )


class ReviewNonJsonRunner(ReviewApprovingRunner):
    def run(self, *args, **kwargs) -> FakeResult:
        return FakeResult(
            parsed={
                "stdout": (
                    "```review\n"
                    "REQUEST_CHANGES\n\n"
                    "- Add focused tests.\n"
                    "```"
                )
            },
            status_short="",
        )


class ProposalCheckRunner:
    calls: list[dict] = []
    outputs: list[str] = []

    def run(
        self,
        adapter,
        prompt: AgentPrompt,
        repo: Path,
        timeout: float,
        agent_name: str | None = None,
        issue_id: int | None = None,
        follow: bool = False,
        **kwargs,
    ) -> FakeResult:
        self.calls.append(
            {
                "prompt": prompt,
                "repo": repo,
                "timeout": timeout,
                "agent_name": agent_name,
                "issue_id": issue_id,
                **kwargs,
            }
        )
        stdout = self.outputs.pop(0) if self.outputs else ""
        return FakeResult(parsed={"stdout": stdout}, status_short="")


class ExplodingRunner:
    def run(self, *args, **kwargs):
        raise AssertionError("agent runner should not be called")


class RecoveryErrorThenRunner:
    calls: list[int | None] = []
    attempts: dict[int | None, int] = {}

    def run(
        self,
        adapter,
        prompt: AgentPrompt,
        repo: Path,
        timeout: float,
        agent_name: str | None = None,
        issue_id: int | None = None,
        follow: bool = False,
        **kwargs,
    ) -> FakeResult:
        self.calls.append(issue_id)
        self.attempts[issue_id] = self.attempts.get(issue_id, 0) + 1
        if issue_id == 1 and self.attempts[issue_id] == 1:
            raise RuntimeError("temporary recovery failure")
        return FakeResult(parsed={"resume_session_id": "def456"})


def _configure_registered_api(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    client: FakeIssuekitClient,
    *,
    assignees: str | None = None,
    triage: str = "",
) -> None:
    config = "api_url = 'https://mine.example'\nproject = 'demo'\n"
    if assignees is not None:
        config += f"assignees = [{assignees}]\n"
    config += triage
    (tmp_path / "issuekit.toml").write_text(config, encoding="utf-8", newline="\n")
    (tmp_path / "issuekit.local.toml").write_text(
        (
            "[worker]\n"
            "machine_id = 'machine'\n"
            "repo_id = 'demo'\n"
            "worker_name = 'checkout'\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    fake_api.install_client(client)
    fake_api.install_client(client)
    fake_api.install_client(client)
    monkeypatch.setattr(
        "issuekit.agentrun.adapter.shutil.which",
        lambda binary: f"/test-bin/{binary}",
    )
    monkeypatch.setattr(
        "issuekit.agentrun.adapters.codex._probe_sandbox",
        lambda _binary, _mode: None,
    )
    monkeypatch.chdir(tmp_path)


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(path), check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=str(path), check=True)
    subprocess.run(["git", "add", "."], cwd=str(path), check=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=str(path),
        check=True,
        stdout=subprocess.DEVNULL,
    )


def _create_reviewable_diff(path: Path) -> None:
    (path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    (path / "code.py").write_text("value = 1\n", encoding="utf-8", newline="\n")
    _init_git_repo(path)
    (path / "code.py").write_text("value = 2\n", encoding="utf-8", newline="\n")


def test_backoff_uses_current_initial_value(monkeypatch) -> None:
    monkeypatch.setattr(serve_loop, "BACKOFF_INITIAL_SEC", 4.0)

    backoff = serve_loop.Backoff()
    assert backoff.current == 4.0

    backoff.step()
    backoff.reset()
    assert backoff.current == 4.0


def test_poll_loop_run_failure_limit_resets_after_success_and_idle() -> None:
    controller = serve_loop.ShutdownController.create()
    controller.sleep = lambda _seconds: False
    statuses = iter(("failed", "success", "idle", "error", "failed"))
    failure_limits: list[tuple[str, int]] = []

    def poll(_attempt: int, _backoff: float) -> serve_loop.PollResult:
        status = next(statuses)
        return serve_loop.PollResult(status=status)

    exit_code = serve_loop.run_poll_loop(
        controller,
        serve_loop.Backoff(),
        poll=poll,
        on_idle=lambda _attempt: None,
        on_success=lambda _result, _count: None,
        on_stopped=lambda: None,
        once=False,
        interval=0,
        max_count=None,
        max_consecutive_failures=2,
        on_failure_limit=lambda result, count: failure_limits.append(
            (result.status, count)
        ),
    )

    assert exit_code == 1
    assert failure_limits == [("failed", 2)]


def test_poll_loop_zero_run_failure_limit_keeps_retrying() -> None:
    controller = serve_loop.ShutdownController.create()
    sleep_durations: list[float] = []
    poll_count = 0

    def sleep(seconds: float) -> bool:
        nonlocal poll_count
        sleep_durations.append(seconds)
        if len(sleep_durations) == 4:
            controller.request()
        return controller.requested

    controller.sleep = sleep

    def poll(_attempt: int, _backoff: float) -> serve_loop.PollResult:
        nonlocal poll_count
        poll_count += 1
        return serve_loop.PollResult("failed", exit_code=1)

    assert (
        serve_loop.run_poll_loop(
            controller,
            serve_loop.Backoff(),
            poll=poll,
            on_idle=lambda _attempt: None,
            on_success=lambda _result, _count: None,
            on_stopped=lambda: None,
            once=False,
            interval=0,
            max_count=None,
            max_consecutive_failures=0,
            on_failure_limit=lambda _result, _count: pytest.fail(
                "unlimited failures must not trigger the limit"
            ),
        )
        == 0
    )
    assert poll_count == 4
    assert sleep_durations == [1.0, 2.0, 4.0, 8.0]


def test_serve_once_empty_queue_exits_without_agent(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ExplodingRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--once"])

    assert exit_code == 0
    assert client.calls == [
        {
            "method": "upsert_repo",
            "body": {
                "repo_key": "demo",
                "canonical_url": None,
                "description": None,
                "meta": {},
            },
        },
        {
            "method": "upsert_worker",
            "body": {
                    "machine_id": "machine",
                    "repo_id": "demo",
                    "worker_name": "checkout",
                "path": tmp_path.resolve().as_posix(),
                "project": "demo",
            },
        },
        {
            "method": "claim_next",
            "body": {"assignee": "codex", "worker": "checkout.demo@machine"},
        }
    ]
    lock_path = tmp_path / ".agent-runs" / "serve.lock"
    assert lock_path.exists()
    assert lock_path.read_text(encoding="utf-8") == f"{os.getpid()}\n"
    assert "event=idle" in capsys.readouterr().err


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink behavior is required")
def test_serve_refuses_symlinked_run_directory_before_writing(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / ".agent-runs").symlink_to(outside, target_is_directory=True)

    exit_code = cli.main(["serve", "--agent", "codex", "--once"])

    assert exit_code == 1
    assert "Refusing .agent-runs: it is a symlink" in capsys.readouterr().err
    assert list(outside.iterdir()) == []
    assert client.calls == []


def test_serve_refuses_tracked_run_directory_before_writing(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    tracked = tmp_path / ".agent-runs" / "tracked.txt"
    tracked.parent.mkdir()
    tracked.write_text("tracked\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", ".agent-runs/tracked.txt"], cwd=tmp_path, check=True)

    exit_code = cli.main(["serve", "--agent", "codex", "--once"])

    assert exit_code == 1
    assert "git tracks 1 files under it" in capsys.readouterr().err
    assert not (tmp_path / ".agent-runs" / "serve.lock").exists()
    assert client.calls == []


def test_serve_once_claims_runs_and_submits(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude", body="# Issue #1: First\n")])
    FakeRunner.calls.clear()
    FakeRunner.models.clear()
    FakeRunner.reasoning_efforts.clear()
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", FakeRunner)

    exit_code = cli.main(
        [
            "serve",
            "--agent",
            "codex",
            "--model",
            "gpt-5.6",
            "--reasoning-effort",
            "medium",
            "--once",
            "--timeout-sec",
            "7",
        ]
    )

    assert exit_code == 0
    assert len(FakeRunner.calls) == 1
    prompt, repo, timeout, agent_name, issue_id, prompt_suffix = FakeRunner.calls[0]
    assert prompt.path == tmp_path / ".agent-runs" / "issue-1.md"
    assert prompt.body == "# Issue #1: First\n"
    assert repo == tmp_path
    assert timeout == 7
    assert agent_name == "codex"
    assert issue_id == 1
    assert prompt_suffix is None
    assert FakeRunner.models == ["gpt-5.6"]
    assert FakeRunner.reasoning_efforts == ["medium"]
    assert [call["method"] for call in client.calls] == ["upsert_repo", "upsert_worker", "claim_next", "submit"]
    assert client.calls[2]["body"]["worker"] == "checkout.demo@machine"
    assert "event=submitted issue=1" in capsys.readouterr().err


def test_serve_review_once_reviews_open_pool_issue(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Review",
                status="in_progress",
                assignee="",
                stage="review",
                implementer="codex",
                worker="machine/demo/implementer",
                author="claude",
            )
        ]
    )
    ReviewApprovingRunner.calls.clear()
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    _create_reviewable_diff(tmp_path)
    monkeypatch.setattr("issuekit.agents.review.AgentRunner", ReviewApprovingRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--review", "--once"])

    assert exit_code == 0
    assert ReviewApprovingRunner.calls == [1]
    assert [call["method"] for call in client.calls] == ["upsert_repo", "upsert_worker", "approve"]
    assert client.calls[2]["body"]["worker"] == "checkout.demo"
    captured = capsys.readouterr()
    assert "event=reviewing issue=1" in captured.err
    assert "event=reviewed issue=1" in captured.err


def test_serve_review_skips_failed_issue_and_reviews_next_issue(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    class FailFirstThenApproveRunner:
        calls: list[int | None] = []

        def run(
            self,
            adapter,
            prompt: AgentPrompt,
            repo: Path,
            timeout: float,
            agent_name: str | None = None,
            issue_id: int | None = None,
            follow: bool = False,
            **kwargs,
        ) -> FakeResult:
            self.calls.append(issue_id)
            if issue_id == 1:
                stdout = "not a review block"
            else:
                stdout = (
                    "```review\n"
                    '{"verdict":"approve","verification":"uv run pytest","notes":""}\n'
                    "```"
                )
            return FakeResult(parsed={"stdout": stdout}, status_short="")

    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First review",
                status="in_progress",
                stage="review",
                implementer="claude",
                author="codex",
            ),
            api_issue(
                2,
                "Second review",
                status="in_progress",
                stage="review",
                implementer="claude",
                author="codex",
            ),
        ]
    )
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    _create_reviewable_diff(tmp_path)
    monkeypatch.setattr(serve_loop, "BACKOFF_INITIAL_SEC", 0.0)
    monkeypatch.setattr(
        "issuekit.agents.review.AgentRunner",
        FailFirstThenApproveRunner,
    )

    exit_code = cli.main(
        ["serve", "--agent", "codex", "--review", "--max-issues", "1", "--interval", "0"]
    )

    assert exit_code == 0
    assert FailFirstThenApproveRunner.calls == [1, 2]
    captured = capsys.readouterr()
    assert "event=review_skipped issue=1 reason=previous_failure" in captured.err
    assert "event=reviewed issue=2" in captured.err


def test_serve_review_once_reports_discarded_decision(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Malformed review",
                status="in_progress",
                assignee="",
                stage="review",
                implementer="codex",
                worker="machine/demo/implementer",
                author="claude",
            )
        ]
    )
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    _create_reviewable_diff(tmp_path)
    monkeypatch.setattr("issuekit.agents.review.AgentRunner", ReviewNonJsonRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--review", "--once"])

    assert exit_code == 1
    assert [call["method"] for call in client.calls] == [
        "upsert_repo",
        "upsert_worker",
    ]
    captured = capsys.readouterr()
    assert "event=review_decision_discarded issue=1" in captured.err
    assert "remedy=rerun_review" in captured.err


def test_serve_review_reports_agent_run_without_local_changes_as_error(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    raw_issue = api_issue(
        1,
        "Agent implementation",
        status="in_progress",
        assignee="",
        stage="review",
        implementer="codex",
        worker="machine/demo/implementer",
        author="claude",
        body=(
            "# Issue #1: Agent implementation\n\n"
            "## Handoff\nImplemented by codex.\n"
            "Run log: `.agent-runs/run.out.log`\n"
        ),
    )
    client = FakeIssuekitClient([raw_issue])
    ReviewApprovingRunner.calls.clear()
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    (tmp_path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    (tmp_path / "code.py").write_text("value = 1\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)
    monkeypatch.setattr("issuekit.agents.review.AgentRunner", ReviewApprovingRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--review", "--once"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert not ReviewApprovingRunner.calls
    assert "event=review_error issue=1" in captured.err
    assert "event=review_decision_discarded" not in captured.err
    assert client.get_issue(1)["status"] == "in_progress"
    assert not any(call["method"] in {"approve", "request_changes"} for call in client.calls)


def test_serve_review_once_ignores_issue_assigned_to_other_reviewer(
    fake_api,
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Other reviewer",
                status="in_progress",
                assignee="claude",
                stage="review",
                implementer="codex",
                worker="machine/demo/implementer",
                author="claude",
            )
        ]
    )
    ReviewApprovingRunner.calls.clear()
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.agents.review.AgentRunner", ReviewApprovingRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--review", "--once"])

    assert exit_code == 0
    assert ReviewApprovingRunner.calls == []
    assert [call["method"] for call in client.calls] == ["upsert_repo", "upsert_worker"]


def test_serve_proposal_checks_once_processes_pending_check(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {
                "id": 1,
                "origin": "source#1@abc",
                "title": "Not here",
                "body": "Send elsewhere.",
            }
        ]
    )
    client.create_proposal_check(
        1,
        target_worker="checkout.demo@machine",
        project="demo",
    )
    client.calls.clear()
    ProposalCheckRunner.calls.clear()
    ProposalCheckRunner.outputs = [
        (
            "```proposal-check\n"
            '{"verdict":"reject","comment":"Out of scope for this repo."}\n'
            "```\n"
        )
    ]
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    _init_git_repo(tmp_path)
    monkeypatch.setattr("issuekit.agents.proposal_check.resolve_adapter", lambda *a, **k: object())
    monkeypatch.setattr(serve, "AgentRunner", ProposalCheckRunner)

    exit_code = cli.main(
        [
            "serve",
            "--agent",
            "codex",
            "--proposal-checks",
            "--once",
            "--timeout-sec",
            "5",
        ]
    )

    assert exit_code == 0
    assert len(ProposalCheckRunner.calls) == 1
    assert ProposalCheckRunner.calls[0]["timeout"] == 5
    assert ProposalCheckRunner.calls[0]["agent_name"] == "codex"
    assert ProposalCheckRunner.calls[0]["abort_event"] is not None
    assert client._proposal_checks[1]["status"] == "answered"
    assert client._proposal_checks[1]["verdict"] == "reject"
    assert [call["method"] for call in client.calls[:2]] == [
        "upsert_repo",
        "upsert_worker",
    ]
    assert [call["method"] for call in client.calls[2:-1]] == [
        "poll_proposal_checks",
        "poll_proposal_checks",
    ]
    assert client.calls[-1]["method"] == "post_proposal_check_result"
    captured = capsys.readouterr()
    assert "event=proposal_checks_cycle_start" in captured.err
    assert "event=proposal_check_decision check=1" in captured.err
    assert "event=proposal_checks_cycle_complete attempt=1 decisions=1 errors=0" in captured.err


def test_serve_proposal_checks_once_idle_does_not_spawn_agent(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    monkeypatch.setattr(serve, "AgentRunner", ExplodingRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--proposal-checks", "--once"])

    assert exit_code == 0
    assert [call["method"] for call in client.calls[:2]] == [
        "upsert_repo",
        "upsert_worker",
    ]
    assert [call["method"] for call in client.calls[2:]] == [
        "poll_proposal_checks",
        "poll_proposal_checks",
    ]
    assert "event=proposal_checks_idle attempt=1" in capsys.readouterr().err


def test_serve_rejects_mutually_exclusive_modes(capsys) -> None:
    exit_code = cli.main(
        ["serve", "--agent", "codex", "--review", "--proposal-checks", "--once"]
    )

    assert exit_code == 2
    error = capsys.readouterr().err
    assert "--review" in error
    assert "--proposal-checks" in error


def test_serve_rejects_options_unsupported_by_mode(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)

    for mode_option, rejected_option in (
        ("--review", "--triage"),
        ("--review", "--priority"),
        ("--proposal-checks", "--triage"),
        ("--proposal-checks", "--priority"),
    ):
        argv = ["serve", "--agent", "codex", mode_option, rejected_option]
        if rejected_option == "--priority":
            argv.append("high")

        assert cli.main(argv) == 1
        error = capsys.readouterr().err
        assert mode_option in error
        assert rejected_option in error


def test_serve_proposal_checks_backs_off_after_cycle_error(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    class Args:
        priority = None
        once = False
        interval = 0
        timeout_sec = 1
        max_issues = None
        review = False
        triage = False
        proposal_checks = True
        proposal_check_limit = 50

    class StopAfterSleep:
        requested = False

        def __init__(self) -> None:
            self.abort_event = threading.Event()

        def sleep(self, seconds: float) -> bool:
            self.requested = True
            return True

    def fail_cycle(*args, **kwargs):
        raise WorkflowError("temporary API failure")

    monkeypatch.setattr(serve, "run_proposal_check_cycle", fail_cycle)

    exit_code = serve._serve_loop(
        Args(),
        mode=serve.ServeMode.PROPOSAL_CHECKS,
        agent="codex",
        config=serve.IssuekitConfig(api_url="https://mine.example"),
        cwd=tmp_path,
        log_path=tmp_path / "serve.log",
        controller=StopAfterSleep(),
    )

    assert exit_code == 0
    assert "event=proposal_checks_cycle_error" in capsys.readouterr().err


def test_serve_proposal_checks_stops_at_run_failure_limit(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    class Args:
        once = False
        interval = 0
        timeout_sec = 1
        proposal_check_limit = 50
        max_run_failures = 2

    def fail_cycle(*args, **kwargs):
        raise WorkflowError("temporary error")

    monkeypatch.setattr(serve_loop, "BACKOFF_INITIAL_SEC", 0.0)
    monkeypatch.setattr(serve, "run_proposal_check_cycle", fail_cycle)

    exit_code = serve._serve_proposal_checks_loop(
        Args(),
        agent="codex",
        config=serve.IssuekitConfig(),
        cwd=tmp_path,
        log_path=tmp_path / "serve.log",
        controller=serve_loop.ShutdownController.create(),
    )

    assert exit_code == 1
    assert "event=proposal_check_failure_limit failures=2" in capsys.readouterr().err


def test_serve_review_stops_at_run_failure_limit(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    class Args:
        once = False
        interval = 0
        max_issues = None
        max_run_failures = 2

    class IdleStore:
        def close(self) -> None:
            pass

    def fail_review(*args, **kwargs):
        raise WorkflowError("temporary error")

    monkeypatch.setattr(serve_loop, "BACKOFF_INITIAL_SEC", 0.0)
    monkeypatch.setattr(serve, "next_review", fail_review)

    exit_code = serve._serve_review_loop(
        Args(),
        agent="codex",
        config=serve.IssuekitConfig(),
        cwd=tmp_path,
        log_path=tmp_path / "serve.log",
        controller=serve_loop.ShutdownController.create(),
        store=IdleStore(),
    )

    assert exit_code == 1
    assert "event=review_failure_limit failures=2" in capsys.readouterr().err


def test_serve_proposal_checks_sleeps_between_successful_cycles(
    monkeypatch,
    tmp_path: Path,
) -> None:
    class Args:
        once = False
        interval = 7.5
        timeout_sec = 1
        proposal_check_limit = 50

    class StopAfterSecondSleep:
        requested = False

        def __init__(self) -> None:
            self.abort_event = threading.Event()
            self.events: list[str] = []
            self.sleeps: list[float] = []

        def sleep(self, seconds: float) -> bool:
            self.events.append("sleep")
            self.sleeps.append(seconds)
            if len(self.sleeps) == 2:
                self.requested = True
            return self.requested

    controller = StopAfterSecondSleep()

    def successful_cycle(*args, **kwargs):
        controller.events.append("poll")
        return [
            SimpleNamespace(
                error=None,
                status="answered",
                hold_error=False,
                hold_issue_id=None,
                hold_origin=None,
                hold_reason=None,
            )
        ]

    monkeypatch.setattr(serve, "run_proposal_check_cycle", successful_cycle)

    assert (
        serve._serve_proposal_checks_loop(
            Args(),
            agent="codex",
            config=serve.IssuekitConfig(api_url="https://mine.example"),
            cwd=tmp_path,
            log_path=tmp_path / "serve.log",
            controller=controller,
        )
        == 0
    )
    assert controller.events == ["poll", "sleep", "poll", "sleep"]
    assert controller.sleeps == [7.5, 7.5]


def test_serve_triage_auto_adopts_before_claiming(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {
                "id": 1,
                "origin": "source#7@abc123",
                "title": "Adopt me",
                "body": "# Issue #1: Adopt me\n",
                "blocking": True,
            }
        ]
    )
    FakeRunner.calls.clear()
    _configure_registered_api(
        fake_api,
        tmp_path,
        monkeypatch,
        client,
        triage=(
            "[triage]\n"
            "trusted_origins = ['source']\n"
            "default_priority = 'high'\n"
            "require_blocking = true\n"
            "max_adoptions_per_cycle = 3\n"
        ),
    )
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", FakeRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--once", "--triage"])

    assert exit_code == 0
    assert [call["method"] for call in client.calls] == [
        "upsert_repo",
        "upsert_worker",
        "adopt_proposal",
        "plan",
        "claim_next",
    ]
    assert client.calls[2]["number"] == 1
    assert client.calls[2]["body"] == {"priority": "high"}
    assert client.calls[3]["body"]["stage"] == "planned"
    assert client.calls[3]["body"]["note"].endswith(
        "issuekit plan 1 --stage todo"
    )
    assert client.calls[4]["body"]["worker"] == "checkout.demo@machine"
    assert client.get_proposal(1)["status"] == "adopted"
    assert client.get_issue(1)["origin_proposal_id"] == "1"
    assert FakeRunner.calls == []
    captured = capsys.readouterr()
    assert "event=auto_adopted proposal=1 issue=1 priority=high" in captured.err
    assert "event=held_for_release issue=1" in captured.err


def test_serve_stops_before_claim_when_automatic_hold_fails(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {
                "id": 1,
                "origin": "source#7@abc123",
                "title": "Adopt me",
                "body": "# Issue #1: Adopt me\n",
            }
        ]
    )
    _configure_registered_api(
        fake_api,
        tmp_path,
        monkeypatch,
        client,
        triage="[triage]\ntrusted_origins = ['source']\n",
    )

    def fail_hold(*args, **kwargs):
        raise WorkflowError("planning unavailable")

    monkeypatch.setattr(client, "plan", fail_hold)

    exit_code = cli.main(["serve", "--agent", "codex", "--once", "--triage"])

    assert exit_code == 1
    assert [call["method"] for call in client.calls] == [
        "upsert_repo",
        "upsert_worker",
        "adopt_proposal",
    ]
    assert client.get_issue(1)["stage"] == "todo"
    assert "event=hold_error issue=1 proposal=1" in capsys.readouterr().err


def test_serve_triage_adoption_error_logs_and_still_claims(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {
                "id": 1,
                "origin": "source#7@abc123",
                "title": "Adopt me",
                "body": "# Issue #1: Adopt me\n",
            },
            {
                "id": 2,
                "origin": "source#8@abc123",
                "title": "Cannot adopt",
                "body": "This adoption will fail.",
            },
        ]
    )
    attempts: list[int] = []
    original_adopt = client.adopt_proposal

    def fail_second_adoption(proposal_id: int, *, priority: str | None = None):
        attempts.append(proposal_id)
        if proposal_id == 2:
            raise WorkflowError("temporary adoption failure")
        return original_adopt(proposal_id, priority=priority)

    monkeypatch.setattr(client, "adopt_proposal", fail_second_adoption)
    FakeRunner.calls.clear()
    _configure_registered_api(
        fake_api,
        tmp_path,
        monkeypatch,
        client,
        triage=(
            "[triage]\n"
            "trusted_origins = ['source']\n"
            "default_priority = 'high'\n"
            "hold_auto_adopted = false\n"
            "max_adoptions_per_cycle = 3\n"
        ),
    )
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", FakeRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--once", "--triage"])

    assert exit_code == 0
    assert attempts == [1, 2]
    assert [call["method"] for call in client.calls] == [
        "upsert_repo",
        "upsert_worker",
        "adopt_proposal",
        "claim_next",
        "submit",
    ]
    assert [call[4] for call in FakeRunner.calls] == [1]
    captured = capsys.readouterr()
    assert "event=auto_adopted proposal=1 issue=1 priority=high" in captured.err
    assert (
        'event=triage_adoption_error proposal=2 error="temporary adoption failure"'
        in captured.err
    )
    assert "event=submitted issue=1" in captured.err


def test_serve_triage_uses_author_agent_when_configured(
    fake_api,
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {
                "id": 1,
                "origin": "source#7@abc123",
                "title": "Adopt me",
                "body": "Body.",
                "blocking": True,
            }
        ]
    )
    _configure_registered_api(
        fake_api,
        tmp_path,
        monkeypatch,
        client,
        triage=(
            "[triage]\n"
            "author_agent = 'codex'\n"
            "trusted_origins = ['source']\n"
        ),
    )
    calls = {"author": 0, "mechanical": 0}

    def fake_author_cycle(config, cwd, **kwargs):
        calls["author"] += 1
        assert kwargs.get("log") is not None
        assert kwargs.get("abort_event") is not None
        return []

    def fake_mechanical(config):
        calls["mechanical"] += 1
        return []

    monkeypatch.setattr(serve, "run_triage_author_cycle", fake_author_cycle)
    monkeypatch.setattr(serve, "auto_adopt_incoming_proposals", fake_mechanical)

    exit_code = cli.main(["serve", "--agent", "codex", "--once", "--triage"])

    # No active issue to claim after triage, so the loop goes idle and exits 0.
    assert exit_code == 0
    assert calls == {"author": 1, "mechanical": 0}


def test_serve_once_recovers_own_orphan_before_polling(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    class RecordingClient(FakeIssuekitClient):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.list_calls: list[dict[str, object]] = []

        def list_all_issues(self, **kwargs):
            self.list_calls.append(kwargs)
            return super().list_all_issues(**kwargs)

    client = RecordingClient(
        [
            api_issue(
                1,
                "Orphan",
                status="in_progress",
                assignee="codex",
                stage="implementing",
                implementer="codex",
                author="claude",
                worker="checkout.demo@machine",
            )
        ]
    )
    FakeRunner.calls.clear()
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", FakeRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--once"])

    assert exit_code == 0
    assert client.list_calls == [
        {
            "status": None,
            "assignee": None,
            "stage": "implementing",
            "include_completed": False,
        }
    ]
    assert [call["method"] for call in client.calls] == ["upsert_repo", "upsert_worker", "submit"]
    assert [call[4] for call in FakeRunner.calls] == [1]
    captured = capsys.readouterr()
    assert "event=recovered issue=1" in captured.err
    assert "event=submitted issue=1" in captured.err


def test_serve_ignores_orphan_for_other_worker(
    fake_api,
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Other Worker",
                status="in_progress",
                assignee="codex",
                stage="implementing",
                implementer="codex",
                author="claude",
                worker="machine/demo/other",
            )
        ]
    )
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ExplodingRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--once"])

    assert exit_code == 0
    assert [call["method"] for call in client.calls] == ["upsert_repo", "upsert_worker", "claim_next"]
    assert client.get_issue(1)["stage"] == "implementing"


def test_serve_no_orphan_claims_normally(
    fake_api,
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    FakeRunner.calls.clear()
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", FakeRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--once"])

    assert exit_code == 0
    assert [call["method"] for call in client.calls] == ["upsert_repo", "upsert_worker", "claim_next", "submit"]
    assert [call[4] for call in FakeRunner.calls] == [1]


def test_serve_preflight_failure_does_not_claim_issue(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    issue_before = client.get_issue(1)

    exit_code = cli.main(
        [
            "serve",
            "--agent",
            "kimi",
            "--reasoning-effort",
            "high",
            "--once",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert client.calls == []
    assert client.get_issue(1) == issue_before
    assert "Agent preflight failed:" in captured.err
    assert "has no effort_argv" in captured.err


def test_serve_preflight_rejects_missing_binary_without_claiming(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    issue_before = client.get_issue(1)
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        "trusted_api_origins = ['https://mine.example']\n"
        "[agents.codex]\nbinary = 'missing-codex'\nknown_paths = []\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))
    monkeypatch.setattr("issuekit.agentrun.adapter.shutil.which", lambda _binary: None)

    exit_code = cli.main(["serve", "--agent", "codex", "--once"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert client.calls == []
    assert client.get_issue(1) == issue_before
    assert "Agent preflight failed: missing-codex executable not found." in captured.err


@pytest.mark.parametrize(
    ("mode_option", "expected_role"),
    (("--review", "reviewer"), ("--proposal-checks", "triage")),
)
def test_serve_preflights_the_agent_role_for_each_mode(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    mode_option: str,
    expected_role: str,
) -> None:
    client = FakeIssuekitClient()
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    roles: list[str] = []

    class AvailableAdapter:
        def resolve_binary(self) -> Path:
            return Path("/test-bin/codex")

    def preflight(agent, *, config, model, reasoning_effort, role):
        roles.append(role)
        return AvailableAdapter()

    monkeypatch.setattr(serve, "preflight_agent", preflight)

    assert cli.main(["serve", "--agent", "codex", mode_option, "--once"]) == 0
    assert roles == [expected_role]


def test_serve_retries_own_claim_after_recovery_error(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Orphan",
                status="in_progress",
                assignee="codex",
                stage="implementing",
                implementer="codex",
                author="claude",
                worker="checkout.demo@machine",
            ),
            api_issue(2, "Ready", author="claude"),
        ]
    )
    RecoveryErrorThenRunner.calls.clear()
    RecoveryErrorThenRunner.attempts.clear()
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", RecoveryErrorThenRunner)
    monkeypatch.setattr(serve_loop, "BACKOFF_INITIAL_SEC", 0.0)

    exit_code = cli.main(["serve", "--agent", "codex", "--max-issues", "1", "--interval", "0"])

    assert exit_code == 0
    assert RecoveryErrorThenRunner.calls == [1, 1]
    assert [call["method"] for call in client.calls] == ["upsert_repo", "upsert_worker", "submit"]
    output = capsys.readouterr().err
    assert "event=run_error issue=1" in output
    assert "event=retrying issue=1" in output


def test_serve_retries_failed_claim_and_releases_it_at_failure_limit(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    class AlwaysFailRunner:
        calls: list[int | None] = []

        def run(self, *args, issue_id=None, **kwargs) -> FakeResult:
            self.calls.append(issue_id)
            return FakeResult(exit_code=1)

    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", AlwaysFailRunner)
    monkeypatch.setattr(serve_loop, "BACKOFF_INITIAL_SEC", 0.0)

    assert cli.main(["serve", "--agent", "codex", "--interval", "0"]) == 1

    assert AlwaysFailRunner.calls == [1, 1, 1]
    assert client.get_issue(1)["stage"] == "todo"
    assert [call["method"] for call in client.calls].count("claim_next") == 1
    reclaim_call = next(call for call in client.calls if call["method"] == "reclaim")
    assert reclaim_call["body"]["reason"] == "serve stopped after 3 consecutive failed runs"
    output = capsys.readouterr().err
    assert output.count("event=retrying issue=1") == 2
    assert "event=run_failure_limit issue=1 failures=3" in output


def test_serve_once_returns_recovery_failure_without_claiming(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Orphan",
                status="in_progress",
                assignee="codex",
                stage="implementing",
                implementer="codex",
                author="claude",
                worker="checkout.demo@machine",
            ),
            api_issue(2, "Ready", author="claude"),
        ]
    )
    RecoveryErrorThenRunner.calls.clear()
    RecoveryErrorThenRunner.attempts.clear()
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", RecoveryErrorThenRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--once"])

    assert exit_code == 1
    assert RecoveryErrorThenRunner.calls == [1]
    assert [call["method"] for call in client.calls] == ["upsert_repo", "upsert_worker"]
    assert "event=run_error issue=1" in capsys.readouterr().err


def test_serve_recovered_issue_counts_toward_max_issues(
    fake_api,
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Orphan",
                status="in_progress",
                assignee="codex",
                stage="implementing",
                implementer="codex",
                author="claude",
                worker="checkout.demo@machine",
            ),
            api_issue(2, "Ready", author="claude"),
        ]
    )
    FakeRunner.calls.clear()
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", FakeRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--max-issues", "1", "--interval", "0"])

    assert exit_code == 0
    assert [call["method"] for call in client.calls] == ["upsert_repo", "upsert_worker", "submit"]
    assert [call[4] for call in FakeRunner.calls] == [1]


def test_serve_max_issues_stops_after_successful_submissions(
    fake_api,
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(1, "First", author="claude"),
            api_issue(2, "Second", author="claude"),
            api_issue(3, "Third", author="claude"),
        ]
    )
    FakeRunner.calls.clear()
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", FakeRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--max-issues", "2", "--interval", "0"])

    assert exit_code == 0
    assert [call["method"] for call in client.calls] == [
        "upsert_repo",
        "upsert_worker",
        "claim_next",
        "submit",
        "claim_next",
        "submit",
    ]
    assert [call[4] for call in FakeRunner.calls] == [1, 2]


def test_serve_requires_registered_worker(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["serve", "--agent", "codex", "--once"])

    assert exit_code == 1
    assert "Run `issuekit add` first" in capsys.readouterr().err


def test_serve_rejects_unknown_agent_without_traceback(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _configure_registered_api(fake_api, tmp_path, monkeypatch, FakeIssuekitClient())

    exit_code = cli.main(["serve", "--agent", "nosuch", "--once"])

    assert exit_code == 1
    captured = capsys.readouterr().err
    assert "Unknown assignee: nosuch" in captured
    assert "Traceback" not in captured


def test_serve_requires_api_url_for_registered_worker(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "project = 'demo'\nassignees = ['codex']\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "issuekit.local.toml").write_text(
        (
            "[worker]\n"
            "machine_id = 'machine'\n"
            "repo_id = 'demo'\n"
            "worker_name = 'checkout'\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["serve", "--agent", "codex", "--once"])

    assert exit_code == 1
    assert "api_url is not configured" in capsys.readouterr().err


def test_serve_uses_single_configured_assignee_when_agent_omitted(
    fake_api,
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient()
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client, assignees="'codex'")
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ExplodingRunner)

    assert cli.main(["serve", "--once"]) == 0
    assert client.calls[2]["body"]["assignee"] == "codex"


def test_serve_resolves_configured_heartbeat_and_cli_override_takes_precedence(
    fake_api,
    tmp_path: Path,
    monkeypatch,
) -> None:
    intervals: list[float] = []

    class FakeHeartbeat:
        def __init__(self, config, cwd, *, interval, on_error) -> None:
            intervals.append(interval)

        def beat(self) -> bool:
            return True

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

    client = FakeIssuekitClient()
    _configure_registered_api(
        fake_api,
        tmp_path,
        monkeypatch,
        client,
        triage="worker_heartbeat_interval_sec = 12.5\n",
    )
    monkeypatch.setattr(serve, "WorkerHeartbeat", FakeHeartbeat)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ExplodingRunner)

    assert cli.main(["serve", "--agent", "codex", "--once"]) == 0
    assert (
        cli.main(
            [
                "serve",
                "--agent",
                "codex",
                "--once",
                "--heartbeat-interval",
                "7.5",
            ]
        )
        == 0
    )
    assert intervals == [12.5, 7.5]


def test_serve_rejects_non_positive_heartbeat_override(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _configure_registered_api(fake_api, tmp_path, monkeypatch, FakeIssuekitClient())

    assert (
        cli.main(
            [
                "serve",
                "--agent",
                "codex",
                "--once",
                "--heartbeat-interval",
                "0",
            ]
        )
        == 1
    )
    assert "--heartbeat-interval must be greater than zero" in capsys.readouterr().err


def test_serve_rejects_negative_max_heartbeat_failures(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _configure_registered_api(fake_api, tmp_path, monkeypatch, FakeIssuekitClient())

    assert (
        cli.main(
            [
                "serve",
                "--agent",
                "codex",
                "--max-heartbeat-failures",
                "-1",
            ]
        )
        == 1
    )
    assert "--max-heartbeat-failures must be non-negative" in capsys.readouterr().err


def test_serve_rejects_negative_max_run_failures(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _configure_registered_api(fake_api, tmp_path, monkeypatch, FakeIssuekitClient())

    assert cli.main(["serve", "--agent", "codex", "--max-run-failures", "-1"]) == 1
    assert "--max-run-failures must be non-negative" in capsys.readouterr().err


def test_worker_heartbeat_logs_failure_state_and_escalates_once(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    last_success = datetime.now(UTC) - timedelta(seconds=301)

    class FakeHeartbeat:
        def __init__(self, config, cwd, *, interval, on_error) -> None:
            self.on_error = on_error

        def beat(self) -> bool:
            self.on_error(
                WorkflowError("registry offline"),
                1,
                last_success,
            )
            return False

        def start(self) -> None:
            self.on_error(WorkflowError("registry offline"), 2, last_success)
            self.on_error(WorkflowError("registry offline"), 3, last_success)

        def stop(self) -> None:
            pass

    monkeypatch.setattr(serve, "WorkerHeartbeat", FakeHeartbeat)
    controller = serve_loop.ShutdownController.create()

    with serve._worker_heartbeat(
        SimpleNamespace(),
        tmp_path,
        tmp_path / "serve.log",
        30.0,
        controller,
    ):
        pass

    err = capsys.readouterr().err
    assert "event=worker_registry_error consecutive=1 last_success=" in err
    assert "event=worker_registry_error consecutive=3 last_success=" in err
    assert err.count("event=worker_registry_escalated") == 1


def test_worker_heartbeat_initial_unexpected_exception_uses_failure_path(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    class FakeHeartbeat:
        def __init__(self, config, cwd, *, interval, on_error) -> None:
            self.on_error = on_error

        def beat(self) -> bool:
            raise RuntimeError("programming error")

        def record_failure(self, exc: Exception) -> None:
            self.on_error(exc, 1, None)

        def start(self) -> None:
            pass

        def stop(self) -> None:
            pass

    monkeypatch.setattr(serve, "WorkerHeartbeat", FakeHeartbeat)
    controller = serve_loop.ShutdownController.create()

    with serve._worker_heartbeat(
        SimpleNamespace(),
        tmp_path,
        tmp_path / "serve.log",
        30.0,
        controller,
    ):
        pass

    assert (
        "event=worker_registry_error consecutive=1 last_success=none "
        'error="programming error"'
    ) in capsys.readouterr().err


@pytest.mark.parametrize(
    ("max_failures", "expected_requested"),
    [(0, False), (2, True)],
)
def test_worker_heartbeat_failure_limit_requests_shutdown(
    tmp_path: Path,
    monkeypatch,
    max_failures: int,
    expected_requested: bool,
) -> None:
    class FakeHeartbeat:
        def __init__(self, config, cwd, *, interval, on_error) -> None:
            self.on_error = on_error

        def beat(self) -> bool:
            self.on_error(WorkflowError("registry offline"), 1, None)
            return False

        def start(self) -> None:
            self.on_error(WorkflowError("registry offline"), 2, None)

        def stop(self) -> None:
            pass

    monkeypatch.setattr(serve, "WorkerHeartbeat", FakeHeartbeat)
    controller = serve_loop.ShutdownController.create()

    with serve._worker_heartbeat(
        SimpleNamespace(),
        tmp_path,
        tmp_path / "serve.log",
        30.0,
        controller,
        max_failures=max_failures,
    ):
        pass

    assert controller.requested is expected_requested


def test_serve_warns_when_heartbeat_is_not_below_staleness_default(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _configure_registered_api(fake_api, tmp_path, monkeypatch, FakeIssuekitClient())
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ExplodingRunner)

    assert (
        cli.main(
            [
                "serve",
                "--agent",
                "codex",
                "--once",
                "--heartbeat-interval",
                "300",
            ]
        )
        == 0
    )
    assert "healthy worker may appear stale between beats" in capsys.readouterr().err


def test_serve_refuses_live_lock(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _configure_registered_api(fake_api, tmp_path, monkeypatch, FakeIssuekitClient())
    run_dir = tmp_path / ".agent-runs"
    run_dir.mkdir()
    lock_path = run_dir / "serve.lock"

    with serve._serve_lock(lock_path):
        exit_code = cli.main(["serve", "--agent", "codex", "--once"])

    assert exit_code == 1
    assert "already running" in capsys.readouterr().err


def test_serve_reuses_existing_unlocked_lock_file(
    fake_api,
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient()
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    run_dir = tmp_path / ".agent-runs"
    run_dir.mkdir()
    (run_dir / "serve.lock").write_text("0\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ExplodingRunner)

    assert cli.main(["serve", "--agent", "codex", "--once"]) == 0
    lock_path = run_dir / "serve.lock"
    assert lock_path.exists()
    assert lock_path.read_text(encoding="utf-8") == f"{os.getpid()}\n"


def test_serve_lock_records_current_pid_and_keeps_file(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / ".agent-runs" / "serve.lock"
    lock_path.parent.mkdir()
    lock_path.write_text(f"{os.getpid()}\n", encoding="utf-8", newline="\n")

    with serve._serve_lock(lock_path):
        assert lock_path.read_text(encoding="utf-8") == f"{os.getpid()}\n"

    assert lock_path.exists()


def test_serve_rejects_lock_already_owned_by_this_process(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / ".agent-runs" / "serve.lock"

    with serve._serve_lock(lock_path):
        with pytest.raises(serve.ServeLockError, match="already running"):
            with serve._serve_lock(lock_path):
                pass


def test_serve_acquires_existing_empty_lock_file(
    tmp_path: Path,
) -> None:
    lock_path = tmp_path / ".agent-runs" / "serve.lock"
    lock_path.parent.mkdir()
    lock_path.touch()

    with serve._serve_lock(lock_path):
        assert lock_path.read_text(encoding="utf-8") == f"{os.getpid()}\n"

    assert lock_path.exists()


def test_serve_lock_uses_windows_msvcrt_path(monkeypatch, tmp_path: Path) -> None:
    class FakeMsvcrt:
        LK_NBLCK = 1

        def __init__(self) -> None:
            self.calls = 0

        def locking(self, fd: int, mode: int, count: int) -> None:
            self.calls += 1
            assert mode == self.LK_NBLCK
            assert count == 1
            assert os.lseek(fd, 0, os.SEEK_CUR) == 0
            if self.calls == 2:
                raise OSError(errno.EACCES, "lock is held")

    fake_msvcrt = FakeMsvcrt()
    open_owner_only = run_dir_module.open_owner_only

    def open_with_nonzero_position(path: Path, flags: int) -> int:
        fd = open_owner_only(path, flags)
        os.lseek(fd, 5, os.SEEK_SET)
        return fd

    monkeypatch.setattr(run_dir_module, "_is_windows", lambda: True)
    monkeypatch.setattr(run_dir_module, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(run_dir_module, "open_owner_only", open_with_nonzero_position)
    lock_path = tmp_path / ".agent-runs" / "serve.lock"

    with serve._serve_lock(lock_path):
        pass

    with pytest.raises(serve.ServeLockError, match="already running"):
        with serve._serve_lock(lock_path):
            pass
    assert fake_msvcrt.calls == 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX flock is not available on Windows")
def test_serve_lock_releases_after_holder_process_exits(tmp_path: Path) -> None:
    lock_path = tmp_path / ".agent-runs" / "serve.lock"
    script = (
        "import sys, time\n"
        "from pathlib import Path\n"
        "from issuekit.agentrun.run_dir import serve_lock\n"
        "with serve_lock(Path(sys.argv[1])):\n"
        "    print('locked', flush=True)\n"
        "    time.sleep(60)\n"
    )
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(lock_path)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        assert process.stdout.readline().strip() == "locked"
        with pytest.raises(serve.ServeLockError, match="already running"):
            with serve._serve_lock(lock_path):
                pass
    finally:
        process.kill()
        process.wait(timeout=5)

    with serve._serve_lock(lock_path):
        assert lock_path.read_text(encoding="utf-8") == f"{os.getpid()}\n"
    assert lock_path.exists()


def test_log_event_quotes_multiline_values_on_one_line(
    tmp_path: Path,
) -> None:
    stream = io.StringIO()
    log_path = tmp_path / "serve.log"

    serve_loop.log_event(
        stream,
        log_path,
        "claim_sync_error",
        error="git failed\nline=2",
    )

    output = stream.getvalue()
    assert output.count("\n") == 1
    assert 'event=claim_sync_error error="git failed\\nline=2"' in output
    assert log_path.read_text(encoding="utf-8") == output


def test_serve_backs_off_after_claim_error(monkeypatch, tmp_path: Path, capsys) -> None:
    class Args:
        priority = None
        once = False
        interval = 0
        timeout_sec = 1
        max_issues = None

    class StopAfterSleep:
        requested = False

        def sleep(self, seconds: float) -> bool:
            self.requested = True
            return True

    def fail_claim(*args, **kwargs):
        raise WorkflowError("temporary API failure")

    monkeypatch.setattr(serve, "claim_next", fail_claim)
    config = serve.IssuekitConfig()

    exit_code = serve._serve_loop(
        Args(),
        mode=serve.ServeMode.IMPLEMENT,
        agent="codex",
        config=config,
        cwd=tmp_path,
        log_path=tmp_path / "serve.log",
        controller=StopAfterSleep(),
    )

    assert exit_code == 0
    assert "event=claim_error" in capsys.readouterr().err


def test_serve_loop_reuses_store_across_idle_polls(monkeypatch, tmp_path: Path) -> None:
    class Args:
        priority = None
        once = False
        interval = 0
        timeout_sec = 1
        max_issues = None

    class StopAfterThreeSleeps:
        requested = False

        def __init__(self) -> None:
            self.sleep_count = 0
            self.abort_event = threading.Event()

        def sleep(self, seconds: float) -> bool:
            self.sleep_count += 1
            if self.sleep_count >= 3:
                self.requested = True
            return True

    class IdleStore:
        def __init__(self) -> None:
            self.claim_count = 0
            self.close_count = 0

        def find_implementing_for_workers(self, workers):
            return []

        def claim_next(self, **kwargs):
            self.claim_count += 1
            return

        def close(self) -> None:
            self.close_count += 1

    stores: list[IdleStore] = []

    def fake_get_store(config):
        store = IdleStore()
        stores.append(store)
        return store

    monkeypatch.setattr(serve, "get_store", fake_get_store)
    controller = StopAfterThreeSleeps()
    config = serve.IssuekitConfig(api_url="https://mine.example")

    exit_code = serve._serve_loop(
        Args(),
        mode=serve.ServeMode.IMPLEMENT,
        agent="codex",
        config=config,
        cwd=tmp_path,
        log_path=tmp_path / "serve.log",
        controller=controller,
    )

    assert exit_code == 0
    assert len(stores) == 1
    assert stores[0].claim_count == 3
    assert stores[0].close_count == 1


def test_serve_retries_failed_hold_before_claiming(fake_api, monkeypatch, tmp_path: Path, capsys) -> None:
    class Args:
        priority = None
        allow_any_branch = False
        no_sync = False
        once = False
        interval = 0
        timeout_sec = 1
        max_issues = None
        triage = False

    client = FakeIssuekitClient(
        proposals=[
            {
                "id": 1,
                "origin": "source#7@abc123",
                "title": "Adopt me",
                "body": "An implementation task.",
            }
        ]
    )
    fake_api.install_client(client)
    monkeypatch.setattr(serve, "get_store", lambda _config: client)
    original_plan = client.plan
    plan_attempts = 0

    def fail_first_plan(*args, **kwargs):
        nonlocal plan_attempts
        plan_attempts += 1
        if plan_attempts == 1:
            raise WorkflowError("temporary planning failure")
        return original_plan(*args, **kwargs)

    monkeypatch.setattr(client, "plan", fail_first_plan)
    poll_events: list[str] = []

    def claim_next(*args, **kwargs):
        poll_events.append("claim")
        assert client.get_issue(1)["stage"] == "planned"
        return

    monkeypatch.setattr(serve, "claim_next", claim_next)
    config = serve.IssuekitConfig(
        api_url="https://mine.example",
        project="target",
        triage=TriagePolicy(
            auto_adopt=True,
            trusted_origins=("source",),
        ),
    )
    controller = serve.ShutdownController.create()
    sleep_count = 0

    def sleep(_seconds: float) -> bool:
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count == 2:
            controller.request()
        return controller.requested

    controller.sleep = sleep

    assert (
        serve._serve_loop(
            Args(),
            mode=serve.ServeMode.IMPLEMENT,
            agent="codex",
            config=config,
            cwd=tmp_path,
            log_path=tmp_path / "serve.log",
            controller=controller,
        )
        == 0
    )

    assert plan_attempts == 2
    assert poll_events == ["claim"]
    assert client.get_issue(1)["stage"] == "planned"
    output = capsys.readouterr().err
    assert "event=hold_error issue=1" in output
    assert "event=held_for_release issue=1" in output


def test_serve_loop_claim_ignores_author_guard_outside_configured_cwd(
    monkeypatch, tmp_path: Path
) -> None:
    # Regression for issuekit#152: the claim path must resolve the author-session
    # guard against the loop's configured cwd, not the process working directory.
    # A live guard in the process CWD must not block a serve loop given another cwd.
    from issuekit.guards.author import create_author_guard

    class Args:
        priority = None
        once = True
        interval = 0
        timeout_sec = 1
        max_issues = None

    class StopController:
        requested = False

        def __init__(self) -> None:
            self.abort_event = threading.Event()

        def sleep(self, seconds: float) -> bool:
            return True

    class IdleStore:
        def __init__(self) -> None:
            self.claim_count = 0
            self.close_count = 0

        def claim_next(self, **kwargs):
            self.claim_count += 1
            return

        def close(self) -> None:
            self.close_count += 1

    process_cwd = tmp_path / "process"
    loop_cwd = tmp_path / "loop"
    process_cwd.mkdir()
    loop_cwd.mkdir()

    config = serve.IssuekitConfig(api_url="https://mine.example")
    # Live author guard in the PROCESS cwd only; the loop's cwd has none.
    create_author_guard(
        process_cwd,
        config=config,
        kind="issue",
        item_id=152,
        ref="issuekit#152",
        author_agent="claude",
    )
    monkeypatch.chdir(process_cwd)

    stores: list[IdleStore] = []

    def fake_get_store(config):
        store = IdleStore()
        stores.append(store)
        return store

    monkeypatch.setattr(serve, "get_store", fake_get_store)

    exit_code = serve._serve_loop(
        Args(),
        mode=serve.ServeMode.IMPLEMENT,
        agent="codex",
        config=config,
        cwd=loop_cwd,
        log_path=loop_cwd / "serve.log",
        controller=StopController(),
    )

    # The guard in process_cwd must not be consulted: the claim reaches the store.
    assert exit_code == 0
    assert stores[0].claim_count == 1


def test_serve_sigint_during_idle_keeps_lock_file(
    fake_api,
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient()
    _configure_registered_api(fake_api, tmp_path, monkeypatch, client)
    controller = serve.ShutdownController.create()

    def sleep_and_signal(seconds: float) -> bool:
        controller.handle_signal(signal.SIGINT, None)
        return True

    controller.sleep = sleep_and_signal
    monkeypatch.setattr(serve.ShutdownController, "create", lambda: controller)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ExplodingRunner)

    assert cli.main(["serve", "--agent", "codex", "--interval", "30"]) == 0
    lock_path = tmp_path / ".agent-runs" / "serve.lock"
    assert lock_path.exists()
    assert lock_path.read_text(encoding="utf-8") == f"{os.getpid()}\n"

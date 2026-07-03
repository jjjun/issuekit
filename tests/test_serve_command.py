from dataclasses import dataclass
from pathlib import Path
import os
import signal
import subprocess

from issuekit import cli
from issuekit import proposals_api
from issuekit import store as store_module
from issuekit import worker_registry
from issuekit.commands import serve
from issuekit.testing import FakeIssuekitClient
from issuekit.workflow import WorkflowError

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


class FakeRunner:
    calls: list[tuple[Path, Path, float, str | None, int | None, str | None]] = []

    def run(
        self,
        adapter,
        plan_path: Path,
        repo: Path,
        timeout: float,
        agent_name: str | None = None,
        issue_id: int | None = None,
        follow: bool = False,
        **kwargs,
    ) -> FakeResult:
        self.calls.append(
            (
                plan_path,
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

    def run(
        self,
        adapter,
        plan_path: Path,
        repo: Path,
        timeout: float,
        agent_name: str | None = None,
        issue_id: int | None = None,
        follow: bool = False,
        **kwargs,
    ) -> FakeResult:
        self.calls.append(issue_id)
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


class ExplodingRunner:
    def run(self, *args, **kwargs):
        raise AssertionError("agent runner should not be called")


class RecoveryErrorThenRunner:
    calls: list[int | None] = []

    def run(
        self,
        adapter,
        plan_path: Path,
        repo: Path,
        timeout: float,
        agent_name: str | None = None,
        issue_id: int | None = None,
        follow: bool = False,
        **kwargs,
    ) -> FakeResult:
        self.calls.append(issue_id)
        if issue_id == 1:
            raise RuntimeError("temporary recovery failure")
        return FakeResult(parsed={"resume_session_id": "def456"})


def _configure_registered_api(
    tmp_path: Path,
    monkeypatch,
    client: FakeIssuekitClient,
    *,
    assignees: str | None = None,
    triage: str = "",
) -> None:
    config = "api_url = 'https://mine.example'\nproject = 'demo'\ndefault_reviewer = 'auto'\n"
    if assignees is not None:
        config += f"assignees = [{assignees}]\n"
    config += triage
    (tmp_path / "issuekit.toml").write_text(config, encoding="utf-8", newline="\n")
    (tmp_path / "issuekit.local.toml").write_text(
        (
            "[worker]\n"
            "machine_id = 'machine'\n"
            "repo_id = 'demo'\n"
            "worker_id = 'checkout'\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.setattr(worker_registry, "IssuekitClient", lambda *args, **kwargs: client)
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


def test_serve_once_empty_queue_exits_without_agent(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    _configure_registered_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ExplodingRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--once"])

    assert exit_code == 0
    assert client.calls == [
        {
            "method": "upsert_worker",
            "body": {
                "machine_id": "machine",
                "repo_id": "demo",
                "worker_id": "checkout",
                "path": tmp_path.resolve().as_posix(),
            },
        },
        {
            "method": "claim_next",
            "body": {"assignee": "codex", "worker": "machine/demo/checkout"},
        }
    ]
    assert not (tmp_path / ".agent-runs" / "serve.lock").exists()
    assert "event=idle" in capsys.readouterr().err


def test_serve_once_claims_runs_and_submits(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude", body="# Issue #1: First\n")])
    FakeRunner.calls.clear()
    _configure_registered_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", FakeRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--once", "--timeout-sec", "7"])

    assert exit_code == 0
    assert len(FakeRunner.calls) == 1
    plan_path, repo, timeout, agent_name, issue_id, prompt_suffix = FakeRunner.calls[0]
    assert plan_path == tmp_path / ".agent-runs" / "issue-1.md"
    assert plan_path.read_text(encoding="utf-8") == "# Issue #1: First\n"
    assert repo == tmp_path
    assert timeout == 7
    assert agent_name == "codex"
    assert issue_id == 1
    assert prompt_suffix is None
    assert [call["method"] for call in client.calls] == ["upsert_worker", "claim_next", "submit"]
    assert client.calls[1]["body"]["worker"] == "machine/demo/checkout"
    assert "event=submitted issue=1" in capsys.readouterr().err


def test_serve_review_once_reviews_open_pool_issue(
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
    _configure_registered_api(tmp_path, monkeypatch, client)
    _create_reviewable_diff(tmp_path)
    monkeypatch.setattr("issuekit.agents.review.AgentRunner", ReviewApprovingRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--review", "--once"])

    assert exit_code == 0
    assert ReviewApprovingRunner.calls == [1]
    assert [call["method"] for call in client.calls] == ["upsert_worker", "approve"]
    assert client.calls[1]["body"]["worker"] == "machine/demo/checkout"
    captured = capsys.readouterr()
    assert "event=reviewing issue=1" in captured.err
    assert "event=reviewed issue=1" in captured.err


def test_serve_review_once_ignores_issue_assigned_to_other_reviewer(
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
    _configure_registered_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.agents.review.AgentRunner", ReviewApprovingRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--review", "--once"])

    assert exit_code == 0
    assert ReviewApprovingRunner.calls == []
    assert [call["method"] for call in client.calls] == ["upsert_worker"]


def test_serve_triage_auto_adopts_before_claiming(
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
        "upsert_worker",
        "adopt_proposal",
        "claim_next",
        "submit",
    ]
    assert client.calls[1]["number"] == 1
    assert client.calls[1]["body"] == {"priority": "high"}
    assert client.calls[2]["body"]["worker"] == "machine/demo/checkout"
    assert client.get_proposal(1)["status"] == "adopted"
    assert client.get_issue(1)["origin_proposal_id"] == "1"
    assert [call[4] for call in FakeRunner.calls] == [1]
    captured = capsys.readouterr()
    assert "event=auto_adopted proposal=1 issue=1 priority=high" in captured.err
    assert "event=submitted issue=1" in captured.err


def test_serve_once_recovers_own_orphan_before_polling(
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
                worker="machine/demo/checkout",
            )
        ]
    )
    FakeRunner.calls.clear()
    _configure_registered_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", FakeRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--once"])

    assert exit_code == 0
    assert [call["method"] for call in client.calls] == ["upsert_worker", "submit"]
    assert [call[4] for call in FakeRunner.calls] == [1]
    captured = capsys.readouterr()
    assert "event=recovered issue=1" in captured.err
    assert "event=submitted issue=1" in captured.err


def test_serve_ignores_orphan_for_other_worker(
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
    _configure_registered_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ExplodingRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--once"])

    assert exit_code == 0
    assert [call["method"] for call in client.calls] == ["upsert_worker", "claim_next"]
    assert client.get_issue(1)["stage"] == "implementing"


def test_serve_no_orphan_claims_normally(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "Ready", author="claude")])
    FakeRunner.calls.clear()
    _configure_registered_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", FakeRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--once"])

    assert exit_code == 0
    assert [call["method"] for call in client.calls] == ["upsert_worker", "claim_next", "submit"]
    assert [call[4] for call in FakeRunner.calls] == [1]


def test_serve_recovery_error_continues_to_poll(
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
                worker="machine/demo/checkout",
            ),
            api_issue(2, "Ready", author="claude"),
        ]
    )
    RecoveryErrorThenRunner.calls.clear()
    _configure_registered_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", RecoveryErrorThenRunner)
    monkeypatch.setattr(serve, "BACKOFF_INITIAL_SEC", 0.0)

    exit_code = cli.main(["serve", "--agent", "codex", "--max-issues", "1", "--interval", "0"])

    assert exit_code == 0
    assert RecoveryErrorThenRunner.calls == [1, 2]
    assert [call["method"] for call in client.calls] == ["upsert_worker", "claim_next", "submit"]
    assert "event=run_error issue=1" in capsys.readouterr().err


def test_serve_recovered_issue_counts_toward_max_issues(
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
                worker="machine/demo/checkout",
            ),
            api_issue(2, "Ready", author="claude"),
        ]
    )
    FakeRunner.calls.clear()
    _configure_registered_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", FakeRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--max-issues", "1", "--interval", "0"])

    assert exit_code == 0
    assert [call["method"] for call in client.calls] == ["upsert_worker", "submit"]
    assert [call[4] for call in FakeRunner.calls] == [1]


def test_serve_max_issues_stops_after_successful_submissions(
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
    _configure_registered_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", FakeRunner)

    exit_code = cli.main(["serve", "--agent", "codex", "--max-issues", "2", "--interval", "0"])

    assert exit_code == 0
    assert [call["method"] for call in client.calls] == [
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


def test_serve_uses_single_configured_assignee_when_agent_omitted(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient()
    _configure_registered_api(tmp_path, monkeypatch, client, assignees="'codex'")
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ExplodingRunner)

    assert cli.main(["serve", "--once"]) == 0
    assert client.calls[1]["body"]["assignee"] == "codex"


def test_serve_refuses_live_lock(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _configure_registered_api(tmp_path, monkeypatch, FakeIssuekitClient())
    run_dir = tmp_path / ".agent-runs"
    run_dir.mkdir()
    (run_dir / "serve.lock").write_text(f"{os.getpid()}\n", encoding="utf-8", newline="\n")

    exit_code = cli.main(["serve", "--agent", "codex", "--once"])

    assert exit_code == 1
    assert "already running" in capsys.readouterr().err


def test_serve_reclaims_stale_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient()
    _configure_registered_api(tmp_path, monkeypatch, client)
    run_dir = tmp_path / ".agent-runs"
    run_dir.mkdir()
    (run_dir / "serve.lock").write_text("0\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ExplodingRunner)

    assert cli.main(["serve", "--agent", "codex", "--once"]) == 0
    assert not (run_dir / "serve.lock").exists()


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
    monkeypatch.setattr(serve, "BACKOFF_INITIAL_SEC", 0.0)
    config = serve.IssuekitConfig()

    exit_code = serve._serve_loop(
        Args(),
        agent="codex",
        config=config,
        cwd=tmp_path,
        issues_dir=tmp_path / "docs" / "issues",
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
            self.abort_event = serve.threading.Event()

        def sleep(self, seconds: float) -> bool:
            self.sleep_count += 1
            if self.sleep_count >= 3:
                self.requested = True
            return True

    class IdleStore:
        def __init__(self) -> None:
            self.claim_count = 0
            self.close_count = 0

        def claim_next(self, **kwargs):
            self.claim_count += 1
            return None

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
        agent="codex",
        config=config,
        cwd=tmp_path,
        issues_dir=tmp_path / "docs" / "issues",
        log_path=tmp_path / "serve.log",
        controller=controller,
    )

    assert exit_code == 0
    assert len(stores) == 2
    assert stores[0].claim_count == 3
    assert stores[0].close_count == 1
    assert stores[1].close_count == 1


def test_serve_loop_claim_ignores_author_guard_outside_configured_cwd(
    monkeypatch, tmp_path: Path
) -> None:
    # Regression for issuekit#152: the claim path must resolve the author-session
    # guard against the loop's configured cwd, not the process working directory.
    # A live guard in the process CWD must not block a serve loop given another cwd.
    from issuekit.author_guard import create_author_guard

    class Args:
        priority = None
        once = True
        interval = 0
        timeout_sec = 1
        max_issues = None

    class StopController:
        requested = False

        def __init__(self) -> None:
            self.abort_event = serve.threading.Event()

        def sleep(self, seconds: float) -> bool:
            return True

    class IdleStore:
        def __init__(self) -> None:
            self.claim_count = 0
            self.close_count = 0

        def claim_next(self, **kwargs):
            self.claim_count += 1
            return None

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
        agent="codex",
        config=config,
        cwd=loop_cwd,
        issues_dir=loop_cwd / "docs" / "issues",
        log_path=loop_cwd / "serve.log",
        controller=StopController(),
    )

    # The guard in process_cwd must not be consulted: the claim reaches the store.
    assert exit_code == 0
    assert stores[0].claim_count == 1


def test_serve_sigint_during_idle_releases_lock(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient()
    _configure_registered_api(tmp_path, monkeypatch, client)
    controller = serve.ShutdownController.create()

    def sleep_and_signal(seconds: float) -> bool:
        controller.handle_signal(signal.SIGINT, None)
        return True

    controller.sleep = sleep_and_signal
    monkeypatch.setattr(serve.ShutdownController, "create", lambda: controller)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ExplodingRunner)

    assert cli.main(["serve", "--agent", "codex", "--interval", "30"]) == 0
    assert not (tmp_path / ".agent-runs" / "serve.lock").exists()

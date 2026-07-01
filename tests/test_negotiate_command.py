import json
from pathlib import Path

from issuekit import cli
from issuekit.agents.runner import AgentResult
from issuekit.commands.negotiate import run_negotiation
from issuekit.config import IssuekitConfig
from issuekit.core import Issue
from issuekit.negotiation import MockNegotiationStore, ThreadStatus, Verdict
from issuekit.testing import FakeIssuekitClient

from tests.issue_helpers import api_issue


def _issue() -> Issue:
    return Issue(
        id=108,
        ref="frontend#108",
        title="Negotiate API contract",
        issue_status="active",
        created="2026-07-01",
        completed="",
        priority="medium",
        assignee="",
        stage="todo",
        implementer="",
        author="codex",
        body="Frontend needs a small list endpoint.",
        metadata={"title": "Negotiate API contract"},
    )


def _block(
    *,
    side: str,
    verdict: str,
    contract: str | None,
    notes: str = "ok",
) -> str:
    contract_json = "null" if contract is None else f'"{contract}"'
    return (
        "```negotiation\n"
        "{"
        f'"side":"{side}",'
        f'"verdict":"{verdict}",'
        f'"contract":{contract_json},'
        f'"notes":"{notes}"'
        "}\n"
        "```"
    )


class CannedRunner:
    def __init__(self, outputs: list[str]):
        self.outputs = outputs
        self.calls: list[dict[str, object]] = []

    def run(
        self,
        adapter,
        plan_path: Path,
        repo: Path,
        timeout: float,
        agent_name: str | None = None,
        issue_id: int | None = None,
        **kwargs,
    ) -> AgentResult:
        index = len(self.calls)
        self.calls.append(
            {
                "plan_path": plan_path,
                "repo": repo,
                "timeout": timeout,
                "agent_name": agent_name,
                "issue_id": issue_id,
                "prompt_override": kwargs.get("prompt_override"),
            }
        )
        return AgentResult(
            exit_code=0,
            stdout_path=repo / f"run-{index + 1}.out.log",
            agent_log_path=repo / f"run-{index + 1}.agent.log",
            elapsed_sec=0.1,
            timed_out=False,
            parsed={"stdout": self.outputs[index]},
            status_short="",
            status_path=repo / f"run-{index + 1}.status.json",
        )


def test_negotiate_converges_when_both_sides_agree_on_same_contract(tmp_path) -> None:
    store = MockNegotiationStore(None)
    runner = CannedRunner(
        [
            _block(side="frontend", verdict="propose", contract="GET /items 200"),
            _block(side="backend", verdict="agree", contract="GET /items 200"),
            _block(side="frontend", verdict="agree", contract="GET /items   200"),
        ]
    )

    result = run_negotiation(
        issue=_issue(),
        to_project="backend",
        frontend_agent="codex",
        backend_agent="claude",
        max_rounds=3,
        timeout=9.0,
        config=IssuekitConfig(api_url="https://mine.example", project="frontend"),
        cwd=tmp_path,
        store=store,
        runner=runner,
    )

    assert result.outcome == "agreed"
    assert result.round_count == 3
    assert result.run_ids == ("run-1", "run-2", "run-3")
    assert store.get_status(result.thread_id) is ThreadStatus.agreed
    thread = store.get_thread(result.thread_id)
    assert [entry.side for entry in thread] == ["frontend", "backend", "frontend"]
    assert [entry.verdict for entry in thread] == [
        Verdict.propose,
        Verdict.agree,
        Verdict.agree,
    ]
    assert runner.calls[0]["agent_name"] == "codex"
    assert runner.calls[1]["agent_name"] == "claude"
    assert "Perspective: you represent the frontend side." in str(
        runner.calls[0]["prompt_override"]
    )


def test_negotiate_blocked_path_stops_immediately(tmp_path) -> None:
    store = MockNegotiationStore(None)
    runner = CannedRunner(
        [
            _block(side="frontend", verdict="propose", contract="GET /items"),
            _block(side="backend", verdict="blocked", contract=None, notes="Need auth."),
            _block(side="frontend", verdict="agree", contract="GET /items"),
        ]
    )

    result = run_negotiation(
        issue=_issue(),
        to_project="backend",
        frontend_agent="codex",
        backend_agent="claude",
        max_rounds=4,
        timeout=9.0,
        config=IssuekitConfig(api_url="https://mine.example", project="frontend"),
        cwd=tmp_path,
        store=store,
        runner=runner,
    )

    assert result.outcome == "blocked"
    assert result.round_count == 2
    assert len(runner.calls) == 2
    assert store.get_status(result.thread_id) is ThreadStatus.blocked


def test_negotiate_escalates_at_max_rounds_without_status_change(tmp_path) -> None:
    store = MockNegotiationStore(None)
    runner = CannedRunner(
        [
            _block(side="frontend", verdict="propose", contract="GET /items"),
            _block(side="backend", verdict="counter", contract="GET /items?page=1"),
            _block(side="frontend", verdict="counter", contract="GET /items?cursor=x"),
        ]
    )

    result = run_negotiation(
        issue=_issue(),
        to_project="backend",
        frontend_agent="codex",
        backend_agent="claude",
        max_rounds=3,
        timeout=9.0,
        config=IssuekitConfig(api_url="https://mine.example", project="frontend"),
        cwd=tmp_path,
        store=store,
        runner=runner,
    )

    assert result.outcome == "escalate"
    assert result.round_count == 3
    assert store.get_status(result.thread_id) is ThreadStatus.negotiating


def test_negotiate_materially_different_agreements_do_not_converge(tmp_path) -> None:
    store = MockNegotiationStore(None)
    runner = CannedRunner(
        [
            _block(side="frontend", verdict="agree", contract="GET /items"),
            _block(side="backend", verdict="agree", contract="POST /items"),
        ]
    )

    result = run_negotiation(
        issue=_issue(),
        to_project="backend",
        frontend_agent="codex",
        backend_agent="claude",
        max_rounds=2,
        timeout=9.0,
        config=IssuekitConfig(api_url="https://mine.example", project="frontend"),
        cwd=tmp_path,
        store=store,
        runner=runner,
    )

    assert result.outcome == "escalate"
    assert store.get_status(result.thread_id) is ThreadStatus.negotiating


def test_negotiate_cli_json_uses_mock_store_and_api_issue(tmp_path, monkeypatch, capsys) -> None:
    from issuekit import store as store_module
    from issuekit.commands import negotiate

    client = FakeIssuekitClient(
        issues=[api_issue(108, "Negotiate API contract", body="Need a list endpoint.")]
    )
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'frontend'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    fake_runner = CannedRunner(
        [
            _block(side="frontend", verdict="propose", contract="GET /items"),
            _block(side="backend", verdict="blocked", contract=None),
        ]
    )
    monkeypatch.setattr(negotiate, "AgentRunner", lambda: fake_runner)
    monkeypatch.chdir(tmp_path)

    assert (
        cli.main(
            [
                "negotiate",
                "--from-issue",
                "108",
                "--to",
                "backend",
                "--frontend-agent",
                "codex",
                "--backend-agent",
                "claude",
                "--max-rounds",
                "2",
                "--mock",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload["outcome"] == "blocked"
    assert payload["thread_id"] == "1"

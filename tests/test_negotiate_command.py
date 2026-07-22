import json
import re
from pathlib import Path

import pytest

from issuekit import cli
from issuekit.agentrun import AgentResult
from issuekit.commands.negotiate import (
    MockIssueCreator,
    _entry_origin,
    _origin_issue_ref,
    finalize_negotiation,
    inspect_thread,
    run_negotiation,
)
from issuekit.config import IssuekitConfig
from issuekit.core import Issue
from issuekit.negotiation import MockNegotiationStore, ThreadStatus, Verdict
from issuekit.testing import FakeIssuekitClient
from issuekit.workflow import WorkflowError

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
                "session_id": kwargs.get("session_id"),
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


def _call_plan_text(runner: CannedRunner, index: int) -> str:
    plan_path = runner.calls[index]["plan_path"]
    assert isinstance(plan_path, Path)
    return plan_path.read_text(encoding="utf-8")


def test_negotiate_converges_when_both_sides_agree_on_same_contract(tmp_path) -> None:
    store = MockNegotiationStore(None)
    runner = CannedRunner(
        [
            _block(side="frontend", verdict="agree", contract="GET /items 200"),
            _block(side="backend", verdict="agree", contract="GET /items 200"),
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
    assert result.round_count == 2
    assert result.run_ids == ("run-1", "run-2")
    assert store.get_status(result.thread_id) is ThreadStatus.agreed
    thread = store.get_thread(result.thread_id)
    assert [entry.side for entry in thread] == ["frontend", "backend"]
    assert [entry.verdict for entry in thread] == [
        Verdict.agree,
        Verdict.agree,
    ]
    assert runner.calls[0]["agent_name"] == "codex"
    assert runner.calls[1]["agent_name"] == "claude"
    assert "Read the negotiation round prompt at:" in str(runner.calls[0]["prompt_override"])
    assert "Perspective: you represent the frontend side." in _call_plan_text(runner, 0)


def test_negotiate_uses_fresh_resumable_side_session_and_full_prompt(tmp_path) -> None:
    store = MockNegotiationStore(None)
    runner = CannedRunner(
        [
            _block(side="frontend", verdict="propose", contract="GET /items"),
            _block(side="backend", verdict="counter", contract="GET /items?page=1"),
            _block(side="frontend", verdict="agree", contract="GET /items?page=1"),
        ]
    )

    result = run_negotiation(
        issue=_issue(),
        to_project="backend",
        frontend_agent="claude",
        backend_agent="codex",
        max_rounds=3,
        timeout=9.0,
        config=IssuekitConfig(api_url="https://mine.example", project="frontend"),
        cwd=tmp_path,
        store=store,
        runner=runner,
    )

    assert result.outcome == "agreed"
    assert isinstance(runner.calls[0]["session_id"], str)
    assert isinstance(runner.calls[2]["session_id"], str)
    assert runner.calls[0]["session_id"] != runner.calls[2]["session_id"]
    assert runner.calls[1]["session_id"] is None
    assert "Compact thread so far:" in _call_plan_text(runner, 1)
    repeated_side_prompt = _call_plan_text(runner, 2)
    assert "Seed:" in repeated_side_prompt
    assert "Origin project: frontend" in repeated_side_prompt
    assert "Target project: backend" in repeated_side_prompt
    assert "Origin issue: frontend#108" in repeated_side_prompt
    assert "Title: Negotiate API contract" in repeated_side_prompt
    assert "Compact thread so far:" in repeated_side_prompt
    assert "frontend propose | verdict=propose | contract=GET /items" in repeated_side_prompt
    assert "backend counter | verdict=counter | contract=GET /items?page=1" in repeated_side_prompt
    assert "Latest counterpart entry:" not in repeated_side_prompt


def test_negotiate_allocates_unique_session_id_per_round_for_same_agent(tmp_path) -> None:
    store = MockNegotiationStore(None)
    runner = CannedRunner(
        [
            _block(side="frontend", verdict="propose", contract="GET /items"),
            _block(side="backend", verdict="counter", contract="GET /items?page=1"),
            _block(side="frontend", verdict="counter", contract="GET /items?cursor=x"),
            _block(side="backend", verdict="counter", contract="GET /items?offset=0"),
        ]
    )

    result = run_negotiation(
        issue=_issue(),
        to_project="backend",
        frontend_agent="claude",
        backend_agent="claude",
        max_rounds=4,
        timeout=9.0,
        config=IssuekitConfig(api_url="https://mine.example", project="frontend"),
        cwd=tmp_path,
        store=store,
        runner=runner,
    )

    session_ids = [call["session_id"] for call in runner.calls]
    assert result.outcome == "escalate"
    assert result.round_count == 4
    assert all(isinstance(session_id, str) for session_id in session_ids)
    assert len(set(session_ids)) == 4


def test_negotiate_failure_includes_round_session_and_log_reason(tmp_path) -> None:
    class FailingRunner(CannedRunner):
        def __init__(self) -> None:
            super().__init__([])

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
            self.calls.append(
                {
                    "plan_path": plan_path,
                    "repo": repo,
                    "timeout": timeout,
                    "agent_name": agent_name,
                    "issue_id": issue_id,
                    "prompt_override": kwargs.get("prompt_override"),
                    "session_id": kwargs.get("session_id"),
                }
            )
            return AgentResult(
                exit_code=1,
                stdout_path=repo / "run-1.out.log",
                agent_log_path=repo / "run-1.agent.log",
                elapsed_sec=0.1,
                timed_out=False,
                parsed={
                    "stdout": "",
                    "stderr": "Error: Session ID abc is already in use.\n",
                },
                status_short="",
                status_path=repo / "run-1.status.json",
            )

    store = MockNegotiationStore(None)
    runner = FailingRunner()

    with pytest.raises(WorkflowError) as excinfo:
        run_negotiation(
            issue=_issue(),
            to_project="backend",
            frontend_agent="claude",
            backend_agent="codex",
            max_rounds=1,
            timeout=9.0,
            config=IssuekitConfig(api_url="https://mine.example", project="frontend"),
            cwd=tmp_path,
            store=store,
            runner=runner,
        )

    message = str(excinfo.value)
    assert "Negotiation round 1 failed for frontend" in message
    assert "agent=claude" in message
    assert "run_id=run-1" in message
    assert f"session_id={runner.calls[0]['session_id']}" in message
    assert "Session ID abc is already in use" in message


def test_negotiate_passes_single_line_pointer_prompt_and_writes_full_plan(tmp_path) -> None:
    store = MockNegotiationStore(None)
    runner = CannedRunner(
        [
            _block(side="frontend", verdict="propose", contract="GET /items"),
        ]
    )

    run_negotiation(
        issue=_issue(),
        to_project="backend",
        frontend_agent="codex",
        backend_agent="claude",
        max_rounds=1,
        timeout=9.0,
        config=IssuekitConfig(api_url="https://mine.example", project="frontend"),
        cwd=tmp_path,
        store=store,
        runner=runner,
    )

    prompt_override = str(runner.calls[0]["prompt_override"])
    plan_path = runner.calls[0]["plan_path"]
    assert isinstance(plan_path, Path)
    assert "\n" not in prompt_override
    assert str(plan_path) in prompt_override
    assert "Read the negotiation round prompt at:" in prompt_override
    assert "Do not implement code; do not modify the tracker." in prompt_override

    plan_text = plan_path.read_text(encoding="utf-8")
    assert "You are participating in an issuekit cross-repo design negotiation." in plan_text
    assert "Perspective: you represent the frontend side." in plan_text
    assert "Compact thread so far:" in plan_text


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


def test_negotiate_resumes_open_thread_for_same_origin_issue(tmp_path) -> None:
    store = MockNegotiationStore(None)
    first = store.create_thread(
        side="frontend",
        verdict=Verdict.propose,
        title="frontend propose",
        body="Start.",
        origin="frontend#108@frontend:round-1",
        contract="GET /items",
    )
    runner = CannedRunner(
        [
            _block(side="backend", verdict="agree", contract="GET /items"),
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

    assert result.thread_id == first.thread_id
    assert result.outcome == "agreed"
    assert len(runner.calls) == 1
    assert runner.calls[0]["agent_name"] == "claude"
    thread = store.get_thread(first.thread_id)
    assert [entry.origin for entry in thread] == [
        "frontend#108@frontend:round-1",
        "frontend#108@backend:round-2",
    ]
    assert store.get_status(first.thread_id) is ThreadStatus.agreed


def test_entry_origin_matches_api_proposal_origin_contract() -> None:
    origin = _entry_origin(
        _issue(),
        config=IssuekitConfig(project="frontend"),
        side="frontend",
        round_number=1,
    )

    assert origin == "frontend#108@frontend:round-1"
    assert re.fullmatch(r"^([^#]+)#([^@]+)@(.+)$", origin)


def test_origin_issue_ref_extracts_issue_ref_from_entry_origin() -> None:
    store = MockNegotiationStore(None)
    first = store.create_thread(
        side="frontend",
        verdict=Verdict.agree,
        title="frontend agree",
        body="Accepted.",
        origin="frontend#108@frontend:round-1",
        contract="GET /items 200",
    )

    assert _origin_issue_ref(store.get_thread(first.thread_id)) == "frontend#108"


def _agreed_store(contract: str = "GET /items 200") -> tuple[MockNegotiationStore, str]:
    store = MockNegotiationStore(None)
    first = store.create_thread(
        side="frontend",
        verdict=Verdict.agree,
        title="frontend agree",
        body="Accepted.",
        origin="frontend#108@frontend:round-1",
        contract=contract,
    )
    store.append_entry(
        first.thread_id,
        side="backend",
        verdict=Verdict.agree,
        title="backend agree",
        body="Accepted.",
        origin="frontend#108@backend:round-2",
        contract=contract,
    )
    store.set_status(first.thread_id, ThreadStatus.agreed)
    return store, first.thread_id


def test_finalize_negotiation_creates_cross_linked_issues() -> None:
    store, thread_id = _agreed_store()
    creator = MockIssueCreator()

    result = finalize_negotiation(
        thread_id=thread_id,
        to_project="backend",
        author_agent="codex",
        priority="medium",
        config=IssuekitConfig(project="frontend"),
        store=store,
        issue_creator=creator,
    )

    assert result.created is True
    assert result.backend_issue_ref == "backend#1"
    assert result.frontend_issue_ref == "frontend#1"
    assert store.get_issue_refs(thread_id) is not None
    backend = creator.issues[result.backend_issue_ref]
    frontend = creator.issues[result.frontend_issue_ref]
    assert "GET /items 200" in backend.body
    assert "GET /items 200" in frontend.body
    assert f"Negotiation thread: {thread_id}" in backend.body
    assert f"Negotiation thread: {thread_id}" in frontend.body
    assert "Frontend/origin issue: frontend#1" in backend.body
    assert "Backend/API issue: backend#1" in frontend.body
    assert "Originating issue: frontend#108" in backend.body
    assert "Originating issue: frontend#108" in frontend.body


def test_finalize_negotiation_uses_longer_fence_for_contract_with_backticks() -> None:
    contract = 'GET /items\n```json\n{"ok": true}\n```'
    store, thread_id = _agreed_store(contract=contract)
    creator = MockIssueCreator()

    result = finalize_negotiation(
        thread_id=thread_id,
        to_project="backend",
        author_agent="codex",
        priority="medium",
        config=IssuekitConfig(project="frontend"),
        store=store,
        issue_creator=creator,
    )

    fenced_contract = f"````\n{contract}\n````"
    assert fenced_contract in creator.issues[result.backend_issue_ref].body
    assert fenced_contract in creator.issues[result.frontend_issue_ref].body


def test_finalize_negotiation_is_idempotent() -> None:
    store, thread_id = _agreed_store()
    creator = MockIssueCreator()
    first = finalize_negotiation(
        thread_id=thread_id,
        to_project="backend",
        author_agent="codex",
        priority="medium",
        config=IssuekitConfig(project="frontend"),
        store=store,
        issue_creator=creator,
    )

    second = finalize_negotiation(
        thread_id=thread_id,
        to_project="backend",
        author_agent="codex",
        priority="medium",
        config=IssuekitConfig(project="frontend"),
        store=store,
        issue_creator=creator,
    )

    assert second.created is False
    assert second.backend_issue_ref == first.backend_issue_ref
    assert second.frontend_issue_ref == first.frontend_issue_ref
    assert sorted(creator.issues) == ["backend#1", "frontend#1"]


def test_finalize_negotiation_refuses_non_agreed_thread() -> None:
    store = MockNegotiationStore(None)
    first = store.create_thread(
        side="frontend",
        verdict=Verdict.propose,
        title="frontend propose",
        body="Start.",
        origin="frontend#108@frontend:round-1",
        contract="GET /items",
    )

    with pytest.raises(WorkflowError) as excinfo:
        finalize_negotiation(
            thread_id=first.thread_id,
            to_project="backend",
            author_agent="codex",
            priority="medium",
            config=IssuekitConfig(project="frontend"),
            store=store,
            issue_creator=MockIssueCreator(),
        )

    assert excinfo.value.code == "invalid_transition"
    assert "not agreed" in str(excinfo.value)
    assert "latest verdict is propose" in str(excinfo.value)


def test_finalize_negotiation_promotes_agreed_in_substance_thread() -> None:
    store = MockNegotiationStore(None)
    first = store.create_thread(
        side="frontend",
        verdict=Verdict.propose,
        title="frontend propose",
        body="Start.",
        origin="frontend#108@frontend:round-1",
        contract="GET /items 200",
    )
    store.append_entry(
        first.thread_id,
        side="backend",
        verdict=Verdict.agree,
        title="backend agree",
        body="Accepted.",
        origin="frontend#108@backend:round-2",
        contract="GET /items 200",
    )

    result = finalize_negotiation(
        thread_id=first.thread_id,
        to_project="backend",
        author_agent="codex",
        priority="medium",
        config=IssuekitConfig(project="frontend"),
        store=store,
        issue_creator=MockIssueCreator(),
    )

    assert result.created is True
    assert store.get_status(first.thread_id) is ThreadStatus.agreed
    assert store.get_agreed_contract(first.thread_id) == "GET /items 200"


def test_inspect_thread_explains_finalize_refusal() -> None:
    store = MockNegotiationStore(None)
    first = store.create_thread(
        side="frontend",
        verdict=Verdict.propose,
        title="frontend propose",
        body="Start.",
        origin="frontend#108@frontend:round-1",
        contract="GET /items",
    )

    inspection = inspect_thread(first.thread_id, store=store)

    payload = inspection.to_dict()
    assert payload["status"] == "negotiating"
    assert payload["finalize_refusal"] == "latest verdict is propose, not agree"
    assert payload["entries"][0]["origin"] == "frontend#108@frontend:round-1"


def test_inspect_thread_has_no_refusal_for_recoverable_agreement() -> None:
    store = MockNegotiationStore(None)
    first = store.create_thread(
        side="frontend",
        verdict=Verdict.propose,
        title="frontend propose",
        body="Start.",
        origin="frontend#108@frontend:round-1",
        contract="GET /items 200",
    )
    store.append_entry(
        first.thread_id,
        side="backend",
        verdict=Verdict.agree,
        title="backend agree",
        body="Accepted.",
        origin="frontend#108@backend:round-2",
        contract="GET /items 200",
    )

    inspection = inspect_thread(first.thread_id, store=store)

    payload = inspection.to_dict()
    assert payload["status"] == "negotiating"
    assert payload["outcome"] == "agreed"
    assert payload["finalize_refusal"] is None


def test_inspect_thread_reports_contract_mismatch_for_non_matching_agreement() -> None:
    store = MockNegotiationStore(None)
    first = store.create_thread(
        side="frontend",
        verdict=Verdict.propose,
        title="frontend propose",
        body="Start.",
        origin="frontend#108@frontend:round-1",
        contract="GET /items 200",
    )
    store.append_entry(
        first.thread_id,
        side="backend",
        verdict=Verdict.agree,
        title="backend agree",
        body="Accepted.",
        origin="frontend#108@backend:round-2",
        contract="POST /items 201",
    )

    inspection = inspect_thread(first.thread_id, store=store)

    payload = inspection.to_dict()
    assert payload["status"] == "negotiating"
    assert payload["outcome"] == "negotiating"
    assert payload["finalize_refusal"] == (
        "latest agree contract does not match a counterpart contract"
    )


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


def test_negotiate_cli_finalize_json_uses_mock_store(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    store = MockNegotiationStore(tmp_path / ".agent-runs" / "negotiations" / "mock.json")
    first = store.create_thread(
        side="frontend",
        verdict=Verdict.agree,
        title="frontend agree",
        body="Accepted.",
        origin="frontend#108@frontend:round-1",
        contract="GET /items 200",
    )
    store.append_entry(
        first.thread_id,
        side="backend",
        verdict=Verdict.agree,
        title="backend agree",
        body="Accepted.",
        origin="frontend#108@backend:round-2",
        contract="GET /items 200",
    )
    store.set_status(first.thread_id, ThreadStatus.agreed)

    assert (
        cli.main(
            [
                "negotiate",
                "--finalize",
                first.thread_id,
                "--to",
                "backend",
                "--mock",
                "--json",
            ]
        )
        == 0
    )

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "thread_id": first.thread_id,
        "backend_issue_ref": "backend#1",
        "frontend_issue_ref": "issuekit#1",
        "created": True,
    }


def test_threads_cli_inspects_mock_thread_json(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)
    store = MockNegotiationStore(tmp_path / ".agent-runs" / "negotiations" / "mock.json")
    first = store.create_thread(
        side="frontend",
        verdict=Verdict.propose,
        title="frontend propose",
        body="Start.",
        origin="issuekit#108@frontend:round-1",
        contract="GET /items",
    )

    assert cli.main(["threads", first.thread_id, "--mock", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["thread_id"] == first.thread_id
    assert payload["status"] == "negotiating"
    assert payload["finalize_refusal"] == "latest verdict is propose, not agree"
    assert payload["entries"][0]["origin"] == "issuekit#108@frontend:round-1"

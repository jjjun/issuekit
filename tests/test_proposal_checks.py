"""Tests for worker-side proposal-check evaluation."""

from __future__ import annotations

import json
import subprocess
import threading
from dataclasses import replace
from pathlib import Path

import pytest

import issuekit.proposals.api as proposals_api
from issuekit.agentrun import AgentPrompt, AgentResult
from issuekit.agents import proposal_check
from issuekit.agents.proposal_check import (
    ProposalCheckParseError,
    list_worker_proposal_checks,
    parse_proposal_check_output,
    run_proposal_check_cycle,
)
from issuekit.agents.registry import resolve_adapter
from issuekit.config import AgentRunConfig, RoleOverlay, load_config
from issuekit.testing import FakeIssuekitClient
from issuekit.workflow import WorkflowError


class FakeRunner:
    def __init__(self, outputs: list[str]) -> None:
        self._outputs = list(outputs)
        self.calls: list[dict] = []

    def run(self, adapter, prompt: AgentPrompt, repo, **kwargs) -> AgentResult:
        self.calls.append({"prompt": prompt, "repo": repo, **kwargs})
        text = self._outputs.pop(0) if self._outputs else ""
        return AgentResult(
            exit_code=0,
            stdout_path=Path("out.log"),
            agent_log_path=Path("agent.log"),
            elapsed_sec=0.1,
            timed_out=False,
            parsed={"stdout": text},
        )


def _write_config(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        (
            "api_url = 'https://mine.example'\n"
            "project = 'target'\n"
            "assignees = ['codex']\n"
            "[triage]\n"
            "default_priority = 'high'\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "issuekit.local.toml").write_text(
        (
            "[worker]\n"
            "machine_id = 'machine'\n"
            "repo_id = 'target'\n"
            "worker_id = 'worker'\n"
        ),
        encoding="utf-8",
        newline="\n",
    )


def _check_block(**fields: str) -> str:
    return "```proposal-check\n" + json.dumps(fields) + "\n```\n"


def _init_git_repo(path: Path) -> None:
    for args in (
        ("init", "-q"),
        ("config", "user.email", "test@example.com"),
        ("config", "user.name", "Test User"),
        ("add", "."),
        ("commit", "-qm", "initial"),
    ):
        result = subprocess.run(["git", *args], cwd=path, check=False)
        assert result.returncode == 0


def _setup(monkeypatch, tmp_path: Path, *, output: str):
    _write_config(tmp_path)
    _init_git_repo(tmp_path)
    client = FakeIssuekitClient(
        proposals=[
            {
                "id": 1,
                "origin": "source#1@abc",
                "title": "Build API",
                "body": "Add the endpoint.",
            }
        ]
    )
    client.create_proposal_check(
        1,
        target_worker="worker.target@machine",
        project="target",
    )
    client.calls.clear()
    runner = FakeRunner([output])
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *a, **k: client)
    monkeypatch.setattr(proposal_check, "resolve_adapter", lambda *a, **k: object())
    monkeypatch.chdir(tmp_path)
    return client, runner, load_config(tmp_path)


def test_proposal_check_forwards_model_to_adapter(monkeypatch, tmp_path) -> None:
    _client, runner, config = _setup(
        monkeypatch,
        tmp_path,
        output=_check_block(verdict="reject", comment="Out of scope."),
    )
    seen = {}
    monkeypatch.setattr(
        proposal_check,
        "resolve_adapter",
        lambda agent, **kwargs: seen.update(agent=agent, **kwargs) or object(),
    )

    run_proposal_check_cycle(
        config,
        tmp_path,
        agent="codex",
        model="gpt-5.6",
        runner_factory=lambda: runner,
    )

    assert seen["agent"] == "codex"
    assert seen["model"] == "gpt-5.6"


def test_proposal_check_uses_triage_role_overlay(monkeypatch, tmp_path) -> None:
    _client, runner, config = _setup(
        monkeypatch,
        tmp_path,
        output=_check_block(verdict="reject", comment="Out of scope."),
    )
    config = replace(
        config,
        agents=(
            (
                "codex",
                AgentRunConfig(
                    binary="codex",
                    headless_argv=("exec",),
                    model_flag="--model",
                    model="gpt-5.5",
                    effort_argv=("-c", "model_reasoning_effort={value}"),
                ),
            ),
        ),
        agent_role_overlays=(
            (
                "codex",
                (("triage", RoleOverlay(model="gpt-5.6", reasoning_effort="high")),),
            ),
        ),
    )
    adapters = []

    def capture_adapter(*args, **kwargs):
        adapter = resolve_adapter(*args, **kwargs)
        adapters.append(adapter)
        return adapter

    monkeypatch.setattr(proposal_check, "resolve_adapter", capture_adapter)

    run_proposal_check_cycle(
        config,
        tmp_path,
        agent="codex",
        runner_factory=lambda: runner,
    )
    run_proposal_check_cycle(
        config,
        tmp_path,
        agent="codex",
        model="gpt-5.7",
        runner_factory=lambda: runner,
    )

    assert adapters[0].build_argv("prompt", Path("/plan.md"))[-4:] == [
        "--model",
        "gpt-5.6",
        "-c",
        "model_reasoning_effort=high",
    ]
    assert adapters[1].build_argv("prompt", Path("/plan.md"))[-4:] == [
        "--model",
        "gpt-5.7",
        "-c",
        "model_reasoning_effort=high",
    ]


def test_proposal_check_approve_adopts_and_posts_issue_ref(monkeypatch, tmp_path) -> None:
    client, runner, config = _setup(
        monkeypatch,
        tmp_path,
        output=_check_block(
            verdict="approve",
            comment="Feasible and in scope.",
            spec_markdown="## Check Addendum\n\nUse the existing API client.",
        ),
    )

    decisions = run_proposal_check_cycle(
        config,
        tmp_path,
        agent="codex",
        runner_factory=lambda: runner,
    )

    assert len(runner.calls) == 1
    assert [decision.to_dict() for decision in decisions] == [
        {
            "check_id": 1,
            "target_project": "target",
            "proposal_id": 1,
            "verdict": "approve",
            "comment": "Feasible and in scope.",
            "status": "answered",
            "adopted_issue_ref": "target#1",
        }
    ]
    assert client.get_proposal(1)["status"] == "adopted"
    assert client.get_issue(1)["body"] == (
        "Add the endpoint.\n\n## Check Addendum\n\nUse the existing API client."
    )
    assert client.calls[-1] == {
        "method": "post_proposal_check_result",
        "number": 1,
        "body": {
            "project": "target",
            "verdict": "approve",
            "comment": "Feasible and in scope.",
            "adopted_issue_ref": "target#1",
        },
    }


def test_proposal_check_retries_result_after_successful_adoption(
    monkeypatch,
    tmp_path,
) -> None:
    client, runner, config = _setup(
        monkeypatch,
        tmp_path,
        output=_check_block(verdict="approve", comment="Feasible."),
    )
    original_post_result = proposal_check._post_result
    attempts = {"count": 0}

    def fail_first_result(*args, **kwargs):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("result endpoint unavailable")
        return original_post_result(*args, **kwargs)

    monkeypatch.setattr(proposal_check, "_post_result", fail_first_result)
    runner._outputs.append(_check_block(verdict="approve", comment="Feasible."))

    first = run_proposal_check_cycle(
        config,
        tmp_path,
        agent="codex",
        runner_factory=lambda: runner,
    )
    second = run_proposal_check_cycle(
        config,
        tmp_path,
        agent="codex",
        runner_factory=lambda: runner,
    )

    assert first[0].status == "error"
    assert second[0].status == "answered"
    assert second[0].adopted_issue_ref == "target#1"
    assert sum(call["method"] == "adopt_proposal" for call in client.calls) == 1


def test_proposal_check_revise_posts_without_adopting(monkeypatch, tmp_path) -> None:
    client, runner, config = _setup(
        monkeypatch,
        tmp_path,
        output=_check_block(
            verdict="revise",
            comment="Clarify the endpoint path before adoption.",
        ),
    )

    decisions = run_proposal_check_cycle(
        config,
        tmp_path,
        agent="codex",
        runner_factory=lambda: runner,
    )

    assert [decision.verdict for decision in decisions] == ["revise"]
    assert client.get_proposal(1)["status"] == "pending"
    assert client._proposal_checks[1]["status"] == "answered"
    assert client._proposal_checks[1]["adopted_issue_ref"] is None


def test_duplicate_pollers_report_already_decided_from_result_guard(
    monkeypatch,
    tmp_path,
) -> None:
    client, runner, config = _setup(
        monkeypatch,
        tmp_path,
        output=_check_block(verdict="reject", comment="Out of scope for this repo."),
    )
    stale_pending = client.poll_proposal_checks(
        target_worker="worker.target@machine",
        status="pending",
    )
    client.calls.clear()

    def stale_poll(*, target_worker, status="pending", limit=50, offset=0):
        return stale_pending[offset : offset + limit]

    monkeypatch.setattr(client, "poll_proposal_checks", stale_poll)
    runner._outputs.append(
        _check_block(verdict="reject", comment="Still out of scope.")
    )

    first = run_proposal_check_cycle(
        config,
        tmp_path,
        agent="codex",
        runner_factory=lambda: runner,
    )
    second = run_proposal_check_cycle(
        config,
        tmp_path,
        agent="codex",
        runner_factory=lambda: runner,
    )

    assert [decision.status for decision in first] == ["answered"]
    assert [decision.status for decision in second] == ["already_decided"]
    assert client._proposal_checks[1]["status"] == "answered"
    assert len(
        [call for call in client.calls if call["method"] == "post_proposal_check_result"]
    ) == 2


def test_already_decided_outside_result_post_is_an_error(
    monkeypatch,
    tmp_path,
) -> None:
    _client, runner, config = _setup(
        monkeypatch,
        tmp_path,
        output=_check_block(verdict="reject", comment="Not used."),
    )

    def fail_evaluation(*args, **kwargs):
        raise WorkflowError("stale evaluation", code="already_decided")

    monkeypatch.setattr(proposal_check, "_evaluate_check", fail_evaluation)

    decisions = run_proposal_check_cycle(
        config,
        tmp_path,
        agent="codex",
        runner_factory=lambda: runner,
    )

    assert decisions[0].status == "error"
    assert decisions[0].error == "stale evaluation"


def test_parse_proposal_check_output_rejects_non_ascii() -> None:
    with pytest.raises(ProposalCheckParseError, match="ASCII-only"):
        parse_proposal_check_output(
            _check_block(verdict="reject", comment="Not here \u2014 send elsewhere.")
        )


def test_parse_proposal_check_output_normalizes_verdict() -> None:
    parsed = parse_proposal_check_output(
        _check_block(verdict=" APPROVE ", comment="Ready.")
    )

    assert parsed["verdict"] == "approve"


def test_parse_proposal_check_output_normalizes_ok_alias() -> None:
    parsed = parse_proposal_check_output(_check_block(verdict=" OK ", comment="Ready."))

    assert parsed["verdict"] == "approve"


def test_cli_proposal_checks_prints_json(monkeypatch, tmp_path, capsys) -> None:
    from issuekit import cli
    from issuekit.commands import proposal_checks as proposal_checks_cmd

    client, runner, _config = _setup(
        monkeypatch,
        tmp_path,
        output=_check_block(verdict="reject", comment="Out of scope for this repo."),
    )
    monkeypatch.setattr(proposal_checks_cmd, "AgentRunner", lambda: runner)

    assert cli.main(["proposal-checks", "--once", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload[0]["verdict"] == "reject"
    assert payload[0]["check_id"] == 1
    assert client._proposal_checks[1]["status"] == "answered"


def test_cli_proposal_checks_list_prints_table_without_agent(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    from issuekit import cli
    from issuekit.commands import proposal_checks as proposal_checks_cmd

    client, _runner, _config = _setup(
        monkeypatch,
        tmp_path,
        output=_check_block(verdict="reject", comment="Not used."),
    )
    monkeypatch.setattr(
        proposal_checks_cmd,
        "AgentRunner",
        lambda: pytest.fail("listing must not construct an agent runner"),
    )

    assert cli.main(["proposal-checks", "--list"]) == 0
    output = capsys.readouterr().out

    assert "id  target_project  proposal_id  status" in output
    assert "1   target          1            pending" in output
    assert client._proposal_checks[1]["status"] == "pending"
    assert client.calls[-1] == {
        "method": "list_proposal_checks",
        "body": {
                "target_worker": "worker.target",
            "status": None,
            "page_size": 500,
        },
    }


def test_cli_proposal_checks_list_status_json_uses_limit_offset(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    from issuekit import cli

    client, _runner, _config = _setup(
        monkeypatch,
        tmp_path,
        output=_check_block(verdict="reject", comment="Not used."),
    )
    client.create_proposal_check(
        1,
        target_worker="worker.target@machine",
        project="target",
    )
    client.post_proposal_check_result(
        1,
        project="target",
        verdict="reject",
        comment="Out of scope.",
    )
    client.calls.clear()

    assert (
        cli.main(
            [
                "proposal-checks",
                "--list",
                "--status",
                "answered",
                "--limit",
                "1",
                "--offset",
                "0",
                "--json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)

    assert len(payload) == 1
    assert payload[0]["id"] == 1
    assert payload[0]["status"] == "answered"
    assert payload[0]["verdict"] == "reject"
    assert client.calls[-1] == {
        "method": "poll_proposal_checks",
        "body": {
                "target_worker": "worker.target",
            "status": "answered",
            "limit": 1,
            "offset": 0,
        },
    }


def test_proposal_check_filtered_pagination_is_global_across_worker_keys(
    monkeypatch,
    tmp_path,
) -> None:
    client, _runner, config = _setup(
        monkeypatch,
        tmp_path,
        output=_check_block(verdict="reject", comment="Not used."),
    )
    client.create_proposal_check(
        1,
        target_worker="worker.target",
        project="target",
    )

    checks = list_worker_proposal_checks(
        config,
        status="pending",
        limit=1,
        offset=1,
    )

    assert [check["id"] for check in checks] == [2]


def test_proposal_check_stops_before_next_item_when_aborted(
    monkeypatch,
    tmp_path,
) -> None:
    client, _runner, config = _setup(
        monkeypatch,
        tmp_path,
        output=_check_block(verdict="reject", comment="Not used."),
    )
    client.create_proposal_check(
        1,
        target_worker="worker.target",
        project="target",
    )
    abort_event = threading.Event()

    class AbortingRunner(FakeRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, **kwargs) -> AgentResult:
            result = super().run(adapter, prompt, repo, **kwargs)
            abort_event.set()
            return result

    runner = AbortingRunner(
        [_check_block(verdict="reject", comment="First only.")]
    )

    decisions = run_proposal_check_cycle(
        config,
        tmp_path,
        agent="codex",
        runner_factory=lambda: runner,
        abort_event=abort_event,
    )

    assert [decision.check_id for decision in decisions] == [1]
    assert len(runner.calls) == 1


def test_cli_proposal_checks_list_and_once_are_mutually_exclusive(capsys) -> None:
    from issuekit import cli

    assert cli.main(["proposal-checks", "--list", "--once"]) == 2
    assert "not allowed with argument" in capsys.readouterr().err


@pytest.mark.parametrize("filename", ["code.py", "変更.py"])
def test_proposal_check_rejects_content_only_worktree_mutations(
    monkeypatch,
    tmp_path,
    filename,
) -> None:
    _client, _runner, config = _setup(
        monkeypatch,
        tmp_path,
        output=_check_block(verdict="reject", comment="Out of scope."),
    )
    changed_path = tmp_path / filename
    changed_path.write_text("value = 1\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)
    changed_path.write_text("value = 2\n", encoding="utf-8", newline="\n")

    class MutatingRunner(FakeRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, **kwargs):
            changed_path.write_text("value = 3\n", encoding="utf-8", newline="\n")
            return super().run(adapter, prompt, repo, **kwargs)

    runner = MutatingRunner([_check_block(verdict="reject", comment="Out of scope.")])

    decisions = run_proposal_check_cycle(
        config,
        tmp_path,
        agent="codex",
        runner_factory=lambda: runner,
    )

    assert decisions[0].status == "error"
    assert decisions[0].error == "Proposal-check agent modified repository state for proposal #1."

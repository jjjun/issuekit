"""Tests for agent-refined triage-author proposal adoption."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from issuekit import proposals_api
from issuekit.agents import triage_author
from issuekit.agents.runner import AgentResult
from issuekit.agents.triage_author import (
    TriageAuthorParseError,
    parse_triage_output,
    run_triage_author_cycle,
)
from issuekit.config import load_config
from issuekit.testing import FakeIssuekitClient


def _write_config(tmp_path: Path, *, author_agent: str = "codex", extra: str = "") -> None:
    (tmp_path / "issuekit.toml").write_text(
        (
            "api_url = 'https://mine.example'\n"
            "project = 'issuekit'\n"
            "[triage]\n"
            f"author_agent = '{author_agent}'\n"
            "trusted_origins = ['mine-py']\n"
            + extra
        ),
        encoding="utf-8",
        newline="\n",
    )


class FakeRunner:
    """Returns pre-seeded agent stdout blocks in call order."""

    def __init__(self, outputs: list[str]) -> None:
        self._outputs = list(outputs)
        self.calls: list[dict] = []

    def run(self, adapter, plan_path, repo, **kwargs) -> AgentResult:
        self.calls.append({"plan_path": plan_path, "repo": repo, **kwargs})
        text = self._outputs.pop(0) if self._outputs else ""
        return AgentResult(
            exit_code=0,
            stdout_path=Path("out.log"),
            agent_log_path=Path("agent.log"),
            elapsed_sec=0.1,
            timed_out=False,
            parsed={"stdout": text},
        )


def _triage_block(**fields: str) -> str:
    return "```triage\n" + json.dumps(fields) + "\n```\n"


def _setup(monkeypatch, tmp_path, *, proposals, outputs, author_agent="codex", extra=""):
    _write_config(tmp_path, author_agent=author_agent, extra=extra)
    client = FakeIssuekitClient(proposals=proposals)
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *a, **k: client)
    monkeypatch.setattr(triage_author, "resolve_adapter", lambda *a, **k: object())
    runner = FakeRunner(outputs)
    monkeypatch.chdir(tmp_path)
    config = load_config(tmp_path)
    events: list[tuple[str, dict]] = []

    def log(event, **fields):
        events.append((event, fields))

    return client, runner, config, events, log


# --- parse unit tests -------------------------------------------------------


def test_parse_triage_output_reads_last_valid_block() -> None:
    stdout = (
        "prose\n"
        + _triage_block(decision="reply", question="broken?")
        + "more\n"
        + _triage_block(decision="adopt", spec_markdown="## Spec\n\nDo it.")
    )
    parsed = parse_triage_output(stdout)
    assert parsed == {"decision": "adopt", "spec_markdown": "## Spec\n\nDo it."}


def test_parse_triage_output_requires_block() -> None:
    with pytest.raises(TriageAuthorParseError, match="No ```triage``` block"):
        parse_triage_output("no block here")


def test_parse_triage_output_rejects_unknown_decision() -> None:
    with pytest.raises(TriageAuthorParseError, match="Invalid triage decision"):
        parse_triage_output(_triage_block(decision="maybe", spec_markdown="x"))


def test_parse_triage_output_requires_decision_field() -> None:
    with pytest.raises(TriageAuthorParseError, match="requires a non-empty"):
        parse_triage_output(_triage_block(decision="adopt", spec_markdown="  "))


def test_parse_triage_output_rejects_non_ascii() -> None:
    with pytest.raises(TriageAuthorParseError, match="ASCII-only"):
        parse_triage_output(_triage_block(decision="discard", reason="em dash — here"))


# --- decision application ---------------------------------------------------


def test_triage_author_adopt_appends_spec(monkeypatch, tmp_path) -> None:
    client, runner, config, events, log = _setup(
        monkeypatch,
        tmp_path,
        proposals=[
            {"id": 5, "origin": "mine-py#3@abc", "title": "Do a thing", "body": "Please."}
        ],
        outputs=[_triage_block(decision="adopt", spec_markdown="## Spec\n\nBuild it.")],
    )

    decisions = run_triage_author_cycle(
        config, tmp_path, runner_factory=lambda: runner, log=log
    )

    assert len(runner.calls) == 1
    assert [d.decision for d in decisions] == ["adopt"]
    assert client.get_proposal(5)["status"] == "adopted"
    issue_id = decisions[0].issue_id
    assert issue_id is not None
    assert client.get_issue(issue_id)["body"] == "Please.\n\n## Spec\n\nBuild it."
    assert ("triage_author_decision", {"proposal": 5, "decision": "adopt", "issue": issue_id}) in events


def test_triage_author_discard(monkeypatch, tmp_path) -> None:
    client, runner, config, events, log = _setup(
        monkeypatch,
        tmp_path,
        proposals=[
            {"id": 7, "origin": "mine-py#9@abc", "title": "Wrong repo", "body": "x"}
        ],
        outputs=[_triage_block(decision="discard", reason="Belongs to mine-py.")],
    )

    decisions = run_triage_author_cycle(
        config, tmp_path, runner_factory=lambda: runner, log=log
    )

    assert [d.decision for d in decisions] == ["discard"]
    assert decisions[0].detail == "Belongs to mine-py."
    assert client.get_proposal(7)["status"] == "discarded"


def test_triage_author_reply_sends_and_skips_next_cycle(monkeypatch, tmp_path) -> None:
    client, runner, config, events, log = _setup(
        monkeypatch,
        tmp_path,
        proposals=[
            {"id": 8, "origin": "mine-py#2@abc", "title": "Vague ask", "body": "help"}
        ],
        outputs=[
            _triage_block(decision="reply", question="What endpoint do you mean?"),
            _triage_block(decision="reply", question="second run should not happen"),
        ],
    )

    first = run_triage_author_cycle(
        config, tmp_path, runner_factory=lambda: runner, log=log
    )

    assert [d.decision for d in first] == ["reply"]
    # Original proposal stays pending; a Re: proposal was sent to the origin.
    assert client.get_proposal(8)["status"] == "pending"
    reply = client.get_proposal(9)
    assert reply["title"] == "Re: issuekit#8: Vague ask"
    assert reply["body"] == "What endpoint do you mean?"
    assert reply["origin"].startswith("issuekit#")
    # detail records the destination-project ref of the sent Re: proposal.
    assert first[0].detail == "mine-py#9"
    state_path = tmp_path / ".agent-runs" / "triage-author-state.json"
    assert json.loads(state_path.read_text(encoding="utf-8"))["8"]["body_sha"]

    # Second cycle: same body -> the replied proposal is skipped, agent not re-run.
    second = run_triage_author_cycle(
        config, tmp_path, runner_factory=lambda: runner, log=log
    )
    assert second == []
    assert len(runner.calls) == 1
    assert ("triage_author_skip", {"proposal": 8, "reason": "replied"}) in events


def test_triage_author_reply_reruns_when_body_changes(monkeypatch, tmp_path) -> None:
    client, runner, config, events, log = _setup(
        monkeypatch,
        tmp_path,
        proposals=[
            {"id": 8, "origin": "mine-py#2@abc", "title": "Vague ask", "body": "help"}
        ],
        outputs=[
            _triage_block(decision="reply", question="First question?"),
            _triage_block(decision="adopt", spec_markdown="## Spec\n\nClear now."),
        ],
    )

    run_triage_author_cycle(config, tmp_path, runner_factory=lambda: runner, log=log)
    # Origin edits the proposal body; the agent should evaluate it again.
    client._proposals[8]["body"] = "help: I mean the /users endpoint"

    second = run_triage_author_cycle(
        config, tmp_path, runner_factory=lambda: runner, log=log
    )
    assert [d.decision for d in second] == ["adopt"]
    assert len(runner.calls) == 2
    assert client.get_proposal(8)["status"] == "adopted"


def test_triage_author_parse_failure_leaves_pending(monkeypatch, tmp_path) -> None:
    client, runner, config, events, log = _setup(
        monkeypatch,
        tmp_path,
        proposals=[
            {"id": 4, "origin": "mine-py#1@abc", "title": "Ask", "body": "y"}
        ],
        outputs=["the agent forgot to emit a block"],
    )

    decisions = run_triage_author_cycle(
        config, tmp_path, runner_factory=lambda: runner, log=log
    )

    assert [d.decision for d in decisions] == ["error"]
    assert client.get_proposal(4)["status"] == "pending"
    assert any(event == "triage_author_error" for event, _ in events)


def test_triage_author_policy_gate_runs_before_agent(monkeypatch, tmp_path) -> None:
    client, runner, config, events, log = _setup(
        monkeypatch,
        tmp_path,
        proposals=[
            {"id": 3, "origin": "stranger#1@abc", "title": "Untrusted", "body": "z"}
        ],
        outputs=[_triage_block(decision="adopt", spec_markdown="nope")],
    )

    decisions = run_triage_author_cycle(
        config, tmp_path, runner_factory=lambda: runner, log=log
    )

    assert decisions == []
    assert runner.calls == []
    assert client.get_proposal(3)["status"] == "pending"


def test_triage_author_caps_evaluations_per_cycle(monkeypatch, tmp_path) -> None:
    client, runner, config, events, log = _setup(
        monkeypatch,
        tmp_path,
        proposals=[
            {"id": 1, "origin": "mine-py#1@abc", "title": "One", "body": "a"},
            {"id": 2, "origin": "mine-py#2@abc", "title": "Two", "body": "b"},
        ],
        outputs=[
            _triage_block(decision="adopt", spec_markdown="## A"),
            _triage_block(decision="adopt", spec_markdown="## B"),
        ],
        extra="max_adoptions_per_cycle = 1\n",
    )

    decisions = run_triage_author_cycle(
        config, tmp_path, runner_factory=lambda: runner, log=log
    )

    assert len(decisions) == 1
    assert len(runner.calls) == 1


def test_run_triage_author_cycle_requires_author_agent(monkeypatch, tmp_path) -> None:
    _write_config(tmp_path, author_agent="")
    monkeypatch.chdir(tmp_path)
    config = load_config(tmp_path)

    with pytest.raises(Exception, match="author_agent"):
        run_triage_author_cycle(config, tmp_path)


# --- triage --once CLI ------------------------------------------------------


def test_cli_triage_once_requires_flag(monkeypatch, tmp_path, capsys) -> None:
    from issuekit import cli

    _write_config(tmp_path, author_agent="codex")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["triage"]) == 1
    assert "--once" in capsys.readouterr().err


def test_cli_triage_once_requires_author_agent(monkeypatch, tmp_path, capsys) -> None:
    from issuekit import cli

    _write_config(tmp_path, author_agent="")
    monkeypatch.chdir(tmp_path)

    assert cli.main(["triage", "--once"]) == 1
    assert "author_agent" in capsys.readouterr().err


def test_cli_triage_once_prints_json_decisions(monkeypatch, tmp_path, capsys) -> None:
    from issuekit import cli
    from issuekit.commands import triage as triage_cmd

    client, runner, _config, _events, _log = _setup(
        monkeypatch,
        tmp_path,
        proposals=[
            {"id": 6, "origin": "mine-py#4@abc", "title": "Ask", "body": "Please."}
        ],
        outputs=[_triage_block(decision="adopt", spec_markdown="## Spec\n\nGo.")],
    )
    monkeypatch.setattr(triage_cmd, "AgentRunner", lambda: runner)

    assert cli.main(["triage", "--once", "--json"]) == 0
    decisions = json.loads(capsys.readouterr().out)
    assert decisions[0]["decision"] == "adopt"
    assert decisions[0]["proposal_id"] == 6
    assert client.get_proposal(6)["status"] == "adopted"

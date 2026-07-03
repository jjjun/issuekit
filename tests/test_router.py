"""Tests for the PM request router."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from issuekit import cli, proposals_api
from issuekit.agents import router
from issuekit.agents.runner import AgentResult
from issuekit.agents.router import RouterParseError, parse_router_output
from issuekit.config import RouterPolicy, load_config
from issuekit.proposals import ProposalError
from issuekit.testing import FakeIssuekitClient


class FakeRunner:
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


def _route_block(payload: dict) -> str:
    return "```route\n" + json.dumps(payload) + "\n```\n"


def _write_config(tmp_path: Path, *, extra_router: str = "") -> None:
    router_lines = "agent = 'codex'\n" + extra_router
    (tmp_path / "issuekit.toml").write_text(
        (
            "api_url = 'https://mine.example'\n"
            "project = 'pm'\n"
            "[router]\n"
            f"{router_lines}"
        ),
        encoding="utf-8",
        newline="\n",
    )


def _clients(monkeypatch, profiles: list[dict]) -> dict[str, FakeIssuekitClient]:
    clients: dict[str, FakeIssuekitClient] = {}
    pm = FakeIssuekitClient()
    pm._profiles = {str(profile["project"]): dict(profile) for profile in profiles}
    clients["pm"] = pm

    def fake_client(*args, **kwargs):
        project = kwargs.get("project") or "pm"
        client = clients.get(project)
        if client is None:
            client = FakeIssuekitClient()
            clients[project] = client
        client.project = project
        return client

    monkeypatch.setattr(proposals_api, "IssuekitClient", fake_client)
    return clients


def _setup(monkeypatch, tmp_path, outputs, *, profiles=None, extra_router: str = ""):
    _write_config(tmp_path, extra_router=extra_router)
    profiles = profiles or [
        {
            "project": "api",
            "summary": "API service",
            "tags": ["python"],
            "profile_md": "Owns HTTP APIs.",
        },
        {
            "project": "ui",
            "summary": "UI app",
            "tags": ["frontend"],
            "profile_md": "Owns the web UI.",
        },
    ]
    clients = _clients(monkeypatch, profiles)
    fake_runner = FakeRunner(outputs)
    monkeypatch.setattr(router, "resolve_adapter", lambda *a, **k: object())
    from issuekit.commands import request as request_cmd

    monkeypatch.setattr(request_cmd, "AgentRunner", lambda: fake_runner)
    monkeypatch.chdir(tmp_path)
    return clients, fake_runner


def test_load_config_reads_router_policy(tmp_path: Path) -> None:
    _write_config(tmp_path, extra_router="max_targets = 2\nmax_clarify_rounds = 1\n")

    config = load_config(tmp_path)

    assert config.router == RouterPolicy(
        agent="codex",
        max_targets=2,
        max_clarify_rounds=1,
    )


def test_load_config_rejects_invalid_router_policy(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "[router]\nagent = 'bad agent'\n",
        encoding="utf-8",
        newline="\n",
    )

    with pytest.raises(ValueError, match="router.agent"):
        load_config(tmp_path)


def test_parse_router_output_rejects_unknown_target_project() -> None:
    with pytest.raises(RouterParseError, match="no candidate profile"):
        parse_router_output(
            _route_block(
                {
                    "decision": "route",
                    "targets": [
                        {"project": "missing", "title": "Title", "body": "Body."}
                    ],
                }
            ),
            candidates=[],
            max_targets=3,
        )


def test_parse_router_output_rejects_forward_target_dependency() -> None:
    candidates = [router.ProjectProfile("api", "", (), ""), router.ProjectProfile("ui", "", (), "")]

    with pytest.raises(RouterParseError, match="earlier"):
        parse_router_output(
            _route_block(
                {
                    "decision": "route",
                    "targets": [
                        {
                            "project": "api",
                            "title": "API",
                            "body": "Body.",
                            "depends_on": ["target:1"],
                        },
                        {"project": "ui", "title": "UI", "body": "Body."},
                    ],
                }
            ),
            candidates=candidates,
            max_targets=3,
        )


def test_request_routes_single_target(monkeypatch, tmp_path, capsys) -> None:
    clients, runner = _setup(
        monkeypatch,
        tmp_path,
        [
            _route_block(
                {
                    "decision": "route",
                    "targets": [
                        {
                            "project": "api",
                            "title": "Add export endpoint",
                            "body": "Add a CSV export endpoint.",
                            "blocking": True,
                        }
                    ],
                }
            )
        ],
    )

    assert cli.main(["request", "Add CSV export", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["request_id"] == 1
    assert payload["decision"] == "route"
    assert payload["targets"][0]["proposal_ref"] == "api#1"
    assert clients["api"].calls[0]["body"]["title"] == "Add export endpoint"
    assert clients["api"].calls[0]["body"]["blocking"] is True
    assert len(runner.calls) == 1


def test_request_routes_multi_target_and_resolves_target_dependency(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    clients, _runner = _setup(
        monkeypatch,
        tmp_path,
        [
            _route_block(
                {
                    "decision": "route",
                    "targets": [
                        {"project": "api", "title": "Add endpoint", "body": "Add API."},
                        {
                            "project": "ui",
                            "title": "Use endpoint",
                            "body": "Call the new API.",
                            "depends_on": ["target:0"],
                        },
                    ],
                }
            )
        ],
    )

    assert cli.main(["request", "Add export UI", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert [target["proposal_ref"] for target in payload["targets"]] == ["api#1", "ui#1"]
    assert clients["ui"].calls[0]["body"]["depends_on"] == ["api#1"]


def test_request_stops_on_send_failure_and_resume_skips_sent_target(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    clients, _runner = _setup(
        monkeypatch,
        tmp_path,
        [
            _route_block(
                {
                    "decision": "route",
                    "targets": [
                        {"project": "api", "title": "Add endpoint", "body": "Add API."},
                        {
                            "project": "ui",
                            "title": "Use endpoint",
                            "body": "Call API.",
                            "depends_on": ["target:0"],
                        },
                    ],
                }
            ),
            _route_block(
                {
                    "decision": "route",
                    "targets": [
                        {"project": "api", "title": "Add endpoint", "body": "Add API."},
                        {
                            "project": "ui",
                            "title": "Use endpoint",
                            "body": "Call API.",
                            "depends_on": ["target:0"],
                        },
                    ],
                }
            ),
        ],
    )
    original_send = proposals_api.send_proposal
    failures_left = {"count": 1}

    def flaky_send(config, proposal):
        if proposal.to == "ui" and failures_left["count"]:
            failures_left["count"] -= 1
            raise ProposalError("ui unavailable")
        return original_send(config, proposal)

    monkeypatch.setattr(proposals_api, "send_proposal", flaky_send)

    assert cli.main(["request", "Add export UI", "--json"]) == 1
    assert "ui unavailable" in capsys.readouterr().err
    state = json.loads((tmp_path / ".agent-runs" / "pm-requests.json").read_text(encoding="utf-8"))
    assert state["1"]["targets"][0]["proposal_ref"] == "api#1"
    assert "proposal_ref" not in state["1"]["targets"][1]

    assert cli.main(["request", "Add export UI", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert [target["proposal_ref"] for target in payload["targets"]] == ["api#1", "ui#1"]
    assert len(clients["api"].calls) == 1
    assert clients["ui"].calls[0]["body"]["depends_on"] == ["api#1"]


def test_request_filters_stale_and_own_project_profiles_from_prompt(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    _clients, runner = _setup(
        monkeypatch,
        tmp_path,
        [_route_block({"decision": "reject", "reason": "No owner."})],
        profiles=[
            {"project": "pm", "summary": "PM", "profile_md": "Own checkout."},
            {"project": "old", "summary": "Old", "profile_md": "Stale.", "stale": True},
            {"project": "api", "summary": "API", "profile_md": "Fresh."},
        ],
    )

    assert cli.main(["request", "Where does this go?"]) == 0
    capsys.readouterr()
    prompt = runner.calls[0]["plan_path"].read_text(encoding="utf-8")

    assert "## Project: api" in prompt
    assert "## Project: pm" not in prompt
    assert "## Project: old" not in prompt


def test_request_clarify_answer_round_cap_turns_second_clarify_into_reject(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    _clients, _runner = _setup(
        monkeypatch,
        tmp_path,
        [
            _route_block({"decision": "clarify", "question": "Which format?"}),
            _route_block({"decision": "clarify", "question": "Which endpoint?"}),
        ],
        extra_router="max_clarify_rounds = 1\n",
    )

    assert cli.main(["request", "Add export", "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["decision"] == "clarify"

    assert cli.main(["request", "--answer", "1", "CSV", "--json"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["decision"] == "reject"
    assert "Clarification limit reached" in second["reason"]


def test_request_zero_clarify_round_cap_rejects_initial_clarify(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    _clients, _runner = _setup(
        monkeypatch,
        tmp_path,
        [_route_block({"decision": "clarify", "question": "Which format?"})],
        extra_router="max_clarify_rounds = 0\n",
    )

    assert cli.main(["request", "Add export", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["decision"] == "reject"
    assert "Clarification limit reached" in payload["reason"]


def test_request_reject_and_dry_run(monkeypatch, tmp_path, capsys) -> None:
    clients, _runner = _setup(
        monkeypatch,
        tmp_path,
        [_route_block({"decision": "reject", "reason": "No profiled owner."})],
    )

    assert cli.main(["request", "Do unknown thing", "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["decision"] == "reject"
    assert clients.get("api") is None
    assert not (tmp_path / ".agent-runs" / "pm-requests.json").exists()


def test_request_status_maps_outgoing_status(monkeypatch, tmp_path, capsys) -> None:
    clients, _runner = _setup(
        monkeypatch,
        tmp_path,
        [
            _route_block(
                {
                    "decision": "route",
                    "targets": [
                        {"project": "api", "title": "Add endpoint", "body": "Add API."}
                    ],
                }
            )
        ],
    )
    assert cli.main(["request", "Add API", "--json"]) == 0
    capsys.readouterr()
    clients["api"]._proposals[1]["status"] = "adopted"
    clients["api"]._proposals[1]["adopted_issue_number"] = 42

    assert cli.main(["request", "--status", "1", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)

    assert status[0]["targets"][0]["status"] == "adopted"
    assert status[0]["targets"][0]["adopted_issue_ref"] == "api#42"


def test_request_requires_router_agent(monkeypatch, tmp_path, capsys) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'pm'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["request", "Add API"]) == 1

    assert "[tool.issuekit.router] agent" in capsys.readouterr().err
    assert not (tmp_path / ".agent-runs").exists()

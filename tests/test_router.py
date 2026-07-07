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


def _register_catalog_projects(
    clients: dict[str, FakeIssuekitClient], *projects: str
) -> None:
    for project in projects:
        clients["pm"].register_catalog_project(project)


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


def _write_request_state(tmp_path: Path, state: dict) -> None:
    state_path = tmp_path / ".agent-runs" / "pm-requests.json"
    state_path.parent.mkdir()
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8", newline="\n")


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
    assert clients["ui"].calls[0]["body"]["depends_on"] == ["api#proposal:1"]


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
    assert clients["ui"].calls[0]["body"]["depends_on"] == ["api#proposal:1"]


def test_request_link_records_existing_proposal_for_unsent_target(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    _write_config(tmp_path)
    clients = _clients(monkeypatch, [])
    _register_catalog_projects(clients, "api", "ui")
    ui_client = FakeIssuekitClient()
    clients["ui"] = ui_client
    ui_client.project = "ui"
    ui_client.create_proposal(
        origin="pm#manual@abc",
        title="Use endpoint",
        body="Call the manually filed proposal.",
    )
    _write_request_state(
        tmp_path,
        {
            "1": {
                "id": 1,
                "original_text": "Add export UI",
                "decision": "route",
                "targets": [
                    {
                        "project": "api",
                        "title": "Add endpoint",
                        "body": "Add API.",
                        "proposal_ref": "api#1",
                    },
                    {
                        "project": "ui",
                        "title": "Use endpoint",
                        "body": "Call API.",
                        "depends_on": ["target:0"],
                    },
                ],
            }
        },
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["request", "--link", "1", "--target", "ui", "ui#1", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    state = json.loads((tmp_path / ".agent-runs" / "pm-requests.json").read_text(encoding="utf-8"))

    assert payload == {
        "request_id": 1,
        "decision": "link",
        "target_project": "ui",
        "proposal_ref": "ui#1",
    }
    assert state["1"]["targets"][1]["proposal_ref"] == "ui#1"
    assert state["1"]["targets"][1]["proposal_id"] == 1
    assert state["1"]["targets"][1]["status"] == "pending"

    assert cli.main(["request", "--status", "1", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status[0]["targets"][1]["proposal_ref"] == "ui#1"
    assert status[0]["targets"][1]["status"] == "pending"


def test_request_link_rejects_unknown_request_id(monkeypatch, tmp_path, capsys) -> None:
    _write_config(tmp_path)
    _clients(monkeypatch, [])
    _write_request_state(tmp_path, {})
    monkeypatch.chdir(tmp_path)

    assert cli.main(["request", "--link", "99", "--target", "api", "api#1"]) == 1

    assert "PM request 99 was not found." in capsys.readouterr().err


def test_request_link_requires_matching_unsent_target_project(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    _write_config(tmp_path)
    _clients(monkeypatch, [])
    _write_request_state(
        tmp_path,
        {
            "1": {
                "id": 1,
                "decision": "route",
                "targets": [{"project": "api", "title": "API", "body": "Body."}],
            }
        },
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["request", "--link", "1", "--target", "ui", "ui#1"]) == 1

    assert "PM request 1 has no target for project ui." in capsys.readouterr().err


def test_request_link_rejects_project_mismatch(monkeypatch, tmp_path, capsys) -> None:
    _write_config(tmp_path)
    _clients(monkeypatch, [])
    _write_request_state(
        tmp_path,
        {
            "1": {
                "id": 1,
                "decision": "route",
                "targets": [{"project": "api", "title": "API", "body": "Body."}],
            }
        },
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["request", "--link", "1", "--target", "api", "ui#1"]) == 1

    assert "Proposal ref ui#1 targets ui, not api." in capsys.readouterr().err


def test_request_link_reports_missing_proposal(monkeypatch, tmp_path, capsys) -> None:
    _write_config(tmp_path)
    _clients(monkeypatch, [])
    _write_request_state(
        tmp_path,
        {
            "1": {
                "id": 1,
                "decision": "route",
                "targets": [{"project": "api", "title": "API", "body": "Body."}],
            }
        },
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["request", "--link", "1", "--target", "api", "api#99"]) == 1

    assert "Proposal api#99 was not found in api." in capsys.readouterr().err


def test_request_link_rejects_already_sent_target(monkeypatch, tmp_path, capsys) -> None:
    _write_config(tmp_path)
    clients = _clients(monkeypatch, [])
    api_client = FakeIssuekitClient()
    clients["api"] = api_client
    api_client.project = "api"
    api_client.create_proposal(origin="pm#manual@abc", title="API", body="Body.")
    _write_request_state(
        tmp_path,
        {
            "1": {
                "id": 1,
                "decision": "route",
                "targets": [
                    {
                        "project": "api",
                        "title": "API",
                        "body": "Body.",
                        "proposal_ref": "api#1",
                    }
                ],
            }
        },
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["request", "--link", "1", "--target", "api", "api#1"]) == 1

    assert "PM request 1 target api is already sent or linked." in capsys.readouterr().err


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
    assert len(_runner.calls) == 2


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


def test_request_inbox_lists_matched_and_unmatched_replies(
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
                        {"project": "api", "title": "Add endpoint", "body": "Add API."}
                    ],
                }
            )
        ],
    )
    assert cli.main(["request", "Add API", "--json"]) == 0
    capsys.readouterr()
    clients["pm"].create_proposal(
        origin="api#1@abc",
        title="Re: api#1: Add endpoint",
        body="Which endpoint path?",
    )
    clients["pm"].create_proposal(
        origin="api#99@abc",
        title="Re: api#99: Unknown",
        body="Who owns this?",
    )
    clients["pm"].create_proposal(origin="api#2@abc", title="Not a reply", body="Ignore.")

    assert cli.main(["request", "--inbox", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["matched"] == [
        {
            "reply_proposal_id": 1,
            "proposal_ref": "api#1",
            "target_project": "api",
            "title": "Re: api#1: Add endpoint",
            "original_title": "Add endpoint",
            "question": "Which endpoint path?",
            "request_id": 1,
            "target_index": 0,
        }
    ]
    assert payload["unmatched"][0]["proposal_ref"] == "api#99"
    assert payload["unmatched"][0]["question"] == "Who owns this?"


def test_request_answer_resends_amended_proposal_and_discards_reply(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
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
                            "title": "Add endpoint",
                            "body": "Original body.",
                            "blocking": True,
                        }
                    ],
                }
            )
        ],
    )
    assert cli.main(["request", "Add API", "--json"]) == 0
    capsys.readouterr()
    reply = clients["pm"].create_proposal(
        origin="api#1@abc",
        title="Re: api#1: Add endpoint",
        body="Which endpoint path?",
    )

    assert cli.main(["request", "--answer", "1", "Use /exports.csv.", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    state = json.loads((tmp_path / ".agent-runs" / "pm-requests.json").read_text(encoding="utf-8"))
    amended = clients["api"].get_proposal(2)

    assert payload["decision"] == "answer"
    assert payload["proposal_ref"] == "api#2"
    assert payload["supersedes"] == "api#1"
    assert amended["title"] == "Add endpoint"
    assert amended["blocking"] is True
    assert amended["body"].startswith("Original body.\n\n## Clarifications")
    assert "Question:\n\nWhich endpoint path?" in amended["body"]
    assert "Answer:\n\nUse /exports.csv." in amended["body"]
    assert amended["body"].endswith("Supersedes: api#1")
    assert state["1"]["targets"][0]["proposal_ref"] == "api#2"
    assert state["1"]["targets"][0]["clarifications"] == [
        {"question": "Which endpoint path?", "answer": "Use /exports.csv."}
    ]
    assert clients["pm"].get_proposal(reply["id"])["status"] == "discarded"
    assert len(runner.calls) == 1


def test_request_answer_send_failure_keeps_old_state_and_reply_pending(
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
                        {"project": "api", "title": "Add endpoint", "body": "Original body."}
                    ],
                }
            )
        ],
    )
    assert cli.main(["request", "Add API", "--json"]) == 0
    capsys.readouterr()
    reply = clients["pm"].create_proposal(
        origin="api#1@abc",
        title="Re: api#1: Add endpoint",
        body="Which endpoint path?",
    )
    original_send = proposals_api.send_proposal

    def fail_amended(config, proposal):
        if proposal.to == "api" and "Supersedes:" in proposal.body:
            raise ProposalError("api unavailable")
        return original_send(config, proposal)

    monkeypatch.setattr(proposals_api, "send_proposal", fail_amended)

    assert cli.main(["request", "--answer", "1", "Use /exports.csv.", "--json"]) == 1
    assert "api unavailable" in capsys.readouterr().err
    state = json.loads((tmp_path / ".agent-runs" / "pm-requests.json").read_text(encoding="utf-8"))

    assert state["1"]["targets"][0]["proposal_ref"] == "api#1"
    assert "clarifications" not in state["1"]["targets"][0]
    assert clients["pm"].get_proposal(reply["id"])["status"] == "pending"


def test_request_answer_requires_target_when_multiple_replies_are_pending(
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
                        {"project": "ui", "title": "Use endpoint", "body": "Use API."},
                    ],
                }
            )
        ],
    )
    assert cli.main(["request", "Add export UI", "--json"]) == 0
    capsys.readouterr()
    clients["pm"].create_proposal(origin="api#1@abc", title="Re: api#1: Add endpoint", body="API?")
    clients["pm"].create_proposal(origin="ui#1@abc", title="Re: ui#1: Use endpoint", body="UI?")

    assert cli.main(["request", "--answer", "1", "Use v1.", "--json"]) == 1
    err = capsys.readouterr().err
    assert "--target" in err
    assert "api: API?" in err
    assert "ui: UI?" in err

    assert cli.main(["request", "--answer", "1", "Use table view.", "--target", "ui", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["target_project"] == "ui"
    assert payload["proposal_ref"] == "ui#2"
    assert clients["pm"].get_proposal(1)["status"] == "pending"
    assert clients["pm"].get_proposal(2)["status"] == "discarded"


def test_request_answer_accumulates_multiple_clarification_rounds(
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
                        {"project": "api", "title": "Add endpoint", "body": "Original body."}
                    ],
                }
            )
        ],
    )
    assert cli.main(["request", "Add API", "--json"]) == 0
    capsys.readouterr()
    clients["pm"].create_proposal(origin="api#1@abc", title="Re: api#1: Add endpoint", body="Q1?")
    assert cli.main(["request", "--answer", "1", "A1.", "--json"]) == 0
    capsys.readouterr()
    clients["pm"].create_proposal(origin="api#2@abc", title="Re: api#2: Add endpoint", body="Q2?")

    assert cli.main(["request", "--answer", "1", "A2.", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    amended = clients["api"].get_proposal(3)

    assert payload["proposal_ref"] == "api#3"
    assert amended["body"].count("## Clarifications") == 1
    assert "### Round 1" in amended["body"]
    assert "Q1?" in amended["body"]
    assert "A1." in amended["body"]
    assert "### Round 2" in amended["body"]
    assert "Q2?" in amended["body"]
    assert "A2." in amended["body"]
    assert "Supersedes: api#1" not in amended["body"]
    assert amended["body"].endswith("Supersedes: api#2")


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

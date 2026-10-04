"""Tests for the PM request router."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import issuekit.proposals.api as proposals_api
from issuekit import cli
from issuekit.agentrun import AgentPrompt
from issuekit.agents import router
from issuekit.agents.registry import resolve_adapter
from issuekit.agents.router import RouterParseError, parse_router_output
from issuekit.config import RouterPolicy, load_config
from issuekit.proposals import ProposalError
from issuekit.testing import FakeIssuekitClient
from tests.agent_fakes import FakeRunner, fenced_block
from tests.git_helpers import init_git_repo


def _route_block(payload: dict) -> str:
    return fenced_block("route", payload)


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


def _clients(fake_api, monkeypatch, profiles: list[dict]) -> dict[str, FakeIssuekitClient]:
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

    fake_api.install_factory(fake_client)
    return clients


def _register_catalog_projects(
    clients: dict[str, FakeIssuekitClient], *projects: str
) -> None:
    for project in projects:
        clients["pm"].register_catalog_project(project)


def _setup(fake_api, monkeypatch, tmp_path, outputs, *, profiles=None, extra_router: str = ""):
    _write_config(tmp_path, extra_router=extra_router)
    init_git_repo(tmp_path)
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
    clients = _clients(fake_api, monkeypatch, profiles)
    fake_runner = FakeRunner(outputs)
    monkeypatch.setattr(router, "resolve_adapter", lambda *a, **k: object())
    from issuekit.commands.request import answers, routing

    monkeypatch.setattr(answers, "AgentRunner", lambda: fake_runner)
    monkeypatch.setattr(routing, "AgentRunner", lambda: fake_runner)
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


def test_router_role_overlay_and_explicit_model_override(tmp_path: Path) -> None:
    (tmp_path / "issuekit.toml").write_text(
        (
            "[router]\nagent = 'claude'\n"
            "[agents.claude.roles.router]\n"
            "model = 'claude-opus-4-8'\nreasoning_effort = 'high'\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    config = load_config(tmp_path)

    overlay = resolve_adapter("claude", config=config, role="router")
    explicit = resolve_adapter(
        "claude", config=config, role="router", model="claude-haiku-4-5"
    )

    overlay_argv = overlay.build_argv("prompt", tmp_path / "plan.md")
    explicit_argv = explicit.build_argv("prompt", tmp_path / "plan.md")
    assert overlay_argv[overlay_argv.index("--model") + 1] == "claude-opus-4-8"
    assert overlay_argv[-2:] == ["--effort", "high"]
    assert explicit_argv[explicit_argv.index("--model") + 1] == "claude-haiku-4-5"


def test_request_passes_overrides_to_each_pre_routing_router_run(
    fake_api,
    monkeypatch, tmp_path, capsys
) -> None:
    _clients, _runner = _setup(
        fake_api,
        monkeypatch,
        tmp_path,
        [
            _route_block({"decision": "clarify", "question": "Which format?"}),
            _route_block({"decision": "reject", "reason": "No owner."}),
        ],
    )
    calls: list[dict] = []

    def capture_adapter(*args, **kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(router, "resolve_adapter", capture_adapter)

    assert cli.main(
        [
            "request",
            "Add export",
            "--model",
            "gpt-5.6",
            "--reasoning-effort",
            "high",
        ]
    ) == 0
    capsys.readouterr()
    assert cli.main(
        [
            "request",
            "--answer",
            "1",
            "CSV",
            "--model",
            "gpt-5.6",
            "--reasoning-effort",
            "high",
        ]
    ) == 0

    assert calls == [
        {
            "config": load_config(tmp_path),
            "model": "gpt-5.6",
            "reasoning_effort": "high",
            "role": "router",
        },
        {
            "config": load_config(tmp_path),
            "model": "gpt-5.6",
            "reasoning_effort": "high",
            "role": "router",
        },
    ]


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


def test_parse_router_output_normalizes_decision() -> None:
    decision = parse_router_output(
        _route_block({"decision": " Clarify ", "question": "Which project?"}),
        candidates=[],
        max_targets=3,
    )

    assert decision.decision == "clarify"


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


@pytest.mark.parametrize("blocking", ["false", 0, 1, [], {}])
def test_parse_router_output_requires_boolean_blocking(blocking: object) -> None:
    candidates = [router.ProjectProfile("api", "", (), "")]

    with pytest.raises(RouterParseError, match="JSON boolean"):
        parse_router_output(
            _route_block(
                {
                    "decision": "route",
                    "targets": [
                        {
                            "project": "api",
                            "title": "API",
                            "body": "Body.",
                            "blocking": blocking,
                        }
                    ],
                }
            ),
            candidates=candidates,
            max_targets=3,
        )


def test_request_routes_single_target(fake_api, monkeypatch, tmp_path, capsys) -> None:
    clients, runner = _setup(
        fake_api,
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
    fake_api,
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    clients, _runner = _setup(
        fake_api,
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
    fake_api,
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    clients, _runner = _setup(
        fake_api,
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


def test_request_resume_matches_saved_targets_by_project_after_reordering(
    fake_api,
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    clients, _runner = _setup(
        fake_api,
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
                        {
                            "project": "ui",
                            "title": "Changed title",
                            "body": "Changed body.",
                        },
                        {"project": "api", "title": "Changed API", "body": "Changed."},
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
    assert cli.main(["request", "Add export UI", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert [target["proposal_ref"] for target in payload["targets"]] == [
        "api#1",
        "ui#1",
    ]
    saved_state = json.loads(
        (tmp_path / ".agent-runs" / "pm-requests.json").read_text(encoding="utf-8")
    )
    assert saved_state["1"]["decision"] == "route"
    assert all(target.get("proposal_ref") for target in saved_state["1"]["targets"])
    assert len(clients["api"].calls) == 1
    assert len(clients["ui"].calls) == 1
    assert clients["ui"].calls[0]["body"]["title"] == "Use endpoint"
    assert clients["ui"].calls[0]["body"]["body"] == "Call API."
    assert clients["ui"].calls[0]["body"]["depends_on"] == ["api#proposal:1"]
    assert _create_origins(clients["api"])[0].startswith(
        "pm#request-1-target-0-api@"
    )
    assert _create_origins(clients["ui"])[0].startswith(
        "pm#request-1-target-1-ui@"
    )


def test_request_resume_sends_only_new_projects_and_resolves_saved_indexes(
    fake_api,
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    clients, _runner = _setup(
        fake_api,
        monkeypatch,
        tmp_path,
        [
            _route_block(
                {
                    "decision": "route",
                    "targets": [
                        {"project": "ui", "title": "UI update", "body": "Update UI."},
                        {"project": "api", "title": "API update", "body": "Update API."},
                        {
                            "project": "worker",
                            "title": "Worker update",
                            "body": "Update worker.",
                            "depends_on": ["target:0"],
                        },
                    ],
                }
            )
        ],
        profiles=[
            {
                "project": "api",
                "summary": "API service",
                "profile_md": "Owns HTTP APIs.",
            },
            {"project": "ui", "summary": "UI app", "profile_md": "Owns the UI."},
            {
                "project": "worker",
                "summary": "Worker service",
                "profile_md": "Owns background jobs.",
            },
        ],
    )
    _write_request_state(
        tmp_path,
        {
            "1": {
                "id": 1,
                "original_text": "Add export UI",
                "decision": "clarify",
                "targets": [
                    {
                        "project": "api",
                        "title": "API update",
                        "body": "Update API.",
                        "proposal_ref": "api#7",
                        "dependency_ref": "api#proposal:7",
                    },
                    {
                        "project": "ui",
                        "title": "UI update",
                        "body": "Update UI.",
                        "proposal_ref": "ui#8",
                        "dependency_ref": "ui#proposal:8",
                    },
                ],
            }
        },
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["request", "Add export UI", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["request_id"] == 1
    assert [target["proposal_ref"] for target in payload["targets"]] == [
        "api#7",
        "ui#8",
        "worker#1",
    ]
    assert "api" not in clients
    assert "ui" not in clients
    assert clients["worker"].calls[0]["body"]["depends_on"] == ["ui#proposal:8"]
    assert _create_origins(clients["worker"])[0].startswith(
        "pm#request-1-target-2-worker@"
    )


def test_request_rejects_duplicate_route_projects_before_sending(
    fake_api,
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    clients, _runner = _setup(
        fake_api,
        monkeypatch,
        tmp_path,
        [
            _route_block(
                {
                    "decision": "route",
                    "targets": [
                        {"project": "api", "title": "First", "body": "First API."},
                        {"project": "api", "title": "Second", "body": "Second API."},
                    ],
                }
            )
        ],
    )

    assert cli.main(["request", "Add export", "--json"]) == 1

    assert "duplicate project target: api" in capsys.readouterr().err
    assert "api" not in clients


def _single_api_route(title: str, body: str) -> str:
    return _route_block(
        {
            "decision": "route",
            "targets": [{"project": "api", "title": title, "body": body}],
        }
    )


def _create_origins(client: FakeIssuekitClient) -> list[str]:
    return [
        call["body"]["origin"] for call in client.calls if call["method"] == "create_proposal"
    ]


def _forget_sent_target(tmp_path: Path, request_id: str) -> None:
    state_path = tmp_path / ".agent-runs" / "pm-requests.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    for key in ("proposal_ref", "dependency_ref", "proposal_id", "sent_at"):
        state[request_id]["targets"][0].pop(key, None)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8", newline="\n")


def test_request_routes_distinct_requests_to_same_target(fake_api, monkeypatch, tmp_path, capsys) -> None:
    clients, _runner = _setup(
        fake_api,
        monkeypatch,
        tmp_path,
        [
            _single_api_route("Add export endpoint", "Add a CSV export endpoint."),
            _single_api_route("Add export audit log", "Record export audit events."),
        ],
    )

    assert cli.main(["request", "Add CSV export", "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert cli.main(["request", "Audit exports", "--json"]) == 0
    second = json.loads(capsys.readouterr().out)

    assert first["targets"][0]["proposal_ref"] == "api#1"
    assert second["targets"][0]["proposal_ref"] == "api#2"
    first_origin, second_origin = _create_origins(clients["api"])
    assert first_origin.startswith("pm#request-1-target-0-api@")
    assert second_origin.startswith("pm#request-2-target-0-api@")
    assert len(clients["api"]._proposals) == 2


def test_request_rerun_dedupes_against_its_own_sent_proposal(
    fake_api,
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    route = _single_api_route("Add export endpoint", "Add a CSV export endpoint.")
    clients, _runner = _setup(fake_api, monkeypatch, tmp_path, [route, route])

    assert cli.main(["request", "Add CSV export", "--json"]) == 0
    capsys.readouterr()
    _forget_sent_target(tmp_path, "1")

    assert cli.main(["request", "Add CSV export", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["request_id"] == 1
    assert payload["targets"][0]["proposal_ref"] == "api#1"
    first_origin, second_origin = _create_origins(clients["api"])
    assert first_origin == second_origin
    assert len(clients["api"]._proposals) == 1


def test_request_rerun_payload_mismatch_suggests_link_or_discard(
    fake_api,
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    clients, _runner = _setup(
        fake_api,
        monkeypatch,
        tmp_path,
        [
            _single_api_route("Add export endpoint", "Add a CSV export endpoint."),
            _single_api_route("Add export endpoint", "Add a JSON export endpoint."),
        ],
    )

    assert cli.main(["request", "Add CSV export", "--json"]) == 0
    capsys.readouterr()
    _forget_sent_target(tmp_path, "1")
    clients["api"]._proposals[1]["body"] = "An unrelated pending proposal body."

    assert cli.main(["request", "Add CSV export", "--json"]) == 1
    err = capsys.readouterr().err

    assert "issuekit request --link 1 --target api api#1" in err
    assert "issuekit discard 1 --to api" in err
    assert "--from-issue" not in err


def test_request_link_records_existing_proposal_for_unsent_target(
    fake_api,
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    _write_config(tmp_path)
    clients = _clients(fake_api, monkeypatch, [])
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


def test_request_link_rejects_unknown_request_id(fake_api, monkeypatch, tmp_path, capsys) -> None:
    _write_config(tmp_path)
    _clients(fake_api, monkeypatch, [])
    _write_request_state(tmp_path, {})
    monkeypatch.chdir(tmp_path)

    assert cli.main(["request", "--link", "99", "--target", "api", "api#1"]) == 1

    assert "PM request 99 was not found." in capsys.readouterr().err


def test_request_link_requires_matching_unsent_target_project(
    fake_api,
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    _write_config(tmp_path)
    _clients(fake_api, monkeypatch, [])
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


def test_request_link_rejects_project_mismatch(fake_api, monkeypatch, tmp_path, capsys) -> None:
    _write_config(tmp_path)
    _clients(fake_api, monkeypatch, [])
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


def test_request_link_reports_missing_proposal(fake_api, monkeypatch, tmp_path, capsys) -> None:
    _write_config(tmp_path)
    _clients(fake_api, monkeypatch, [])
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


def test_request_link_rejects_already_sent_target(fake_api, monkeypatch, tmp_path, capsys) -> None:
    _write_config(tmp_path)
    clients = _clients(fake_api, monkeypatch, [])
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
    fake_api,
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    _clients, runner = _setup(
        fake_api,
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
    prompt = runner.calls[0]["prompt"]

    assert "## Project: api" in prompt.body
    assert "## Project: pm" not in prompt.body
    assert "## Project: old" not in prompt.body


def test_request_clarify_answer_round_cap_turns_second_clarify_into_reject(
    fake_api,
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    _clients, _runner = _setup(
        fake_api,
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
    fake_api,
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    _clients, _runner = _setup(
        fake_api,
        monkeypatch,
        tmp_path,
        [_route_block({"decision": "clarify", "question": "Which format?"})],
        extra_router="max_clarify_rounds = 0\n",
    )

    assert cli.main(["request", "Add export", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["decision"] == "reject"
    assert "Clarification limit reached" in payload["reason"]


def test_request_reject_and_dry_run(fake_api, monkeypatch, tmp_path, capsys) -> None:
    clients, _runner = _setup(
        fake_api,
        monkeypatch,
        tmp_path,
        [_route_block({"decision": "reject", "reason": "No profiled owner."})],
    )

    assert cli.main(["request", "Do unknown thing", "--dry-run", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["decision"] == "reject"
    assert clients.get("api") is None
    assert not (tmp_path / ".agent-runs" / "pm-requests.json").exists()


def test_request_status_maps_outgoing_status(fake_api, monkeypatch, tmp_path, capsys) -> None:
    clients, _runner = _setup(
        fake_api,
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


def test_request_status_all_lists_raw_proposal_rows_once_per_project(
    fake_api,
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    clients, _runner = _setup(fake_api, monkeypatch, tmp_path, [])
    clients["api"] = FakeIssuekitClient(
        proposals=[
            {"id": 1, "origin": "pm#1@abc", "status": "pending"},
            {
                "id": 2,
                "origin": "pm#2@abc",
                "status": "adopted",
                "adopted_issue_number": 51,
            },
        ]
    )
    clients["ui"] = FakeIssuekitClient(
        proposals=[{"id": 1, "origin": "pm#3@abc", "status": "discarded"}]
    )
    calls_by_project: dict[str, list[str | None]] = {}

    def fail_if_called(*_args, **_kwargs):
        pytest.fail("status loaded proposal enrichment")

    for project in ("api", "ui"):
        client = clients[project]
        calls_by_project[project] = []
        original_list_proposals = client.list_proposals

        def counted_list_proposals(
            *,
            status=None,
            _project=project,
            _original=original_list_proposals,
            **kwargs,
        ):
            calls_by_project[_project].append(status)
            return _original(status=status, **kwargs)

        monkeypatch.setattr(client, "list_proposals", counted_list_proposals)
        monkeypatch.setattr(client, "list_project_profiles", fail_if_called)
        monkeypatch.setattr(client, "list_workers", fail_if_called)
        monkeypatch.setattr(client, "list_proposal_checks_for_proposal", fail_if_called)
        monkeypatch.setattr(client, "get_issue", fail_if_called)

    _write_request_state(
        tmp_path,
        {
            "1": {
                "original_text": "First",
                "decision": "route",
                "targets": [
                    {"project": "api", "proposal_ref": "api#1"},
                    {"project": "ui", "proposal_ref": "ui#1"},
                ],
            },
            "2": {
                "original_text": "Second",
                "decision": "route",
                "targets": [
                    {"project": "api", "proposal_ref": "api#2"},
                    {"project": "api", "proposal_ref": "api#1"},
                ],
            },
            "3": {
                "original_text": "Third",
                "decision": "route",
                "targets": [{"project": "ui", "proposal_ref": "ui#1"}],
            },
        },
    )

    assert cli.main(["request", "--status", "all", "--json"]) == 0
    status = json.loads(capsys.readouterr().out)

    assert calls_by_project == {
        "api": ["pending", "adopted", "discarded"],
        "ui": ["pending", "adopted", "discarded"],
    }
    assert status[1]["targets"][0]["status"] == "adopted"
    assert status[1]["targets"][0]["adopted_issue_ref"] == "api#51"
    assert status[2]["targets"][0]["status"] == "discarded"


def test_request_inbox_lists_matched_and_unmatched_replies(
    fake_api,
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    clients, _runner = _setup(
        fake_api,
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
    fake_api,
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    clients, runner = _setup(
        fake_api,
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
    fake_api,
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    clients, _runner = _setup(
        fake_api,
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
    fake_api,
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    clients, _runner = _setup(
        fake_api,
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
    fake_api,
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    clients, _runner = _setup(
        fake_api,
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


@pytest.mark.parametrize("filename", ["code.py", "変更.py"])
def test_router_allows_change_to_already_dirty_worktree_path(
    fake_api,
    monkeypatch, tmp_path, filename
) -> None:
    _clients, _runner = _setup(
        fake_api,
        monkeypatch,
        tmp_path,
        [_route_block({"decision": "reject", "reason": "Not applicable."})],
    )
    changed_path = tmp_path / filename
    changed_path.write_text("value = 1\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path)
    changed_path.write_text("value = 2\n", encoding="utf-8", newline="\n")

    class MutatingRunner(FakeRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, **kwargs):
            changed_path.write_text("value = 3\n", encoding="utf-8", newline="\n")
            return super().run(adapter, prompt, repo, **kwargs)

    config = load_config(tmp_path)
    runner = MutatingRunner([_route_block({"decision": "reject", "reason": "No."})])

    decision = router.run_router(
        config,
        tmp_path,
        request_id=1,
        request_text="Route this.",
        runner_factory=lambda: runner,
    )

    assert decision.decision == "reject"

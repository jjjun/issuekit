import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from issuekit import cli
from issuekit.config import IssuekitConfig
from issuekit.testing import FakeIssuekitClient
from issuekit.workers import registry as worker_registry
from tests.api_helpers import configure_api
from tests.issue_helpers import api_issue


def _configure_project_api(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_api,
    worker_client: FakeIssuekitClient,
    project_issues: dict[str, list[dict[str, object]]],
    project_errors: dict[str, Exception] | None = None,
) -> None:
    issue_clients = {
        project: FakeIssuekitClient(issues)
        for project, issues in project_issues.items()
    }
    for issue_client in issue_clients.values():
        issue_client._workers.update(worker_client._workers)
    if "demo" in issue_clients:
        worker_client._issues.update(issue_clients["demo"]._issues)
        issue_clients["demo"] = worker_client
    errors = project_errors or {}

    def issue_client(
        api_url: str,
        *,
        project: str,
        timeout: float,
        **kwargs,
    ):
        if project in errors:
            raise errors[project]
        if project not in issue_clients:
            client = FakeIssuekitClient()
            client._workers.update(worker_client._workers)
            issue_clients[project] = client
        return issue_clients[project]

    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    fake_api.install_factory(issue_client)
    monkeypatch.chdir(tmp_path)


def test_workers_parser_preserves_json_before_subcommand() -> None:
    parser = cli.build_parser()

    prune_args = parser.parse_args(["workers", "--json", "prune", "--dry-run"])
    remove_args = parser.parse_args(["workers", "--json", "remove", "checkout.mine-py"])

    assert prune_args.json is True
    assert prune_args.dry_run is True
    assert remove_args.json is True


@pytest.mark.parametrize(
    ("argv", "action"),
    [
        (["workers", "--repo-id", "mine-py", "prune"], "prune"),
        (["workers", "--repo-id", "mine-py", "remove", "checkout.mine-py"], "remove"),
    ],
)
def test_workers_rejects_repo_filter_for_unsupported_actions(
    argv: list[str],
    action: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(argv) == 2

    assert f"--repo-id is not supported by workers {action}" in capsys.readouterr().err


def test_workers_command_lists_registered_workers(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient()
    client.upsert_worker(
        machine_id="machine",
        repo_id="mine-py",
        worker_name="checkout",
        path="/repo",
        role="api-server",
        description="Hosts the mine-py issue API.",
    )
    configure_api(tmp_path, monkeypatch, fake_api, client)

    assert cli.main(["workers"]) == 0

    out = capsys.readouterr().out
    assert "checkout.mine-py  role=api-server" in out
    assert "machine=machine" in out
    assert "Hosts the mine-py issue API." in out


def test_workers_command_json_and_repo_filter(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient()
    client.upsert_worker(
        machine_id="machine", repo_id="mine-py", worker_name="c1", path="/a", role="api"
    )
    client.upsert_worker(
        machine_id="machine", repo_id="issuekit", worker_name="c2", path="/b", role="cli"
    )
    configure_api(tmp_path, monkeypatch, fake_api, client)

    assert cli.main(["workers", "--repo-id", "mine-py", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert [row["repo_id"] for row in payload] == ["mine-py"]
    assert client.calls[-1] == {
        "method": "list_workers",
        "body": {"repo_id": "mine-py", "project": None},
    }


def test_workers_command_prints_repo_and_worker_metadata(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient()
    client.upsert_repo(
        repo_key="mine-py",
        description="Mine API service.",
        meta={"domain": "api"},
    )
    client.upsert_worker(
        machine_id="machine",
        repo_id="mine-py",
        worker_name="checkout",
        path="/repo",
        meta={"queue": "fast"},
    )
    configure_api(tmp_path, monkeypatch, fake_api, client)

    assert cli.main(["workers"]) == 0

    out = capsys.readouterr().out
    assert "repo: Mine API service." in out
    assert "repo_metadata: domain=api" in out
    assert "worker_metadata: queue=fast" in out


def test_workers_command_reports_missing_api_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("ISSUEKIT_API_URL", raising=False)
    (tmp_path / "issuekit.toml").write_text(
        "project = 'demo'\n", encoding="utf-8", newline="\n"
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["workers"]) == 1

    assert "requires api_url" in capsys.readouterr().err


def test_workers_command_handles_empty_catalog(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient()
    configure_api(tmp_path, monkeypatch, fake_api, client)

    assert cli.main(["workers"]) == 0

    assert "No workers registered." in capsys.readouterr().out


def test_workers_remove_deletes_by_dotted_key(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient()
    client.upsert_worker(
        machine_id="machine",
        repo_id="mine-py",
        worker_name="checkout",
        path="/repo",
    )
    configure_api(tmp_path, monkeypatch, fake_api, client)

    assert cli.main(["workers", "remove", "checkout.mine-py", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["display"] == "checkout.mine-py"
    assert payload["deleted"] == {"id": "checkout.mine-py", "deleted": True}
    assert [call["method"] for call in client.calls[-2:]] == [
        "list_workers",
        "delete_worker",
    ]


def test_workers_remove_ambiguity_lists_machine_qualified_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workers = [
        {"machine_id": "machine-a", "repo_id": "mine-py", "worker_name": "checkout"},
        {"machine_id": "machine-b", "repo_id": "mine-py", "worker_name": "checkout"},
    ]
    monkeypatch.setattr(worker_registry, "list_api_workers", lambda config: workers)

    with pytest.raises(worker_registry.WorkerRemovalError) as exc_info:
        worker_registry.resolve_api_worker(
            IssuekitConfig(project="mine-py"), "checkout.mine-py"
        )

    assert str(exc_info.value) == (
        "Worker address is ambiguous: checkout.mine-py "
        "(checkout.mine-py@machine-a, checkout.mine-py@machine-b)"
    )


def test_workers_remove_rejects_legacy_id(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient()
    client.upsert_worker(
        machine_id="machine",
        repo_id="mine-py",
        worker_name="checkout",
        path="/repo",
    )
    configure_api(tmp_path, monkeypatch, fake_api, client)

    assert cli.main(["workers", "remove", "machine/mine-py/checkout", "--json"]) == 1

    assert "Worker was not found" in capsys.readouterr().err


def test_workers_remove_refuses_implementing_holder_without_force(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                7,
                "Held",
                status="in_progress",
                stage="implementing",
                worker="checkout.mine-py",
            )
        ]
    )
    client.upsert_worker(
        machine_id="machine",
        repo_id="mine-py",
        worker_name="checkout",
        path="/repo",
    )
    configure_api(tmp_path, monkeypatch, fake_api, client)

    assert cli.main(["workers", "remove", "checkout.mine-py"]) == 1

    assert "holds implementing issue(s) #7" in capsys.readouterr().err
    assert "delete_worker" not in [call["method"] for call in client.calls]


def test_workers_remove_force_deletes_implementing_holder(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                7,
                "Held",
                status="in_progress",
                stage="implementing",
                worker="checkout.mine-py@machine",
            )
        ]
    )
    client.upsert_worker(
        machine_id="machine",
        repo_id="mine-py",
        worker_name="checkout",
        path="/repo",
    )
    configure_api(tmp_path, monkeypatch, fake_api, client)

    assert cli.main(["workers", "remove", "checkout.mine-py", "--force", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["implementing_issues"][0]["id"] == 7
    assert client.calls[-1]["method"] == "delete_worker"


def test_workers_prune_and_remove_check_implementing_claim_in_worker_project(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient()
    client.upsert_worker(
        machine_id="machine",
        repo_id="remote-repo",
        worker_name="checkout",
        project="remote",
        path="/remote",
    )
    client._workers["checkout.remote-repo"]["last_seen"] = "2000-01-01T00:00:00Z"
    _configure_project_api(
        tmp_path,
        monkeypatch,
        fake_api,
        client,
        {
            "demo": [],
            "remote": [
                api_issue(
                    251,
                    "Held remotely",
                    status="in_progress",
                    stage="implementing",
                    worker="checkout.remote-repo",
                )
            ],
        },
    )

    assert cli.main(["workers", "prune", "--dry-run", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["candidates"] == []

    assert cli.main(["workers", "remove", "checkout.remote-repo"]) == 1

    assert "holds implementing issue(s) #251" in capsys.readouterr().err
    assert "delete_worker" not in [call["method"] for call in client.calls]


def test_workers_prune_skips_and_remove_requires_force_when_project_is_unreadable(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient()
    client.upsert_worker(
        machine_id="machine",
        repo_id="remote-repo",
        worker_name="checkout",
        project="remote",
        path="/remote",
    )
    client._workers["checkout.remote-repo"]["last_seen"] = "2000-01-01T00:00:00Z"
    _configure_project_api(
        tmp_path,
        monkeypatch,
        fake_api,
        client,
        {"demo": []},
        {"remote": OSError("backend unavailable")},
    )

    assert cli.main(["workers", "prune", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "Warning: skipped workers from project remote" in out
    assert "backend unavailable" in out

    assert cli.main(["workers", "prune", "--dry-run", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["candidates"] == []
    assert payload["skipped_projects"] == [
        {"project": "remote", "error": "backend unavailable"}
    ]

    assert cli.main(["workers", "remove", "checkout.remote-repo"]) == 1

    assert "Cannot read issues for worker project remote" in capsys.readouterr().err
    assert "delete_worker" not in [call["method"] for call in client.calls]

    assert cli.main(["workers", "remove", "checkout.remote-repo", "--force"]) == 0

    assert "delete_worker" in [call["method"] for call in client.calls]


def test_workers_prune_aborts_when_candidate_ids_change_with_same_count(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient()
    for worker_name in ("first", "second"):
        client.upsert_worker(
            machine_id="machine",
            repo_id="demo",
            worker_name=worker_name,
            project="demo",
            path=f"/{worker_name}",
        )
        client._workers[f"{worker_name}.demo"]["last_seen"] = "2000-01-01T00:00:00Z"
    configure_api(tmp_path, monkeypatch, fake_api, client)
    worker_rows = [
        dict(client._workers["first.demo"]),
        dict(client._workers["second.demo"]),
    ]
    listed_workers = iter(([worker_rows[0]], [worker_rows[1]]))
    monkeypatch.setattr(
        worker_registry,
        "list_api_workers",
        lambda config: next(listed_workers),
    )
    monkeypatch.setattr("builtins.input", lambda prompt: "1")

    assert cli.main(["workers", "prune", "--yes"]) == 1

    assert (
        "candidate count changed or the candidate set changed"
        in capsys.readouterr().err
    )
    assert "delete_worker" not in [call["method"] for call in client.calls]


def test_workers_prune_dry_run_filters_to_stale_issueless_untargeted_workers(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                10,
                "Held",
                status="in_progress",
                stage="implementing",
                worker="held.mine-py",
            ),
            api_issue(
                11,
                "Directed",
                stage="todo",
                target_worker="targeted.mine-py",
            ),
        ]
    )
    for worker in ("stale", "held", "targeted", "fresh"):
        client.upsert_worker(
            machine_id="machine",
            repo_id="mine-py",
            worker_name=worker,
            path=f"/{worker}",
        )
    client._workers["stale.mine-py"]["last_seen"] = "2000-01-01T00:00:00Z"
    client._workers["held.mine-py"]["last_seen"] = "2000-01-01T00:00:00Z"
    client._workers["targeted.mine-py"]["last_seen"] = "2000-01-01T00:00:00Z"
    client._workers["fresh.mine-py"]["last_seen"] = "2999-01-01T00:00:00Z"
    configure_api(tmp_path, monkeypatch, fake_api, client)

    assert cli.main(["workers", "prune", "--dry-run", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert [item["display"] for item in payload["candidates"]] == ["stale.mine-py"]
    assert "delete_worker" not in [call["method"] for call in client.calls]


def test_workers_prune_does_not_match_qualified_claim_from_another_machine(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                10,
                "Held on main1",
                status="in_progress",
                stage="implementing",
                worker="checkout.mine-py@main1",
            )
        ]
    )
    configure_api(tmp_path, monkeypatch, fake_api, client)
    worker_rows = [
        {
            "id": "checkout.mine-py",
            "machine_id": "main1",
            "repo_id": "mine-py",
            "worker_name": "checkout",
            "project": "demo",
            "last_seen": "2999-01-01T00:00:00Z",
        },
        {
            "id": "checkout.mine-py",
            "machine_id": "pike3",
            "repo_id": "mine-py",
            "worker_name": "checkout",
            "project": "demo",
            "last_seen": "2000-01-01T00:00:00Z",
        },
    ]
    monkeypatch.setattr(
        worker_registry,
        "list_api_workers",
        lambda config: worker_rows,
    )

    assert cli.main(["workers", "prune", "--dry-run", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert [
        item["worker"]["machine_id"] for item in payload["candidates"]
    ] == ["pike3"]


def test_workers_prune_json_option_before_subcommand_prints_json(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_api(tmp_path, monkeypatch, fake_api, FakeIssuekitClient())

    assert cli.main(["workers", "--json", "prune", "--dry-run"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["dry_run"] is True
    assert payload["candidates"] == []


def test_workers_prune_warns_when_staleness_is_not_wider_than_heartbeat(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    configure_api(tmp_path, monkeypatch, fake_api, FakeIssuekitClient())

    assert (
        cli.main(
            ["workers", "prune", "--stale-after-sec", "60", "--dry-run"]
        )
        == 0
    )

    assert "healthy worker may appear stale between beats" in capsys.readouterr().err


def test_workers_prune_requires_count_confirmation_before_delete(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient()
    client.upsert_worker(
        machine_id="machine",
        repo_id="mine-py",
        worker_name="stale",
        path="/stale",
    )
    client._workers["stale.mine-py"]["last_seen"] = "2000-01-01T00:00:00Z"
    configure_api(tmp_path, monkeypatch, fake_api, client)
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr("builtins.input", lambda: "1")

    assert cli.main(["workers", "prune", "--json"]) == 0

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert captured.err.startswith("Type 1 to delete 1 stale worker(s): ")
    assert payload["deleted"] == [{"id": "stale.mine-py", "deleted": True}]
    assert client.calls[-1] == {
        "method": "delete_worker",
        "body": {"id": "stale.mine-py"},
    }


def test_workers_prune_noninteractive_requires_yes(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient()
    client.upsert_worker(
        machine_id="machine",
        repo_id="mine-py",
        worker_name="stale",
        path="/stale",
    )
    client._workers["stale.mine-py"]["last_seen"] = "2000-01-01T00:00:00Z"
    configure_api(tmp_path, monkeypatch, fake_api, client)

    assert cli.main(["workers", "prune", "--json"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "requires --yes when stdin is non-interactive" in captured.err
    assert all(call["method"] != "delete_worker" for call in client.calls)


def test_workers_prune_eof_is_not_confirmation(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient()
    client.upsert_worker(
        machine_id="machine",
        repo_id="mine-py",
        worker_name="stale",
        path="/stale",
    )
    client._workers["stale.mine-py"]["last_seen"] = "2000-01-01T00:00:00Z"
    configure_api(tmp_path, monkeypatch, fake_api, client)
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))

    def end_input() -> str:
        raise EOFError

    monkeypatch.setattr("builtins.input", end_input)

    assert cli.main(["workers", "prune", "--json"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Worker prune was not confirmed" in captured.err
    assert all(call["method"] != "delete_worker" for call in client.calls)


def test_workers_prune_yes_allows_noninteractive_deletion(
    fake_api,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient()
    client.upsert_worker(
        machine_id="machine",
        repo_id="mine-py",
        worker_name="stale",
        path="/stale",
    )
    client._workers["stale.mine-py"]["last_seen"] = "2000-01-01T00:00:00Z"
    configure_api(tmp_path, monkeypatch, fake_api, client)

    assert cli.main(["workers", "prune", "--json", "--yes"]) == 0

    captured = capsys.readouterr()
    assert json.loads(captured.out)["deleted"] == [
        {"id": "stale.mine-py", "deleted": True}
    ]
    assert captured.err == ""

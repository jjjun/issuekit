import json
from pathlib import Path

from issuekit import cli
from issuekit.commands import info as info_command
from issuekit import store as store_module
from issuekit.author_guard import create_author_guard
from issuekit.config import load_config
from issuekit.testing import FakeIssuekitClient

from tests.issue_helpers import api_issue


def _configure_api(
    tmp_path: Path,
    monkeypatch,
    client: FakeIssuekitClient,
    *,
    project: str = "demo",
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        f"api_url = 'https://mine.example'\nproject = '{project}'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.setattr(info_command, "api_client", lambda config: client)
    monkeypatch.chdir(tmp_path)


def _issue_client() -> FakeIssuekitClient:
    return FakeIssuekitClient(
        [
            api_issue(1, "First", priority="high"),
            api_issue(2, "Done", status="completed", priority="low", completed="2026-01-02"),
        ]
    )


def test_info_json_shape(tmp_path: Path, monkeypatch) -> None:
    _configure_api(tmp_path, monkeypatch, _issue_client())

    exit_code = cli.main(["info", "--json"])

    assert exit_code == 0


def test_info_json_output(tmp_path: Path, monkeypatch, capsys) -> None:
    _configure_api(tmp_path, monkeypatch, _issue_client())

    cli.main(["info", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["counts"] == {"active": 1, "completed": 1, "total": 2}
    assert "nextIssueId" not in payload
    assert "duplicateIds" not in payload
    assert "indexes" not in payload
    assert payload["activeIssues"][0]["ref"] == "demo#1"
    assert payload["activeIssues"][0]["stage"] is None
    assert payload["incomingProposals"] == []


def test_info_reads_issue_list_once_for_counts(tmp_path: Path, monkeypatch, capsys) -> None:
    class RecordingClient(FakeIssuekitClient):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.list_calls: list[dict[str, object]] = []
            self.count_calls: list[dict[str, object]] = []

        def list_all_issues(self, **kwargs):
            self.list_calls.append(kwargs)
            return super().list_all_issues(**kwargs)

        def count_issues(self, **kwargs):
            self.count_calls.append(kwargs)
            return len(
                super().list_all_issues(
                    status=kwargs.get("status"),
                    include_completed=bool(kwargs.get("include_completed")),
                )
            )

    client = RecordingClient(
        [
            api_issue(1, "First", priority="high"),
            api_issue(2, "Done", status="completed", completed="2026-01-02"),
        ]
    )
    _configure_api(tmp_path, monkeypatch, client)

    cli.main(["info", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["counts"] == {"active": 1, "completed": 1, "total": 2}
    assert client.list_calls == [
        {"status": None, "assignee": None, "stage": None, "include_completed": False}
    ]
    assert client.count_calls == [{"status": "completed", "include_completed": True}]


def test_info_json_lists_incoming_proposals(tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(1, "First", priority="high"),
            api_issue(2, "Done", status="completed", completed="2026-01-02"),
        ],
        proposals=[
            {
                "id": 9,
                "origin": "mine-js-monorepo#0@f8b6c5b3",
                "created": "2026-06-03",
                "title": "Show Pending Proposal",
                "body": "Body",
            }
        ],
    )
    _configure_api(tmp_path, monkeypatch, client, project="issuekit")

    cli.main(["info", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["counts"] == {"active": 1, "completed": 1, "total": 2}
    assert payload["incomingProposals"] == [
        {
            "origin": "mine-js-monorepo#0@f8b6c5b3",
            "title": "Show Pending Proposal",
            "created": "2026-06-03",
            "id": 9,
        }
    ]


def test_info_text_lists_incoming_proposals(tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {
                "id": 9,
                "origin": "mine-js-monorepo#0@f8b6c5b3",
                "created": "2026-06-03",
                "title": "Show Pending Proposal",
                "body": "Body",
            }
        ]
    )
    _configure_api(tmp_path, monkeypatch, client, project="issuekit")

    exit_code = cli.main(["info"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Incoming proposals: 1" in captured.out
    assert "Incoming proposals\n- #9 mine-js-monorepo#0@f8b6c5b3: Show Pending Proposal" in captured.out


def test_info_ignores_triaged_incoming_proposals(tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient(
        proposals=[
            {
                "id": 9,
                "origin": "mine-js-monorepo#0@f8b6c5b3",
                "created": "2026-06-03",
                "title": "Show Pending Proposal",
                "body": "Body",
                "status": "adopted",
            }
        ]
    )
    _configure_api(tmp_path, monkeypatch, client, project="issuekit")

    cli.main(["info", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["incomingProposals"] == []


def test_info_text_omits_retired_index_status(tmp_path: Path, monkeypatch, capsys) -> None:
    _configure_api(tmp_path, monkeypatch, _issue_client())

    exit_code = cli.main(["info"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Indexes:" not in captured.out
    assert "Next issue id:" not in captured.out
    assert "Incoming proposals: 0" in captured.out
    assert "\nIncoming proposals\n" not in captured.out


def test_info_json_includes_stage_when_present(tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(1, "First", priority="high"),
            api_issue(3, "Review", status="in_progress", stage="review"),
            api_issue(2, "Done", status="completed", completed="2026-01-02"),
        ]
    )
    _configure_api(tmp_path, monkeypatch, client)

    cli.main(["info", "--json"])
    payload = json.loads(capsys.readouterr().out)

    review_issue = next(i for i in payload["activeIssues"] if i["id"] == 3)
    assert review_issue["status"] == "in_progress"
    assert review_issue["stage"] == "review"


def test_info_json_includes_worker_when_present(tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                3,
                "Held",
                status="in_progress",
                stage="implementing",
                worker="checkout.demo",
            )
        ]
    )
    _configure_api(tmp_path, monkeypatch, client)

    cli.main(["info", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert payload["activeIssues"][0]["worker"] == "checkout.demo"


def test_info_json_includes_dependency_fields(tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Dependent",
                depends_on=["mine-py#42"],
                dependency_state="waiting",
                dependencies=[
                    {
                        "ref": "mine-py#42",
                        "state": "waiting",
                        "status": "in_progress",
                        "stage": "review",
                    }
                ],
            )
        ]
    )
    _configure_api(tmp_path, monkeypatch, client)

    cli.main(["info", "--json"])
    payload = json.loads(capsys.readouterr().out)

    issue = payload["activeIssues"][0]
    assert issue["depends_on"] == ["mine-py#42"]
    assert issue["dependency_state"] == "waiting"
    assert issue["dependencies"][0]["status"] == "in_progress"


def test_info_text_renders_stage_when_present(tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient([api_issue(3, "Review", status="in_progress", stage="review")])
    _configure_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(["info"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "[in_progress, stage=review]" in captured.out


def test_info_text_renders_dependency_details(tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Dependent",
                depends_on=["mine-py#42"],
                dependency_state="waiting",
                dependencies=[
                    {
                        "ref": "mine-py#42",
                        "state": "waiting",
                        "status": "in_progress",
                        "stage": "review",
                    }
                ],
            )
        ]
    )
    _configure_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(["info"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "dependency_state=waiting" in captured.out
    assert "depends_on=mine-py#42 state=waiting status=in_progress stage=review" in captured.out


def test_info_text_renders_status_only_when_no_stage(tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", stage="")])
    _configure_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(["info"])
    captured = capsys.readouterr()

    assert exit_code == 0
    assert "[active]" in captured.out
    assert "stage=" not in captured.out


def test_info_surfaces_author_guard(tmp_path: Path, monkeypatch, capsys) -> None:
    _configure_api(tmp_path, monkeypatch, _issue_client())
    create_author_guard(
        tmp_path,
        config=load_config(tmp_path),
        kind="issue",
        item_id=7,
        ref="demo#7",
        author_agent="codex",
    )

    cli.main(["info"])
    text = capsys.readouterr().out

    assert "Author guard: STOP_NOW issue demo#7" in text

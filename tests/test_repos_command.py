import json
from pathlib import Path

import pytest

from issuekit import cli
from issuekit import worker_registry
from issuekit.testing import FakeIssuekitClient
from issuekit.workflow import WorkflowError


def _configure_api(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(worker_registry, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)


def test_repos_remove_deletes_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeIssuekitClient()
    client.upsert_repo(repo_key="mine-py")
    _configure_api(tmp_path, monkeypatch, client)

    assert cli.main(["repos", "remove", "mine-py", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "repo_key": "mine-py",
        "deleted": {"repo_key": "mine-py", "deleted": True},
    }
    assert client.calls[-1] == {
        "method": "delete_repo",
        "body": {"repo_key": "mine-py"},
    }


def test_repos_remove_reports_reference_counts_on_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class ConflictClient(FakeIssuekitClient):
        def delete_repo(self, repo_key: str):
            raise WorkflowError(
                "repo is referenced",
                code="http_409",
                details={"reference_counts": {"workers": 2, "issues": 1}},
            )

    _configure_api(tmp_path, monkeypatch, ConflictClient())

    assert cli.main(["repos", "remove", "mine-py"]) == 1

    err = capsys.readouterr().err
    assert "Repo mine-py cannot be removed" in err
    assert "issues=1" in err
    assert "workers=2" in err


def test_repos_remove_reports_nested_reference_counts_on_repo_referenced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    class ConflictClient(FakeIssuekitClient):
        def delete_repo(self, repo_key: str):
            raise WorkflowError(
                "repo is referenced",
                code="repo_referenced",
                details={
                    "code": "repo_referenced",
                    "message": "repo is referenced",
                    "details": {
                        "issue_project_count": 190,
                        "proposal_target_project_count": 78,
                        "worker_repo_key_count": 2,
                    },
                },
            )

    _configure_api(tmp_path, monkeypatch, ConflictClient())

    assert cli.main(["repos", "remove", "issuekit"]) == 1

    err = capsys.readouterr().err
    assert "Repo issuekit cannot be removed because it is still referenced" in err
    assert "issue_project_count=190" in err
    assert "proposal_target_project_count=78" in err
    assert "worker_repo_key_count=2" in err

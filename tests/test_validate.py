from pathlib import Path

from issuekit import cli
from issuekit import store as store_module
from issuekit.testing import FakeIssuekitClient
from issuekit.workflow import WorkflowError

from tests.issue_helpers import api_issue


def test_validate_requires_api_url_by_default(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["validate"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "API validation failed" in captured.err
    assert "API store requires api_url" in captured.err


def test_validate_api_mode_checks_connectivity_and_shape(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First")])
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["validate"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "API validation passed (1 issues)." in captured.out


def test_validate_api_mode_fails_on_malformed_issue_response(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    class MalformedClient:
        def health(self):
            return {"status": "ok", "migration_revision": "test"}

        def list_all_issues(self, **kwargs):
            return [{"id": 1, "status": "active"}]

    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: MalformedClient())
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["validate"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "API validation failed" in captured.err
    assert "missing required field" in captured.err


def test_validate_api_mode_fails_when_health_revision_is_missing(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    class MissingRevisionClient:
        def health(self):
            return {"status": "ok"}

        def list_all_issues(self, **kwargs):
            return [api_issue(1, "First")]

    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: MissingRevisionClient())
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["validate"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "API validation failed" in captured.err
    assert "Health response did not include migration_revision" in captured.err


def test_validate_api_mode_reports_health_request_errors(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    class HealthErrorClient:
        def health(self):
            raise WorkflowError("health endpoint was unavailable", code="http_404")

        def list_all_issues(self, **kwargs):
            return [api_issue(1, "First")]

    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: HealthErrorClient())
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["validate"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "API validation failed" in captured.err
    assert "health endpoint was unavailable" in captured.err

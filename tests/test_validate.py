from pathlib import Path

import pytest

from issuekit import cli
from issuekit import store as store_module
from issuekit.commands import validate
from issuekit.testing import FakeIssuekitClient
from issuekit.workflow import WorkflowError

from tests.issue_helpers import api_issue


class CloseTrackingClient(FakeIssuekitClient):
    def __init__(self, issues=None) -> None:
        super().__init__(issues)
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


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
    client = CloseTrackingClient([api_issue(1, "First")])
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
    assert client.close_count == 1


def test_validate_api_mode_fails_on_malformed_issue_response(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    class MalformedClient(CloseTrackingClient):
        def list_all_issues(self, **kwargs):
            return [{"id": 1, "status": "active"}]

    client = MalformedClient()
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["validate"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "API validation failed" in captured.err
    assert "missing required field" in captured.err
    assert client.close_count == 1


def test_validate_api_mode_fails_when_health_revision_is_missing(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    class MissingRevisionClient(CloseTrackingClient):
        def health(self):
            return {"status": "ok"}

    client = MissingRevisionClient([api_issue(1, "First")])
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["validate"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "API validation failed" in captured.err
    assert "Health response did not include migration_revision" in captured.err
    assert client.close_count == 1


def test_validate_health_missing_revision_uses_schema_drift_code() -> None:
    class MissingRevisionClient:
        def health(self):
            return {"status": "ok"}

    class Store:
        client = MissingRevisionClient()

    with pytest.raises(WorkflowError) as excinfo:
        validate._validate_health(Store())

    assert excinfo.value.code == "server_schema_drift"


def test_validate_health_non_object_payload_uses_invalid_response_code() -> None:
    class NonObjectClient:
        def health(self):
            return ["ok"]

    class Store:
        client = NonObjectClient()

    with pytest.raises(WorkflowError) as excinfo:
        validate._validate_health(Store())

    assert excinfo.value.code == "invalid_response"


def test_validate_api_mode_reports_health_request_errors(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    class HealthErrorClient(CloseTrackingClient):
        def health(self):
            raise WorkflowError("health endpoint was unavailable", code="http_404")

    client = HealthErrorClient([api_issue(1, "First")])
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["validate"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "API validation failed" in captured.err
    assert "health endpoint was unavailable" in captured.err
    assert client.close_count == 1

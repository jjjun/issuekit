from pathlib import Path

from issuekit import cli
from issuekit import store as store_module
from issuekit.author_guard import read_author_guard
from issuekit.testing import FakeIssuekitClient


def _configure_api(tmp_path: Path, monkeypatch, client: FakeIssuekitClient) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)


def _configure_api_project(
    repo: Path,
    monkeypatch,
    client: FakeIssuekitClient,
    *,
    project: str,
) -> None:
    (repo / "issuekit.toml").write_text(
        f"api_url = 'https://mine.example'\nproject = '{project}'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(repo)


def test_author_command_creates_issue_via_api(tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient()
    body_file = tmp_path / "plan.md"
    body_file.write_text(
        "## Problem\n\nSomething is missing.\n\n## Test Plan\n\n- uv run pytest\n",
        encoding="utf-8",
        newline="\n",
    )
    _configure_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(
        [
            "author",
            "--title",
            "Add Author Command",
            "--body-file",
            str(body_file),
            "--priority",
            "high",
            "--agent",
            "codex",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "API validation passed" not in captured.out
    assert "Authored issue: demo#1" in captured.out
    assert "STOP_NOW" in captured.out
    guard = read_author_guard(tmp_path)
    assert guard is not None
    assert guard.kind == "issue"
    assert guard.id == "1"
    assert guard.project == "demo"
    assert guard.author_agent == "codex"
    assert client.calls[0] == {
        "method": "create_issue",
        "body": {
            "title": "Add Author Command",
            "body": "## Problem\n\nSomething is missing.\n\n## Test Plan\n\n- uv run pytest",
            "priority": "high",
            "author": "codex",
        },
    }


def test_author_command_can_assign_explicit_implementer(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    _configure_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(
        [
            "author",
            "--title",
            "Assigned Handoff",
            "--body",
            "## Problem\n\nAssign this.\n\n## Test Plan\n\n- issuekit validate",
            "--agent",
            "claude",
            "--assign",
            "kimi",
        ]
    )

    assert exit_code == 0
    capsys.readouterr()
    assert client.get_issue(1)["author"] == "claude"
    assert client.get_issue(1)["assignee"] == "kimi"


def test_author_command_blocks_likely_cross_project_direct_authoring(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (tmp_path / "issuekit.workspace.toml").write_text(
        "[projects]\nsource = \"source\"\ntarget = \"target\"\n",
        encoding="utf-8",
        newline="\n",
    )
    client = FakeIssuekitClient()
    _configure_api_project(target, monkeypatch, client, project="target")

    exit_code = cli.main(
        [
            "author",
            "--title",
            "Fix source handoff",
            "--body",
            "## Problem\n\nsource needs behavior that belongs here.\n",
            "--agent",
            "codex",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Cross-project author preflight stopped direct issue creation" in captured.err
    assert "issuekit propose --to target" in captured.err
    assert "--direct-local-author" in captured.err
    assert client.calls == []
    assert read_author_guard(target) is None


def test_author_command_direct_local_author_overrides_cross_project_preflight(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (tmp_path / "issuekit.workspace.toml").write_text(
        "[projects]\nsource = \"source\"\ntarget = \"target\"\n",
        encoding="utf-8",
        newline="\n",
    )
    client = FakeIssuekitClient()
    _configure_api_project(target, monkeypatch, client, project="target")

    exit_code = cli.main(
        [
            "author",
            "--title",
            "Fix source handoff",
            "--body",
            "## Problem\n\nsource compatibility is a target-local issue.\n",
            "--agent",
            "codex",
            "--direct-local-author",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Authored issue: target#1" in captured.out
    assert client.get_issue(1)["title"] == "Fix source handoff"


def test_author_command_origin_project_context_requires_proposal(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    _configure_api_project(tmp_path, monkeypatch, client, project="target")

    exit_code = cli.main(
        [
            "author",
            "--title",
            "Origin supplied",
            "--body",
            "## Problem\n\nThe body does not name the origin.\n",
            "--agent",
            "codex",
            "--origin-project",
            "source",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "--origin-project points at source" in captured.err
    assert "issuekit propose --to target" in captured.err
    assert client.calls == []

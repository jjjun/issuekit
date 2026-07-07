import json
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


def test_author_command_requires_local_project_context(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ISSUEKIT_API_URL", "https://mine.example")
    monkeypatch.setenv("ISSUEKIT_PROJECT", "issuekit")

    exit_code = cli.main(
        [
            "author",
            "--title",
            "Misfiled issue",
            "--body",
            "## Problem\n\nThis came from a scratch directory.\n",
            "--agent",
            "codex",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "needs a local issuekit project context" in captured.err
    assert "--project <project>" in captured.err


def test_author_command_project_override_allows_scratch_cwd(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ISSUEKIT_API_URL", "https://mine.example")
    monkeypatch.setenv("ISSUEKIT_PROJECT", "wrong-project")
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)

    exit_code = cli.main(
        [
            "author",
            "--project",
            "demo",
            "--title",
            "Explicit project",
            "--body",
            "## Problem\n\nThe target project was provided explicitly.\n",
            "--agent",
            "codex",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Authored issue: demo#1" in captured.out
    guard = read_author_guard(tmp_path)
    assert guard is not None
    assert guard.project == "demo"
    assert client.calls[0]["body"]["title"] == "Explicit project"


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


def test_author_command_attaches_dependency_refs_and_prints_json(
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
            "Dependent Handoff",
            "--body",
            "## Problem\n\nWait for the API.",
            "--agent",
            "claude",
            "--depends-on",
            "mine-py#42",
            "--json",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["depends_on"] == ["mine-py#42"]
    assert output["stop"] == "STOP_NOW"
    assert client.calls[0]["body"]["depends_on"] == ["mine-py#42"]


def test_author_command_accepts_explicit_dependency_refs(
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
            "Explicit Dependencies",
            "--body",
            "## Problem\n\nWait for both upstream refs.",
            "--agent",
            "claude",
            "--depends-on",
            "mine-py#42 mine-py#issue:43",
            "--depends-on",
            "mine-py#proposal:44",
            "--json",
        ]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["depends_on"] == [
        "mine-py#42",
        "mine-py#issue:43",
        "mine-py#proposal:44",
    ]
    assert client.calls[0]["body"]["depends_on"] == output["depends_on"]


def test_author_command_rejects_malformed_dependency_prefix(
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
            "Bad Dependency",
            "--body",
            "## Problem\n\nBad ref.",
            "--agent",
            "claude",
            "--depends-on",
            "mine-py#foo:42",
        ]
    )

    assert exit_code == 1
    err = capsys.readouterr().err
    assert "Invalid dependency reference: mine-py#foo:42" in err
    assert "project#N, project#issue:N, or project#proposal:N" in err
    assert client.calls == []


def test_author_command_warns_for_bare_ref_collision(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    class CollisionClient(FakeIssuekitClient):
        def create_issue(self, issue, *, session=None):
            created = super().create_issue(issue, session=session)
            created["dependencies"] = [
                {
                    "ref": "mine-py#42",
                    "state": "attention",
                    "issue_status": "completed",
                    "proposal": {"id": 42, "status": "pending"},
                }
            ]
            return created

    client = CollisionClient()
    _configure_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(
        [
            "author",
            "--title",
            "Ambiguous Dependency",
            "--body",
            "## Problem\n\nBad ref.",
            "--agent",
            "claude",
            "--depends-on",
            "mine-py#42",
        ]
    )

    assert exit_code == 0
    assert "Dependency reference mine-py#42 is ambiguous" in capsys.readouterr().err


def test_author_command_records_configured_session(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient()
    monkeypatch.setenv("ISSUEKIT_SESSION", "author-123")
    _configure_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(
        [
            "author",
            "--title",
            "Session Handoff",
            "--body",
            "## Problem\n\nRecord the author session.\n",
            "--agent",
            "codex",
        ]
    )

    assert exit_code == 0
    capsys.readouterr()
    guard = read_author_guard(tmp_path)
    assert guard is not None
    assert guard.author_session == "author-123"
    assert client.calls[0]["body"]["session"] == "author-123"
    assert client.get_issue(1)["author_session"] == "author-123"


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


def test_author_command_allows_local_when_worker_repo_id_differs_from_project(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    # Regression for #163: a checkout whose worker repo_id ("py-mine") differs
    # from its configured project key ("mine-py") must still author a local issue
    # without tripping the cross-project preflight. repo_id is a worker identity,
    # not the current project.
    client = FakeIssuekitClient()
    _configure_api_project(tmp_path, monkeypatch, client, project="mine-py")
    (tmp_path / "issuekit.local.toml").write_text(
        "[worker]\n"
        'machine_id = "machine"\n'
        'repo_id = "py-mine"\n'
        'worker_id = "checkout"\n',
        encoding="utf-8",
        newline="\n",
    )

    exit_code = cli.main(
        [
            "author",
            "--title",
            "Local fix",
            "--body",
            "## Problem\n\nA purely local change with no other project references.\n",
            "--agent",
            "codex",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Authored issue: mine-py#1" in captured.out
    assert client.get_issue(1)["title"] == "Local fix"


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

import argparse

import pytest

from issuekit import cli


EXPECTED_COMMANDS = {
    "info",
    "login",
    "logout",
    "author",
    "implement",
    "validate",
    "migrate-to-api",
    "complete",
    "approve",
    "claim",
    "submit-review",
    "request-changes",
    "queue",
    "runs",
    "check-encoding",
    "protocol",
    "init",
    "setup",
    "add-ref",
    "list-refs",
    "propose",
    "incoming",
    "adopt",
    "discard",
}


def _subparser_action(parser: argparse.ArgumentParser) -> argparse._SubParsersAction:
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise AssertionError("parser has no subparser action")


def test_parser_registers_all_subcommands() -> None:
    parser = cli.build_parser()

    subparsers = _subparser_action(parser)

    assert set(subparsers.choices) == EXPECTED_COMMANDS


def test_help_lists_all_subcommands(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["--help"])

    captured = capsys.readouterr()

    assert exit_code == 0
    for command in EXPECTED_COMMANDS:
        assert command in captured.out


def test_subcommand_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["complete", "--help"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "id" in captured.out
    assert "--summary" in captured.out
    assert "--verification" in captured.out


def test_approve_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["approve", "--help"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "id" in captured.out
    assert "--verification" in captured.out
    assert "--reviewer" in captured.out


def test_complete_requires_id(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["complete"])

    captured = capsys.readouterr()

    assert exit_code != 0
    assert "id" in captured.err


@pytest.mark.parametrize("command", sorted(EXPECTED_COMMANDS))
def test_handlers_are_stubs(command: str) -> None:
    if command in {
        "check-encoding",
        "approve",
        "claim",
        "complete",
        "migrate-to-api",
        "implement",
        "info",
        "login",
        "logout",
        "init",
        "queue",
        "protocol",
        "request-changes",
        "runs",
        "setup",
        "submit-review",
        "validate",
        "add-ref",
        "author",
        "list-refs",
        "propose",
        "incoming",
        "adopt",
        "discard",
    }:
        pytest.skip(f"{command} is implemented")

    argv = [command]
    if command == "complete":
        argv.append("1")

    with pytest.raises(NotImplementedError, match=command):
        cli.main(argv)


def test_proposal_cli_round_trip(tmp_path, monkeypatch, capsys) -> None:
    from tests.issue_helpers import make_issue_tree

    source = tmp_path / "source"
    target = tmp_path / "target"
    make_issue_tree(source)
    make_issue_tree(target)
    body_file = source / "proposal.md"
    body_file.write_text("## Suggested Change\n\nDo the thing.\n", encoding="utf-8", newline="\n")

    monkeypatch.chdir(source)
    assert cli.main(["add-ref", "target", "--path", str(target)]) == 0
    assert cli.main(
        [
            "propose",
            "--to",
            "target",
            "--from-issue",
            "1",
            "--title",
            "Suggest Thing",
            "--body-file",
            str(body_file),
        ]
    ) == 0

    proposal_files = list((target / "docs" / "issues" / "incoming").glob("*.md"))
    assert len(proposal_files) == 1

    monkeypatch.chdir(target)
    assert cli.main(["incoming", "--json"]) == 0
    assert "Suggest Thing" in capsys.readouterr().out
    assert cli.main(["adopt", proposal_files[0].name, "--priority", "high"]) == 0

    active_files = list((target / "docs" / "issues" / "active").glob("003_*.md"))
    assert len(active_files) == 1
    content = active_files[0].read_text(encoding="utf-8")
    assert "origin: source#1@" in content
    assert "Origin: `source#1@" in content
    assert not proposal_files[0].exists()


def test_list_refs_shows_effective_source_and_self(tmp_path, monkeypatch, capsys) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (target / "issuekit.toml").write_text(
        "use_filesystem_store = true\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "issuekit.workspace.toml").write_text(
        "[projects]\nsource = \"source\"\ntarget = \"target\"\n",
        encoding="utf-8",
        newline="\n",
    )

    monkeypatch.chdir(source)
    assert cli.main(["list-refs"]) == 0

    lines = capsys.readouterr().out.splitlines()
    assert f"source\tself\t{source.resolve().as_posix()}" in lines
    assert f"target\tworkspace\t{target.resolve().as_posix()}" in lines


def test_add_ref_scope_workspace_writes_shared_registry(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    workspace_file = tmp_path / "issuekit.workspace.toml"
    workspace_file.write_text("[projects]\n", encoding="utf-8", newline="\n")

    monkeypatch.chdir(source)
    assert cli.main(
        ["add-ref", "target", "--path", str(target), "--scope", "workspace"]
    ) == 0

    assert "Added workspace ref target: target" in capsys.readouterr().out
    assert 'target = "target"' in workspace_file.read_text(encoding="utf-8")


def test_add_ref_scope_workspace_fails_without_workspace(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()

    monkeypatch.chdir(source)
    assert cli.main(
        ["add-ref", "target", "--path", str(target), "--scope", "workspace"]
    ) == 1

    assert "No issuekit.workspace.toml found" in capsys.readouterr().err


def test_login_command_uses_credentials_and_ignores_env_token(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    from issuekit.commands import auth

    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ISSUEKIT_API_PASSWORD", "secret")
    monkeypatch.setenv("ISSUEKIT_API_TOKEN", "external")
    created = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            created.append((args, kwargs))
            self.token_expiry = 1780000000.0

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return None

        def login(self, *, force=False):
            assert force is True
            return "token"

    monkeypatch.setattr(auth, "IssuekitClient", FakeClient)

    assert cli.main(["login", "--user", "svc"]) == 0

    assert created[0][0] == ("https://mine.example",)
    assert created[0][1]["username"] == "svc"
    assert created[0][1]["password"] == "secret"
    assert created[0][1]["use_env_token"] is False
    assert "Logged in to https://mine.example as svc" in capsys.readouterr().out


def test_logout_command_ignores_env_token(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    from issuekit.commands import auth

    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ISSUEKIT_API_TOKEN", "external")
    created = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            created.append((args, kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return None

        def logout(self):
            return None

    monkeypatch.setattr(auth, "IssuekitClient", FakeClient)

    assert cli.main(["logout"]) == 0

    assert created[0][0] == ("https://mine.example",)
    assert created[0][1]["use_env_token"] is False
    assert "Logged out of https://mine.example." in capsys.readouterr().out


def test_workspace_refs_drive_propose_and_reply_round_trip(
    tmp_path,
    monkeypatch,
) -> None:
    from issuekit.proposals import list_incoming

    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (target / "issuekit.toml").write_text(
        "use_filesystem_store = true\n",
        encoding="utf-8",
        newline="\n",
    )
    body_file = source / "proposal.md"
    reply_file = target / "reply.md"
    body_file.write_text("## Suggested Change\n\nDo the thing.\n", encoding="utf-8", newline="\n")
    reply_file.write_text("## Suggested Change\n\nImplemented.\n", encoding="utf-8", newline="\n")
    (tmp_path / "issuekit.workspace.toml").write_text(
        "[projects]\nsource = \"source\"\ntarget = \"target\"\n",
        encoding="utf-8",
        newline="\n",
    )

    monkeypatch.chdir(source)
    assert cli.main(
        [
            "propose",
            "--to",
            "target",
            "--title",
            "Workspace Proposal",
            "--body-file",
            str(body_file),
        ]
    ) == 0

    proposal_files = list((target / "docs" / "issues" / "incoming").glob("*.md"))
    assert len(proposal_files) == 1

    monkeypatch.chdir(target)
    assert cli.main(["adopt", proposal_files[0].name]) == 0
    assert cli.main(["claim", "--assignee", "codex"]) == 0
    assert cli.main(["complete", "1", "--force", "--summary", "Implemented.", "--verification", "pytest"]) == 0
    assert cli.main(
        [
            "propose",
            "--reply",
            "1",
            "--title",
            "Implemented Reply",
            "--body-file",
            str(reply_file),
        ]
    ) == 0

    replies = list_incoming(source / "docs" / "issues")

    assert len(replies) == 1
    assert replies[0].to == "source"
    assert replies[0].reply_to.startswith("source#0@")
    assert replies[0].origin.startswith("target#1@")

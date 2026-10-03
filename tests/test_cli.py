import argparse
import json
import os
import signal

import pytest

from issuekit import cli
from issuekit.commands._common import run_agent_command
from issuekit.config import IssuekitConfig
from issuekit.guards.author import create_author_guard
from issuekit.proposals.model import ProposalError
from issuekit.workflow import WorkflowError

EXPECTED_COMMANDS = {
    "info",
    "show",
    "next-review",
    "add",
    "register",
    "login",
    "logout",
    "author-guard",
    "author",
    "edit",
    "implement",
    "negotiate",
    "proposal-check-request",
    "proposal-checks",
    "threads",
    "validate",
    "complete",
    "approve",
    "review",
    "claim",
    "claims",
    "submit-review",
    "request-changes",
    "queue",
    "orphans",
    "dispatch",
    "readdress",
    "reclaim",
    "repos",
    "workers",
    "runs",
    "serve",
    "check-encoding",
    "protocol",
    "init",
    "setup",
    "dev-tool",
    "add-ref",
    "list-refs",
    "propose",
    "incoming",
    "outgoing",
    "adopt",
    "discard",
    "request",
    "triage",
    "profile",
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
    assert "--force" in captured.out
    assert "Directly complete an active issue" in captured.out


@pytest.mark.skipif(os.name == "nt", reason="SIGTERM interruption is POSIX-only")
def test_agent_command_translates_sigterm_and_restores_handler() -> None:
    previous_handler = signal.getsignal(signal.SIGTERM)

    with pytest.raises(KeyboardInterrupt):
        run_agent_command(lambda: signal.raise_signal(signal.SIGTERM))

    assert signal.getsignal(signal.SIGTERM) is previous_handler


def test_all_registered_subcommand_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    parser = cli.build_parser()
    subparsers = _subparser_action(parser)

    for command in sorted(subparsers.choices):
        assert cli.main([command, "--help"]) == 0
        captured = capsys.readouterr()
        assert "usage: issuekit" in captured.out


def test_approve_help_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["approve", "--help"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "id" in captured.out
    assert "--verification" in captured.out
    assert "--reviewer" in captured.out
    assert "--force" not in captured.out
    assert "Bypass the review-stage requirement." not in captured.out


def test_complete_requires_id(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["complete"])

    captured = capsys.readouterr()

    assert exit_code != 0
    assert "id" in captured.err


def test_author_guard_bare_command_shows_guard(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["author-guard"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "No author-session guard." in captured.out
    assert captured.err == ""


@pytest.mark.parametrize(
    "error",
    [
        ValueError("invalid configuration"),
        WorkflowError("API is unavailable"),
        ProposalError("proposal API failed"),
    ],
)
def test_cli_formats_expected_uncaught_errors(
    error: Exception,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(_args) -> int:
        raise error

    monkeypatch.setattr(cli.info, "run", fail)

    assert cli.main(["info"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == f"error: {error}\n"


def test_cli_formats_invalid_config_without_traceback(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "worker_heartbeat_interval_sec = 0\n", encoding="utf-8", newline="\n"
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(["protocol"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == (
        "error: worker_heartbeat_interval_sec must be greater than zero.\n"
    )


@pytest.mark.parametrize(
    "argv",
    [
        ["info"],
        ["check-encoding"],
        ["triage", "--once"],
        ["profile"],
        ["validate"],
        ["init"],
        ["protocol"],
        ["incoming"],
        ["outgoing", "--to", "target"],
        ["adopt", "1"],
        ["discard", "1"],
    ],
)
def test_diagnostic_commands_format_invalid_config_without_tracebacks(
    argv: list[str],
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "worker_heartbeat_interval_sec = 0\n", encoding="utf-8", newline="\n"
    )
    monkeypatch.chdir(tmp_path)

    assert cli.main(argv) == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out + captured.err
    assert captured.err.startswith("error: worker_heartbeat_interval_sec")


def test_author_guard_show_check_and_clear_cover_all_guards(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    config = IssuekitConfig(project="demo")
    create_author_guard(
        tmp_path,
        config=config,
        kind="issue",
        item_id=1,
        ref="demo#1",
        author_agent="codex",
    )
    create_author_guard(
        tmp_path,
        config=config,
        kind="proposal",
        item_id=2,
        ref="other#2",
        target_project="other",
        author_agent="codex",
    )

    assert cli.main(["author-guard", "show", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert [guard["ref"] for guard in shown["authorGuards"]] == ["demo#1", "other#2"]

    assert cli.main(["author-guard", "clear", "--ref", "demo#1"]) == 0
    capsys.readouterr()
    assert cli.main(["author-guard", "check", "--json"]) == 0
    checked = json.loads(capsys.readouterr().out)
    assert checked["ok"] is True
    assert checked["blocking"] is False
    assert [guard["kind"] for guard in checked["authorGuards"]] == ["proposal"]

    assert cli.main(["author-guard", "clear"]) == 0
    capsys.readouterr()
    assert cli.main(["author-guard", "show", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["authorGuards"] == []


def test_author_guard_help_lists_separation_guards(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["author-guard", "--help"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert "Separation-of-duties guard reference" in captured.out
    assert "Author-session STOP guard" in captured.out
    assert "Server author-implementer guard" in captured.out
    assert "Distinct-reviewer guard" in captured.out
    assert "Work-branch guard" in captured.out
    assert "issuekit#162 and issuekit#163" not in captured.out


@pytest.mark.parametrize("command", sorted(EXPECTED_COMMANDS))
def test_handlers_are_stubs(command: str) -> None:
    if command in {
        "check-encoding",
        "add",
        "register",
        "approve",
        "review",
        "claim",
        "claims",
        "complete",
        "negotiate",
        "proposal-check-request",
        "proposal-checks",
        "threads",
        "implement",
        "info",
        "show",
        "next-review",
        "login",
        "logout",
        "init",
        "queue",
        "orphans",
        "dispatch",
        "readdress",
        "reclaim",
        "repos",
        "workers",
        "protocol",
        "request-changes",
        "runs",
        "serve",
        "setup",
        "dev-tool",
        "submit-review",
        "validate",
        "add-ref",
        "author",
        "author-guard",
        "edit",
        "list-refs",
        "propose",
        "incoming",
        "outgoing",
        "adopt",
        "discard",
        "request",
        "triage",
        "profile",
    }:
        pytest.skip(f"{command} is implemented")

    argv = [command]
    if command == "complete":
        argv.append("1")

    with pytest.raises(NotImplementedError, match=command):
        cli.main(argv)


def test_proposal_cli_round_trip(tmp_path, monkeypatch, capsys) -> None:
    import issuekit.proposals.api as proposals_api
    from issuekit.testing import FakeIssuekitClient

    client = FakeIssuekitClient(
        proposals=[{"id": 1, "origin": "source#1@abc123", "title": "Suggest Thing", "body": "Body"}]
    )
    client.register_catalog_project("target")
    body_file = tmp_path / "proposal.md"
    body_file.write_text("## Suggested Change\n\nDo the thing.\n", encoding="utf-8", newline="\n")
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'source'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)

    monkeypatch.chdir(tmp_path)
    assert cli.main(
        [
            "propose",
            "--to",
            "target",
            "--title",
            "Suggest Thing",
            "--body-file",
            str(body_file),
        ]
    ) == 0
    assert cli.main(["incoming", "--json"]) == 0
    assert "Suggest Thing" in capsys.readouterr().out
    assert cli.main(["adopt", "1", "--priority", "high"]) == 0
    assert client.get_proposal(1)["status"] == "adopted"


def test_list_refs_shows_effective_source_and_self(tmp_path, monkeypatch, capsys) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    source.mkdir()
    target.mkdir()
    (target / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\n",
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


def test_login_command_prompts_for_username_on_tty(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    from issuekit.commands import auth

    class TtyStdin:
        def isatty(self):
            return True

    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ISSUEKIT_API_USER", raising=False)
    monkeypatch.delenv("ISSUEKIT_API_PASSWORD", raising=False)
    monkeypatch.setattr(auth.sys, "stdin", TtyStdin())
    monkeypatch.setattr("builtins.input", lambda prompt: "  prompted-user  ")
    monkeypatch.setattr(auth.getpass, "getpass", lambda prompt: "secret")
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

    assert cli.main(["login"]) == 0

    assert created[0][1]["username"] == "prompted-user"
    assert created[0][1]["password"] == "secret"
    assert "Logged in to https://mine.example as prompted-user" in capsys.readouterr().out


def test_login_command_non_tty_missing_username_does_not_prompt(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    from issuekit.commands import auth

    class NonTtyStdin:
        def isatty(self):
            return False

    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("ISSUEKIT_API_USER", raising=False)
    monkeypatch.setenv("ISSUEKIT_API_PASSWORD", "secret")
    monkeypatch.setattr(auth.sys, "stdin", NonTtyStdin())
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: pytest.fail("input should not be called for non-TTY login"),
    )

    assert cli.main(["login"]) == 1

    assert (
        "Error: API username is required; pass --user or set ISSUEKIT_API_USER."
        in capsys.readouterr().err
    )


@pytest.mark.parametrize(
    ("argv", "env_user", "expected_username"),
    [
        (["login", "--user", "cli-user"], None, "cli-user"),
        (["login"], "env-user", "env-user"),
    ],
)
def test_login_command_existing_username_sources_bypass_prompt(
    tmp_path,
    monkeypatch,
    argv,
    env_user,
    expected_username,
) -> None:
    from issuekit.commands import auth

    class TtyStdin:
        def isatty(self):
            return True

    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.chdir(tmp_path)
    if env_user is None:
        monkeypatch.delenv("ISSUEKIT_API_USER", raising=False)
    else:
        monkeypatch.setenv("ISSUEKIT_API_USER", env_user)
    monkeypatch.setenv("ISSUEKIT_API_PASSWORD", "secret")
    monkeypatch.setattr(auth.sys, "stdin", TtyStdin())
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: pytest.fail("input should not be called when username is already set"),
    )
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

    assert cli.main(argv) == 0

    assert created[0][1]["username"] == expected_username


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
    import issuekit.proposals.api as proposals_api
    from issuekit import store as store_module
    from issuekit.testing import FakeIssuekitClient
    from tests.issue_helpers import api_issue

    class NoListClient(FakeIssuekitClient):
        def list_all_issues(self, *args, **kwargs):
            raise AssertionError("build_proposal should fetch the reply source issue directly")

    client = NoListClient(
        issues=[api_issue(1, "Implemented", status="completed", origin="source#0@abc123")]
    )
    client.register_catalog_project("source")
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'target'\n",
        encoding="utf-8",
        newline="\n",
    )
    body_file = tmp_path / "proposal.md"
    reply_file = tmp_path / "reply.md"
    body_file.write_text("## Suggested Change\n\nDo the thing.\n", encoding="utf-8", newline="\n")
    reply_file.write_text("## Suggested Change\n\nImplemented.\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(proposals_api, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)

    monkeypatch.chdir(tmp_path)
    assert cli.main(
        [
            "propose",
            "--to",
            "source",
            "--title",
            "Workspace Proposal",
            "--body-file",
            str(body_file),
        ]
    ) == 0
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

    proposals = client.list_proposals(status="pending")
    assert [proposal["title"] for proposal in proposals] == ["Workspace Proposal", "Implemented Reply"]
    assert proposals[1]["reply_to"].startswith("source#0@")
    assert proposals[1]["origin"].startswith("target#1@")

import argparse

import pytest

from issuekit import cli


EXPECTED_COMMANDS = {
    "info",
    "validate",
    "generate-indexes",
    "complete",
    "claim",
    "submit-review",
    "request-changes",
    "queue",
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


def test_complete_requires_id(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["complete"])

    captured = capsys.readouterr()

    assert exit_code != 0
    assert "id" in captured.err


@pytest.mark.parametrize("command", sorted(EXPECTED_COMMANDS))
def test_handlers_are_stubs(command: str) -> None:
    if command in {
        "check-encoding",
        "claim",
        "complete",
        "generate-indexes",
        "info",
        "init",
        "queue",
        "protocol",
        "request-changes",
        "setup",
        "submit-review",
        "validate",
        "add-ref",
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

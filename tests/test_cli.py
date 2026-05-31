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
    "init",
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
        "request-changes",
        "submit-review",
        "validate",
    }:
        pytest.skip(f"{command} is implemented")

    argv = [command]
    if command == "complete":
        argv.append("1")

    with pytest.raises(NotImplementedError, match=command):
        cli.main(argv)

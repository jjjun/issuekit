"""Keep every command help screen stable across parser refactors."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from issuekit import cli

SNAPSHOT_PATH = Path(__file__).parent / "snapshots" / "cli_help.json"


def _help_paths(parser: argparse.ArgumentParser, prefix: tuple[str, ...] = ()) -> list[tuple[str, ...]]:
    paths = [prefix]
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, subparser in action.choices.items():
                paths.extend(_help_paths(subparser, (*prefix, name)))
    return paths


def test_cli_help_matches_snapshot(monkeypatch, capsys) -> None:
    monkeypatch.setenv("COLUMNS", "100")
    expected = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    actual = []

    for path in _help_paths(cli.build_parser()):
        exit_code = cli.main([*path, "--help"])
        captured = capsys.readouterr()

        assert exit_code == 0, path
        assert captured.err == "", path
        actual.append({"path": list(path), "stdout": captured.out})

    assert actual == expected

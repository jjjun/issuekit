from __future__ import annotations

import pytest

from issuekit import cli


@pytest.mark.parametrize(
    "arguments",
    [
        ["dispatch", "bad", "--target-worker", "checkout.demo"],
        ["readdress", "bad"],
        ["reclaim", "bad"],
    ],
)
def test_issue_id_commands_use_shared_validation_error(arguments, capsys) -> None:
    assert cli.main(arguments) == 1

    error = capsys.readouterr().err
    assert "Invalid issue id: bad" in error
    assert "invalid literal for int()" not in error

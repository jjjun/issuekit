from issuekit import cli


def test_generate_indexes_command_is_retired(capsys) -> None:
    exit_code = cli.main(["generate-indexes"])

    assert exit_code != 0
    assert "invalid choice" in capsys.readouterr().err

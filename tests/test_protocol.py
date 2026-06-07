import pytest

from issuekit import cli
from issuekit.protocol import render_protocol


def test_render_protocol_returns_each_agent_and_both() -> None:
    codex = render_protocol("codex")
    claude = render_protocol("claude")
    both = render_protocol(None)

    assert "claim_next_task" in codex
    assert "submit_for_review" in codex
    assert "ASCII summary" in codex
    assert "next_review" in claude
    assert "request_changes" in claude
    assert "ASCII verification" in claude
    assert "ASCII notes" in claude
    for rendered in (codex, claude, both):
        assert "belongs to another registered repo" in rendered
        assert "issuekit list-refs" in rendered
        assert "target repo owns triage" in rendered
    assert codex.rstrip() in both
    assert claude in both
    both.encode("ascii")


def test_render_protocol_returns_implementer_for_unknown_agent() -> None:
    assert render_protocol("other") == render_protocol("codex")


def test_render_protocol_returns_role_for_kimi() -> None:
    assert render_protocol("kimi") == render_protocol("codex")
    assert render_protocol("kimi", role="reviewer") == render_protocol("claude")


def test_render_protocol_returns_author_role() -> None:
    author = render_protocol(role="author")
    assert "issuekit info" in author
    assert "docs/issues/active/" in author
    assert "Do not call `claim_next_task`" in author
    author.encode("ascii")


def test_render_protocol_rejects_unknown_role() -> None:
    with pytest.raises(ValueError, match="unknown role"):
        render_protocol(role="other")


def test_protocol_command_prints_agent_text(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["protocol", "--agent", "codex"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == render_protocol("codex")
    captured.out.encode("ascii")


def test_protocol_command_prints_kimi_text(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["protocol", "--agent", "kimi"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == render_protocol("kimi")
    captured.out.encode("ascii")


def test_protocol_command_prints_role_text(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["protocol", "--role", "reviewer"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == render_protocol("claude")
    captured.out.encode("ascii")


def test_protocol_command_prints_author_role_text(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["protocol", "--role", "author"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == render_protocol(role="author")
    captured.out.encode("ascii")


def test_protocol_command_prints_both_agents(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["protocol"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == render_protocol(None)
    assert "Handoff protocol (codex)" in captured.out
    assert "Handoff protocol (claude)" in captured.out

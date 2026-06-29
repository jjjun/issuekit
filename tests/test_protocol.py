import pytest

from issuekit import cli
from issuekit.protocol import render_protocol, render_server_instructions


def test_render_protocol_returns_each_agent_and_both() -> None:
    codex = render_protocol("codex")
    claude = render_protocol("claude")
    both = render_protocol(None)

    for rendered in (codex, claude, both):
        assert "Delegation cycle overview" in rendered
        assert "author -> implement -> review cycle" in rendered
        assert "open implement pool" in rendered
        assert "open review pool" in rendered
        assert "author role and implementer role must be different sessions" in rendered
        assert "implementer and reviewer must be different sessions" in rendered
        assert "author may also be the reviewer" in rendered
        assert "belongs to another project" in rendered
        assert "issuekit propose --to <project>" in rendered
        assert "owns triage" in rendered
        assert "issuekit implement <id> --agent <agent> --timeout-sec <n>" in rendered
        assert "launches the configured agent" in rendered
        assert "submits the completed work for review" in rendered
    assert "claim_next_task" in codex
    assert "submit_for_review" in codex
    assert 'assignee="<agent>"' in codex
    assert "ASCII summary" in codex
    assert "next_review" in claude
    assert "request_changes" in claude
    assert "ASCII verification" in claude
    assert "ASCII notes" in claude
    assert "issuekit complete <id>" in claude
    assert "CLI `approve` alias" in claude
    assert "work is incomplete" in claude
    assert "Handoff protocol (author)" in both
    assert "Handoff protocol (implementer)" in both
    assert "Handoff protocol (reviewer)" in both
    assert "The implementer handles issuekit tasks" in both
    assert "The reviewer handles issuekit tasks" in both
    both.encode("ascii")


def test_render_protocol_returns_implementer_for_unknown_agent() -> None:
    assert render_protocol("other") == render_protocol("codex")


def test_render_protocol_returns_role_for_kimi() -> None:
    assert render_protocol("kimi") == render_protocol("codex")
    assert render_protocol("kimi", role="reviewer") == render_protocol("claude")


def test_render_protocol_returns_author_role() -> None:
    author = render_protocol(role="author")
    assert "Delegation cycle overview" in author
    assert "issuekit author" in author
    assert "API allocates the issue id" in author
    assert "Do not call `claim_next_task`" in author
    assert "implementation-ready issues" in author
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
    assert "Delegation cycle overview" in captured.out
    assert "Handoff protocol (author)" in captured.out
    assert "Handoff protocol (implementer)" in captured.out
    assert "Handoff protocol (reviewer)" in captured.out


def test_render_server_instructions_includes_cycle_and_pointer() -> None:
    lean = render_server_instructions()
    assert "Delegation cycle overview" in lean
    assert 'get_protocol(role="author")' in lean
    assert 'get_protocol(role="implementer")' in lean
    assert 'get_protocol(role="reviewer")' in lean
    assert "author" in lean
    assert "implementer" in lean
    assert "reviewer" in lean
    lean.encode("ascii")


def test_render_server_instructions_is_substantially_smaller_than_full() -> None:
    lean = render_server_instructions()
    full = render_protocol(None)
    assert len(lean) < len(full) // 2


def test_render_protocol_roles_remain_self_contained() -> None:
    for role in ("author", "implementer", "reviewer"):
        rendered = render_protocol(role=role)
        assert "Delegation cycle overview" in rendered
        assert f"Handoff protocol ({role})" in rendered
        rendered.encode("ascii")

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
        assert "Separation-of-duties guard reference" in rendered
        assert "README.md#separation-of-duties-guards" in rendered
        assert "Server author-implementer guard" in rendered
        assert "Distinct-reviewer guard" in rendered
        assert "issuekit#162 and issuekit#163" in rendered
        assert "belongs to another project" in rendered
        assert "issuekit propose --to <project>" in rendered
        assert "owns triage" in rendered
        assert "dependency-first" in rendered
        assert "--depends-on <project#N|project#issue:N|project#proposal:N>" in rendered
        assert "project#proposal:N" in rendered
        assert "issuekit implement <id> --agent <agent> --timeout-sec <n>" in rendered
        assert "launches the configured agent" in rendered
        assert "submits the completed work for review" in rendered
        assert "sanctioned orchestration path" in rendered
        assert "Prefer a clean worktree before orchestrating" in rendered
        assert "Transport closed" in rendered
        assert "issuekit info --json" in rendered
        assert "Copyable CLI examples" in rendered
        assert 'issuekit author --title "Short title"' in rendered
        assert "Author with upstream dependency" in rendered
        assert (
            'issuekit author --title "Short title" --body-file issue.md '
            "--priority medium --agent codex --depends-on upstream#proposal:123"
        ) in rendered
        assert "issuekit claim --assignee codex" in rendered
        assert "issuekit claim --id 123 --assignee codex" in rendered
        assert 'issuekit submit-review 123 --summary "Implemented."' in rendered
        assert 'issuekit request-changes 123 --notes "Add focused tests."' in rendered
        assert 'issuekit approve 123 --verification "uv run pytest"' in rendered
        assert 'issuekit complete 123 --summary "Done."' in rendered
        assert "issuekit incoming --json" in rendered
        assert "issuekit propose --to <project> --title <t> --body <b> --blocking --json" in rendered
        assert "issuekit propose --to <project> --title <t> --body <b> --depends-on upstream#proposal:123 --json" in rendered
        assert "issuekit adopt 42 --priority medium --json" in rendered
        assert "issuekit outgoing --to <project> --json" in rendered
        assert "issuekit serve --agent codex --triage" in rendered
        assert "Upstream feedback loop" in rendered
        assert "issuekit propose --to issuekit" in rendered
        assert "issuekit outgoing --to issuekit" in rendered
    assert "claim_next_task" in codex
    assert "submit_for_review" in codex
    assert 'submit_for_review(id, summary, branch, commit, reviewer=None)' in codex
    assert 'submit_for_review(id, summary, branch, commit, assignee="<agent>"' not in codex
    assert "ASCII summary" in codex
    assert "Write maintainable, idiomatic code" in codex
    assert "dependency_state=waiting" in codex
    assert "explicit claim returns a dependency warning" in codex
    assert "otherwise obfuscate string literals" in codex
    assert "`importlib`, `getattr`, `setattr`, or `globals()`" in codex
    assert "next_review" in claude
    assert "request_changes" in claude
    assert "ASCII verification" in claude
    assert "ASCII notes" in claude
    assert "issuekit approve <id> --verification <text>" in claude
    assert "issuekit complete <id>" in claude
    assert "once it is available" not in claude
    assert "work is incomplete" in claude
    assert "readability and maintainability as review criteria" in claude
    assert "gratuitous obfuscation" in claude
    assert "unexplained style deviations" in claude
    assert "Handoff protocol (author)" in both
    assert "Handoff protocol (implementer)" in both
    assert "Handoff protocol (pm)" in both
    assert "Handoff protocol (reviewer)" in both
    assert "Handoff protocol (triage)" in both
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
    assert "pass\n   `--depends-on <project#N|project#issue:N|project#proposal:N>`" in author
    assert "respect the dependency state" in author
    assert "records that authoring session" in author
    assert "Do not call `claim_next_task`" in author
    assert "the author may run" in author
    assert "that same token" in author
    assert "for the claim and submit mutations" in author
    assert "implementation-ready issues" in author
    author.encode("ascii")


def test_render_protocol_returns_triage_role() -> None:
    triage = render_protocol(role="triage")
    triage_words = " ".join(triage.split())
    assert "Delegation cycle overview" in triage
    assert "Handoff protocol (triage)" in triage
    assert "issuekit incoming --json" in triage
    assert "value" in triage and "fit" in triage and "dependencies" in triage and "cost" in triage
    assert "issuekit adopt <id> --priority <p>" in triage
    assert "code-verified review" in triage
    assert "verify each factual claim" in triage
    assert "claims that are wrong or already implemented" in triage_words
    assert "Identify design decisions" in triage
    assert "resolve each with a recommendation" in triage_words
    assert "MCP `adopt_proposal(append=...)`" in triage
    assert "issuekit adopt <id> --priority <p> --append-file <file> --json" in triage_words
    assert "Reviewer design decisions (<date>, verified against current code)" in triage
    assert "recommended implementation order" in triage_words
    assert "issuekit discard <id>" in triage
    assert "missing a required upstream prerequisite" in triage
    assert "Do not implement adopted issues in the triage session" in triage
    assert "[triage] trusted_origins" in triage
    assert "issuekit serve --triage" in triage
    assert "issuekit propose --blocking" in triage
    triage.encode("ascii")


def test_render_protocol_returns_pm_role() -> None:
    pm = render_protocol(role="pm")
    assert "Delegation cycle overview" in pm
    assert "Handoff protocol (pm)" in pm
    assert 'issuekit request "Add dashboard export support"' in pm
    assert "issuekit request --answer 7" in pm
    assert "issuekit request --inbox" in pm
    assert '--target api' in pm
    assert "Supersedes:" in pm
    assert "issuekit request --status --json" in pm
    assert "Do not run `issuekit claim`" in pm
    assert "Target projects" in pm
    pm.encode("ascii")


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


def test_protocol_command_prints_pm_role_text(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["protocol", "--role", "pm"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == render_protocol(role="pm")
    captured.out.encode("ascii")


def test_protocol_command_prints_both_agents(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = cli.main(["protocol"])

    captured = capsys.readouterr()

    assert exit_code == 0
    assert captured.out == render_protocol(None)
    assert "Delegation cycle overview" in captured.out
    assert "Handoff protocol (author)" in captured.out
    assert "Handoff protocol (implementer)" in captured.out
    assert "Handoff protocol (pm)" in captured.out
    assert "Handoff protocol (reviewer)" in captured.out


def test_render_server_instructions_includes_cycle_and_pointer() -> None:
    lean = render_server_instructions()
    assert "Delegation cycle overview" in lean
    assert 'get_protocol(role="author")' in lean
    assert 'get_protocol(role="implementer")' in lean
    assert 'get_protocol(role="pm")' in lean
    assert 'get_protocol(role="reviewer")' in lean
    assert 'get_protocol(role="triage")' in lean
    assert "author" in lean
    assert "implementer" in lean
    assert "reviewer" in lean
    lean.encode("ascii")


def test_render_server_instructions_is_substantially_smaller_than_full() -> None:
    lean = render_server_instructions()
    full = render_protocol(None)
    assert len(lean) < len(full) // 2


def test_render_protocol_roles_remain_self_contained() -> None:
    for role in ("author", "implementer", "pm", "reviewer", "triage"):
        rendered = render_protocol(role=role)
        assert "Delegation cycle overview" in rendered
        assert f"Handoff protocol ({role})" in rendered
        rendered.encode("ascii")


def test_authoring_constraints_block_present_for_each_role() -> None:
    # The block lives in the shared cycle overview, so every role and the
    # server instructions surface it without per-role duplication.
    for role in ("author", "implementer", "pm", "reviewer", "triage"):
        rendered = render_protocol(role=role)
        assert "Authoring constraints:" in rendered
        assert "must be ASCII-only" in rendered
        assert "--direct-local-author" in rendered
    server = render_server_instructions()
    assert "Authoring constraints:" in server
    assert "--direct-local-author" in server


def test_authoring_constraints_block_appears_once() -> None:
    # Single source of truth: the block is not copy-pasted into each role text.
    both = render_protocol(None)
    assert both.count("Authoring constraints:") == 1

import re
import shlex
from pathlib import Path

import pytest

from issuekit import cli
from issuekit.guards.separation import SEPARATION_GUARD_REFERENCE
from issuekit.prompts.protocol import render_protocol, render_server_instructions


def test_render_protocol_returns_each_agent_and_both() -> None:
    codex = render_protocol("codex")
    claude = render_protocol("claude")
    both = render_protocol(None)
    normalized_codex = " ".join(codex.split())
    normalized_claude = " ".join(claude.split())
    normalized_both = " ".join(both.split())

    for rendered, normalized in (
        (codex, normalized_codex),
        (claude, normalized_claude),
        (both, normalized_both),
    ):
        assert "Delegation cycle overview" in normalized
        assert "author -> implement -> review cycle" in normalized
        assert "Open implement pool" in normalized
        assert "open review pool" in normalized
        assert "default_reviewer" not in rendered
        assert "require_distinct_reviewer" not in rendered
        assert "API mode" not in rendered
        assert "Assignment chooses the implementing agent" in normalized
        assert "issuekit dispatch <id> --target-worker" in normalized
        assert "issuekit readdress <id>" in normalized
        assert "Authors and implementers must use different sessions" in normalized
        assert "Implementers and reviewers must use different sessions" in normalized
        assert "An author may review work done by another implementer" in normalized
        assert "Separation-of-duties guard reference" in normalized
        assert "docs/guides/separation-of-duties.md" in rendered
        assert "Server author-implementer guard" in normalized
        assert "Distinct-reviewer guard" in normalized
        assert "issuekit#162 and issuekit#163" not in rendered
        assert "issuekit repository's" in rendered
        assert "belongs to another project" in normalized
        assert "issuekit propose --to <project>" in rendered
        assert "owns triage" in normalized
        assert "dependency-first" in normalized
        assert "--depends-on <project#N|project#issue:N|project#proposal:N>" in rendered
        assert "project#proposal:N" in rendered
        assert "issuekit implement <id> --agent <agent> --timeout-sec <n>" in rendered
        assert "issuekit negotiate --finalize <thread_id>" in rendered
        assert "uv run issuekit" not in rendered
        assert "non-API mode" not in rendered
        assert "launches the configured agent" in normalized
        assert "submits the completed work for review" in normalized
        assert "sanctioned orchestration path" in normalized
        assert "Prefer a clean worktree before orchestrating" in normalized
        assert "Transport closed" in normalized
        assert "issuekit info --json" in rendered
        assert "docs/guides/commands.md" in rendered
        assert "Proposal-system CLI fallback:" in rendered
        assert (
            "For proposal-system CLI equivalents, see the Proposal-system CLI fallback list"
            in rendered
        )
        assert "Upstream feedback loop" in normalized
        assert "issuekit propose --to issuekit" in rendered
        assert "issuekit outgoing --to issuekit" in rendered
        assert "Adoption notes are recorded only on the receiving project's issue" in normalized
        assert "default_implementer" in rendered
        assert (
            "Operators: agent flags, models and roles are configured as described"
            in normalized
        )
    assert "claim_next_task" in codex
    assert "resolves the" in normalized_codex
    assert "implementer from `default_implementer`" in normalized_codex
    assert "`claim_next_task(assignee=\"<agent>\")` for an explicit implementer" in codex
    assert "submit_for_review" in codex
    assert 'submit_for_review(id, summary, branch, commit, reviewer=None)' in codex
    assert 'submit_for_review(id, summary, branch, commit, assignee="<agent>"' not in codex
    assert "ASCII summary" in normalized_codex
    assert "Write maintainable, idiomatic code" in normalized_codex
    assert "dependency_state=waiting" in codex
    assert "explicit claim returns a dependency warning" in normalized_codex
    assert "otherwise obfuscate string literals" in normalized_codex
    assert "`importlib`, `getattr`, `setattr`, or `globals()`" in codex
    assert "When `issuekit implement` or `issuekit serve` launched you" in normalized_codex
    assert "skip steps 1, 5 and 6" in normalized_codex
    assert "next_review" in claude
    assert "request_changes" in claude
    assert "ASCII verification" in normalized_claude
    assert "ASCII notes" in normalized_claude
    assert "issuekit approve <id> --verification <text>" in claude
    assert "inspect the open review pool" in normalized_claude
    assert "inspect that reviewer's assigned issues" in normalized_claude
    assert "The CLI Pass" not in normalized_claude
    assert "Pass `--summary <text>` to `issuekit approve`" in normalized_claude
    assert "issuekit complete <id>" in claude
    assert "once it is available" not in normalized_claude
    assert "work is incomplete" in normalized_claude
    assert "readability and maintainability as review criteria" in normalized_claude
    assert "gratuitous obfuscation" in normalized_claude
    assert "unexplained style deviations" in normalized_claude
    assert "Handoff protocol (author)" in both
    assert "Handoff protocol (implementer)" in both
    assert "Handoff protocol (pm)" in both
    assert "Handoff protocol (reviewer)" in both
    assert "Handoff protocol (triage)" in both
    assert "The implementer handles issuekit tasks" in both
    assert "The reviewer handles issuekit tasks" in both
    assert "Use `propose` for specified changes owned elsewhere" in both
    assert "Negotiation is CLI-only; MCP only inspects threads" in normalized_both
    assert "docs/guides/negotiation.md" in both
    both.encode("ascii")


def test_proposal_cli_fallback_examples_parse() -> None:
    rendered = render_protocol(None)
    protocol_fallback = rendered.split("Proposal-system CLI fallback:", 1)[1].split(
        "\n\nWhen an orchestrator", 1
    )[0]
    protocol_commands = re.findall(r"`(issuekit [^`]+)`", protocol_fallback)

    guide_path = Path(__file__).parents[1] / "docs/guides/cross-project-proposals.md"
    guide = guide_path.read_text(encoding="utf-8")
    guide_fallback = guide.split("## Proposal-system CLI fallback\n", 1)[1].split(
        "\n## ", 1
    )[0]
    guide_commands = re.findall(r"`(issuekit [^`]+)`", guide_fallback)

    assert len(protocol_commands) == 6
    assert guide_commands
    parser = cli.build_parser()
    for command in protocol_commands + guide_commands:
        command = command.replace("<p>", "medium")
        command = re.sub(r"<[^>]+>", "sample", command)
        parser.parse_args(shlex.split(command)[1:])


def test_copyable_protocol_commands_parse() -> None:
    rendered = render_protocol(None)
    examples = rendered.split("Copyable CLI examples:\n", 1)[1].split(
        "\n\n# Handoff protocol", 1
    )[0]
    commands = re.findall(r"`(issuekit [^`]+)`", examples)

    assert commands
    parser = cli.build_parser()
    for command in commands:
        command = re.sub(r"<[^>]+>", "sample", command)
        parser.parse_args(shlex.split(command)[1:])


def test_shared_protocol_rules_have_one_full_definition() -> None:
    guide_path = Path(__file__).parents[1] / "docs/guides/cross-project-proposals.md"
    guide = guide_path.read_text(encoding="utf-8")
    assert guide.count("`propose(to, title, body)` ->") == 1

    fallback_reference = (
        "For proposal-system CLI equivalents, see the Proposal-system CLI fallback list"
    )
    for role in ("author", "implementer", "reviewer", "triage", "pm"):
        rendered = render_protocol(role=role)
        assert rendered.count("Proposal-system CLI fallback:") == 1
        assert rendered.count(fallback_reference) == 1
        assert rendered.count("**Dependency-first multi-project work:**") == 1
        assert rendered.count("Bare `project#N` refs can be shadowed") == 1
        assert rendered.count(
            "Same-name review is allowed there only with distinct implementer and reviewer sessions"
        ) == 1
        assert rendered.count("This is a sanctioned orchestration path:") == 1
        assert (
            rendered.count(
                "`issuekit propose --to <project> --title <t> --body <b> --json`"
            )
            == 1
        )


def test_separation_of_duties_guide_matches_protocol_table() -> None:
    reference_rows = [
        line for line in SEPARATION_GUARD_REFERENCE.splitlines() if line.startswith("|")
    ]
    guide_path = Path(__file__).parents[1] / "docs/guides/separation-of-duties.md"
    guide_rows = [
        line
        for line in guide_path.read_text(encoding="utf-8").splitlines()
        if line.startswith("|")
    ]

    assert guide_rows == reference_rows


def test_rendered_protocol_does_not_recommend_removed_codex_flag() -> None:
    rendered_protocols = (
        render_protocol("codex"),
        render_protocol("claude"),
        render_protocol(None),
        render_server_instructions(),
    )

    for rendered in rendered_protocols:
        assert "full-auto" not in rendered


def test_protocol_configuration_pointer_and_issue_guard_guidance_are_current() -> None:
    for role in ("author", "implementer"):
        rendered = render_protocol(role=role)
        normalized = " ".join(rendered.split())

        assert "issuekit repository's `docs/guides/configuration.md`" in rendered
        assert "README.md#separation-of-duties-guards" not in rendered
        assert "issuekit repository's `docs/guides/separation-of-duties.md`" in normalized
        assert "or run `issuekit author-guard --help`" in normalized
        assert (
            "`issuekit author` writes an issue guard and emits `STOP_NOW`. It "
            "blocks direct lifecycle work on that issue and pool claims from the "
            "checkout until `issuekit author-guard clear`"
            in normalized
        )
        assert "proposal guards do not block local issue lifecycle work." in normalized
        assert (
            "proposal guard records the handoff without interrupting a current"
            in normalized.lower()
        )
        assert "continue that task unless sending the proposal was your only task." in normalized.lower()
        assert (
            "While an issue guard is recorded, pool claims from that checkout "
            "(`claim` without `--id`, `serve`) are blocked for every issue"
            in normalized
        )
        assert (
            "Proposal guards record the handoff but do not block local issue "
            "lifecycle commands."
            in normalized
        )
        rendered.encode("ascii")


def test_render_protocol_returns_implementer_for_unknown_agent() -> None:
    assert render_protocol("other") == render_protocol("codex")


def test_render_protocol_returns_role_for_kimi() -> None:
    assert render_protocol("kimi") == render_protocol("codex")
    assert render_protocol("kimi", role="reviewer") == render_protocol("claude")


def test_render_protocol_uses_injected_agent_roles() -> None:
    assert render_protocol("claude", agent_roles={"claude": "implementer"}) == render_protocol(
        "codex"
    )


def test_render_protocol_returns_author_role() -> None:
    author = render_protocol(role="author")
    normalized_author = " ".join(author.split())
    assert "Delegation cycle overview" in author
    assert "issuekit author" in author
    assert "API allocates the issue id" in author
    assert "pass\n   `--depends-on <project#N|project#issue:N|project#proposal:N>`" in author
    assert "respect the dependency state" in author
    assert (
        "records an authoring session, using `ISSUEKIT_SESSION` when set or generating one otherwise"
        in normalized_author
    )
    assert "Do not call `claim_next_task`" in author
    assert "author needs to drive a configured external" in normalized_author
    assert "`ISSUEKIT_SESSION` is passed to the child" in normalized_author
    assert "for both claim and submit mutations" in normalized_author
    assert "implementation-ready issues" in author
    assert "proposal-check-request --to <project> --proposal <id>" in author
    assert "`create_proposal_check` tool" in author
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


def test_proposal_origin_deduplication_guidance_is_shared_across_roles() -> None:
    expected_phrases = (
        "A target inbox allows one pending proposal per origin `<project>#<id>@<commit>`",
        "`--from-issue` and `--reply` distinguish source issues, not proposals",
        "A different second payload exits 1 with `payload_mismatch: true`",
        "To send separately, omit `--from-issue` (implicit `#0`; omit `--reply` too to drop its link) or resolve the pending proposal",
    )
    for role in ("author", "implementer", "pm", "reviewer", "triage"):
        rendered = " ".join(render_protocol(role=role).split())
        for phrase in expected_phrases:
            assert phrase in rendered



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


def test_protocol_command_uses_configured_agent_role(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "[agent_roles]\nclaude = 'implementer'\n", encoding="utf-8", newline="\n"
    )
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["protocol", "--agent", "claude"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out == render_protocol("codex")


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


def test_render_server_instructions_include_short_handoff_guidance() -> None:
    lean = render_server_instructions()
    assert 'get_protocol(role="<role>")' in lean
    assert "author, implementer, reviewer, triage, pm" in lean
    assert "pull-based cycle" in lean
    assert "one JSON document" in lean
    assert "trust the exit status" in lean
    assert "Transport closed" in lean
    assert "issuekit protocol --role <role>" in lean
    assert "issuekit show <id> --json" in lean
    assert "STOP_NOW" in lean
    lean.encode("ascii")


def test_render_server_instructions_are_within_size_limit() -> None:
    lean = render_server_instructions()
    assert len(lean) <= 2000
    assert "get_protocol(role=" in lean[:400]


def test_machine_readable_output_guidance_is_shared() -> None:
    expected = (
        "Machine-readable output:",
        "stdout contains exactly one JSON document",
        "non-zero means the request was not fully applied",
        "`payload_mismatch` or `append_error`",
        "Do not merge stderr into stdout (`2>&1`)",
        "`${PIPESTATUS[0]}`",
        "do not retry until `issuekit queue` or",
        "`issuekit outgoing --to <project>`",
    )
    author = " ".join(render_protocol(role="author").split())
    for phrase in expected:
        assert phrase in author
    server = " ".join(render_server_instructions().split())
    assert "one JSON document" in server
    assert "trust the exit status" in server


def test_render_protocol_roles_remain_self_contained() -> None:
    for role in ("author", "implementer", "pm", "reviewer", "triage"):
        rendered = render_protocol(role=role)
        assert "Delegation cycle overview" in rendered
        assert f"Handoff protocol ({role})" in rendered
        rendered.encode("ascii")


def test_authoring_constraints_block_present_for_each_role() -> None:
    # The block lives in the shared cycle overview, so every role sees it
    # without per-role duplication.
    for role in ("author", "implementer", "pm", "reviewer", "triage"):
        rendered = render_protocol(role=role)
        assert "Authoring constraints:" in rendered
        assert "must be ASCII-only" in rendered
        assert "--direct-local-author" in rendered


def test_authoring_constraints_block_appears_once() -> None:
    # Single source of truth: the block is not copy-pasted into each role text.
    both = render_protocol(None)
    assert both.count("Authoring constraints:") == 1

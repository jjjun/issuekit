import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from issuekit import cli
from issuekit import store as store_module
from issuekit.agentrun import AgentPrompt
from issuekit.agents import review as review_agent
from issuekit.core import Issue
from issuekit.testing import FakeIssuekitClient
from tests.issue_helpers import api_issue


@dataclass(frozen=True)
class FakeResult:
    exit_code: int = 0
    stdout_path: Path = Path("out.log")
    agent_log_path: Path = Path("agent.log")
    elapsed_sec: float = 1.25
    timed_out: bool = False
    parsed: dict[str, str] | None = None
    status_short: str | None = ""
    status_path: Path | None = Path("status.json")


class ApprovingRunner:
    calls: list[tuple[AgentPrompt, Path, float, str | None, int | None]] = []

    def run(
        self,
        adapter,
        prompt: AgentPrompt,
        repo: Path,
        timeout: float,
        agent_name: str | None = None,
        issue_id: int | None = None,
        follow: bool = False,
        **kwargs,
    ) -> FakeResult:
        self.calls.append(
            (
                prompt,
                repo,
                timeout,
                agent_name,
                issue_id,
            )
        )
        return FakeResult(
            parsed={
                "stdout": (
                    "```review\n"
                    '{"verdict":"approve","verification":"uv run pytest","notes":"Looks good."}\n'
                    "```"
                )
            }
        )


class RequestChangesRunner(ApprovingRunner):
    def run(self, *args, **kwargs) -> FakeResult:
        return FakeResult(
            parsed={
                "stdout": (
                    "```review\n"
                    '{"verdict":"request-changes","verification":"","notes":"Add focused tests."}\n'
                    "```"
                )
            }
        )


class NonAsciiApprovingRunner(ApprovingRunner):
    def run(self, *args, **kwargs) -> FakeResult:
        return FakeResult(
            parsed={
                "stdout": (
                    "```review\n"
                    '{"verdict":"approve","verification":"\u691c\u8a3c\u6e08\u307f","notes":""}\n'
                    "```"
                )
            }
        )


class NonAsciiRequestChangesRunner(ApprovingRunner):
    def run(self, *args, **kwargs) -> FakeResult:
        return FakeResult(
            parsed={
                "stdout": (
                    "```review\n"
                    '{"verdict":"request-changes","verification":"",'
                    '"notes":"Add tests \u2014 including edge cases."}\n'
                    "```"
                )
            }
        )


class MalformedReviewRunner(ApprovingRunner):
    def run(self, *args, **kwargs) -> FakeResult:
        return FakeResult(parsed={"stdout": "The implementation looks good.\n"})


class TimedOutReviewRunner(ApprovingRunner):
    def run(self, *args, **kwargs) -> FakeResult:
        return FakeResult(timed_out=True)


class NonJsonReviewRunner(ApprovingRunner):
    def run(self, *args, **kwargs) -> FakeResult:
        return FakeResult(
            parsed={
                "stdout": (
                    "```review\n"
                    "REQUEST_CHANGES\n\n"
                    "- Add focused tests.\n"
                    "```"
                )
            }
        )


class CloseTrackingClient(FakeIssuekitClient):
    def __init__(self) -> None:
        super().__init__()
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


def _configure_registered_api(tmp_path: Path, monkeypatch, client: FakeIssuekitClient) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\nproject = 'demo'\ndefault_reviewer = 'auto'\n",
        encoding="utf-8",
        newline="\n",
    )
    (tmp_path / "issuekit.local.toml").write_text(
        (
            "[worker]\n"
            "machine_id = 'machine'\n"
            "repo_id = 'demo'\n"
            "worker_id = 'reviewer'\n"
        ),
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setattr(store_module, "IssuekitClient", lambda *args, **kwargs: client)
    monkeypatch.chdir(tmp_path)


def _init_git_repo(path: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(path), check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=str(path), check=True)
    subprocess.run(["git", "config", "core.autocrlf", "false"], cwd=str(path), check=True)
    subprocess.run(["git", "add", "."], cwd=str(path), check=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=str(path),
        check=True,
        stdout=subprocess.DEVNULL,
    )


def test_review_command_closes_lookup_store_when_issue_is_missing(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = CloseTrackingClient()
    _configure_registered_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(["review", "99", "--agent", "codex"])

    assert exit_code == 1
    assert "Active issue #99 was not found." in capsys.readouterr().err
    assert client.close_count == 1


def _create_reviewable_diff(path: Path) -> None:
    (path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    (path / "code.py").write_text("value = 1\n", encoding="utf-8", newline="\n")
    _init_git_repo(path)
    (path / "code.py").write_text("value = 2\n", encoding="utf-8", newline="\n")


def _issue() -> Issue:
    return Issue(
        id=1,
        ref="demo#1",
        title="Review me",
        issue_status="in_progress",
        created="2026-01-01",
        completed="",
        priority="medium",
        assignee="",
        stage="review",
        implementer="codex",
        author="claude",
        body="# Issue #1: Review me\n",
        metadata={},
        worker="machine/demo/implementer",
    )


def test_review_command_approves_with_distinct_worker_identity(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Review me",
                status="in_progress",
                assignee="",
                stage="review",
                implementer="codex",
                worker="machine/demo/implementer",
                author="claude",
            )
        ]
    )
    ApprovingRunner.calls.clear()
    _configure_registered_api(tmp_path, monkeypatch, client)
    _create_reviewable_diff(tmp_path)
    monkeypatch.setattr("issuekit.commands.review.AgentRunner", ApprovingRunner)

    exit_code = cli.main(["review", "1", "--agent", "codex", "--timeout-sec", "9"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert len(ApprovingRunner.calls) == 1
    prompt, repo, timeout, agent_name, issue_id = ApprovingRunner.calls[0]
    assert prompt.path == tmp_path / ".agent-runs" / "review-issue-1.md"
    prompt_text = prompt.body
    assert "Review issue demo#1" in prompt_text
    assert "Review correctness, tests, readability, maintainability" in prompt_text
    assert "Request changes for gratuitous obfuscation" in prompt_text
    assert "All JSON string values must be ASCII-only" in prompt_text
    assert repo == tmp_path
    assert timeout == 9
    assert agent_name == "codex"
    assert issue_id == 1
    assert "fenced review block" in prompt.pointer
    assert "review_decision verdict=approve" in captured.out
    assert client.get_issue(1)["status"] == "completed"
    assert client.calls[-1] == {
        "method": "approve",
        "number": 1,
        "body": {
            "summary": "Approved by reviewer agent.",
            "verification": "uv run pytest",
            "reviewer": "codex",
                "worker": "reviewer.demo",
        },
    }


def test_review_command_approves_with_sanitized_non_ascii_verification(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Review me",
                status="in_progress",
                assignee="",
                stage="review",
                implementer="codex",
                worker="machine/demo/implementer",
                author="claude",
            )
        ]
    )
    _configure_registered_api(tmp_path, monkeypatch, client)
    _create_reviewable_diff(tmp_path)
    monkeypatch.setattr("issuekit.commands.review.AgentRunner", NonAsciiApprovingRunner)

    exit_code = cli.main(["review", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "review_decision verdict=approve" in captured.out
    assert "field verification contained non-ASCII text" in captured.err
    assert client.get_issue(1)["status"] == "completed"
    assert client.calls[-1]["body"]["verification"] == (
        "[verification sanitized from non-ASCII]"
    )


def test_review_command_sends_runtime_on_both_verdicts(tmp_path: Path, monkeypatch) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Review me",
                status="in_progress",
                assignee="",
                stage="review",
                implementer="codex",
                worker="machine/demo/implementer",
                author="claude",
            ),
            api_issue(
                2,
                "Needs tests",
                status="in_progress",
                assignee="claude",
                stage="review",
                implementer="codex",
                worker="machine/demo/implementer",
                author="claude",
            ),
        ]
    )
    _configure_registered_api(tmp_path, monkeypatch, client)
    _create_reviewable_diff(tmp_path)
    monkeypatch.setattr("issuekit.commands.review.AgentRunner", ApprovingRunner)

    assert cli.main(
        ["review", "1", "--agent", "codex", "--model", "review-model", "--reasoning-effort", "high"]
    ) == 0
    assert client.calls[-1]["body"]["agent_model"] == "review-model"
    assert client.calls[-1]["body"]["agent_reasoning_effort"] == "high"

    monkeypatch.setattr("issuekit.commands.review.AgentRunner", RequestChangesRunner)
    assert cli.main(
        ["review", "2", "--agent", "claude", "--model", "review-model", "--reasoning-effort", "high"]
    ) == 0
    assert client.calls[-1]["body"]["agent_model"] == "review-model"
    assert client.calls[-1]["body"]["agent_reasoning_effort"] == "high"


def test_review_command_requests_changes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Needs tests",
                status="in_progress",
                assignee="claude",
                stage="review",
                implementer="codex",
                worker="machine/demo/implementer",
                author="claude",
            )
        ]
    )
    _configure_registered_api(tmp_path, monkeypatch, client)
    _create_reviewable_diff(tmp_path)
    monkeypatch.setattr("issuekit.commands.review.AgentRunner", RequestChangesRunner)

    exit_code = cli.main(["review", "1", "--agent", "claude"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "review_decision verdict=request-changes" in captured.out
    assert client.get_issue(1)["stage"] == "changes_requested"
    assert client.calls[-1] == {
        "method": "request_changes",
        "number": 1,
        "body": {
            "notes": "Add focused tests.",
            "reviewer": "claude",
                "worker": "reviewer.demo",
        },
    }


def test_review_command_requests_changes_with_sanitized_non_ascii_notes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Needs tests",
                status="in_progress",
                assignee="claude",
                stage="review",
                implementer="codex",
                worker="machine/demo/implementer",
                author="claude",
            )
        ]
    )
    _configure_registered_api(tmp_path, monkeypatch, client)
    _create_reviewable_diff(tmp_path)
    monkeypatch.setattr(
        "issuekit.commands.review.AgentRunner",
        NonAsciiRequestChangesRunner,
    )

    exit_code = cli.main(["review", "1", "--agent", "claude"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "review_decision verdict=request-changes" in captured.out
    assert "field notes contained non-ASCII text" in captured.err
    assert client.get_issue(1)["stage"] == "changes_requested"
    assert client.calls[-1]["body"]["notes"] == (
        "Add tests - including edge cases.\n\n"
        "[notes sanitized from non-ASCII]"
    )


def test_review_command_rejects_same_worker_self_review(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Same worker",
                status="in_progress",
                assignee="",
                stage="review",
                implementer="codex",
                worker="reviewer.demo@machine",
                author="claude",
            )
        ]
    )
    ApprovingRunner.calls.clear()
    _configure_registered_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.commands.review.AgentRunner", ApprovingRunner)

    exit_code = cli.main(["review", "1", "--agent", "codex"])

    assert exit_code == 1
    assert not ApprovingRunner.calls
    assert "self-review by the same worker is not allowed" in capsys.readouterr().err


def test_review_command_reports_discarded_decision_for_malformed_review_output(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Malformed review",
                status="in_progress",
                assignee="",
                stage="review",
                implementer="codex",
                worker="machine/demo/implementer",
                author="claude",
            )
        ]
    )
    _configure_registered_api(tmp_path, monkeypatch, client)
    _create_reviewable_diff(tmp_path)
    monkeypatch.setattr("issuekit.commands.review.AgentRunner", MalformedReviewRunner)

    exit_code = cli.main(["review", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "agent_exit_code=0" in captured.out
    assert (
        "review_decision=discarded "
        "(unparseable review block: No ```review``` block found"
        in captured.out
    )
    assert "issuekit request-changes 1 --notes <text>" in captured.out
    assert "issuekit approve 1 --verification <text>" in captured.out
    assert "No ```review``` block found" in captured.err
    assert client.calls == []


def test_review_command_reports_no_decision_for_timed_out_review(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Timed-out review",
                status="in_progress",
                assignee="",
                stage="review",
                implementer="codex",
                worker="machine/demo/implementer",
                author="claude",
            )
        ]
    )
    _configure_registered_api(tmp_path, monkeypatch, client)
    _create_reviewable_diff(tmp_path)
    monkeypatch.setattr("issuekit.commands.review.AgentRunner", TimedOutReviewRunner)

    exit_code = cli.main(["review", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 124
    assert "review_decision=none (no decision recorded)" in captured.out
    assert client.calls == []


def test_review_command_discards_fenced_non_json_review_output(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Non-JSON review",
                status="in_progress",
                assignee="",
                stage="review",
                implementer="codex",
                worker="machine/demo/implementer",
                author="claude",
            )
        ]
    )
    _configure_registered_api(tmp_path, monkeypatch, client)
    _create_reviewable_diff(tmp_path)
    monkeypatch.setattr("issuekit.commands.review.AgentRunner", NonJsonReviewRunner)

    exit_code = cli.main(["review", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "agent_exit_code=0" in captured.out
    assert (
        "review_decision=discarded "
        "(unparseable review block: Review block was not valid JSON"
        in captured.out
    )
    assert "Review block was not valid JSON" in captured.err
    assert client.calls == []


def test_review_command_self_review_names_no_eligible_reviewer(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Only reviewer",
                status="in_progress",
                assignee="",
                stage="review",
                implementer="codex",
                worker="",
                author="claude",
            )
        ]
    )
    _configure_registered_api(tmp_path, monkeypatch, client)
    (tmp_path / "issuekit.toml").write_text(
        "api_url = 'https://mine.example'\n"
        "project = 'demo'\n"
        "default_reviewer = 'auto'\n"
        "disabled_agents = ['kimi', 'claude']\n",
        encoding="utf-8",
        newline="\n",
    )

    exit_code = cli.main(["review", "1", "--agent", "codex"])

    error = capsys.readouterr().err
    assert exit_code == 1
    assert "self-review is not allowed" in error
    assert "no eligible reviewer via --agent" in error


def test_review_command_self_review_keeps_plain_message_when_another_agent_exists(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Another reviewer",
                status="in_progress",
                assignee="",
                stage="review",
                implementer="codex",
                worker="",
                author="claude",
            )
        ]
    )
    _configure_registered_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(["review", "1", "--agent", "codex"])

    error = capsys.readouterr().err
    assert exit_code == 1
    assert "Issue #1 was implemented by codex; self-review is not allowed." in error
    assert "no eligible reviewer" not in error


def test_review_command_rejects_empty_implementation_diff_before_agent(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Nothing local",
                status="in_progress",
                assignee="",
                stage="review",
                implementer="codex",
                worker="machine/demo/implementer",
                author="claude",
            )
        ]
    )
    ApprovingRunner.calls.clear()
    _configure_registered_api(tmp_path, monkeypatch, client)
    (tmp_path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    (tmp_path / "code.py").write_text("value = 1\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)
    monkeypatch.setattr("issuekit.commands.review.AgentRunner", ApprovingRunner)

    exit_code = cli.main(["review", "1", "--agent", "codex"])

    assert exit_code == 1
    assert not ApprovingRunner.calls
    assert not (tmp_path / ".agent-runs" / "review-issue-1.md").exists()
    assert "No implementation diff is available" in capsys.readouterr().err
    assert [call["method"] for call in client.calls] == []


def test_review_command_allows_handoff_evidence_without_local_diff(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    raw_issue = api_issue(
        1,
        "Host operation",
        status="in_progress",
        assignee="",
        stage="review",
        implementer="codex",
        worker="machine/demo/implementer",
        author="claude",
        body="# Issue #1: Host operation\n\nReview the live host state.\n",
    )
    raw_issue.update(
        {
            "summary": "Restarted the service on host a.",
            "branch": "main",
            "commit": "abc1234",
            "verification": "systemctl status demo.service",
        }
    )
    client = FakeIssuekitClient([raw_issue])
    ApprovingRunner.calls.clear()
    _configure_registered_api(tmp_path, monkeypatch, client)
    (tmp_path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    (tmp_path / "code.py").write_text("value = 1\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)
    monkeypatch.setattr("issuekit.commands.review.AgentRunner", ApprovingRunner)

    exit_code = cli.main(["review", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert len(ApprovingRunner.calls) == 1
    prompt_text = ApprovingRunner.calls[0][0].body
    assert "Review the submitted handoff evidence against the issue." in prompt_text
    assert "No local implementation diff is available in this checkout." in prompt_text
    assert "Handoff summary: Restarted the service on host a." in prompt_text
    assert "Branch: main" in prompt_text
    assert "Commit: abc1234" in prompt_text
    assert "Verification evidence: systemctl status demo.service" in prompt_text
    assert "review_decision verdict=approve" in captured.out
    assert client.get_issue(1)["status"] == "completed"


def test_body_handoff_evidence_requires_content_and_keeps_continuations() -> None:
    issue = replace(
        _issue(),
        body=(
            "# Issue\n\n"
            "Checks:\n"
            "- uv run pytest\n"
            "  uv run issuekit check-encoding\n\n"
            "## Notes\n"
            "not evidence\n"
        ),
    )

    evidence = review_agent._handoff_evidence_text(issue)

    assert "Checks:\n- uv run pytest\n  uv run issuekit check-encoding" in evidence
    assert "not evidence" not in evidence
    assert review_agent._handoff_evidence_text(
        replace(_issue(), body="Checks:\n\n## Notes\nNothing.\n")
    ) == ""


def test_collect_git_diff_context_includes_untracked_text_and_binary(
    tmp_path: Path,
) -> None:
    (tmp_path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    (tmp_path / "tracked.py").write_text("value = 1\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)
    (tmp_path / "new file.py").write_text("first\nsecond\n", encoding="utf-8", newline="\n")
    (tmp_path / "asset.bin").write_bytes(b"\0binary")

    context = review_agent._collect_git_diff_context(tmp_path)

    assert context.has_changed_files is True
    assert "--- /dev/null" in context.text
    assert "+++ b/new file.py" in context.text
    assert "+first\n+second" in context.text
    assert "[untracked binary file: asset.bin]" in context.text


def test_combined_review_evidence_applies_one_size_limit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "new.py").write_text("new = True\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(review_agent, "_MAX_DIFF_CHARS", 200)

    evidence = review_agent._combined_diff_evidence(
        tmp_path,
        "+" + ("x" * 400),
        (review_agent.GitStatusEntry(status="??", path=Path("new.py")),),
    )

    assert len(evidence) <= 200
    assert "untracked file omitted by review context size limit: new.py" in evidence


def test_large_untracked_file_is_omitted_without_reading(
    tmp_path: Path,
    monkeypatch,
) -> None:
    path = tmp_path / "large.bin"
    path.write_bytes(b"x" * 201)
    monkeypatch.setattr(review_agent, "_MAX_DIFF_CHARS", 200)
    original_read_bytes = Path.read_bytes

    def fail_read_bytes(self: Path) -> bytes:
        if self == path:
            raise AssertionError(f"unexpected read: {self}")
        return original_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", fail_read_bytes)

    assert review_agent._untracked_diff_section(tmp_path, path.relative_to(tmp_path)) == (
        "[untracked file omitted by review context size limit: large.bin]"
    )


def test_reviewable_status_filters_only_agent_run_paths() -> None:
    runtime_entry = review_agent.GitStatusEntry(
        status="??",
        path=Path(".agent-runs/review-issue-1.md"),
    )

    assert review_agent._has_reviewable_changed_files((runtime_entry,)) is False
    assert review_agent._has_reviewable_changed_files(
        (
            review_agent.GitStatusEntry(
                status="R ",
                path=Path(".agent-runs/code.py"),
                original_path=Path("code.py"),
            ),
        )
    ) is True


def test_review_prompt_surfaces_obfuscation_hints() -> None:
    diff_context = review_agent.ReviewDiffContext(
        text=(
            "diff --git a/code.py b/code.py\n"
            "+_module = importlib.import_module(\"basekit.\" + \"doc\" + \"ker_manager\")\n"
            "+_klass = getattr(_module, \"Doc\" + \"kerComposeGenerator\")\n"
            "+globals()[\"generate_\" + \"doc\" + \"ker_compose\"] = _generate\n"
        ),
        has_changed_files=True,
        suspicious_warnings=review_agent._suspicious_readability_warnings(
            "+importlib.import_module(\"basekit.\" + \"doc\")\n"
            "+getattr(_module, \"Doc\" + \"kerComposeGenerator\")\n"
            "+globals()[\"generate_\" + \"doc\"] = value\n"
        ),
    )

    prompt = review_agent._render_review_prompt(
        _issue(),
        diff_context=diff_context,
    )

    assert "Automated readability hints:" in prompt
    assert "string-concatenated import_module path" in prompt
    assert "string-concatenated getattr name" in prompt
    assert "globals() attribute injection" in prompt


def test_readability_warnings_only_inspect_added_lines() -> None:
    suspicious = 'globals()["generated"] = value'

    assert review_agent._suspicious_readability_warnings(
        f" {suspicious}\n-{suspicious}\n"
    ) == ()
    assert review_agent._suspicious_readability_warnings(f"+{suspicious}\n") == (
        "globals() attribute injection",
    )


def test_collect_git_diff_context_tolerates_missing_diff_stdout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        review_agent,
        "git_status_short",
        lambda *args, **kwargs: " M code.py\n",
    )
    monkeypatch.setattr(
        review_agent,
        "git_status_entries",
        lambda *args, **kwargs: (
            review_agent.GitStatusEntry(status=" M", path=Path("code.py")),
        ),
    )
    monkeypatch.setattr(review_agent, "_git_stdout", lambda *args, **kwargs: None)

    context = review_agent._collect_git_diff_context(tmp_path)

    assert context.has_changed_files is True
    assert "git diff HEAD --:\n(unavailable or empty)" in context.text
    assert context.suspicious_warnings == ()


def test_parse_review_output_sanitizes_non_ascii_field(capsys) -> None:
    verdict = review_agent.parse_review_output(
        "```review\n"
        '{"verdict":"request-changes","verification":"","notes":"\u76f4\u3057\u3066"}\n'
        "```"
    )

    assert verdict.notes == "[notes sanitized from non-ASCII]"
    assert "field notes contained non-ASCII text" in capsys.readouterr().err


def test_parse_review_output_normalizes_request_changes_verdict() -> None:
    verdict = review_agent.parse_review_output(
        "```review\n"
        '{"verdict":"request_changes","verification":"","notes":"Add tests."}\n'
        "```"
    )

    assert verdict.verdict == "request-changes"


def test_parse_review_output_skips_newer_invalid_json_block() -> None:
    verdict = review_agent.parse_review_output(
        "```review\n"
        '{"verdict":"approve","verification":"pytest","notes":""}\n'
        "```\n"
        "```review\n"
        '{"verdict":"approve"\n'
        "```"
    )

    assert verdict.verdict == "approve"


def test_parse_review_output_rejects_newer_non_object_block() -> None:
    with pytest.raises(
        review_agent.ReviewParseError,
        match="Review block JSON must be an object",
    ):
        review_agent.parse_review_output(
            "```review\n"
            '{"verdict":"approve","verification":"pytest","notes":""}\n'
            "```\n"
            "```review\n"
            "[]\n"
            "```"
        )


def test_parse_review_output_accepts_json_fallback_block() -> None:
    verdict = review_agent.parse_review_output(
        "```json\n"
        '{"verdict":"approve","verification":"pytest","notes":""}\n'
        "```"
    )

    assert verdict.verdict == "approve"


def test_parse_review_output_accepts_bare_fallback_block() -> None:
    verdict = review_agent.parse_review_output(
        "```\n"
        '{"verdict":"approve","verification":"pytest","notes":""}\n'
        "```"
    )

    assert verdict.verdict == "approve"


def test_parse_review_output_joins_list_valued_notes() -> None:
    verdict = review_agent.parse_review_output(
        "```review\n"
        '{"verdict":"request-changes","verification":"","notes":["Add tests.","Fix docs."]}\n'
        "```"
    )

    assert verdict.notes == "Add tests.\nFix docs."


def test_parse_review_output_prefers_review_block_over_json_block() -> None:
    verdict = review_agent.parse_review_output(
        "```review\n"
        '{"verdict":"approve","verification":"pytest","notes":""}\n'
        "```\n"
        "```json\n"
        '{"unrelated":true}\n'
        "```"
    )

    assert verdict.verdict == "approve"


def test_parse_review_output_rejects_json_fallback_without_required_keys() -> None:
    with pytest.raises(
        review_agent.ReviewParseError,
        match="Review block is missing required key",
    ):
        review_agent.parse_review_output(
            "```json\n"
            '{"verdict":"approve"}\n'
            "```"
        )


@pytest.mark.parametrize("filename", ["code.py", "変更.py"])
def test_review_command_blocks_verdict_when_agent_mutates_worktree(
    tmp_path: Path,
    monkeypatch,
    capsys,
    filename,
) -> None:
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "Read only",
                status="in_progress",
                assignee="",
                stage="review",
                implementer="codex",
                worker="machine/demo/implementer",
                author="claude",
            )
        ]
    )
    _configure_registered_api(tmp_path, monkeypatch, client)
    (tmp_path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    code_path = tmp_path / filename
    code_path.write_text("value = 1\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)
    code_path.write_text("value = 2\n", encoding="utf-8", newline="\n")

    class MutatingRunner(ApprovingRunner):
        def run(
            self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs
        ) -> FakeResult:
            code_path.write_text("value = 3\n", encoding="utf-8", newline="\n")
            return super().run(adapter, prompt, repo, timeout, **kwargs)

    monkeypatch.setattr("issuekit.commands.review.AgentRunner", MutatingRunner)

    exit_code = cli.main(["review", "1", "--agent", "codex"])

    assert exit_code == 1
    assert "reviewer run modified repository state" in capsys.readouterr().err
    assert [call["method"] for call in client.calls] == []

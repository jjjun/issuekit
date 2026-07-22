from dataclasses import dataclass
from pathlib import Path
import subprocess

import pytest

from issuekit import cli
from issuekit import store as store_module
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
    calls: list[tuple[Path, Path, float, str | None, int | None, str | None]] = []

    def run(
        self,
        adapter,
        plan_path: Path,
        repo: Path,
        timeout: float,
        agent_name: str | None = None,
        issue_id: int | None = None,
        follow: bool = False,
        **kwargs,
    ) -> FakeResult:
        self.calls.append(
            (
                plan_path,
                repo,
                timeout,
                agent_name,
                issue_id,
                kwargs.get("prompt_override"),
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


class MalformedReviewRunner(ApprovingRunner):
    def run(self, *args, **kwargs) -> FakeResult:
        return FakeResult(parsed={"stdout": "The implementation looks good.\n"})


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
    plan_path, repo, timeout, agent_name, issue_id, prompt_override = ApprovingRunner.calls[0]
    assert plan_path == tmp_path / ".agent-runs" / "review-issue-1.md"
    prompt_text = plan_path.read_text(encoding="utf-8")
    assert "Review issue demo#1" in prompt_text
    assert "Review correctness, tests, readability, maintainability" in prompt_text
    assert "Request changes for gratuitous obfuscation" in prompt_text
    assert "All JSON string values must be ASCII-only" in prompt_text
    assert repo == tmp_path
    assert timeout == 9
    assert agent_name == "codex"
    assert issue_id == 1
    assert "fenced review block" in (prompt_override or "")
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


def test_review_command_reports_no_decision_for_malformed_review_output(
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
    assert "review_decision=none (no decision recorded)" in captured.out
    assert "No ```review``` block found" in captured.err
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
    prompt_text = (tmp_path / ".agent-runs" / "review-issue-1.md").read_text(
        encoding="utf-8"
    )
    assert "Review the submitted handoff evidence against the issue." in prompt_text
    assert "No local implementation diff is available in this checkout." in prompt_text
    assert "Handoff summary: Restarted the service on host a." in prompt_text
    assert "Branch: main" in prompt_text
    assert "Commit: abc1234" in prompt_text
    assert "Verification evidence: systemctl status demo.service" in prompt_text
    assert "review_decision verdict=approve" in captured.out
    assert client.get_issue(1)["status"] == "completed"


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
            "importlib.import_module(\"basekit.\" + \"doc\")\n"
            "getattr(_module, \"Doc\" + \"kerComposeGenerator\")\n"
            "globals()[\"generate_\" + \"doc\"] = value\n"
        ),
    )

    prompt = review_agent._render_review_prompt(
        _issue(),
        cwd=Path("."),
        diff_context=diff_context,
    )

    assert "Automated readability hints:" in prompt
    assert "string-concatenated import_module path" in prompt
    assert "string-concatenated getattr name" in prompt
    assert "globals() attribute injection" in prompt


def test_collect_git_diff_context_tolerates_missing_diff_stdout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        review_agent,
        "git_status_short",
        lambda *args, **kwargs: " M code.py\n",
    )
    monkeypatch.setattr(review_agent, "_git_stdout", lambda *args, **kwargs: None)

    context = review_agent._collect_git_diff_context(tmp_path)

    assert context.has_changed_files is True
    assert "git diff HEAD --:\n(unavailable or empty)" in context.text
    assert context.suspicious_warnings == ()


def test_parse_review_output_names_non_ascii_field() -> None:
    with pytest.raises(review_agent.ReviewParseError, match="notes must be ASCII-only"):
        review_agent.parse_review_output(
            "```review\n"
            '{"verdict":"request-changes","verification":"","notes":"\u76f4\u3057\u3066"}\n'
            "```"
        )


def test_review_command_blocks_verdict_when_agent_mutates_worktree(
    tmp_path: Path,
    monkeypatch,
    capsys,
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
    (tmp_path / "code.py").write_text("value = 1\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)
    (tmp_path / "code.py").write_text("value = 2\n", encoding="utf-8", newline="\n")

    class MutatingRunner(ApprovingRunner):
        def run(self, adapter, plan_path, repo, timeout, **kwargs) -> FakeResult:
            (repo / "code.py").write_text("value = 3\n", encoding="utf-8", newline="\n")
            return super().run(adapter, plan_path, repo, timeout, **kwargs)

    monkeypatch.setattr("issuekit.commands.review.AgentRunner", MutatingRunner)

    exit_code = cli.main(["review", "1", "--agent", "codex"])

    assert exit_code == 1
    assert "reviewer run modified the worktree" in capsys.readouterr().err
    assert [call["method"] for call in client.calls] == []

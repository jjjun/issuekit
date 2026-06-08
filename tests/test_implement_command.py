from dataclasses import dataclass
from pathlib import Path

import pytest

from issuekit import cli
from issuekit.commands.complete import complete_issue
from issuekit.config import IssuekitConfig
from issuekit.core import read_issues
from issuekit.workflow import WorkflowError
from tests.issue_helpers import issue_text, make_issue_tree, write_indexes, write_issue


@dataclass(frozen=True)
class FakeResult:
    exit_code: int = 0
    stdout_path: Path = Path("out.log")
    agent_log_path: Path = Path("agent.log")
    elapsed_sec: float = 1.25
    timed_out: bool = False
    parsed: dict[str, str] | None = None
    status_short: str | None = " M tracked.py\n?? new.py"
    status_path: Path | None = Path("status.json")


class FakeRunner:
    calls: list[tuple[object, Path, Path, float, str | None, int | None]] = []

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
        self.calls.append((adapter, plan_path, repo, timeout, agent_name, issue_id))
        return FakeResult(parsed={"resume_session_id": "abc123"})


def test_implement_command_resolves_issue_and_invokes_runner(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "default_reviewer = 'auto'\n",
        encoding="utf-8",
        newline="\n",
    )
    issues_dir = make_issue_tree(tmp_path)
    FakeRunner.calls.clear()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", FakeRunner)

    exit_code = cli.main(["implement", "1", "--agent", "kimi", "--timeout-sec", "12"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert len(FakeRunner.calls) == 1
    _, plan_path, repo, timeout, agent_name, issue_id = FakeRunner.calls[0]
    assert plan_path == issues_dir / "active" / "001_first.md"
    assert repo == tmp_path
    assert timeout == 12
    assert agent_name == "kimi"
    assert issue_id == 1
    assert "issue=1 file=active/001_first.md agent=kimi" in captured.out
    assert "status_file=status.json" in captured.out
    assert "--- git status --short ---" in captured.out
    assert "?? new.py" in captured.out
    assert "WARNING: implementation changes are unstaged and not committed." in captured.out
    assert "resume_session_id=abc123" in captured.out
    assert (
        "submitted_review id=1 file=active/001_first.md assignee= stage=review"
        in captured.out
    )
    [issue] = read_issues(issues_dir, "active")
    assert issue.assignee == ""
    assert issue.stage == "review"
    assert issue.implementer == "kimi"
    assert "## Handoff" in issue.file_path.read_text(encoding="utf-8")


def test_implement_command_does_not_commit_or_push(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "default_reviewer = 'auto'\n",
        encoding="utf-8",
        newline="\n",
    )
    make_issue_tree(tmp_path)

    def reject_commit_or_push(argv, *args, **kwargs):
        if list(argv[:2]) in (["git", "commit"], ["git", "push"]):
            raise AssertionError(f"unexpected git write command: {argv}")
        raise AssertionError(f"unexpected subprocess call: {argv}")

    monkeypatch.setattr("subprocess.run", reject_commit_or_push)

    class ModifyingRunner(FakeRunner):
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
            return FakeResult(status_short=" M tracked.py")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", ModifyingRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0


def test_implement_command_omits_uncommitted_warning_when_no_changes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "default_reviewer = 'auto'\n",
        encoding="utf-8",
        newline="\n",
    )
    make_issue_tree(tmp_path)

    class CleanRunner(FakeRunner):
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
            return FakeResult(status_short="")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", CleanRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0
    captured = capsys.readouterr()
    assert "No changes." in captured.out
    assert "WARNING: implementation changes" not in captured.out


def test_implement_command_does_not_submit_failed_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    issues_dir = make_issue_tree(tmp_path)

    class FailingRunner(FakeRunner):
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
            return FakeResult(exit_code=2, status_short=" M tracked.py")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", FailingRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 2
    [issue] = read_issues(issues_dir, "active")
    assert issue.assignee == "codex"
    assert issue.stage == "implementing"
    assert "## Handoff" not in issue.file_path.read_text(encoding="utf-8")


def test_implement_command_open_review_preserves_self_review_guard(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "default_reviewer = 'auto'\nrequire_distinct_reviewer = true\n",
        encoding="utf-8",
        newline="\n",
    )
    issues_dir = make_issue_tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", FakeRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0

    [issue] = read_issues(issues_dir, "active")
    assert issue.assignee == ""
    assert issue.stage == "review"
    assert issue.implementer == "codex"
    with pytest.raises(WorkflowError, match="self-review is not allowed"):
        complete_issue(
            issues_dir,
            1,
            reviewer="codex",
            config=IssuekitConfig(
                default_reviewer="auto",
                require_distinct_reviewer=True,
            ),
        )


def test_implement_command_reports_author_self_assignment(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(1, "First", assignee="codex", author="codex"),
    )
    write_indexes(issues_dir)
    FakeRunner.calls.clear()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", FakeRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    assert exit_code == 1
    assert not FakeRunner.calls
    assert "author self-implementation is not allowed" in capsys.readouterr().err


def test_implement_command_reports_missing_issue(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    make_issue_tree(tmp_path)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["implement", "99", "--agent", "kimi"])

    assert exit_code == 1
    assert "Active issue #99 was not found." in capsys.readouterr().err

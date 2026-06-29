from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys

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


def test_implement_command_no_longer_restores_agent_tracker_mutations(
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
    script = tmp_path / "mutate_tracker.py"
    script.write_text(
        "\n".join(
            [
                "import pathlib, shutil, sys",
                "plan = pathlib.Path(sys.argv[1])",
                "issues_dir = plan.parents[1]",
                "completed = issues_dir / 'completed' / plan.name",
                "completed.parent.mkdir(parents=True, exist_ok=True)",
                "shutil.move(str(plan), str(completed))",
                "(issues_dir / 'indexes' / 'active.md').write_text('mutated\\n', encoding='utf-8')",
                "(issues_dir / 'indexes' / 'stray.md').write_text('stray\\n', encoding='utf-8')",
            ]
        ),
        encoding="utf-8",
        newline="\n",
    )

    class MutatingAdapter:
        def resolve_binary(self) -> Path:
            return Path(sys.executable)

        def build_argv(self, prompt: str, plan_path: Path) -> list[str]:
            return [str(script), str(plan_path)]

        def parse_output(self, stdout: str, stderr: str) -> dict[str, str]:
            return {}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "issuekit.commands.implement.resolve_adapter",
        lambda *args, **kwargs: MutatingAdapter(),
    )

    exit_code = cli.main(["implement", "1", "--agent", "kimi", "--timeout-sec", "12"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "WARNING: implementer modified issue tracker" not in captured.err
    assert (issues_dir / "completed" / "001_first.md").exists()
    assert (issues_dir / "indexes" / "stray.md").exists()
    assert "moved to completed/001_first.md during implementation" in captured.err


def test_implement_command_reports_completed_move_when_submit_cannot_find_issue(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = make_issue_tree(tmp_path)

    class CompletingRunner(FakeRunner):
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
            completed_path = issues_dir / "completed" / plan_path.name
            completed_path.parent.mkdir(parents=True, exist_ok=True)
            plan_path.replace(completed_path)
            return FakeResult(status_short=" M code.py")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", CompletingRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "moved to completed/001_first.md during implementation" in captured.err
    assert "Implementers must not mutate docs/issues/ tracker state" in captured.err


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
        if list(argv[:2]) == ["git", "rev-parse"]:
            return subprocess.CompletedProcess(argv, 1, "", "")
        if list(argv[:3]) == ["git", "--no-pager", "diff"]:
            return subprocess.CompletedProcess(argv, 1, "", "")
        if list(argv[:3]) == ["git", "--no-pager", "status"]:
            return subprocess.CompletedProcess(argv, 1, "", "")
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


def test_implement_command_mojibake_gate_blocks_submit(
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
    (tmp_path / "code.py").write_text("print('clean')\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)

    class MojibakeRunner(FakeRunner):
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
            (repo / "code.py").write_text(
                "comment = '\u7e67'\n",
                encoding="utf-8",
                newline="\n",
            )
            return FakeResult(status_short=" M code.py")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", MojibakeRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "mojibake gate blocked submit_for_review" in captured.err
    assert "- code.py" in captured.err
    [issue] = read_issues(issues_dir, "active")
    assert issue.assignee == "codex"
    assert issue.stage == "implementing"


def test_implement_command_blocks_tracker_only_git_changes(
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
    _init_git_repo(tmp_path)

    class TrackerOnlyRunner(FakeRunner):
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
            (issues_dir / "active" / "tracker_note.txt").write_text(
                "tracker only\n",
                encoding="utf-8",
                newline="\n",
            )
            return FakeResult(status_short="?? docs/issues/active/tracker_note.txt")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", TrackerOnlyRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "agent produced no implementation changes; not submitting for review" in captured.err
    [issue] = read_issues(issues_dir, "active")
    assert issue.assignee == "codex"
    assert issue.stage == "implementing"


def test_implement_command_git_non_tracker_change_still_submits(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "default_reviewer = 'auto'\n",
        encoding="utf-8",
        newline="\n",
    )
    issues_dir = make_issue_tree(tmp_path)
    _init_git_repo(tmp_path)

    class CodeChangingRunner(FakeRunner):
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
            (repo / "new.py").write_text("print('changed')\n", encoding="utf-8", newline="\n")
            return FakeResult(status_short="?? new.py")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", CodeChangingRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0
    [issue] = read_issues(issues_dir, "active")
    assert issue.stage == "review"


def test_implement_command_mojibake_gate_ignores_tracker_paths_with_code_change(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "default_reviewer = 'auto'\n",
        encoding="utf-8",
        newline="\n",
    )
    issues_dir = make_issue_tree(tmp_path)
    _init_git_repo(tmp_path)

    class TrackerAndCodeRunner(FakeRunner):
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
            (issues_dir / "active" / "tracker_note.txt").write_text(
                "\u7e67\n",
                encoding="utf-8",
                newline="\n",
            )
            (repo / "new.py").write_text("print('clean')\n", encoding="utf-8", newline="\n")
            return FakeResult(status_short="?? docs/issues/active/tracker_note.txt\n?? new.py")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", TrackerAndCodeRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0
    [issue] = read_issues(issues_dir, "active")
    assert issue.stage == "review"


def test_implement_command_diff_shape_warning_does_not_block(
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
    (tmp_path / "code.py").write_text(
        "".join(f"line {index}\n" for index in range(50)),
        encoding="utf-8",
        newline="\n",
    )
    _init_git_repo(tmp_path)

    class DeletingRunner(FakeRunner):
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
            (repo / "code.py").write_text("line 0\n", encoding="utf-8", newline="\n")
            return FakeResult(status_short=" M code.py")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", DeletingRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "WARNING: heavy deletion diff detected: code.py deletes 49 lines" in captured.err
    [issue] = read_issues(issues_dir, "active")
    assert issue.stage == "review"


def test_implement_command_reinjects_review_feedback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        "default_reviewer = 'auto'\n",
        encoding="utf-8",
        newline="\n",
    )
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(
        issues_dir / "active" / "001_first.md",
        issue_text(
            1,
            "First",
            status="in_progress",
            assignee="codex",
            stage="changes_requested",
            implementer="codex",
        )
        + "\n## Review Feedback\n\n- Add tests only.\n",
    )
    write_indexes(issues_dir)

    class PromptRunner(FakeRunner):
        prompt_suffix: str | None = None

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
            self.prompt_suffix = kwargs.get("prompt_suffix")
            return FakeResult(status_short="")

    runner = PromptRunner()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", lambda: runner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0
    assert runner.prompt_suffix is not None
    assert "Address ONLY these notes" in runner.prompt_suffix
    assert "- Add tests only." in runner.prompt_suffix


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


def test_implement_rejects_invalid_issue_id(tmp_path: Path, monkeypatch, capsys) -> None:
    issues_dir = tmp_path / "docs" / "issues"
    write_issue(issues_dir / "active" / "001_first.md", issue_text(1, "First"))
    write_indexes(issues_dir)
    monkeypatch.chdir(tmp_path)

    exit_code = cli.main(["implement", "bad-id", "--agent", "codex"])

    assert exit_code == 1
    assert "Invalid issue id: bad-id" in capsys.readouterr().err

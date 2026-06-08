from dataclasses import dataclass
from pathlib import Path

from issuekit import cli
from tests.issue_helpers import make_issue_tree


@dataclass(frozen=True)
class FakeResult:
    exit_code: int = 0
    stdout_path: Path = Path("out.log")
    stderr_path: Path = Path("err.log")
    elapsed_sec: float = 1.25
    timed_out: bool = False
    parsed: dict[str, str] | None = None
    status_short: str | None = " M tracked.py\n?? new.py"


class FakeRunner:
    calls: list[tuple[object, Path, Path, float]] = []

    def run(self, adapter, plan_path: Path, repo: Path, timeout: float) -> FakeResult:
        self.calls.append((adapter, plan_path, repo, timeout))
        return FakeResult(parsed={"resume_session_id": "abc123"})


def test_implement_command_resolves_issue_and_invokes_runner(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    issues_dir = make_issue_tree(tmp_path)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", FakeRunner)

    exit_code = cli.main(["implement", "1", "--agent", "kimi", "--timeout-sec", "12"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert len(FakeRunner.calls) == 1
    _, plan_path, repo, timeout = FakeRunner.calls[0]
    assert plan_path == issues_dir / "active" / "001_first.md"
    assert repo == tmp_path
    assert timeout == 12
    assert "issue=1 file=active/001_first.md agent=kimi" in captured.out
    assert "--- git status --short ---" in captured.out
    assert "?? new.py" in captured.out
    assert "resume_session_id=abc123" in captured.out


def test_implement_command_does_not_commit_or_push(
    tmp_path: Path,
    monkeypatch,
) -> None:
    make_issue_tree(tmp_path)

    def reject_commit_or_push(argv, *args, **kwargs):
        if list(argv[:2]) in (["git", "commit"], ["git", "push"]):
            raise AssertionError(f"unexpected git write command: {argv}")
        raise AssertionError(f"unexpected subprocess call: {argv}")

    monkeypatch.setattr("subprocess.run", reject_commit_or_push)

    class ModifyingRunner(FakeRunner):
        def run(self, adapter, plan_path: Path, repo: Path, timeout: float) -> FakeResult:
            return FakeResult(status_short=" M tracked.py")

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", ModifyingRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0


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

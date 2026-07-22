from dataclasses import dataclass
from pathlib import Path
import subprocess

from issuekit import cli
from issuekit import store as store_module
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
    status_short: str | None = " M tracked.py\n?? new.py"
    status_path: Path | None = Path("status.json")


class FakeRunner:
    calls: list[tuple[object, Path, Path, float, str | None, int | None, str | None]] = []
    issuekit_sessions: list[str | None] = []

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
                adapter,
                plan_path,
                repo,
                timeout,
                agent_name,
                issue_id,
                kwargs.get("prompt_suffix"),
            )
        )
        self.issuekit_sessions.append(kwargs.get("issuekit_session"))
        return FakeResult(parsed={"resume_session_id": "abc123"})


def _configure_api(
    tmp_path: Path,
    monkeypatch,
    client: FakeIssuekitClient,
    *,
    extra_config: str = "",
) -> None:
    (tmp_path / "issuekit.toml").write_text(
        (
            "api_url = 'https://mine.example'\n"
            "project = 'demo'\n"
            "default_reviewer = 'auto'\n"
            f"{extra_config}"
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


def test_implement_command_materializes_api_issue_and_submits_review(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude", body="# Issue #1: First\n")])
    FakeRunner.calls.clear()
    FakeRunner.issuekit_sessions.clear()
    _configure_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", FakeRunner)

    exit_code = cli.main(["implement", "1", "--agent", "kimi", "--timeout-sec", "12"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert len(FakeRunner.calls) == 1
    _, plan_path, repo, timeout, agent_name, issue_id, prompt_suffix = FakeRunner.calls[0]
    assert plan_path == tmp_path / ".agent-runs" / "issue-1.md"
    assert plan_path.read_text(encoding="utf-8") == "# Issue #1: First\n"
    assert repo == tmp_path
    assert timeout == 12
    assert agent_name == "kimi"
    assert issue_id == 1
    assert prompt_suffix is None
    assert FakeRunner.issuekit_sessions[0] is not None
    assert FakeRunner.issuekit_sessions[0].startswith("run-")
    assert "issue=1 ref=demo#1 agent=kimi" in captured.out
    assert "submitted_review id=1 ref=demo#1 assignee= stage=review" in captured.out
    run_session = FakeRunner.issuekit_sessions[0]
    assert client.calls[0] == {
        "method": "claim",
        "number": 1,
        "body": {"assignee": "kimi", "session": run_session},
    }
    assert client.calls[-1]["method"] == "submit"
    assert client.calls[-1]["body"]["session"] == run_session
    assert client.calls[-1]["body"]["summary"] == (
        "Implemented by kimi via issuekit implement "
        "(orchestrated by issuekit@unregistered-worker)."
    )


def test_implement_command_blocks_wrong_work_branch_before_agent(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    FakeRunner.calls.clear()
    _configure_api(tmp_path, monkeypatch, client, extra_config="work_branch = 'main'\n")
    monkeypatch.setattr("issuekit.branch_guard.git_current_branch", lambda cwd: "feature")
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", FakeRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    assert exit_code == 1
    assert "Work-branch guard blocks claim issue #1" in capsys.readouterr().err
    assert FakeRunner.calls == []
    assert client.calls == []


def test_implement_command_does_not_commit_or_push(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    _configure_api(tmp_path, monkeypatch, client)

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
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", FakeRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0


def test_implement_command_mojibake_gate_blocks_submit(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    _configure_api(tmp_path, monkeypatch, client)
    (tmp_path / "code.py").write_text("print('clean')\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)

    class MojibakeRunner(FakeRunner):
        def run(self, adapter, plan_path, repo, timeout, **kwargs) -> FakeResult:
            (repo / "code.py").write_text(
                "comment = '\u7e67\uff62\u7e5d\u4e5d\u0393'\n", encoding="utf-8", newline="\n"
            )
            return FakeResult(status_short=" M code.py")

    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", MojibakeRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "mojibake gate blocked submit_for_review" in captured.err
    assert "- code.py:1:12: U+7E67" in captured.err
    assert "recovers to U+" in captured.err
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_mojibake_gate_allows_legitimate_japanese(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    _configure_api(tmp_path, monkeypatch, client)
    (tmp_path / "code.py").write_text(
        "title = '\u95be\u5024'\nvalue = 1\n", encoding="utf-8", newline="\n"
    )
    _init_git_repo(tmp_path)

    class JapaneseRunner(FakeRunner):
        def run(self, adapter, plan_path, repo, timeout, **kwargs) -> FakeResult:
            (repo / "code.py").write_text(
                "title = '\u95be\u5024'\nvalue = 2\n", encoding="utf-8", newline="\n"
            )
            return FakeResult(status_short=" M code.py")

    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", JapaneseRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0
    assert [call["method"] for call in client.calls] == ["claim", "submit"]


def test_implement_command_mojibake_gate_blocks_unconfirmed_changed_text(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    _configure_api(tmp_path, monkeypatch, client)
    (tmp_path / "code.py").write_text("value = 1\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)

    class LossyRunner(FakeRunner):
        def run(self, adapter, plan_path, repo, timeout, **kwargs) -> FakeResult:
            (repo / "code.py").write_text(
                "value = 1\ntitle = '\u7e5d\u30fb\u305b\u7e5d\u533b\u3044\u7e5d\u4e5d\u03931'\n",
                encoding="utf-8",
                newline="\n",
            )
            return FakeResult(status_short=" M code.py")

    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", LossyRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 1
    captured = capsys.readouterr()
    assert "- code.py:2:10: U+7E5D" in captured.err
    assert "failed CP932 reverse confirmation" in captured.err
    assert "add its repo-relative path to check_encoding_exclude" in captured.err
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_mojibake_gate_allows_excluded_legitimate_japanese(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    _configure_api(
        tmp_path,
        monkeypatch,
        client,
        extra_config="check_encoding_exclude = ['titles/**']\n",
    )
    title_path = tmp_path / "titles" / "anime.py"
    title_path.parent.mkdir()
    title_path.write_text("title = 'clean'\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)

    class JapaneseRunner(FakeRunner):
        def run(self, adapter, plan_path, repo, timeout, **kwargs) -> FakeResult:
            (repo / "titles" / "anime.py").write_text(
                "title = '\u87f2\u5e2b'\n", encoding="utf-8", newline="\n"
            )
            return FakeResult(status_short=" M titles/anime.py")

    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", JapaneseRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0
    assert [call["method"] for call in client.calls] == ["claim", "submit"]


def test_implement_command_mojibake_gate_blocks_confirmed_excluded_text(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    _configure_api(
        tmp_path,
        monkeypatch,
        client,
        extra_config="check_encoding_exclude = ['titles/**']\n",
    )
    title_path = tmp_path / "titles" / "anime.py"
    title_path.parent.mkdir()
    title_path.write_text("title = 'clean'\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)

    class MojibakeRunner(FakeRunner):
        def run(self, adapter, plan_path, repo, timeout, **kwargs) -> FakeResult:
            (repo / "titles" / "anime.py").write_text(
                "title = '\u7e67\uff62\u7e5d\u4e5d\u0393'\n",
                encoding="utf-8",
                newline="\n",
            )
            return FakeResult(status_short=" M titles/anime.py")

    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", MojibakeRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 1
    assert "recovers to U+" in capsys.readouterr().err
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_mojibake_gate_ignores_unchanged_corruption(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    _configure_api(tmp_path, monkeypatch, client)
    (tmp_path / "code.py").write_text(
        "comment = '\u8389'\nvalue = 1\n", encoding="utf-8", newline="\n"
    )
    _init_git_repo(tmp_path)

    class UnchangedCorruptionRunner(FakeRunner):
        def run(self, adapter, plan_path, repo, timeout, **kwargs) -> FakeResult:
            (repo / "code.py").write_text(
                "comment = '\u8389'\nvalue = 2\n", encoding="utf-8", newline="\n"
            )
            return FakeResult(status_short=" M code.py")

    monkeypatch.setattr(
        "issuekit.commands.implement.AgentRunner", UnchangedCorruptionRunner
    )

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0
    assert [call["method"] for call in client.calls] == ["claim", "submit"]


def test_implement_command_mojibake_gate_allows_configured_halfwidth_kana(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    _configure_api(
        tmp_path,
        monkeypatch,
        client,
        extra_config="gate_halfwidth_kana = false\n",
    )
    (tmp_path / "code.py").write_text(
        "print('clean')\n",
        encoding="utf-8",
        newline="\n",
    )
    _init_git_repo(tmp_path)

    class HalfwidthKanaRunner(FakeRunner):
        def run(self, adapter, plan_path, repo, timeout, **kwargs) -> FakeResult:
            (repo / "code.py").write_text(
                "comment = '\uff71'\n",
                encoding="utf-8",
                newline="\n",
            )
            return FakeResult(status_short=" M code.py")

    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", HalfwidthKanaRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0
    assert [call["method"] for call in client.calls] == ["claim", "submit"]


def test_implement_command_blocks_when_git_has_no_implementation_changes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    _configure_api(tmp_path, monkeypatch, client)
    (tmp_path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)

    class CleanRunner(FakeRunner):
        def run(self, adapter, plan_path, repo, timeout, **kwargs) -> FakeResult:
            return FakeResult(status_short="")

    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", CleanRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "agent produced no implementation changes; not submitting for review" in captured.err
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_accepts_agent_side_review_when_no_changes(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    _configure_api(tmp_path, monkeypatch, client)
    (tmp_path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)

    class AgentSubmittingRunner(FakeRunner):
        def run(self, adapter, plan_path, repo, timeout, **kwargs) -> FakeResult:
            client.submit(1, summary="Submitted by agent.")
            return FakeResult(status_short="")

    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", AgentSubmittingRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "already at review after the agent run" in captured.out
    assert "submitted_review id=1 ref=demo#1 assignee= stage=review" in captured.out
    assert [call["method"] for call in client.calls] == ["claim", "submit"]


def test_implement_command_allows_no_change_submit_with_flag(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    _configure_api(tmp_path, monkeypatch, client)
    (tmp_path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)

    class CleanRunner(FakeRunner):
        def run(self, adapter, plan_path, repo, timeout, **kwargs) -> FakeResult:
            return FakeResult(status_short="")

    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", CleanRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex", "--allow-no-changes"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "No implementation changes detected" in captured.out
    assert [call["method"] for call in client.calls] == ["claim", "submit"]


def test_implement_command_reinjects_review_feedback(
    tmp_path: Path,
    monkeypatch,
) -> None:
    body = "# Issue #1: First\n\n## Review Feedback\n\n- Add tests only.\n"
    client = FakeIssuekitClient(
        [
            api_issue(
                1,
                "First",
                status="in_progress",
                assignee="codex",
                stage="changes_requested",
                implementer="codex",
                author="claude",
                body=body,
            )
        ]
    )
    FakeRunner.calls.clear()
    _configure_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", FakeRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0
    prompt_suffix = FakeRunner.calls[0][6]
    assert prompt_suffix is not None
    assert "Address ONLY these notes" in prompt_suffix
    assert "- Add tests only." in prompt_suffix


def test_implement_command_does_not_submit_failed_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    _configure_api(tmp_path, monkeypatch, client)

    class FailingRunner(FakeRunner):
        def run(self, adapter, plan_path, repo, timeout, **kwargs) -> FakeResult:
            return FakeResult(exit_code=2, status_short=" M tracked.py")

    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", FailingRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 2
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_reports_author_self_assignment(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", assignee="codex", author="codex")])
    FakeRunner.calls.clear()
    _configure_api(tmp_path, monkeypatch, client)
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", FakeRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    assert exit_code == 1
    assert not FakeRunner.calls
    assert "self-implementation is not allowed" in capsys.readouterr().err


def test_implement_command_reports_missing_issue(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _configure_api(tmp_path, monkeypatch, FakeIssuekitClient())

    exit_code = cli.main(["implement", "99", "--agent", "kimi"])

    assert exit_code == 1
    assert "Active issue #99 was not found." in capsys.readouterr().err


def test_implement_rejects_invalid_issue_id(tmp_path: Path, monkeypatch, capsys) -> None:
    _configure_api(tmp_path, monkeypatch, FakeIssuekitClient())

    exit_code = cli.main(["implement", "bad-id", "--agent", "codex"])

    assert exit_code == 1
    assert "Invalid issue id: bad-id" in capsys.readouterr().err

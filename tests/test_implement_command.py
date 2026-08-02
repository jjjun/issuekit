import subprocess
from dataclasses import dataclass, replace
from pathlib import Path

from issuekit import cli
from issuekit import store as store_module
from issuekit.agentrun import AgentPrompt
from issuekit.agents import run_claimed as run_claimed_agent
from issuekit.agents.run_claimed import review_feedback_prompt
from issuekit.config import IssuekitConfig
from issuekit.core import Issue
from issuekit.gitutil import GitStatusEntry
from issuekit.testing import FakeIssuekitClient
from tests.issue_helpers import api_issue


def test_review_feedback_prompt_stops_at_immediate_next_section() -> None:
    assert (
        review_feedback_prompt(
            "## Review Feedback\n\n## Implementation Notes\n\nDo not include this."
        )
        is None
    )


def test_rename_across_issues_directory_is_an_implementation_change(
    tmp_path: Path,
) -> None:
    issues_dir = tmp_path / "issues"
    snapshot = run_claimed_agent.ImplementationChangeSnapshot(
        root=tmp_path,
        status_entries=(
            GitStatusEntry(
                status="R ",
                path=Path("issues/moved.py"),
                original_path=Path("code.py"),
            ),
        ),
        changed_paths=(Path("issues/moved.py"), Path("code.py")),
        readable_paths=(Path("issues/moved.py"),),
    )

    assert run_claimed_agent._implementation_entries(
        snapshot,
        tmp_path,
        issues_dir,
    )


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
    report_path: Path | None = None


class FakeRunner:
    calls: list[
        tuple[object, AgentPrompt, Path, float, str | None, int | None, str | None]
    ] = []
    issuekit_sessions: list[str | None] = []

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
                adapter,
                prompt,
                repo,
                timeout,
                agent_name,
                issue_id,
                kwargs.get("prompt_suffix"),
            )
        )
        self.issuekit_sessions.append(kwargs.get("issuekit_session"))
        return FakeResult(parsed={"resume_session_id": "abc123"})


class CloseTrackingClient(FakeIssuekitClient):
    def __init__(self) -> None:
        super().__init__()
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


class SelectionAdapter:
    def effective_runtime(self) -> tuple[None, None]:
        return None, None


def _claimed_issue() -> Issue:
    return Issue(
        id=1,
        ref="demo#1",
        title="First",
        issue_status="active",
        created="2026-01-01",
        completed="",
        priority="medium",
        assignee="codex",
        stage="implementing",
        implementer="codex",
        author="claude",
        body="# Issue #1: First\n",
        metadata={},
    )


def _stub_implementation_snapshot(
    tmp_path: Path,
) -> run_claimed_agent.ImplementationChangeSnapshot:
    return run_claimed_agent.ImplementationChangeSnapshot(
        root=tmp_path,
        status_entries=(),
        changed_paths=(),
        readable_paths=(),
    )


def test_implementation_prompt_names_implementer_report_channel(tmp_path: Path) -> None:
    prompt = run_claimed_agent.implementation_prompt(tmp_path / "issue-1.md")

    assert "$ISSUEKIT_IMPLEMENTER_REPORT_FILE" in prompt
    assert "answers to any reporting requests in the plan" in prompt


def test_run_and_submit_uses_agent_runner_by_default(
    tmp_path: Path, monkeypatch
) -> None:
    constructed: list[str] = []

    class SelectedAgentRunner(FakeRunner):
        def __init__(self) -> None:
            constructed.append("exec")

        def run(self, *args, **kwargs) -> FakeResult:
            return FakeResult(exit_code=1, status_short=None)

    monkeypatch.setattr(run_claimed_agent, "AgentRunner", SelectedAgentRunner)
    monkeypatch.setattr(
        run_claimed_agent, "resolve_adapter", lambda *args, **kwargs: SelectionAdapter()
    )
    monkeypatch.setattr(
        run_claimed_agent,
        "_implementation_change_snapshot",
        lambda cwd: _stub_implementation_snapshot(tmp_path),
    )

    outcome = run_claimed_agent.run_and_submit(
        _claimed_issue(),
        agent="codex",
        config=IssuekitConfig(),
        cwd=tmp_path,
        issues_dir=tmp_path / "issues",
        timeout=10,
    )

    assert outcome.exit_code == 1
    assert constructed == ["exec"]


def test_run_and_submit_selects_app_server_runner_when_opted_in(
    tmp_path: Path, monkeypatch
) -> None:
    constructed: list[tuple[IssuekitConfig, Issue, bool]] = []

    class SelectedAppServerRunner(FakeRunner):
        def __init__(
            self, config: IssuekitConfig, issue: Issue, *, recovery: bool
        ) -> None:
            constructed.append((config, issue, recovery))

        def run(self, *args, **kwargs) -> FakeResult:
            return FakeResult(exit_code=1, status_short=None)

    codex_config = replace(
        dict(IssuekitConfig().agents)["codex"], runtime="codex_app_server"
    )
    config = IssuekitConfig(agents=(("codex", codex_config),))
    monkeypatch.setattr(
        run_claimed_agent, "AppServerAttemptRunner", SelectedAppServerRunner
    )
    monkeypatch.setattr(
        run_claimed_agent, "resolve_adapter", lambda *args, **kwargs: SelectionAdapter()
    )
    monkeypatch.setattr(
        run_claimed_agent,
        "_implementation_change_snapshot",
        lambda cwd: _stub_implementation_snapshot(tmp_path),
    )

    outcome = run_claimed_agent.run_and_submit(
        _claimed_issue(),
        agent="codex",
        config=config,
        cwd=tmp_path,
        issues_dir=tmp_path / "issues",
        timeout=10,
    )

    assert outcome.exit_code == 1
    assert constructed == [(config, _claimed_issue(), False)]


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
    _, prompt, repo, timeout, agent_name, issue_id, prompt_suffix = FakeRunner.calls[0]
    assert prompt.path == tmp_path / ".agent-runs" / "issue-1.md"
    assert prompt.body == "# Issue #1: First\n"
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
        "(orchestrated by issuekit@unregistered-worker).\n"
        "Run log: `out.log`"
    )


def test_submission_summary_includes_sanitized_implementer_report(tmp_path: Path) -> None:
    report_path = tmp_path / ".agent-runs" / "run.report.md"
    report_path.parent.mkdir()
    report_path.write_text(
        "Verified guard \u2014 passed.\n\u65e5\u672c\u8a9e",
        encoding="utf-8",
        newline="\n",
    )
    result = FakeResult(
        stdout_path=tmp_path / ".agent-runs" / "run.out.log",
        report_path=report_path,
    )

    summary = run_claimed_agent._submission_summary("Implemented.", result, tmp_path)

    assert summary == (
        "Implemented.\n"
        "Run log: `.agent-runs/run.out.log`\n\n"
        "Implementer report:\n"
        "Verified guard - passed."
    )
    assert summary.isascii()


def test_submission_summary_sanitizes_non_ascii_run_log_path() -> None:
    result = FakeResult(
        stdout_path=Path("D:/\u65e5\u672c\u8a9e/runs/run.out.log"),
    )

    summary = run_claimed_agent._submission_summary(
        "Implemented.",
        result,
        Path("G:/workspace/projects/issuekit"),
    )

    assert summary.isascii()


def test_submission_summary_bounds_implementer_report(tmp_path: Path) -> None:
    report_path = tmp_path / "run.report.md"
    report_path.write_text(
        "x" * (run_claimed_agent.MAX_IMPLEMENTER_REPORT_CHARS + 1),
        encoding="utf-8",
    )
    result = FakeResult(stdout_path=tmp_path / "run.out.log", report_path=report_path)

    summary = run_claimed_agent._submission_summary("Implemented.", result, tmp_path)
    included_report = summary.split("Implementer report:\n", 1)[1]

    assert len(included_report) == run_claimed_agent.MAX_IMPLEMENTER_REPORT_CHARS
    assert included_report.endswith("[Implementer report truncated; see run log.]")


def test_implement_command_uses_default_implementer(tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    FakeRunner.calls.clear()
    _configure_api(tmp_path, monkeypatch, client, extra_config="default_implementer = 'kimi'\n")
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", FakeRunner)

    assert cli.main(["implement", "1"]) == 0
    assert "agent=kimi" in capsys.readouterr().out


def test_implement_command_sends_effective_agent_runtime(tmp_path: Path, monkeypatch) -> None:
    client = FakeIssuekitClient(
        [api_issue(1, "First", author="claude"), api_issue(2, "Second", author="claude")]
    )
    _configure_api(
        tmp_path,
        monkeypatch,
        client,
        extra_config=(
            "[agents.codex]\n"
            "model = 'configured-model'\n"
            "reasoning_effort = 'medium'\n"
        ),
    )
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", FakeRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0

    assert client.calls[-1]["body"]["agent_model"] == "configured-model"
    assert client.calls[-1]["body"]["agent_reasoning_effort"] == "medium"

    assert cli.main(["implement", "2", "--agent", "codex", "--model", "run-model"]) == 0

    assert client.calls[-1]["body"].get("agent_model") == "run-model"
    assert client.calls[-1]["body"]["agent_reasoning_effort"] == "medium"


def test_implement_command_omits_agent_runtime_when_disabled(tmp_path: Path, monkeypatch) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    _configure_api(
        tmp_path,
        monkeypatch,
        client,
        extra_config="send_agent_runtime = false\n[agents.codex]\nmodel = 'configured-model'\n",
    )
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", FakeRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0

    assert "agent_model" not in client.calls[-1]["body"]
    assert "agent_reasoning_effort" not in client.calls[-1]["body"]


def test_implement_command_blocks_wrong_work_branch_before_agent(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    FakeRunner.calls.clear()
    _configure_api(tmp_path, monkeypatch, client, extra_config="work_branch = 'main'\n")
    monkeypatch.setattr("issuekit.guards.branch.git_current_branch", lambda cwd: "feature")
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
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "code.py").write_text(
                "comment = '\u7e67\uff62\u7e5d\u4e5d\u0393'\n", encoding="utf-8", newline="\n"
            )
            return FakeResult(status_short=" M code.py")

    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", MojibakeRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "mojibake gate blocked submit_for_review" in captured.err
    assert "uv run issuekit check-encoding --gate" in captured.err
    assert "- code.py:1:12: U+7E67" in captured.err
    assert "recovers to U+" in captured.err
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_mojibake_gate_scans_full_file_when_diff_fails(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    _configure_api(tmp_path, monkeypatch, client)
    (tmp_path / "code.py").write_text("print('clean')\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)

    class MojibakeRunner(FakeRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "code.py").write_text(
                "comment = '\u7e67\uff62\u7e5d\u4e5d\u0393'\n",
                encoding="utf-8",
                newline="\n",
            )
            return FakeResult(status_short=" M code.py")

    original_run_git = run_claimed_agent.run_git

    def fail_changed_line_diff(args, cwd, **kwargs):
        if "diff" in args and "--unified=0" in args:
            return None
        return original_run_git(args, cwd, **kwargs)

    monkeypatch.setattr(run_claimed_agent, "run_git", fail_changed_line_diff)
    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", MojibakeRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 1
    assert "mojibake gate blocked submit_for_review" in capsys.readouterr().err
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_mojibake_gate_blocks_non_ascii_path(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    _configure_api(tmp_path, monkeypatch, client)
    path = tmp_path / "日本語.py"
    path.write_text("print('clean')\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)

    class MojibakeRunner(FakeRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "日本語.py").write_text(
                "comment = '\u7e67\uff62\u7e5d\u4e5d\u0393'\n", encoding="utf-8", newline="\n"
            )
            return FakeResult(status_short=" M 日本語.py")

    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", MojibakeRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 1
    assert "- 日本語.py:1:12: U+7E67" in capsys.readouterr().err
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
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
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
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
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


def test_check_encoding_gate_matches_submit_gate_for_issue_308_tree(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    _configure_api(tmp_path, monkeypatch, client)
    test_path = tmp_path / "tests" / "test_gitutil.py"
    test_path.parent.mkdir()
    test_path.write_text(
        "value = 'clean'\n",
        encoding="utf-8",
        newline="\n",
    )
    _init_git_repo(tmp_path)
    gate_exit_codes: list[int] = []

    class IncidentRunner(FakeRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "tests" / "test_gitutil.py").write_text(
                "value = '\u8b4c'\n",
                encoding="utf-8",
                newline="\n",
            )
            gate_exit_codes.append(cli.main(["check-encoding", "--gate"]))
            return FakeResult(status_short=" M tests/test_gitutil.py")

    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", IncidentRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 1
    assert gate_exit_codes == [1]
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
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
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
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "titles" / "anime.py").write_text(
                "title = '\u7e67\uff62\u7e5d\u4e5d\u0393'\n",
                encoding="utf-8",
                newline="\n",
            )
            return FakeResult(status_short=" M titles/anime.py")

    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", MojibakeRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 1
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_mojibake_gate_scans_file_without_source_extension(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    _configure_api(tmp_path, monkeypatch, client)
    (tmp_path / "script.sh").write_text(
        "echo clean\n",
        encoding="utf-8",
        newline="\n",
    )
    _init_git_repo(tmp_path)

    class MojibakeRunner(FakeRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "script.sh").write_text(
                "echo '\u7e67\uff62\u7e5d\u4e5d\u0393'\n",
                encoding="utf-8",
                newline="\n",
            )
            return FakeResult(status_short=" M script.sh")

    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", MojibakeRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 1
    assert "- script.sh:1:7: U+7E67" in capsys.readouterr().err
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_mojibake_gate_reports_invalid_utf8(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    _configure_api(tmp_path, monkeypatch, client)
    (tmp_path / "code.py").write_text(
        "value = 'clean'\n",
        encoding="utf-8",
        newline="\n",
    )
    _init_git_repo(tmp_path)

    class InvalidUtf8Runner(FakeRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "code.py").write_bytes(b"value = '\xff'\n")
            return FakeResult(status_short=" M code.py")

    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", InvalidUtf8Runner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 1
    assert "- code.py:1:1: invalid UTF-8" in capsys.readouterr().err
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
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
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
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
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
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            return FakeResult(status_short="")

    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", CleanRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "agent produced no implementation changes; not submitting for review" in captured.err
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_submits_deletion_only_change(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "Delete old code", author="claude")])
    _configure_api(tmp_path, monkeypatch, client)
    deleted_path = tmp_path / "obsolete.py"
    deleted_path.write_text("obsolete = True\n", encoding="utf-8", newline="\n")
    _init_git_repo(tmp_path)

    class DeletingRunner(FakeRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            deleted_path.unlink()
            return FakeResult(status_short=" D obsolete.py")

    monkeypatch.setattr("issuekit.commands.implement.AgentRunner", DeletingRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0
    assert [call["method"] for call in client.calls] == ["claim", "submit"]


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
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
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
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
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
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
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
    client = CloseTrackingClient()
    _configure_api(tmp_path, monkeypatch, client)

    exit_code = cli.main(["implement", "99", "--agent", "kimi"])

    assert exit_code == 1
    assert "Active issue #99 was not found." in capsys.readouterr().err
    assert client.close_count == 1


def test_implement_rejects_invalid_issue_id(tmp_path: Path, monkeypatch, capsys) -> None:
    _configure_api(tmp_path, monkeypatch, FakeIssuekitClient())

    exit_code = cli.main(["implement", "bad-id", "--agent", "codex"])

    assert exit_code == 1
    assert "Invalid issue id: bad-id" in capsys.readouterr().err

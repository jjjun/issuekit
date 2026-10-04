import json
import os
import signal
import subprocess
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from issuekit import cli
from issuekit.agentrun import AgentBinaryNotFoundError, AgentPrompt
from issuekit.agents import implementation_changes, implementer_flow, implementer_report
from issuekit.agents import run_claimed as run_claimed_agent
from issuekit.agents.handoff import (
    NO_IMPLEMENTATION_CHANGES_MARKER,
    review_feedback_prompt,
)
from issuekit.config import IssuekitConfig
from issuekit.core import Issue
from issuekit.errors import WorkflowError
from issuekit.gitutil import GitStatusEntry
from issuekit.testing import FakeIssuekitClient
from tests.agent_fakes import FakeResult
from tests.api_helpers import configure_api
from tests.git_helpers import init_git_repo
from tests.issue_helpers import api_issue


def test_review_feedback_prompt_keeps_markdown_headings_until_handoff() -> None:
    prompt = review_feedback_prompt(
        "## Review Feedback\n\n## Required changes\n\nFix this.\n\n"
        "## Handoff\n\nDo not include this."
    )

    assert prompt is not None
    assert "## Required changes\n\nFix this." in prompt
    assert "Do not include this." not in prompt


def test_implementation_entries_include_docs_issues_changes(
    tmp_path: Path,
) -> None:
    snapshot = implementation_changes.ImplementationChangeSnapshot(
        root=tmp_path,
        status_entries=(
            GitStatusEntry(
                status=" M",
                path=Path("docs/issues/x.md"),
            ),
        ),
        changed_paths=(Path("docs/issues/x.md"),),
        readable_paths=(Path("docs/issues/x.md"),),
    )

    assert [entry.path for entry in implementation_changes.implementation_entries(snapshot)] == [
        Path("docs/issues/x.md")
    ]


def test_snapshot_all_status_entries_includes_attributable_and_preexisting(
    tmp_path: Path,
) -> None:
    existing_path = tmp_path / "existing.py"
    existing_path.write_text("value = 1\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path, message="baseline", autocrlf=True)
    existing_path.write_text("value = 2\n", encoding="utf-8", newline="\n")

    fingerprint_before = implementation_changes.worktree_fingerprint(tmp_path)
    changed_path = tmp_path / "docs" / "issues" / "x.md"
    changed_path.parent.mkdir(parents=True)
    changed_path.write_text("value = 1\n", encoding="utf-8", newline="\n")
    snapshot = implementation_changes.implementation_change_snapshot(
        tmp_path, fingerprint_before
    )

    attributable = implementation_changes.implementation_entries(snapshot)
    assert [entry.path for entry in attributable] == [Path("docs/issues/x.md")]
    all_entries = implementation_changes.all_implementation_entries(snapshot)
    assert sorted(entry.path for entry in all_entries) == [
        Path("docs/issues/x.md"),
        Path("existing.py"),
    ]


class ImplementRunner:
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
        return FakeResult(
            parsed={"resume_session_id": "abc123"},
            status_short=" M tracked.py\n?? new.py",
        )


class CloseTrackingClient(FakeIssuekitClient):
    def __init__(self) -> None:
        super().__init__()
        self.close_count = 0

    def close(self) -> None:
        self.close_count += 1


class SelectionAdapter:
    def resolve_binary(self) -> Path:
        return Path("/test-bin/agent")

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
) -> implementation_changes.ImplementationChangeSnapshot:
    return implementation_changes.ImplementationChangeSnapshot(
        root=tmp_path,
        status_entries=(),
        changed_paths=(),
        readable_paths=(),
    )


def test_implementation_prompt_names_implementer_report_channel(tmp_path: Path) -> None:
    prompt = implementer_flow.implementation_prompt(tmp_path / "issue-1.md")

    assert "$ISSUEKIT_IMPLEMENTER_REPORT_FILE" in prompt
    assert "answers to any reporting requests in the plan" in prompt


def test_run_and_submit_uses_agent_runner_by_default(
    tmp_path: Path, monkeypatch
) -> None:
    constructed: list[str] = []

    class SelectedAgentRunner(ImplementRunner):
        def __init__(self) -> None:
            constructed.append("exec")

        def run(self, *args, **kwargs) -> FakeResult:
            return FakeResult(exit_code=1, status_short=None)

    monkeypatch.setattr(run_claimed_agent, "AgentRunner", SelectedAgentRunner)
    monkeypatch.setattr(
        implementer_flow, "resolve_adapter", lambda *args, **kwargs: SelectionAdapter()
    )
    monkeypatch.setattr(
        implementation_changes,
        "implementation_change_snapshot",
        lambda cwd, fingerprint_before: _stub_implementation_snapshot(tmp_path),
    )

    outcome = run_claimed_agent.run_and_submit(
        _claimed_issue(),
        agent="codex",
        config=IssuekitConfig(),
        cwd=tmp_path,
        timeout=10,
    )

    assert outcome.exit_code == 1
    assert constructed == ["exec"]


def test_run_and_submit_selects_app_server_runner_when_opted_in(
    tmp_path: Path, monkeypatch
) -> None:
    constructed: list[tuple[IssuekitConfig, Issue, bool]] = []

    class SelectedAppServerRunner(ImplementRunner):
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
        implementer_flow, "resolve_adapter", lambda *args, **kwargs: SelectionAdapter()
    )
    monkeypatch.setattr(
        implementation_changes,
        "implementation_change_snapshot",
        lambda cwd, fingerprint_before: _stub_implementation_snapshot(tmp_path),
    )

    outcome = run_claimed_agent.run_and_submit(
        _claimed_issue(),
        agent="codex",
        config=config,
        cwd=tmp_path,
        timeout=10,
    )

    assert outcome.exit_code == 1
    assert constructed == [(config, _claimed_issue(), False)]


def configure_implement_api(
    tmp_path: Path,
    monkeypatch,
    fake_api,
    client: FakeIssuekitClient,
    *,
    extra_config: str = "",
) -> None:
    configure_api(
        tmp_path,
        monkeypatch,
        fake_api,
        client,
        extra_config=extra_config,
    )
    monkeypatch.setattr(
        "issuekit.agentrun.adapter.shutil.which",
        lambda binary: f"/test-bin/{binary}",
    )


def test_implement_command_materializes_api_issue_and_submits_review(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude", body="# Issue #1: First\n")])
    ImplementRunner.calls.clear()
    ImplementRunner.issuekit_sessions.clear()
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ImplementRunner)

    exit_code = cli.main(["implement", "1", "--agent", "kimi", "--timeout-sec", "12"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert len(ImplementRunner.calls) == 1
    _, prompt, repo, timeout, agent_name, issue_id, prompt_suffix = ImplementRunner.calls[0]
    assert prompt.path == tmp_path / ".agent-runs" / "issue-1.md"
    assert prompt.body == "# Issue #1: First\n"
    assert repo == tmp_path
    assert timeout == 12
    assert agent_name == "kimi"
    assert issue_id == 1
    assert prompt_suffix is None
    assert ImplementRunner.issuekit_sessions[0] is not None
    assert ImplementRunner.issuekit_sessions[0].startswith("run-")
    assert "issue=1 ref=demo#1 agent=kimi" in captured.out
    assert "submitted_review id=1 ref=demo#1 assignee= stage=review" in captured.out
    run_session = ImplementRunner.issuekit_sessions[0]
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


def test_implement_command_selects_app_server_runtime(
    fake_api,
    tmp_path: Path, monkeypatch, capsys
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    constructed: list[str] = []

    class SelectedAgentRunner(ImplementRunner):
        def __init__(self) -> None:
            constructed.append("exec")

        def run(self, *args, **kwargs) -> FakeResult:
            return FakeResult(exit_code=1, status_short=None)

    class SelectedAppServerRunner(ImplementRunner):
        def __init__(
            self, config: IssuekitConfig, issue: Issue, *, recovery: bool
        ) -> None:
            constructed.append("app_server")

        def run(self, *args, **kwargs) -> FakeResult:
            return FakeResult(exit_code=1, status_short=None)

    configure_implement_api(
        tmp_path,
        monkeypatch,
        fake_api,
        client,
    )
    machine_path = tmp_path / "machine.toml"
    machine_path.write_text(
        "trusted_api_origins = ['https://mine.example']\n"
        "[agents.codex]\nruntime = 'codex_app_server'\n",
        encoding="utf-8",
        newline="\n",
    )
    monkeypatch.setenv("ISSUEKIT_CONFIG", str(machine_path))
    monkeypatch.setattr(run_claimed_agent, "AgentRunner", SelectedAgentRunner)
    monkeypatch.setattr(
        run_claimed_agent, "AppServerAttemptRunner", SelectedAppServerRunner
    )

    exit_code = cli.main(["implement", "1", "--agent", "codex", "--timeout-sec", "12"])

    assert exit_code == 1
    assert constructed == ["app_server"]
    assert "agent_exit_code=1" in capsys.readouterr().out


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

    summary = implementer_report.submission_summary("Implemented.", result, tmp_path)

    assert summary == (
        "Implemented.\n"
        "Run log: `.agent-runs/run.out.log`\n\n"
        "Implementer report:\n"
        "Verified guard - passed."
    )
    assert summary.isascii()


def test_submission_summary_marks_allowed_no_changes(tmp_path: Path) -> None:
    result = FakeResult(stdout_path=tmp_path / ".agent-runs" / "run.out.log")

    summary = implementer_report.submission_summary(
        "Implemented by codex via issuekit implement.",
        result,
        tmp_path,
        no_implementation_changes=True,
    )

    assert summary.splitlines() == [
        "Implemented by codex via issuekit implement.",
        "Run log: `.agent-runs/run.out.log`",
        NO_IMPLEMENTATION_CHANGES_MARKER,
    ]


def test_submission_summary_sanitizes_non_ascii_run_log_path() -> None:
    result = FakeResult(
        stdout_path=Path("D:/\u65e5\u672c\u8a9e/runs/run.out.log"),
    )

    summary = implementer_report.submission_summary(
        "Implemented.",
        result,
        Path("G:/workspace/projects/issuekit"),
    )

    assert summary.isascii()


def test_submission_summary_bounds_implementer_report(tmp_path: Path) -> None:
    report_path = tmp_path / "run.report.md"
    report_path.write_text(
        "x" * (implementer_report.MAX_IMPLEMENTER_REPORT_CHARS + 1),
        encoding="utf-8",
    )
    result = FakeResult(stdout_path=tmp_path / "run.out.log", report_path=report_path)

    summary = implementer_report.submission_summary("Implemented.", result, tmp_path)
    included_report = summary.split("Implementer report:\n", 1)[1]

    assert len(included_report) == implementer_report.MAX_IMPLEMENTER_REPORT_CHARS
    assert included_report.endswith("[Implementer report truncated; see run log.]")


def test_implement_command_uses_default_implementer(fake_api, tmp_path: Path, monkeypatch, capsys) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    ImplementRunner.calls.clear()
    configure_implement_api(tmp_path, monkeypatch, fake_api, client, extra_config="default_implementer = 'kimi'\n")
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ImplementRunner)

    assert cli.main(["implement", "1"]) == 0
    assert "agent=kimi" in capsys.readouterr().out


def test_implement_command_sends_effective_agent_runtime(fake_api, tmp_path: Path, monkeypatch) -> None:
    client = FakeIssuekitClient(
        [api_issue(1, "First", author="claude"), api_issue(2, "Second", author="claude")]
    )
    configure_implement_api(
        tmp_path,
        monkeypatch,
        fake_api,
        client,
        extra_config=(
            "[agents.codex]\n"
            "model = 'configured-model'\n"
            "reasoning_effort = 'medium'\n"
        ),
    )
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ImplementRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0

    assert client.calls[-1]["body"]["agent_model"] == "configured-model"
    assert client.calls[-1]["body"]["agent_reasoning_effort"] == "medium"

    assert cli.main(["implement", "2", "--agent", "codex", "--model", "run-model"]) == 0

    assert client.calls[-1]["body"].get("agent_model") == "run-model"
    assert client.calls[-1]["body"]["agent_reasoning_effort"] == "medium"


def test_implement_command_blocks_wrong_work_branch_before_agent(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    ImplementRunner.calls.clear()
    configure_implement_api(tmp_path, monkeypatch, fake_api, client, extra_config="work_branch = 'main'\n")
    monkeypatch.setattr("issuekit.guards.branch.git_current_branch", lambda cwd: "feature")
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ImplementRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    assert exit_code == 1
    assert "Work-branch guard blocks claim issue #1" in capsys.readouterr().err
    assert ImplementRunner.calls == []
    assert client.calls == []


def test_implement_command_does_not_commit_or_push(
    fake_api,
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)

    def reject_commit_or_push(argv, *args, **kwargs):
        if list(argv[:2]) in (["git", "commit"], ["git", "push"]):
            raise AssertionError(f"unexpected git write command: {argv}")
        if argv[0] == "git" and "rev-parse" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "")
        if argv[0] == "git" and "diff" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "")
        if list(argv[:2]) == ["git", "-c"] and "status" in argv:
            return subprocess.CompletedProcess(argv, 1, "", "")
        raise AssertionError(f"unexpected subprocess call: {argv}")

    monkeypatch.setattr("subprocess.run", reject_commit_or_push)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ImplementRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0


def test_implement_command_mojibake_gate_blocks_submit(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    (tmp_path / "code.py").write_text("print('clean')\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path, message="baseline", autocrlf=True)

    class MojibakeRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "code.py").write_text(
                "comment = '\u7e67\uff62\u7e5d\u4e5d\u0393'\n", encoding="utf-8", newline="\n"
            )
            return FakeResult(status_short=" M code.py")

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", MojibakeRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "mojibake gate blocked submit_for_review" in captured.err
    assert "uv run issuekit check-encoding --gate" in captured.err
    assert "- code.py:1:12: U+7E67" in captured.err
    assert "recovers to U+" in captured.err
    assert "check_encoding_exclude matches" not in captured.err
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_mojibake_gate_scans_full_file_when_diff_fails(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    (tmp_path / "code.py").write_text("print('clean')\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path, message="baseline", autocrlf=True)

    class MojibakeRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "code.py").write_text(
                "comment = '\u7e67\uff62\u7e5d\u4e5d\u0393'\n",
                encoding="utf-8",
                newline="\n",
            )
            return FakeResult(status_short=" M code.py")

    original_run_git = implementation_changes.run_git

    def fail_changed_line_diff(args, cwd, **kwargs):
        if "diff" in args and "--unified=0" in args:
            return None
        return original_run_git(args, cwd, **kwargs)

    monkeypatch.setattr(implementation_changes, "run_git", fail_changed_line_diff)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", MojibakeRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 1
    assert "mojibake gate blocked submit_for_review" in capsys.readouterr().err
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_mojibake_gate_blocks_non_ascii_path(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    path = tmp_path / "日本語.py"
    path.write_text("print('clean')\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path, message="baseline", autocrlf=True)

    class MojibakeRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "日本語.py").write_text(
                "comment = '\u7e67\uff62\u7e5d\u4e5d\u0393'\n", encoding="utf-8", newline="\n"
            )
            return FakeResult(status_short=" M 日本語.py")

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", MojibakeRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 1
    assert "- 日本語.py:1:12: U+7E67" in capsys.readouterr().err
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_mojibake_gate_allows_legitimate_japanese(
    fake_api,
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    (tmp_path / "code.py").write_text(
        "title = '\u95be\u5024'\nvalue = 1\n", encoding="utf-8", newline="\n"
    )
    init_git_repo(tmp_path, message="baseline", autocrlf=True)

    class JapaneseRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "code.py").write_text(
                "title = '\u95be\u5024'\nvalue = 2\n", encoding="utf-8", newline="\n"
            )
            return FakeResult(status_short=" M code.py")

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", JapaneseRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0
    assert [call["method"] for call in client.calls] == ["claim", "submit"]


def test_implement_command_mojibake_gate_blocks_unconfirmed_changed_text(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    (tmp_path / "code.py").write_text("value = 1\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path, message="baseline", autocrlf=True)

    class LossyRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "code.py").write_text(
                "value = 1\ntitle = '\u7e5d\u30fb\u305b\u7e5d\u533b\u3044\u7e5d\u4e5d\u03931'\n",
                encoding="utf-8",
                newline="\n",
            )
            return FakeResult(status_short=" M code.py")

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", LossyRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 1
    captured = capsys.readouterr()
    assert "- code.py:2:10: U+7E5D" in captured.err
    assert "failed CP932 reverse confirmation" in captured.err
    assert "add its repo-relative path to check_encoding_exclude" in captured.err
    assert [call["method"] for call in client.calls] == ["claim"]


def test_check_encoding_gate_matches_submit_gate_for_issue_308_tree(
    fake_api,
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    test_path = tmp_path / "tests" / "test_gitutil.py"
    test_path.parent.mkdir()
    test_path.write_text(
        "value = 'clean'\n",
        encoding="utf-8",
        newline="\n",
    )
    init_git_repo(tmp_path, message="baseline", autocrlf=True)
    gate_exit_codes: list[int] = []

    class IncidentRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "tests" / "test_gitutil.py").write_text(
                "value = '\u8b4c'\n",
                encoding="utf-8",
                newline="\n",
            )
            gate_exit_codes.append(cli.main(["check-encoding", "--gate"]))
            return FakeResult(status_short=" M tests/test_gitutil.py")

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", IncidentRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 1
    assert gate_exit_codes == [1]
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_mojibake_gate_allows_excluded_legitimate_japanese(
    fake_api,
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(
        tmp_path,
        monkeypatch,
        fake_api,
        client,
        extra_config="check_encoding_exclude = ['titles/**']\n",
    )
    title_path = tmp_path / "titles" / "anime.py"
    title_path.parent.mkdir()
    title_path.write_text("title = 'clean'\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path, message="baseline", autocrlf=True)

    class JapaneseRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "titles" / "anime.py").write_text(
                "title = '\u87f2\u5e2b'\n", encoding="utf-8", newline="\n"
            )
            return FakeResult(status_short=" M titles/anime.py")

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", JapaneseRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0
    assert [call["method"] for call in client.calls] == ["claim", "submit"]


def test_implement_command_mojibake_gate_blocks_confirmed_excluded_text(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(
        tmp_path,
        monkeypatch,
        fake_api,
        client,
        extra_config="check_encoding_exclude = ['titles/**']\n",
    )
    title_path = tmp_path / "titles" / "anime.py"
    title_path.parent.mkdir()
    title_path.write_text("title = 'clean'\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path, message="baseline", autocrlf=True)

    class MojibakeRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "titles" / "anime.py").write_text(
                "title = '\u7e67\uff62\u7e5d\u4e5d\u0393'\n",
                encoding="utf-8",
                newline="\n",
            )
            return FakeResult(status_short=" M titles/anime.py")

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", MojibakeRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 1
    captured = capsys.readouterr()
    assert "check_encoding_exclude matches 1 of these path(s)" in captured.err
    assert "suppress unconfirmed candidates only" in captured.err
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_mojibake_gate_scans_file_without_source_extension(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    (tmp_path / "script.sh").write_text(
        "echo clean\n",
        encoding="utf-8",
        newline="\n",
    )
    init_git_repo(tmp_path, message="baseline", autocrlf=True)

    class MojibakeRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "script.sh").write_text(
                "echo '\u7e67\uff62\u7e5d\u4e5d\u0393'\n",
                encoding="utf-8",
                newline="\n",
            )
            return FakeResult(status_short=" M script.sh")

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", MojibakeRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 1
    assert "- script.sh:1:7: U+7E67" in capsys.readouterr().err
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_mojibake_gate_reports_invalid_utf8(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    (tmp_path / "code.py").write_text(
        "value = 'clean'\n",
        encoding="utf-8",
        newline="\n",
    )
    init_git_repo(tmp_path, message="baseline", autocrlf=True)

    class InvalidUtf8Runner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "code.py").write_bytes(b"value = '\xff'\n")
            return FakeResult(status_short=" M code.py")

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", InvalidUtf8Runner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 1
    assert "- code.py:1:1: invalid UTF-8" in capsys.readouterr().err
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_mojibake_gate_ignores_unchanged_corruption(
    fake_api,
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    (tmp_path / "code.py").write_text(
        "comment = '\u8389'\nvalue = 1\n", encoding="utf-8", newline="\n"
    )
    init_git_repo(tmp_path, message="baseline", autocrlf=True)

    class UnchangedCorruptionRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "code.py").write_text(
                "comment = '\u8389'\nvalue = 2\n", encoding="utf-8", newline="\n"
            )
            return FakeResult(status_short=" M code.py")

    monkeypatch.setattr(
        "issuekit.agents.run_claimed.AgentRunner", UnchangedCorruptionRunner
    )

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0
    assert [call["method"] for call in client.calls] == ["claim", "submit"]


def test_implement_command_mojibake_gate_allows_configured_halfwidth_kana(
    fake_api,
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(
        tmp_path,
        monkeypatch,
        fake_api,
        client,
        extra_config="gate_halfwidth_kana = false\n",
    )
    (tmp_path / "code.py").write_text(
        "print('clean')\n",
        encoding="utf-8",
        newline="\n",
    )
    init_git_repo(tmp_path, message="baseline", autocrlf=True)

    class HalfwidthKanaRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "code.py").write_text(
                "comment = '\uff71'\n",
                encoding="utf-8",
                newline="\n",
            )
            return FakeResult(status_short=" M code.py")

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", HalfwidthKanaRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0
    assert [call["method"] for call in client.calls] == ["claim", "submit"]


def test_implement_command_blocks_when_git_has_no_implementation_changes(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    (tmp_path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path, message="baseline", autocrlf=True)

    class CleanRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            return FakeResult(status_short="")

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", CleanRunner)
    monkeypatch.setattr(
        implementer_report,
        "read_status",
        lambda path: SimpleNamespace(
            last_log_line="Blocked: no workspace runner.",
            failure_reason=None,
        ),
    )

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "agent produced no implementation changes; not submitting for review" in captured.err
    assert "Last agent log line: Blocked: no workspace runner." in captured.err
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_prefers_failure_reason_over_last_log_line(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    (tmp_path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path, message="baseline", autocrlf=True)

    class CleanRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            return FakeResult(status_short="")

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", CleanRunner)
    monkeypatch.setattr(
        implementer_report,
        "read_status",
        lambda path: SimpleNamespace(
            last_log_line="Ignoring N permissions.allow entries: workspace not trusted.",
            failure_reason="Failed to authenticate: OAuth session expired",
        ),
    )

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Agent failure reason: Failed to authenticate: OAuth session expired" in captured.err
    assert "workspace not trusted" not in captured.err
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_blocks_preexisting_dirty_worktree_without_agent_changes(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    modified_path = tmp_path / "modified.py"
    modified_path.write_text("value = 1\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path, message="baseline", autocrlf=True)
    modified_path.write_text("value = 2\n", encoding="utf-8", newline="\n")
    (tmp_path / "untracked.py").write_text("value = 3\n", encoding="utf-8", newline="\n")

    class CleanRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            return FakeResult(status_short=" M modified.py\n?? untracked.py")

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", CleanRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "agent produced no implementation changes; not submitting for review" in captured.err
    assert "this looks like a resumed run" in captured.err
    assert "--allow-no-changes" in captured.err
    assert [call["method"] for call in client.calls] == ["claim"]
    assert "not_submitted id=1 stage=implementing reason=no_changes" in captured.out
    assert (
        "HINT: the changes present in the worktree were made before this "
        "run started, not by this run; this looks like a resumed run" in captured.out
    )
    assert (
        "Submit them with `issuekit implement 1 --allow-no-changes` if they "
        "are complete." in captured.out
    )
    assert "HINT: a common cause of reason=no_changes or reason=missing_report" not in captured.out


def test_implement_command_warns_before_run_when_resuming_over_stale_prior_run(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from datetime import datetime, timedelta

    from issuekit.agentrun.status import STALE_AFTER_SEC, RunStatus, status_path, write_status

    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    (tmp_path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    modified_path = tmp_path / "modified.py"
    modified_path.write_text("value = 1\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path, message="baseline", autocrlf=True)
    modified_path.write_text("value = 2\n", encoding="utf-8", newline="\n")

    old = (datetime.now() - timedelta(seconds=STALE_AFTER_SEC + 30)).replace(
        microsecond=0
    ).isoformat()
    write_status(
        status_path(tmp_path / ".agent-runs", "20261004-100000"),
        RunStatus(
            run_id="20261004-100000",
            agent="codex",
            issue=1,
            status="running",
            pid=999,
            started_at=old,
            ended_at=None,
            elapsed_sec=None,
            exit_code=None,
            plan="docs/issues/active/001_first.md",
            stdout_log=".agent-runs/20261004-100000.out.log",
            agent_log=".agent-runs/20261004-100000.agent.log",
            heartbeat_at=old,
        ),
    )

    class CleanRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            return FakeResult(status_short=" M modified.py")

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", CleanRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert (
        "WARNING: the worktree already holds uncommitted changes, and this "
        "issue's most recent local run (run=20261004-100000) looks dead" in captured.out
    )
    assert "HINT: the changes present in the worktree were made" in captured.out


def test_implement_command_does_not_warn_when_prior_run_is_fresh(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from datetime import datetime

    from issuekit.agentrun.status import RunStatus, status_path, write_status

    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    (tmp_path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    modified_path = tmp_path / "modified.py"
    modified_path.write_text("value = 1\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path, message="baseline", autocrlf=True)
    modified_path.write_text("value = 2\n", encoding="utf-8", newline="\n")

    fresh = datetime.now().replace(microsecond=0).isoformat()
    write_status(
        status_path(tmp_path / ".agent-runs", "20261004-100001"),
        RunStatus(
            run_id="20261004-100001",
            agent="codex",
            issue=1,
            status="running",
            pid=999,
            started_at=fresh,
            ended_at=None,
            elapsed_sec=None,
            exit_code=None,
            plan="docs/issues/active/001_first.md",
            stdout_log=".agent-runs/20261004-100001.out.log",
            agent_log=".agent-runs/20261004-100001.agent.log",
            heartbeat_at=fresh,
        ),
    )

    class CleanRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            return FakeResult(status_short=" M modified.py")

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", CleanRunner)

    cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert "WARNING: the worktree already holds uncommitted changes" not in captured.out


def test_implement_command_warns_when_prior_run_already_reconciled_to_abandoned(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    from datetime import datetime, timedelta

    from issuekit.agentrun.status import STALE_AFTER_SEC, RunStatus, status_path, write_status

    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    (tmp_path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    modified_path = tmp_path / "modified.py"
    modified_path.write_text("value = 1\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path, message="baseline", autocrlf=True)
    modified_path.write_text("value = 2\n", encoding="utf-8", newline="\n")

    old = (datetime.now() - timedelta(seconds=STALE_AFTER_SEC + 30)).replace(
        microsecond=0
    ).isoformat()
    write_status(
        status_path(tmp_path / ".agent-runs", "20261004-100002"),
        RunStatus(
            run_id="20261004-100002",
            agent="codex",
            issue=1,
            status="abandoned",
            pid=999,
            started_at=old,
            ended_at=old,
            elapsed_sec=None,
            exit_code=None,
            plan="docs/issues/active/001_first.md",
            stdout_log=".agent-runs/20261004-100002.out.log",
            agent_log=".agent-runs/20261004-100002.agent.log",
            heartbeat_at=old,
            terminal_reason="heartbeat_lost",
        ),
    )

    class CleanRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            return FakeResult(status_short=" M modified.py")

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", CleanRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert (
        "WARNING: the worktree already holds uncommitted changes, and this "
        "issue's most recent local run (run=20261004-100002) looks dead" in captured.out
    )
    assert "HINT: the changes present in the worktree were made" in captured.out


def test_implement_command_submits_agent_change_with_preexisting_dirty_worktree(
    fake_api,
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    modified_path = tmp_path / "modified.py"
    modified_path.write_text("value = 1\n", encoding="utf-8", newline="\n")
    changed_path = tmp_path / "changed.py"
    changed_path.write_text("value = 1\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path, message="baseline", autocrlf=True)
    modified_path.write_text("value = 2\n", encoding="utf-8", newline="\n")
    (tmp_path / "untracked.py").write_text("value = 3\n", encoding="utf-8", newline="\n")

    class ChangingRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            changed_path.write_text("value = 4\n", encoding="utf-8", newline="\n")
            return FakeResult(
                status_short=" M changed.py\n M modified.py\n?? untracked.py"
            )

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ChangingRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0
    assert [call["method"] for call in client.calls] == ["claim", "submit"]


def test_implement_command_submits_deletion_only_change(
    fake_api,
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "Delete old code", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    deleted_path = tmp_path / "obsolete.py"
    deleted_path.write_text("obsolete = True\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path, message="baseline", autocrlf=True)

    class DeletingRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            deleted_path.unlink()
            return FakeResult(status_short=" D obsolete.py")

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", DeletingRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0
    assert [call["method"] for call in client.calls] == ["claim", "submit"]


def test_implement_command_accepts_agent_side_review_when_no_changes(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    (tmp_path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path, message="baseline", autocrlf=True)

    class AgentSubmittingRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            client.submit(1, summary="Submitted by agent.")
            return FakeResult(status_short="")

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", AgentSubmittingRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "already at review after the agent run" in captured.out
    assert "submitted_review id=1 ref=demo#1 assignee= stage=review" in captured.out
    assert [call["method"] for call in client.calls] == ["claim", "submit"]


def test_implement_command_allows_no_change_submit_with_flag(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    (tmp_path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path, message="baseline", autocrlf=True)

    class CleanRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            return FakeResult(status_short="")

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", CleanRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex", "--allow-no-changes"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "No implementation changes detected" in captured.out
    assert [call["method"] for call in client.calls] == ["claim", "submit"]
    summary_lines = client.calls[-1]["body"]["summary"].splitlines()
    assert summary_lines[1].startswith("Run log: ")
    assert summary_lines[2:] == [NO_IMPLEMENTATION_CHANGES_MARKER]


def test_implement_command_blocks_submit_when_report_is_missing(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    report_path = tmp_path / ".agent-runs" / "run.report.md"

    class NoReportRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "code.py").write_text("value = 1\n", encoding="utf-8", newline="\n")
            return FakeResult(status_short=" M code.py", report_path=report_path)

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", NoReportRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "implementer report missing; not submitting for review" in captured.err
    assert "not_submitted id=1 stage=implementing reason=missing_report" in captured.out
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_blocks_submit_when_report_is_whitespace_only(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    report_path = tmp_path / ".agent-runs" / "run.report.md"
    report_path.parent.mkdir()
    report_path.write_text("   \n", encoding="utf-8", newline="\n")

    class BlankReportRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "code.py").write_text("value = 1\n", encoding="utf-8", newline="\n")
            return FakeResult(status_short=" M code.py", report_path=report_path)

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", BlankReportRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "not_submitted id=1 stage=implementing reason=missing_report" in captured.out
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_allows_missing_report_with_flag(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    report_path = tmp_path / ".agent-runs" / "run.report.md"

    class NoReportRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "code.py").write_text("value = 1\n", encoding="utf-8", newline="\n")
            return FakeResult(status_short=" M code.py", report_path=report_path)

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", NoReportRunner)

    exit_code = cli.main(
        ["implement", "1", "--agent", "codex", "--allow-missing-report"]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Implementer report missing; submitting anyway" in captured.out
    assert [call["method"] for call in client.calls] == ["claim", "submit"]


def test_implement_command_treats_already_at_review_as_submitted_without_report(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    (tmp_path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path, message="baseline", autocrlf=True)
    report_path = tmp_path / ".agent-runs" / "run.report.md"

    class AgentSubmittingRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            client.submit(1, summary="Submitted by agent.")
            return FakeResult(status_short="", report_path=report_path)

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", AgentSubmittingRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "already at review after the agent run" in captured.out
    assert "submitted_review id=1 ref=demo#1 assignee= stage=review" in captured.out
    assert [call["method"] for call in client.calls] == ["claim", "submit"]


def test_implement_command_keeps_review_feedback_in_plan_body(
    fake_api,
    tmp_path: Path,
    monkeypatch,
) -> None:
    notes = "- " + "review note. " * 16_000
    body = f"# Issue #1: First\n\n## Review Feedback\n\n{notes}\n"
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
    ImplementRunner.calls.clear()
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)

    class PromptCapturingRunner(ImplementRunner):
        argvs: list[list[str]] = []

        def run(
            self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs
        ) -> FakeResult:
            self.argvs.append(adapter.build_argv(prompt.pointer, prompt.path))
            return super().run(adapter, prompt, repo, timeout, **kwargs)

    PromptCapturingRunner.argvs.clear()
    monkeypatch.setattr(
        "issuekit.agents.run_claimed.AgentRunner", PromptCapturingRunner
    )

    assert cli.main(["implement", "1", "--agent", "codex"]) == 0
    prompt = PromptCapturingRunner.calls[0][1]
    argv = PromptCapturingRunner.argvs[0]
    assert "## Review feedback to address" in prompt.body
    assert prompt.body.endswith(review_feedback_prompt(body) + "\n")
    assert (
        "The issue is back from review: address only the review feedback below, "
        "keeping the rest of the implementation as it is."
    ) in argv[1]
    assert "Implement it fully" not in argv[1]
    assert "review note." not in " ".join(argv)
    assert PromptCapturingRunner.calls[0][6] is None


def test_implement_command_does_not_submit_failed_run(
    fake_api,
    tmp_path: Path,
    monkeypatch,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)

    class FailingRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            return FakeResult(exit_code=2, status_short=" M tracked.py")

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", FailingRunner)

    assert cli.main(["implement", "1", "--agent", "codex"]) == 2
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_prints_post_run_line_on_successful_submit(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ImplementRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "not_submitted" not in captured.out
    assert (
        "post_run id=1 stage=review submitted=true agent_exit=0 cli_exit=0"
        in captured.out
    )


def test_implement_prints_post_run_when_agent_process_cannot_launch(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    real_popen = subprocess.Popen

    def fail_agent_launch(argv, *args, **kwargs):
        if argv[0] == "/test-bin/codex":
            raise PermissionError(13, "Permission denied", argv[0])
        return real_popen(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", fail_agent_launch)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert (
        "post_run id=1 stage=implementing submitted=false agent_exit=unknown cli_exit=1"
        in captured.out
    )
    assert "Traceback" not in captured.out + captured.err
    status_files = list((tmp_path / ".agent-runs").glob("*.status.json"))
    assert len(status_files) == 1
    assert (
        json.loads(status_files[0].read_text(encoding="utf-8"))["status"]
        == "failed"
    )


def test_implement_preflight_failure_does_not_claim_issue(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    issue_before = client.get_issue(1)

    exit_code = cli.main(
        ["implement", "1", "--agent", "kimi", "--reasoning-effort", "high"]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert all(call["method"] != "claim" for call in client.calls)
    assert client.get_issue(1) == issue_before
    assert "has no effort_argv" in captured.err
    assert "not_submitted id=1 stage= reason=run_error:" in captured.out
    assert (
        "post_run id=1 stage= submitted=false agent_exit=unknown cli_exit=1"
        in captured.out
    )


def test_implement_releases_claim_when_binary_disappears_after_preflight(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)

    class DisappearingBinaryAdapter:
        calls = 0

        def resolve_binary(self) -> Path:
            self.calls += 1
            if self.calls == 3:
                raise AgentBinaryNotFoundError(
                    "codex executable not found. Tried PATH and known per-OS locations."
                )
            return Path("/test-bin/codex")

        def effective_runtime(self) -> tuple[None, None]:
            return None, None

        def compose_prompt(self, prompt: str) -> str:
            return prompt

    def preflight(*args, **kwargs):
        adapter = DisappearingBinaryAdapter()
        adapter.resolve_binary()
        return adapter

    monkeypatch.setattr(
        "issuekit.commands.implement.preflight_agent",
        preflight,
    )

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert [call["method"] for call in client.calls] == ["claim", "reclaim"]
    released_issue = client.get_issue(1)
    assert released_issue["stage"] == "todo"
    assert released_issue["assignee"] == ""
    assert released_issue["worker"] == ""
    assert "claim_release id=1 result=reclaimed stage=todo" in captured.err
    assert "not_submitted id=1 stage=todo reason=run_error:" in captured.out


def test_implement_releases_claim_when_adapter_fails_after_preflight(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)

    class FailingAdapter:
        def resolve_binary(self) -> Path:
            return Path("/test-bin/codex")

        def effective_runtime(self) -> tuple[None, None]:
            raise ValueError("adapter runtime setup failed")

    def preflight(*args, **kwargs):
        adapter = FailingAdapter()
        adapter.resolve_binary()
        return adapter

    monkeypatch.setattr("issuekit.commands.implement.preflight_agent", preflight)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert [call["method"] for call in client.calls] == ["claim", "reclaim"]
    released_issue = client.get_issue(1)
    assert released_issue["stage"] == "todo"
    assert released_issue["assignee"] == ""
    assert released_issue["worker"] == ""
    assert "claim_release id=1 result=reclaimed stage=todo" in captured.err
    assert "not_submitted id=1 stage=todo reason=run_error:" in captured.out


def test_implement_command_prints_not_submitted_reason_for_failed_run(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)

    class FailingRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            return FakeResult(exit_code=2, status_short=" M tracked.py")

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", FailingRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert (
        "not_submitted id=1 stage=implementing reason=agent_failed" in captured.out
    )
    assert (
        "post_run id=1 stage=implementing submitted=false agent_exit=2 cli_exit=2"
        in captured.out
    )


def test_implement_command_rejects_zero_exit_error_envelope_and_reports_denied_tools(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)

    class ErrorEnvelopeRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            return FakeResult(
                parsed={
                    "is_error": "true",
                    "failure_reason": "Failed to authenticate",
                    "permission_denials": "2",
                    "permission_denied_tools": "Bash, Read",
                }
            )

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ErrorEnvelopeRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "permission_denied_tools=Bash, Read" in captured.out
    assert "not_submitted id=1 stage=implementing reason=agent_failed" in captured.out
    assert "post_run id=1 stage=implementing submitted=false agent_exit=0 cli_exit=1" in captured.out
    assert [call["method"] for call in client.calls] == ["claim"]


@pytest.mark.skipif(os.name == "nt", reason="SIGINT behavior is POSIX-specific")
def test_implement_command_reports_sigint_as_interrupted(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)

    def interrupt_run(*args, **kwargs):
        signal.raise_signal(signal.SIGINT)

    monkeypatch.setattr("issuekit.commands.implement.run_and_submit", interrupt_run)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 130
    assert "not_submitted id=1 stage=implementing reason=interrupted" in captured.out
    assert (
        "post_run id=1 stage=implementing submitted=false agent_exit=unknown cli_exit=130"
        in captured.out
    )


def test_implement_command_prints_not_submitted_reason_for_no_changes(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    (tmp_path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path, message="baseline", autocrlf=True)

    class CleanRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            return FakeResult(status_short="")

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", CleanRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "not_submitted id=1 stage=implementing reason=no_changes" in captured.out
    assert (
        "post_run id=1 stage=implementing submitted=false agent_exit=0 cli_exit=1"
        in captured.out
    )


def test_implement_command_prints_final_message_tail_and_hint_for_no_changes(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    (tmp_path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path, message="baseline", autocrlf=True)

    class StalledRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            return FakeResult(
                status_short="",
                parsed={
                    "stdout": (
                        "The baseline test run is executing in the "
                        "background; I'll be notified automatically when it "
                        "finishes. I won't poll - waiting now."
                    )
                },
            )

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", StalledRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "not_submitted id=1 stage=implementing reason=no_changes" in captured.out
    assert (
        "final_message_tail=The baseline test run is executing in the "
        "background; I'll be notified automatically when it finishes. "
        "I won't poll - waiting now." in captured.out
    )
    assert (
        "HINT: a common cause of reason=no_changes or reason=missing_report "
        "is a verification command started in the background" in captured.out
    )
    assert "retry with `issuekit implement 1`" in captured.out


def test_implement_command_prints_final_message_tail_for_missing_report(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    report_path = tmp_path / ".agent-runs" / "run.report.md"

    class StalledNoReportRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            (repo / "code.py").write_text("value = 1\n", encoding="utf-8", newline="\n")
            return FakeResult(
                status_short=" M code.py",
                report_path=report_path,
                parsed={"stdout": "I'll wait for the background test run to complete."},
            )

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", StalledNoReportRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "not_submitted id=1 stage=implementing reason=missing_report" in captured.out
    assert (
        "final_message_tail=I'll wait for the background test run to complete."
        in captured.out
    )
    assert "HINT: a common cause of reason=no_changes or reason=missing_report" in captured.out


def test_implement_command_truncates_long_final_message_tail(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    (tmp_path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path, message="baseline", autocrlf=True)
    long_message = "x" * 500

    class VerboseStalledRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            return FakeResult(status_short="", parsed={"stdout": long_message})

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", VerboseStalledRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert f"final_message_tail=...{'x' * 400}" in captured.out
    assert f"final_message_tail={'x' * 500}" not in captured.out


def test_implement_command_sanitizes_non_ascii_final_message_tail(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    (tmp_path / ".gitignore").write_text(".agent-runs/\n", encoding="utf-8", newline="\n")
    init_git_repo(tmp_path, message="baseline", autocrlf=True)

    class NonAsciiStalledRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            return FakeResult(
                status_short="",
                parsed={"stdout": "I’ll wait — no need to poll café."},
            )

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", NonAsciiStalledRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "final_message_tail=I'll wait - no need to poll cafe." in captured.out
    assert "post_run id=1" in captured.out


def test_implement_command_omits_final_message_tail_when_not_stall_shaped(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)

    class FailingRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            return FakeResult(
                exit_code=2,
                status_short=" M tracked.py",
                parsed={"stdout": "Should not be surfaced for agent_failed."},
            )

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", FailingRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "final_message_tail" not in captured.out
    assert "HINT: a common cause of reason=no_changes" not in captured.out


def test_implement_command_prints_submit_error_reason_when_guard_blocks_submit(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ImplementRunner)

    def raise_guard_error(*args, **kwargs):
        raise WorkflowError("Author-session guard blocks submit.")

    monkeypatch.setattr(implementer_flow, "submit_for_review", raise_guard_error)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Author-session guard blocks submit." in captured.err
    assert (
        "not_submitted id=1 stage=implementing "
        "reason=submit_error:Author-session guard blocks submit." in captured.out
    )
    assert (
        "post_run id=1 stage=implementing submitted=false agent_exit=0 cli_exit=1"
        in captured.out
    )


def test_implement_command_prints_unknown_stage_when_stage_lookup_also_fails(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    should_fail_lookup = {"value": False}

    class FlakyAfterSubmitClient(FakeIssuekitClient):
        def get_issue(self, number):
            if should_fail_lookup["value"]:
                raise WorkflowError("API unreachable.", code="request_failed")
            return super().get_issue(number)

    client = FlakyAfterSubmitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ImplementRunner)

    def raise_guard_error(*args, **kwargs):
        should_fail_lookup["value"] = True
        raise WorkflowError("Author-session guard blocks submit.")

    monkeypatch.setattr(implementer_flow, "submit_for_review", raise_guard_error)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "Author-session guard blocks submit." in captured.err
    assert (
        "not_submitted id=1 stage=unknown "
        "reason=submit_error:Author-session guard blocks submit." in captured.out
    )
    assert (
        "post_run id=1 stage=unknown submitted=false agent_exit=0 cli_exit=1"
        in captured.out
    )


def test_implement_command_prints_recovery_hint_for_startup_failure(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)

    class StartupFailureRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            return FakeResult(
                exit_code=1,
                status_short=None,
                parsed={
                    "is_error": "true",
                    "num_turns": "1",
                    "failure_reason": "Failed to authenticate: OAuth session expired",
                },
            )

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", StartupFailureRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "failure_reason=Failed to authenticate: OAuth session expired" in captured.out
    assert "HINT:" in captured.err
    assert "issuekit implement 1" in captured.err
    assert "issuekit reclaim 1" in captured.err
    assert [call["method"] for call in client.calls] == ["claim"]


def test_implement_command_omits_recovery_hint_when_usage_is_nonzero(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", author="claude")])
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)

    class RealFailureRunner(ImplementRunner):
        def run(self, adapter, prompt: AgentPrompt, repo, timeout, **kwargs) -> FakeResult:
            return FakeResult(
                exit_code=1,
                status_short=None,
                parsed={
                    "is_error": "true",
                    "num_turns": "12",
                    "usage_input_tokens": "500",
                },
            )

    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", RealFailureRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    assert exit_code == 1
    assert "HINT:" not in capsys.readouterr().err


def test_implement_command_reports_author_self_assignment(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeIssuekitClient([api_issue(1, "First", assignee="codex", author="codex")])
    ImplementRunner.calls.clear()
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)
    monkeypatch.setattr("issuekit.agents.run_claimed.AgentRunner", ImplementRunner)

    exit_code = cli.main(["implement", "1", "--agent", "codex"])

    assert exit_code == 1
    assert not ImplementRunner.calls
    assert "Same-name implementation is allowed only" in capsys.readouterr().err


def test_implement_command_reports_missing_issue(
    fake_api,
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = CloseTrackingClient()
    configure_implement_api(tmp_path, monkeypatch, fake_api, client)

    exit_code = cli.main(["implement", "99", "--agent", "kimi"])

    assert exit_code == 1
    assert "Active issue #99 was not found." in capsys.readouterr().err
    assert client.close_count == 1


def test_implement_rejects_invalid_issue_id(fake_api, tmp_path: Path, monkeypatch, capsys) -> None:
    configure_implement_api(tmp_path, monkeypatch, fake_api, FakeIssuekitClient())

    exit_code = cli.main(["implement", "bad-id", "--agent", "codex"])

    assert exit_code == 1
    assert "Invalid issue id: bad-id" in capsys.readouterr().err

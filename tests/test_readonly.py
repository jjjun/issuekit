from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from issuekit.agentrun import AgentPrompt, AgentResult
from issuekit.agents import readonly
from issuekit.agents.readonly import (
    prompt_from_spec,
    require_clean_run,
    run_readonly_evaluation,
)
from issuekit.prompts import TRIAGE_PROMPT
from issuekit.workflow import WorkflowError


def test_prompt_from_spec_rejects_unknown_keyword(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument 'context'"):
        prompt_from_spec(
            TRIAGE_PROMPT,
            cwd=tmp_path,
            filename="triage.md",
            body="Rendered prompt.",
            context="unused",
        )


def _init_git_repo(path: Path) -> None:
    (path / "tracked.txt").write_text("baseline\n", encoding="utf-8", newline="\n")
    for args in (
        ("init", "-q"),
        ("config", "user.email", "test@example.com"),
        ("config", "user.name", "Test User"),
        ("add", "."),
        ("commit", "-qm", "initial"),
    ):
        subprocess.run(["git", *args], cwd=path, check=True)


class ResultRunner:
    def __init__(
        self,
        *,
        mutate=None,
        exit_code: int = 0,
        timed_out: bool = False,
    ) -> None:
        self.mutate = mutate
        self.exit_code = exit_code
        self.timed_out = timed_out
        self.calls = 0

    def run(self, *args, **kwargs) -> AgentResult:
        self.calls += 1
        if self.mutate is not None:
            self.mutate()
        return AgentResult(
            exit_code=self.exit_code,
            stdout_path=Path("out.log"),
            agent_log_path=Path("agent.log"),
            elapsed_sec=0.1,
            timed_out=self.timed_out,
            parsed={"stdout": "ok"},
        )


def test_readonly_evaluation_fails_closed_before_launch_outside_git(
    tmp_path: Path,
) -> None:
    runner = ResultRunner()

    with pytest.raises(WorkflowError, match="repository root snapshot failed"):
        run_readonly_evaluation(
            agent="codex",
            adapter=object(),
            cwd=tmp_path,
            timeout=1,
            runner_factory=lambda: runner,
            prompt=AgentPrompt(tmp_path / "prompt.md", "body", "pointer"),
            label="Test",
            subject="subject",
        )

    assert runner.calls == 0


def test_readonly_evaluation_identifies_failed_status_snapshot(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _init_git_repo(tmp_path)
    runner = ResultRunner()
    monkeypatch.setattr(readonly, "git_status_entries", lambda *args, **kwargs: None)

    with pytest.raises(WorkflowError, match="worktree status snapshot failed"):
        run_readonly_evaluation(
            agent="codex",
            adapter=object(),
            cwd=tmp_path,
            timeout=1,
            runner_factory=lambda: runner,
            prompt=AgentPrompt(tmp_path / "prompt.md", "body", "pointer"),
            label="Test",
            subject="subject",
        )

    assert runner.calls == 0


@pytest.mark.parametrize(
    "relative_path",
    [
        Path(".agent-runs/pm-requests.json"),
        Path(".agent-runs/triage-author-state.json"),
        Path(".agent-runs/negotiations/thread.json"),
    ],
)
def test_readonly_evaluation_protects_durable_agent_run_state(
    tmp_path: Path,
    relative_path: Path,
) -> None:
    _init_git_repo(tmp_path)
    state_path = tmp_path / relative_path
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text("before\n", encoding="utf-8", newline="\n")
    runner = ResultRunner(
        mutate=lambda: state_path.write_text(
            "after\n",
            encoding="utf-8",
            newline="\n",
        )
    )

    run = run_readonly_evaluation(
        agent="codex",
        adapter=object(),
        cwd=tmp_path,
        timeout=1,
        runner_factory=lambda: runner,
        prompt=AgentPrompt(tmp_path / ".agent-runs/prompt.md", "body", "pointer"),
        label="Test",
        subject="subject",
    )

    assert run.repository_modified is True


@pytest.mark.parametrize(
    ("exit_code", "timed_out", "error_type"),
    [(0, True, TimeoutError), (2, False, RuntimeError)],
)
def test_failed_readonly_run_also_reports_repository_mutation(
    tmp_path: Path,
    capsys,
    exit_code,
    timed_out,
    error_type,
) -> None:
    _init_git_repo(tmp_path)
    changed_path = tmp_path / "tracked.txt"
    runner = ResultRunner(
        mutate=lambda: changed_path.write_text(
            "changed\n",
            encoding="utf-8",
            newline="\n",
        ),
        exit_code=exit_code,
        timed_out=timed_out,
    )
    run = run_readonly_evaluation(
        agent="codex",
        adapter=object(),
        cwd=tmp_path,
        timeout=1,
        runner_factory=lambda: runner,
        prompt=AgentPrompt(tmp_path / ".agent-runs/prompt.md", "body", "pointer"),
        label="Test",
        subject="subject",
    )

    with pytest.raises(error_type):
        require_clean_run(
            run,
            err=sys.stderr,
            mutation_log_message="ERROR: repository mutation detected.",
        )

    assert "repository mutation detected" in capsys.readouterr().err

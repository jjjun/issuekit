"""Run an already-claimed issue through an agent and submit it for review."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import TextIO

from issuekit.agentrun import (
    AgentBinaryNotFoundError,
    AgentPrompt,
    AgentResult,
    AgentRunner,
)
from issuekit.agentrun.adapter import AgentAdapter
from issuekit.agentrun.status import is_dead, list_statuses
from issuekit.agents import implementation_changes, implementer_flow
from issuekit.agents.app_server_runtime import AppServerAttemptRunner
from issuekit.agents.readonly import worktree_fingerprint
from issuekit.agents.registry import resolve_adapter
from issuekit.config import IssuekitConfig
from issuekit.core import Issue
from issuekit.gitutil import git_root, git_status_entries
from issuekit.guards.author import AuthorOrchestrationContext

RunReporter = Callable[[Issue, AgentResult], None]
RunnerFactory = Callable[[], AgentRunner]


def preflight_agent(
    agent: str,
    *,
    config: IssuekitConfig,
    model: str | None = None,
    reasoning_effort: str | None = None,
    role: str = "implementer",
) -> AgentAdapter:
    """Resolve the configured agent and verify its executable before claiming work."""

    adapter = resolve_adapter(
        agent,
        config=config,
        model=model,
        reasoning_effort=reasoning_effort,
        role=role,
    )
    # App Server uses this same adapter binary to launch the local runtime.
    adapter.resolve_binary()
    return adapter


def resumed_changes_hint(issue_id: int) -> str:
    """Return the recovery hint for a resumed run over unattributed worktree changes."""

    return (
        "HINT: the changes present in the worktree were made "
        "before this run started, not by this run; this looks "
        "like a resumed run over a previous attempt's unsubmitted "
        f"edits. Submit them with `issuekit implement {issue_id} "
        "--allow-no-changes` if they are complete."
    )


def run_and_submit(
    issue: Issue,
    *,
    agent: str,
    config: IssuekitConfig,
    cwd: Path,
    timeout: float,
    model: str | None = None,
    reasoning_effort: str | None = None,
    follow: bool = False,
    prompt_suffix: str | None = None,
    allow_no_changes: bool = False,
    allow_author_guard_override: bool = False,
    allow_any_branch: bool = False,
    session: str | None = None,
    orchestration: AuthorOrchestrationContext | None = None,
    submit_summary: str | None = None,
    abort_event: threading.Event | None = None,
    reporter: RunReporter | None = None,
    runner_factory: RunnerFactory | None = None,
    adapter: AgentAdapter | None = None,
    store=None,
    out: TextIO | None = None,
    err: TextIO | None = None,
    allow_missing_report: bool = False,
) -> implementer_flow.RunOutcome:
    """Run an agent for an already-claimed issue and submit successful work."""
    out = out or sys.stdout
    err = err or sys.stderr
    issue_id = issue.id
    if issue_id is None:
        raise ValueError("Claimed issue is missing an id.")

    options = implementer_flow.ImplementerRunOptions(
        agent=agent,
        config=config,
        cwd=cwd,
        timeout=timeout,
        model=model,
        reasoning_effort=reasoning_effort,
        follow=follow,
        prompt_suffix=prompt_suffix,
        allow_no_changes=allow_no_changes,
        allow_any_branch=allow_any_branch,
        allow_author_guard_override=allow_author_guard_override,
        session=session,
        orchestration=orchestration,
        submit_summary=submit_summary,
        abort_event=abort_event,
        reporter=reporter,
        runner_factory=runner_factory,
        store=store,
        out=out,
        err=err,
        allow_missing_report=allow_missing_report,
    )
    adapter, agent_model, agent_reasoning_effort = implementer_flow.resolve_ready_adapter(
        issue_id, options, adapter
    )
    prompt = implementer_flow.build_prompt(issue, cwd, prompt_suffix)
    result, snapshot = _launch(issue, adapter, prompt, options)
    failed_outcome = implementer_flow.failed_run_outcome(issue, result, err=err)
    if failed_outcome is not None:
        return failed_outcome
    return implementer_flow.gate_and_submit(
        issue, result, snapshot, options, agent_model, agent_reasoning_effort
    )


def _launch(
    issue: Issue,
    adapter: AgentAdapter,
    prompt: AgentPrompt,
    options: implementer_flow.ImplementerRunOptions,
) -> tuple[AgentResult, implementation_changes.ImplementationChangeSnapshot]:
    issue_id = issue.id
    run_dir = options.cwd / ".agent-runs"
    runner_factory = options.runner_factory
    if runner_factory is None:
        run_config = dict(options.config.agents).get(options.agent)
        if run_config is not None and run_config.runtime == "codex_app_server":
            runner_factory = partial(
                AppServerAttemptRunner,
                options.config,
                issue,
                recovery=options.prompt_suffix is not None,
            )
        else:
            runner_factory = AgentRunner
    fingerprint_before = worktree_fingerprint(options.cwd)
    _warn_if_resuming_stale_run(issue_id, options.cwd, run_dir, out=options.out)
    try:
        result = runner_factory().run(
            adapter,
            prompt,
            options.cwd,
            timeout=float(options.timeout),
            agent_name=options.agent,
            issue_id=issue_id,
            follow=options.follow,
            run_dir=run_dir,
            abort_event=options.abort_event,
            issuekit_session=options.session,
            implementer_report=True,
        )
    except (AgentBinaryNotFoundError, FileNotFoundError, ValueError):
        implementer_flow.release_claim_after_run_error(
            issue_id, config=options.config, store=options.store, err=options.err
        )
        raise
    if options.reporter is not None:
        options.reporter(issue, result)
    snapshot = implementation_changes.implementation_change_snapshot(
        options.cwd, fingerprint_before
    )
    return result, snapshot


def _warn_if_resuming_stale_run(
    issue_id: int,
    cwd: Path,
    run_dir: Path,
    *,
    out: TextIO,
) -> None:
    """Warn, before launching the agent, when this looks like a resume over a
    dead run's unsubmitted edits. This never skips the run and never submits
    anything on its own; it only reaches the --allow-no-changes recovery hint
    before a full agent turn is spent discovering it via the post-run path.
    """
    root = git_root(cwd)
    if root != cwd.resolve():
        return
    entries = git_status_entries(cwd)
    if not entries:
        return
    previous_runs = [status for status in list_statuses(run_dir) if status.issue == issue_id]
    if not previous_runs or not is_dead(previous_runs[0]):
        return
    print(
        "WARNING: the worktree already holds uncommitted changes, and this "
        f"issue's most recent local run (run={previous_runs[0].run_id}) looks "
        "dead (heartbeat stale). " + resumed_changes_hint(issue_id),
        file=out,
    )

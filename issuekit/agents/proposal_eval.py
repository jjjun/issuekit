"""Shared read-only proposal evaluation helpers for agent-backed flows."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import threading
from typing import Any, TextIO

from issuekit.agents.readonly import run_readonly_evaluation, stdout_text
from issuekit.workflow import WorkflowError


def run_readonly_proposal_evaluation(
    proposal: Mapping[str, Any],
    *,
    agent: str,
    adapter: object,
    cwd: Path,
    timeout: float,
    runner_factory,
    err: TextIO,
    prompt_filename: str,
    prompt_text: str,
    prompt_override: str,
    label: str,
    mutation_log_message: str,
    abort_event: threading.Event | None = None,
) -> str:
    """Run an agent on a proposal prompt and reject output if the worktree changed."""

    proposal_id = int(proposal["id"])
    run = run_readonly_evaluation(
        agent=agent,
        adapter=adapter,
        cwd=cwd,
        timeout=timeout,
        runner_factory=runner_factory,
        prompt_filename=prompt_filename,
        prompt_text=prompt_text,
        prompt_override=prompt_override,
        label=label,
        subject=f"proposal #{proposal_id}",
        abort_event=abort_event,
    )
    if run.result.timed_out:
        raise TimeoutError(f"{run.label} agent timed out for {run.subject}.")
    if run.result.exit_code != 0:
        raise RuntimeError(
            f"{run.label} agent exited {run.result.exit_code} for {run.subject}."
        )
    if run.worktree_modified:
        print(mutation_log_message, file=err)
        raise WorkflowError(f"{run.label} agent modified the worktree for {run.subject}.")
    return stdout_text(run.result)

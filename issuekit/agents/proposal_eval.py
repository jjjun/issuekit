"""Shared read-only proposal evaluation helpers for agent-backed flows."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import threading
from typing import Any, TextIO

from issuekit.agents.readonly import require_clean_run, run_readonly_evaluation
from issuekit.agentrun import AgentPrompt


def run_readonly_proposal_evaluation(
    proposal: Mapping[str, Any],
    *,
    agent: str,
    adapter: object,
    cwd: Path,
    timeout: float,
    runner_factory,
    err: TextIO,
    prompt: AgentPrompt,
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
        prompt=prompt,
        label=label,
        subject=f"proposal #{proposal_id}",
        abort_event=abort_event,
    )
    return require_clean_run(
        run,
        err=err,
        mutation_log_message=mutation_log_message,
    )

"""Shared read-only proposal evaluation helpers for agent-backed flows."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import threading
from typing import Any, TextIO

from issuekit.agents.runner import AgentResult
from issuekit.gitutil import git_status_short
from issuekit.prompts import parse_newest_json_block
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
    run_dir = cwd / ".agent-runs"
    run_dir.mkdir(exist_ok=True)
    prompt_path = run_dir / prompt_filename
    prompt_path.write_text(prompt_text, encoding="utf-8", newline="\n")
    fingerprint_before = worktree_fingerprint(cwd)

    result = runner_factory().run(
        adapter,
        prompt_path,
        cwd,
        timeout=float(timeout),
        agent_name=agent,
        prompt_override=prompt_override,
        abort_event=abort_event,
    )
    if result.timed_out:
        raise TimeoutError(f"{label} agent timed out for proposal #{proposal_id}.")
    if result.exit_code != 0:
        raise RuntimeError(
            f"{label} agent exited {result.exit_code} for proposal #{proposal_id}."
        )
    fingerprint_after = worktree_fingerprint(cwd)
    if fingerprint_before != fingerprint_after:
        print(mutation_log_message, file=err)
        raise WorkflowError(f"{label} agent modified the worktree for proposal #{proposal_id}.")
    return stdout_text(result)


def worktree_fingerprint(cwd: Path) -> tuple[tuple[str, str], ...] | None:
    status = git_status_short(cwd, strip=False, untracked_files="all")
    if status is None:
        return None
    entries: list[tuple[str, str]] = []
    for line in status.splitlines():
        if len(line) < 4:
            continue
        raw_path = line[3:]
        if " -> " in raw_path:
            raw_path = raw_path.rsplit(" -> ", 1)[1]
        raw_path = raw_path.strip('"')
        path = Path(raw_path)
        if path.parts and path.parts[0] == ".agent-runs":
            continue
        entries.append((line[:2], path.as_posix()))
    return tuple(sorted(entries))


def stdout_text(result: AgentResult) -> str:
    if result.parsed and "stdout" in result.parsed:
        return result.parsed["stdout"]
    return result.stdout_path.read_text(encoding="utf-8", errors="replace")

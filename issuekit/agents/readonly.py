"""Shared read-only agent execution helpers."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import threading
from typing import TextIO

from issuekit.agentrun import AgentPrompt, AgentResult
from issuekit.gitutil import git_status_short
from issuekit.prompts import PromptSpec
from issuekit.workflow import WorkflowError


@dataclass(frozen=True)
class ReadonlyAgentRun:
    """Completed agent run together with its worktree mutation result."""

    result: AgentResult
    worktree_modified: bool
    label: str
    subject: str


def prompt_from_spec(
    spec: PromptSpec,
    *,
    cwd: Path,
    filename: str,
    body: str,
) -> AgentPrompt:
    """Build a runtime prompt from a prompt spec and rendered body."""

    path = cwd / ".agent-runs" / filename
    return AgentPrompt(
        path=path,
        body=body,
        pointer=spec.render_pointer(prompt_path=path),
    )


def run_readonly_evaluation(
    *,
    agent: str,
    adapter: object,
    cwd: Path,
    timeout: float,
    runner_factory,
    prompt: AgentPrompt,
    label: str,
    subject: str,
    issue_id: int | None = None,
    session_id: str | None = None,
    follow: bool = False,
    abort_event: threading.Event | None = None,
) -> ReadonlyAgentRun:
    """Run an agent from a prompt file and record whether it changed the worktree."""

    fingerprint_before = worktree_fingerprint(cwd)

    result = runner_factory().run(
        adapter,
        prompt,
        cwd,
        timeout=float(timeout),
        agent_name=agent,
        issue_id=issue_id,
        session_id=session_id,
        follow=follow,
        abort_event=abort_event,
    )
    fingerprint_after = worktree_fingerprint(cwd)
    return ReadonlyAgentRun(
        result=result,
        worktree_modified=fingerprint_before != fingerprint_after,
        label=label,
        subject=subject,
    )


def require_clean_run(
    run: ReadonlyAgentRun,
    *,
    err: TextIO,
    mutation_log_message: str,
) -> str:
    """Return agent output after enforcing read-only execution requirements."""

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


def worktree_fingerprint(cwd: Path) -> tuple[tuple[str, str, str], ...] | None:
    """Return status entries and content digests, excluding agent runtime files."""

    status = git_status_short(cwd, strip=False, untracked_files="all")
    if status is None:
        return None
    entries: list[tuple[str, str, str]] = []
    for line in status.splitlines():
        if len(line) < 4:
            continue
        raw_path = line[3:]
        if " -> " in raw_path:
            raw_path = raw_path.rsplit(" -> ", 1)[1]
        path = Path(raw_path.strip('"'))
        if path.parts and path.parts[0] == ".agent-runs":
            continue
        entries.append((line[:2], path.as_posix(), _file_digest(cwd / path)))
    return tuple(sorted(entries))


def stdout_text(result: AgentResult) -> str:
    """Return captured agent stdout, preferring adapter-parsed output."""

    if result.parsed and "stdout" in result.parsed:
        return result.parsed["stdout"]
    return result.stdout_path.read_text(encoding="utf-8", errors="replace")


def _file_digest(path: Path) -> str:
    try:
        if not path.exists() or not path.is_file():
            return ""
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""

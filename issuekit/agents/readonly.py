"""Shared read-only agent execution helpers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import hashlib
from pathlib import Path
import threading
from typing import Any

from issuekit.agents.runner import AgentResult
from issuekit.gitutil import git_status_short


@dataclass(frozen=True)
class ReadonlyAgentRun:
    """Completed agent run together with its worktree mutation result."""

    result: AgentResult
    worktree_modified: bool
    label: str
    subject: str


def run_readonly_evaluation(
    *,
    agent: str,
    adapter: object,
    cwd: Path,
    timeout: float,
    runner_factory,
    prompt_filename: str,
    prompt_text: str,
    prompt_override: str,
    label: str,
    subject: str,
    run_kwargs: Mapping[str, Any] | None = None,
    abort_event: threading.Event | None = None,
) -> ReadonlyAgentRun:
    """Run an agent from a prompt file and record whether it changed the worktree."""

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
        **(run_kwargs or {}),
    )
    fingerprint_after = worktree_fingerprint(cwd)
    return ReadonlyAgentRun(
        result=result,
        worktree_modified=fingerprint_before != fingerprint_after,
        label=label,
        subject=subject,
    )


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

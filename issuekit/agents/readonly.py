"""Shared read-only agent execution helpers."""

from __future__ import annotations

import hashlib
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from issuekit.agentrun import AgentPrompt, AgentResult
from issuekit.gitutil import git_root, git_status_entries, run_git
from issuekit.prompts import PromptSpec
from issuekit.workflow import WorkflowError


@dataclass(frozen=True)
class ReadonlyAgentRun:
    """Completed agent run together with its repository mutation result."""

    result: AgentResult
    repository_modified: bool
    repository_changed_paths: tuple[str, ...]
    label: str
    subject: str
    repository_error: str | None = None


@dataclass(frozen=True)
class RepositoryFingerprint:
    """Git and durable workflow state required for read-only evaluation."""

    worktree: tuple[tuple[str, str, str, str], ...]
    head: str
    branch: str
    durable_state: tuple[tuple[str, str], ...]


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
    """Run an agent from a prompt file and record attributable repository changes.

    Content changes to paths that were already dirty are ignored because a shared
    checkout cannot attribute those writes to the agent. This weakens the guard
    for those paths; HEAD, branch, durable state, newly dirty paths, disappearing
    baseline paths, deletions, and renames remain protected.
    """

    fingerprint_before = repository_fingerprint(cwd)

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
    repository_error = None
    try:
        fingerprint_after = repository_fingerprint(cwd)
    except WorkflowError as exc:
        fingerprint_after = None
        repository_error = str(exc)
    changed_paths = (
        ()
        if fingerprint_after is None
        else _repository_changed_paths(fingerprint_before, fingerprint_after)
    )
    return ReadonlyAgentRun(
        result=result,
        repository_modified=fingerprint_after is None or bool(changed_paths),
        repository_changed_paths=changed_paths,
        label=label,
        subject=subject,
        repository_error=repository_error,
    )


def require_clean_run(
    run: ReadonlyAgentRun,
    *,
    err: TextIO,
    mutation_log_message: str,
) -> str:
    """Return agent output after enforcing read-only execution requirements."""

    if run.repository_modified:
        print(repository_mutation_message(mutation_log_message, run), file=err)
        if run.repository_error:
            print(f"ERROR: {run.repository_error}", file=err)
    if run.result.timed_out:
        raise TimeoutError(f"{run.label} agent timed out for {run.subject}.")
    if run.result.exit_code != 0:
        raise RuntimeError(
            f"{run.label} agent exited {run.result.exit_code} for {run.subject}."
        )
    if run.repository_modified:
        message = f"{run.label} agent modified repository state for {run.subject}."
        raise WorkflowError(repository_mutation_message(message, run))
    return stdout_text(run.result)


def repository_mutation_message(message: str, run: ReadonlyAgentRun) -> str:
    """Append a bounded list of changed fingerprint paths to a diagnostic."""

    if not run.repository_changed_paths:
        return message
    limit = 10
    displayed = ", ".join(run.repository_changed_paths[:limit])
    remaining = len(run.repository_changed_paths) - limit
    suffix = f", and {remaining} more" if remaining > 0 else ""
    return f"{message.rstrip('.')} (changed paths: {displayed}{suffix})."


def worktree_fingerprint(cwd: Path) -> tuple[tuple[str, str, str, str], ...] | None:
    """Return status entries and content digests, excluding agent runtime files."""

    status_entries = git_status_entries(cwd)
    if status_entries is None:
        return None
    entries: list[tuple[str, str, str, str]] = []
    for entry in status_entries:
        paths = tuple(
            path for path in (entry.path, entry.original_path) if path is not None
        )
        if paths and all(path.parts and path.parts[0] == ".agent-runs" for path in paths):
            continue
        entries.append(
            (
                entry.status,
                entry.path.as_posix(),
                entry.original_path.as_posix() if entry.original_path is not None else "",
                _file_digest(cwd / entry.path),
            )
        )
    return tuple(sorted(entries))


def repository_fingerprint(cwd: Path) -> RepositoryFingerprint:
    """Return a complete baseline or fail closed with the missing component."""

    root = git_root(cwd)
    if root is None:
        raise WorkflowError(
            "Cannot establish read-only repository fingerprint: "
            "repository root snapshot failed."
        )
    worktree = worktree_fingerprint(cwd)
    if worktree is None:
        raise WorkflowError(
            "Cannot establish read-only repository fingerprint: "
            "worktree status snapshot failed."
        )
    head_result = run_git(["rev-parse", "--verify", "HEAD"], cwd)
    if head_result is None or head_result.returncode != 0 or not head_result.stdout.strip():
        raise WorkflowError(
            "Cannot establish read-only repository fingerprint: HEAD snapshot failed."
        )
    branch_result = run_git(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd)
    if branch_result is None or branch_result.returncode not in {0, 1}:
        raise WorkflowError(
            "Cannot establish read-only repository fingerprint: branch snapshot failed."
        )
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else "(detached)"
    return RepositoryFingerprint(
        worktree=worktree,
        head=head_result.stdout.strip(),
        branch=branch,
        durable_state=_durable_state_fingerprint(root),
    )


def _repository_changed_paths(
    before: RepositoryFingerprint,
    after: RepositoryFingerprint,
) -> tuple[str, ...]:
    changed: set[str] = set()
    before_entries = {entry[1]: entry for entry in before.worktree}
    after_entries = {entry[1]: entry for entry in after.worktree}

    for path in before_entries.keys() - after_entries.keys():
        changed.add(path)
    for path, entry in after_entries.items():
        before_entry = before_entries.get(path)
        if before_entry is None:
            changed.add(path)
            if entry[2]:
                changed.add(entry[2])
        elif "D" in entry[0] and "D" not in before_entry[0]:
            changed.add(path)
        elif entry[2] and entry[2] != before_entry[2]:
            changed.update((path, entry[2]))

    if before.head != after.head:
        changed.add("HEAD")
    if before.branch != after.branch:
        changed.add("branch")

    before_durable = dict(before.durable_state)
    after_durable = dict(after.durable_state)
    for path in before_durable.keys() | after_durable.keys():
        if before_durable.get(path) != after_durable.get(path):
            changed.add(path)

    return tuple(sorted(changed))


def stdout_text(result: AgentResult) -> str:
    """Return captured agent stdout, preferring adapter-parsed output."""

    if result.parsed and "stdout" in result.parsed:
        return result.parsed["stdout"]
    return result.stdout_path.read_text(encoding="utf-8", errors="replace")


def _file_digest(path: Path) -> str:
    try:
        if not path.is_file():
            return ""
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return ""


def _durable_state_fingerprint(cwd: Path) -> tuple[tuple[str, str], ...]:
    run_dir = cwd / ".agent-runs"
    paths = [
        run_dir / "pm-requests.json",
        run_dir / "triage-author-state.json",
    ]
    negotiations_dir = run_dir / "negotiations"
    try:
        paths.extend(path for path in negotiations_dir.rglob("*") if path.is_file())
    except OSError:
        pass
    return tuple(
        sorted(
            (
                path.relative_to(cwd).as_posix(),
                _file_digest(path),
            )
            for path in paths
            if path.is_file()
        )
    )

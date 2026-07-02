"""Run an already-claimed issue through an agent and submit it for review."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import sys
import threading
from typing import TextIO

from issuekit.agents.runner import AgentResult, AgentRunner, resolve_adapter
from issuekit.config import IssuekitConfig
from issuekit.core import Issue, has_mojibake
from issuekit.gitutil import git_root, git_status_short, run_git
from issuekit.store import get_store
from issuekit.workflow import submit_for_review


@dataclass(frozen=True)
class RunOutcome:
    """Outcome for an agent run against one claimed issue."""

    issue: Issue
    result: AgentResult
    exit_code: int
    reviewed_issue: Issue | None = None


RunReporter = Callable[[Issue, AgentResult], None]
RunnerFactory = Callable[[], AgentRunner]


def run_and_submit(
    issue: Issue,
    *,
    agent: str,
    config: IssuekitConfig,
    cwd: Path,
    issues_dir: Path,
    timeout: float,
    model: str | None = None,
    follow: bool = False,
    prompt_suffix: str | None = None,
    allow_no_changes: bool = False,
    abort_event: threading.Event | None = None,
    reporter: RunReporter | None = None,
    runner_factory: RunnerFactory | None = None,
    store=None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> RunOutcome:
    """Run an agent for an already-claimed issue and submit successful work."""
    out = out or sys.stdout
    err = err or sys.stderr
    issue_id = issue.id
    if issue_id is None:
        raise ValueError("Claimed issue is missing an id.")

    adapter = resolve_adapter(agent, config=config, model=model)
    run_dir = cwd / ".agent-runs"
    run_dir.mkdir(exist_ok=True)
    plan_path = run_dir / f"issue-{issue_id}.md"
    plan_path.write_text(issue.body, encoding="utf-8", newline="\n")
    runner_factory = runner_factory or AgentRunner
    result = runner_factory().run(
        adapter,
        plan_path,
        cwd,
        timeout=float(timeout),
        agent_name=agent,
        issue_id=issue_id,
        follow=follow,
        prompt_suffix=prompt_suffix,
        abort_event=abort_event,
    )
    if reporter is not None:
        reporter(issue, result)

    if result.timed_out:
        return RunOutcome(issue=issue, result=result, exit_code=124)
    if result.exit_code != 0:
        return RunOutcome(
            issue=issue,
            result=result,
            exit_code=result.exit_code if result.exit_code >= 0 else 1,
        )
    if result.status_short:
        print(
            "WARNING: implementation changes are unstaged and not committed. "
            "Review the diff, then stage and commit the changes after review.",
            file=out,
        )

    owned_store = None
    if store is None:
        owned_store = get_store(config)
        store = owned_store

    try:
        if git_root(cwd) == cwd.resolve() and not _touched_implementation_paths(cwd, issues_dir):
            current_issue = store.get_issue(issue_id)
            if current_issue is not None and current_issue.stage == "review":
                print(
                    "Issue is already at review after the agent run; treating it as submitted.",
                    file=out,
                )
                return RunOutcome(
                    issue=issue,
                    result=result,
                    exit_code=0,
                    reviewed_issue=current_issue,
                )
            if not allow_no_changes:
                current_stage = current_issue.stage if current_issue is not None else "unknown"
                print(
                    "ERROR: agent produced no implementation changes; not submitting for review. "
                    f"The issue is currently at stage={current_stage}.",
                    file=err,
                )
                return RunOutcome(issue=issue, result=result, exit_code=1)
            print(
                "No implementation changes detected; submitting for review because "
                "--allow-no-changes was set.",
                file=out,
            )

        run_config = dict(config.agents).get(agent)
        if run_config is not None and run_config.diff_shape_warn_deletions is not None:
            _warn_heavy_deletions(
                cwd,
                issues_dir,
                deletion_threshold=run_config.diff_shape_warn_deletions,
                err=err,
            )
        if run_config is not None and run_config.mojibake_gate:
            mojibake_files = _mojibake_touched_files(cwd, issues_dir)
            if mojibake_files:
                print(
                    "ERROR: mojibake gate blocked submit_for_review. "
                    "Fix the following touched files before submitting:",
                    file=err,
                )
                for path in mojibake_files:
                    print(f"- {path}", file=err)
                return RunOutcome(issue=issue, result=result, exit_code=1)

        reviewed_issue = submit_for_review(
            issue_id,
            summary=f"Implemented by {agent} via issuekit implement.",
            config=config,
            store=store,
        )
        return RunOutcome(
            issue=issue,
            result=result,
            exit_code=0,
            reviewed_issue=reviewed_issue,
        )
    finally:
        if owned_store is not None:
            owned_store.close()


def review_feedback_prompt(issue_body: str) -> str | None:
    lines = issue_body.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == "## Review Feedback":
            start = index + 1
    if start is None:
        return None

    collected: list[str] = []
    for line in lines[start:]:
        if line.startswith("## ") and collected:
            break
        collected.append(line)
    notes = "\n".join(collected).strip()
    if not notes:
        return None
    return (
        "A reviewer requested the following changes. Address ONLY these notes; "
        "do not re-touch unrelated lines:\n\n"
        f"{notes}"
    )


def _mojibake_touched_files(repo: Path, issues_dir: Path) -> list[str]:
    hits: list[str] = []
    for rel_path in _touched_implementation_paths(repo, issues_dir):
        path = repo / rel_path
        if not path.exists() or not path.is_file():
            continue
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            hits.append(rel_path.as_posix())
            continue
        if has_mojibake(text):
            hits.append(rel_path.as_posix())
    return hits


def _touched_implementation_paths(repo: Path, issues_dir: Path) -> tuple[Path, ...]:
    return tuple(
        rel_path
        for rel_path in _touched_paths(repo)
        if not _is_under_issues_dir(repo / rel_path, issues_dir)
    )


def _warn_heavy_deletions(
    repo: Path,
    issues_dir: Path,
    *,
    deletion_threshold: int,
    err: TextIO,
) -> None:
    if git_root(repo) != repo.resolve():
        return
    result = run_git(["--no-pager", "diff", "--numstat", "HEAD", "--"], repo)
    if result is None:
        return
    if result.returncode != 0:
        return

    for line in result.stdout.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        _added, deleted, raw_path = parts
        if not deleted.isdigit() or int(deleted) <= deletion_threshold:
            continue
        rel_path = Path(raw_path)
        if _is_under_issues_dir(repo / rel_path, issues_dir):
            continue
        print(
            "WARNING: heavy deletion diff detected: "
            f"{rel_path.as_posix()} deletes {deleted} lines "
            f"(threshold {deletion_threshold}).",
            file=err,
        )


def _touched_paths(repo: Path) -> tuple[Path, ...]:
    if git_root(repo) != repo.resolve():
        return ()
    status_short = git_status_short(repo, strip=False, untracked_files="all")
    if status_short is None:
        return ()

    paths: list[Path] = []
    for line in status_short.splitlines():
        if len(line) < 4:
            continue
        status = line[:2]
        raw_path = line[3:]
        if " -> " in raw_path:
            raw_path = raw_path.rsplit(" -> ", 1)[1]
        path = Path(raw_path.strip('"'))
        if "D" in status and not (repo / path).exists():
            continue
        paths.append(path)
    return tuple(paths)


def _is_under_issues_dir(path: Path, issues_dir: Path) -> bool:
    try:
        path.resolve().relative_to(issues_dir.resolve())
        return True
    except ValueError:
        return False

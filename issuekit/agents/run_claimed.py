"""Run an already-claimed issue through an agent and submit it for review."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import sys
import threading
from typing import TextIO

from issuekit.agents.registry import resolve_adapter
from issuekit.agentrun import AgentResult, AgentRunner
from issuekit.author_guard import AuthorOrchestrationContext
from issuekit.config import IssuekitConfig
from issuekit.core import Issue
from issuekit.encoding import (
    confirmed_mojibake_hits,
    find_encoding_artifacts,
    is_encoding_excluded_path,
    line_number_at,
    newline_offsets,
    print_mojibake_hit,
)
from issuekit.gitutil import git_root, run_git
from issuekit.prompts import render_review_feedback_prompt
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


def implementation_prompt(plan_path: Path) -> str:
    """Return the Issuekit implementation prompt for a claimed issue."""

    return (
        f"Read the plan file at: {plan_path} . Implement it fully by editing "
        "files directly in this repository. Do NOT run git commit or git push - "
        "leave all changes unstaged for review. Edit only code, tests, and "
        "supporting project files needed for the implementation. Write "
        "maintainable, idiomatic code that matches surrounding imports, naming, "
        "and comment density; use normal imports and real identifiers when they "
        "work. Do not split or obfuscate string literals, import paths, or "
        "identifiers, and do not use importlib/getattr/setattr/globals() "
        "indirection unless dynamic loading is truly required. Issuekit owns the "
        "API-backed issue lifecycle, including claim, submit, review, approval, "
        "and completion state; do not run issuekit claim, submit-review, "
        "request-changes, approve, or complete, and do not mutate tracker state "
        "or issue lifecycle metadata directly. If the plan is ambiguous, make "
        "the most reasonable choice and note it at the end."
    )


def run_and_submit(
    issue: Issue,
    *,
    agent: str,
    config: IssuekitConfig,
    cwd: Path,
    issues_dir: Path,
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

    adapter = resolve_adapter(
        agent,
        config=config,
        model=model,
        reasoning_effort=reasoning_effort,
    )
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
        prompt_override=implementation_prompt(plan_path),
        run_dir=run_dir,
        abort_event=abort_event,
        issuekit_session=session,
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

        policy = dict(config.agent_policies).get(agent)
        if policy is not None and policy.diff_shape_warn_deletions is not None:
            _warn_heavy_deletions(
                cwd,
                issues_dir,
                deletion_threshold=policy.diff_shape_warn_deletions,
                err=err,
            )
        if policy is not None and policy.mojibake_gate:
            confirmed_hits, unconfirmed_hits = _mojibake_touched_hits(
                cwd,
                issues_dir,
                include_halfwidth_katakana=config.gate_halfwidth_kana,
                exclude_patterns=config.check_encoding_exclude,
            )
            if confirmed_hits or unconfirmed_hits:
                print(
                    "ERROR: mojibake gate blocked submit_for_review. "
                    "Fix the following changed lines before submitting:",
                    file=err,
                )
                for hit in confirmed_hits:
                    print_mojibake_hit(hit, err, prefix="- ", context_prefix="  ")
                    print(f"  recovers to {hit['recovered']}", file=err)
                for hit in unconfirmed_hits:
                    print_mojibake_hit(hit, err, prefix="- ", context_prefix="  ")
                    print("  failed CP932 reverse confirmation", file=err)
                if unconfirmed_hits:
                    print(
                        "To allow known-legitimate unconfirmed text, add its "
                        "repo-relative path to check_encoding_exclude.",
                        file=err,
                    )
                return RunOutcome(issue=issue, result=result, exit_code=1)

        reviewed_issue = submit_for_review(
            issue_id,
            summary=submit_summary or f"Implemented by {agent} via issuekit implement.",
            config=config,
            store=store,
            cwd=cwd,
            allow_author_guard_override=allow_author_guard_override,
            allow_any_branch=allow_any_branch,
            session=session,
            orchestration=orchestration,
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
    return render_review_feedback_prompt(notes)


def _mojibake_touched_hits(
    repo: Path,
    issues_dir: Path,
    *,
    include_halfwidth_katakana: bool,
    exclude_patterns: tuple[str, ...],
) -> tuple[list[dict[str, int | str]], list[dict[str, int | str]]]:
    confirmed_hits: list[dict[str, int | str]] = []
    unconfirmed_hits: list[dict[str, int | str]] = []
    paths = _touched_implementation_paths(repo, issues_dir)
    changed_lines_by_path = _changed_line_numbers(repo, paths)
    tracked_paths = _tracked_paths(repo, paths)
    for rel_path in paths:
        path = repo / rel_path
        if not path.exists() or not path.is_file():
            continue
        try:
            text = path.read_bytes().decode("utf-8")
        except UnicodeDecodeError:
            confirmed_hits.append(
                {
                    "file": rel_path.as_posix(),
                    "line": 1,
                    "column": 1,
                    "code_point": "invalid UTF-8",
                    "context": "unable to decode file as UTF-8",
                    "recovered": "not applicable",
                }
            )
            continue
        # The submit gate defaults to the CLI's strict kana check, but projects
        # with generated half-width katakana can set gate_halfwidth_kana = false.
        offsets = newline_offsets(text)
        changed_lines = changed_lines_by_path.get(rel_path, set())
        if not changed_lines and rel_path not in tracked_paths:
            changed_lines = set(range(1, len(offsets) + 2))
        artifacts = [
            (index, character)
            for index, character in find_encoding_artifacts(
                text,
                include_halfwidth_katakana=include_halfwidth_katakana,
            )
            if line_number_at(offsets, index) in changed_lines
        ]
        confirmed, unconfirmed = confirmed_mojibake_hits(
            rel_path.as_posix(),
            text,
            artifacts,
            offsets=offsets,
        )
        confirmed_hits.extend(confirmed)
        if not is_encoding_excluded_path(rel_path.as_posix(), exclude_patterns):
            unconfirmed_hits.extend(unconfirmed)
    return confirmed_hits, unconfirmed_hits


def _changed_line_numbers(
    repo: Path, rel_paths: tuple[Path, ...]
) -> dict[Path, set[int]]:
    if not rel_paths:
        return {}
    result = run_git(
        [
            "-c",
            "core.quotepath=false",
            "--no-pager",
            "diff",
            "--unified=0",
            "HEAD",
            "--",
            *(rel_path.as_posix() for rel_path in rel_paths),
        ],
        repo,
    )
    if result is None or result.returncode != 0:
        return {}
    return _added_line_numbers(result.stdout)


def _tracked_paths(repo: Path, rel_paths: tuple[Path, ...]) -> set[Path]:
    if not rel_paths:
        return set()
    result = run_git(
        ["ls-files", "-z", "--", *(rel_path.as_posix() for rel_path in rel_paths)],
        repo,
    )
    if result is None or result.returncode != 0:
        return set()
    return {Path(path) for path in result.stdout.split("\0") if path}


def _added_line_numbers(diff: str) -> dict[Path, set[int]]:
    changed_lines: dict[Path, set[int]] = {}
    rel_path: Path | None = None
    line_number: int | None = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            rel_path = None
            line_number = None
        elif line.startswith("+++ /dev/null"):
            rel_path = None
        elif line.startswith("+++ b/"):
            rel_path = Path(line[6:])
            changed_lines.setdefault(rel_path, set())
        elif line.startswith("@@"):
            plus_range = line.split(" ")[2]
            start = plus_range[1:].split(",", 1)[0]
            line_number = int(start)
        elif rel_path is not None and line_number is not None and line.startswith("+"):
            changed_lines[rel_path].add(line_number)
            line_number += 1
        elif line_number is not None and not line.startswith("-"):
            line_number += 1
    return changed_lines


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
    result = run_git(
        [
            "-c",
            "core.quotepath=false",
            "--no-pager",
            "status",
            "--short",
            "--untracked-files=all",
        ],
        repo,
    )
    if result is None or result.returncode != 0:
        return ()

    paths: list[Path] = []
    for line in result.stdout.splitlines():
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

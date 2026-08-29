"""Run an already-claimed issue through an agent and submit it for review."""

from __future__ import annotations

import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import TextIO

from issuekit.agentrun import AgentPrompt, AgentResult, AgentRunner
from issuekit.agentrun.runner import implementation_report_instruction
from issuekit.agentrun.status import read_status
from issuekit.agents.app_server_runtime import AppServerAttemptRunner
from issuekit.agents.readonly import worktree_fingerprint
from issuekit.agents.registry import resolve_adapter
from issuekit.config import IssuekitConfig
from issuekit.core import Issue
from issuekit.encoding import (
    MojibakeScanOptions,
    changed_line_numbers,
    changed_readable_paths,
    is_encoding_excluded_path,
    print_mojibake_hit,
    sanitize_to_ascii,
    scan_mojibake,
)
from issuekit.gitutil import GitStatusEntry, git_root, git_status_entries, run_git
from issuekit.guards.author import AuthorOrchestrationContext
from issuekit.prompts import render_review_feedback_prompt
from issuekit.store import managed_issue_store
from issuekit.workflow import submit_for_review


@dataclass(frozen=True)
class RunOutcome:
    """Outcome for an agent run against one claimed issue."""

    issue: Issue
    result: AgentResult
    exit_code: int
    reviewed_issue: Issue | None = None
    reason: str | None = None
    resumed_changes: bool = False


@dataclass(frozen=True)
class ImplementationChangeSnapshot:
    """Agent-attributable repository changes captured after an implementation run."""

    root: Path | None
    status_entries: tuple[GitStatusEntry, ...] | None
    changed_paths: tuple[Path, ...]
    readable_paths: tuple[Path, ...]
    all_status_entries: tuple[GitStatusEntry, ...] | None = None


RunReporter = Callable[[Issue, AgentResult], None]
RunnerFactory = Callable[[], AgentRunner]
MAX_IMPLEMENTER_REPORT_CHARS = 4000


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
        "the most reasonable choice and note it at the end. "
        + implementation_report_instruction(
            "the path in $ISSUEKIT_IMPLEMENTER_REPORT_FILE"
        )
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
    allow_missing_report: bool = False,
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
        role="implementer",
    )
    agent_model, agent_reasoning_effort = adapter.effective_runtime()
    if not config.send_agent_runtime:
        agent_model = None
        agent_reasoning_effort = None
    run_dir = cwd / ".agent-runs"
    plan_path = run_dir / f"issue-{issue_id}.md"
    prompt = AgentPrompt(
        path=plan_path,
        body=issue.body,
        pointer=implementation_prompt(plan_path),
    )
    if runner_factory is None:
        run_config = dict(config.agents).get(agent)
        if run_config is not None and run_config.runtime == "codex_app_server":
            runner_factory = partial(
                AppServerAttemptRunner,
                config,
                issue,
                recovery=prompt_suffix is not None,
            )
        else:
            runner_factory = AgentRunner
    fingerprint_before = worktree_fingerprint(cwd)
    result = runner_factory().run(
        adapter,
        prompt,
        cwd,
        timeout=float(timeout),
        agent_name=agent,
        issue_id=issue_id,
        follow=follow,
        prompt_suffix=prompt_suffix,
        run_dir=run_dir,
        abort_event=abort_event,
        issuekit_session=session,
        implementer_report=True,
    )
    if reporter is not None:
        reporter(issue, result)
    snapshot = _implementation_change_snapshot(cwd, fingerprint_before)

    if result.timed_out:
        return RunOutcome(issue=issue, result=result, exit_code=124, reason="timed_out")
    if result.exit_code != 0:
        if _is_startup_failure(result):
            print(
                "HINT: the agent parsed a failure with no turns or token usage, "
                "which looks like a startup failure rather than an implementation "
                f"attempt. The claim on issue #{issue_id} was left in place; "
                f"re-run `issuekit implement {issue_id}` or release it with "
                f"`issuekit reclaim {issue_id}`.",
                file=err,
            )
        return RunOutcome(
            issue=issue,
            result=result,
            exit_code=result.exit_code if result.exit_code >= 0 else 1,
            reason="agent_failed",
        )
    if result.status_short:
        print(
            "WARNING: implementation changes are unstaged and not committed. "
            "Review the diff, then stage and commit the changes after review.",
            file=out,
        )

    with managed_issue_store(config, store) as active_store:
        implementation_entries = _implementation_entries(snapshot, cwd, issues_dir)
        if snapshot.root == cwd.resolve() and not implementation_entries:
            current_issue = active_store.get_issue(issue_id)
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
                log_detail = _diagnostic_log_detail(result)
                # implementation_entries is already empty here, so every
                # non-issues-dir entry in the unfiltered set is pre-existing.
                unattributed_entries = _all_implementation_entries(
                    snapshot, cwd, issues_dir
                )
                if unattributed_entries:
                    print(
                        "ERROR: agent produced no implementation changes; not "
                        "submitting for review. The worktree holds uncommitted "
                        "modifications that were made before this run started, "
                        "not by this run; this looks like a resumed run over a "
                        "previous attempt's unsubmitted edits. If they are "
                        "complete, submit them with --allow-no-changes. The "
                        f"issue is currently at stage={current_stage}.{log_detail}",
                        file=err,
                    )
                    return RunOutcome(
                        issue=issue,
                        result=result,
                        exit_code=1,
                        reason="no_changes",
                        resumed_changes=True,
                    )
                print(
                    "ERROR: agent produced no implementation changes; not submitting for review. "
                    f"The issue is currently at stage={current_stage}.{log_detail}",
                    file=err,
                )
                return RunOutcome(
                    issue=issue, result=result, exit_code=1, reason="no_changes"
                )
            print(
                "No implementation changes detected; submitting for review because "
                "--allow-no-changes was set.",
                file=out,
            )

        if result.report_path is not None and not _has_report_content(result.report_path):
            if not allow_missing_report:
                print(
                    "ERROR: implementer report missing; not submitting for review. "
                    f"Expected a report at {_display_path(result.report_path, cwd)}. "
                    "The agent must await every verification command it starts and "
                    "write its closing implementation and verification report before "
                    "the run ends.",
                    file=err,
                )
                return RunOutcome(
                    issue=issue, result=result, exit_code=1, reason="missing_report"
                )
            print(
                "Implementer report missing; submitting anyway because "
                "--allow-missing-report was set.",
                file=out,
            )

        policy = dict(config.agent_policies).get(agent)
        if policy is not None and policy.diff_shape_warn_deletions is not None:
            _warn_heavy_deletions(
                snapshot,
                cwd,
                issues_dir,
                deletion_threshold=policy.diff_shape_warn_deletions,
                err=err,
            )
        if policy is not None and policy.mojibake_gate:
            confirmed_hits, unconfirmed_hits = _mojibake_touched_hits(
                snapshot,
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
                excluded_confirmed_paths = {
                    str(hit["file"])
                    for hit in confirmed_hits
                    if is_encoding_excluded_path(
                        str(hit["file"]), config.check_encoding_exclude
                    )
                }
                if excluded_confirmed_paths:
                    print(
                        "check_encoding_exclude matches "
                        f"{len(excluded_confirmed_paths)} of these path(s); exclusions "
                        "suppress unconfirmed candidates only, so confirmed mojibake "
                        "is still reported.",
                        file=err,
                    )
                print(
                    "Reproduce this gate locally with "
                    "`uv run issuekit check-encoding --gate`.",
                    file=err,
                )
                if unconfirmed_hits:
                    print(
                        "To allow known-legitimate unconfirmed text, add its "
                        "repo-relative path to check_encoding_exclude.",
                        file=err,
                    )
                return RunOutcome(
                    issue=issue, result=result, exit_code=1, reason="mojibake_gate"
                )

        reviewed_issue = submit_for_review(
            issue_id,
            summary=_submission_summary(
                submit_summary or f"Implemented by {agent} via issuekit implement.",
                result,
                cwd,
            ),
            config=config,
            store=active_store,
            cwd=cwd,
            allow_author_guard_override=allow_author_guard_override,
            allow_any_branch=allow_any_branch,
            session=session,
            orchestration=orchestration,
            agent_model=agent_model,
            agent_reasoning_effort=agent_reasoning_effort,
        )
        return RunOutcome(
            issue=issue,
            result=result,
            exit_code=0,
            reviewed_issue=reviewed_issue,
        )


def _submission_summary(prefix: str, result: AgentResult, cwd: Path) -> str:
    run_log = sanitize_to_ascii(_display_path(result.stdout_path, cwd))
    summary = f"{prefix}\nRun log: `{run_log}`"
    if result.report_path is None or not result.report_path.is_file():
        return summary
    try:
        with result.report_path.open(encoding="utf-8", errors="replace") as stream:
            raw_report = stream.read(MAX_IMPLEMENTER_REPORT_CHARS + 1)
    except OSError:
        return summary
    report = sanitize_to_ascii(raw_report).strip()
    if not report:
        return summary

    truncation_marker = "\n[Implementer report truncated; see run log.]"
    if (
        len(raw_report) > MAX_IMPLEMENTER_REPORT_CHARS
        or len(report) > MAX_IMPLEMENTER_REPORT_CHARS
    ):
        report = (
            report[: MAX_IMPLEMENTER_REPORT_CHARS - len(truncation_marker)].rstrip()
            + truncation_marker
        )
    return f"{summary}\n\nImplementer report:\n{report}"


def _diagnostic_log_detail(result: AgentResult) -> str:
    if result.status_path is None:
        return ""
    try:
        status = read_status(result.status_path)
    except (OSError, ValueError):
        return ""
    if status.failure_reason:
        return f" Agent failure reason: {status.failure_reason}"
    if status.last_log_line:
        return f" Last agent log line: {status.last_log_line}"
    return ""


def _is_startup_failure(result: AgentResult) -> bool:
    parsed = result.parsed or {}
    if parsed.get("is_error") != "true":
        return False
    num_turns = parsed.get("num_turns")
    if num_turns is not None:
        try:
            if int(num_turns) <= 1:
                return True
        except ValueError:
            pass
    usage_values = [value for key, value in parsed.items() if key.startswith("usage_")]
    return not usage_values or all(value == "0" for value in usage_values)


def _has_report_content(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return bool(text.strip())


def _display_path(path: Path, cwd: Path) -> str:
    try:
        return path.resolve().relative_to(cwd.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


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
        if line.startswith("## "):
            break
        collected.append(line)
    notes = "\n".join(collected).strip()
    if not notes:
        return None
    return render_review_feedback_prompt(notes)


def _mojibake_touched_hits(
    snapshot: ImplementationChangeSnapshot,
    repo: Path,
    issues_dir: Path,
    *,
    include_halfwidth_katakana: bool,
    exclude_patterns: tuple[str, ...],
) -> tuple[list[dict[str, int | str]], list[dict[str, int | str]]]:
    paths = changed_readable_paths(
        repo,
        snapshot.status_entries or (),
        excluded_root=issues_dir,
        readable_paths=snapshot.readable_paths,
    )
    changed_lines_by_path = changed_line_numbers(repo, paths, git_runner=run_git)
    untracked_paths = {
        entry.path
        for entry in snapshot.status_entries or ()
        if entry.status == "??"
    }
    result = scan_mojibake(
        repo,
        paths,
        options=MojibakeScanOptions(
            failure_classes=frozenset({"confirmed", "unconfirmed"}),
            include_halfwidth_katakana=include_halfwidth_katakana,
            source_extensions=None,
            line_scope="changed-lines",
            exclude_patterns=exclude_patterns,
            # Exclusions suppress false positives from known-legitimate text;
            # confirmed reversible corruption still blocks every path.
            excluded_hit_classes=frozenset({"unconfirmed"}),
        ),
        changed_lines_by_path=changed_lines_by_path,
        whole_file_paths=untracked_paths,
    )
    return list(result.confirmed_hits), list(result.unconfirmed_hits)


def _warn_heavy_deletions(
    snapshot: ImplementationChangeSnapshot,
    repo: Path,
    issues_dir: Path,
    *,
    deletion_threshold: int,
    err: TextIO,
) -> None:
    if snapshot.root != repo.resolve():
        return
    implementation_entries = _implementation_entries(snapshot, repo, issues_dir)
    if not implementation_entries:
        return
    result = run_git(["--no-pager", "diff", "--numstat", "-z", "HEAD", "--"], repo)
    if result is None:
        return
    if result.returncode != 0:
        return

    for _added, deleted, paths in _numstat_records(result.stdout):
        if not any(
            entry.path in paths or entry.original_path in paths
            for entry in implementation_entries
        ):
            continue
        if not deleted.isdigit() or int(deleted) <= deletion_threshold:
            continue
        rel_path = paths[-1]
        print(
            "WARNING: heavy deletion diff detected: "
            f"{rel_path.as_posix()} deletes {deleted} lines "
            f"(threshold {deletion_threshold}).",
            file=err,
        )


def _numstat_records(output: str) -> tuple[tuple[str, str, tuple[Path, ...]], ...]:
    fields = output.split("\0")
    if fields and not fields[-1]:
        fields.pop()
    records: list[tuple[str, str, tuple[Path, ...]]] = []
    index = 0
    while index < len(fields):
        parts = fields[index].split("\t", 2)
        if len(parts) != 3:
            break
        added, deleted, raw_path = parts
        if raw_path:
            paths = (Path(raw_path),)
            index += 1
        else:
            if index + 2 >= len(fields):
                break
            paths = (Path(fields[index + 1]), Path(fields[index + 2]))
            index += 3
        records.append((added, deleted, paths))
    return tuple(records)


def _implementation_change_snapshot(
    repo: Path,
    fingerprint_before: tuple[tuple[str, str, str, str], ...] | None = None,
) -> ImplementationChangeSnapshot:
    root = git_root(repo)
    all_status_entries = git_status_entries(repo) if root == repo.resolve() else None
    fingerprint_after = (
        worktree_fingerprint(repo) if all_status_entries is not None else None
    )
    entries = _attributable_entries(all_status_entries, fingerprint_before, fingerprint_after)
    changed_paths: list[Path] = []
    readable_paths: list[Path] = []
    seen_changed: set[Path] = set()
    seen_readable: set[Path] = set()
    for entry in entries or ():
        for path in (entry.path, entry.original_path):
            if path is not None and path not in seen_changed:
                seen_changed.add(path)
                changed_paths.append(path)
        current = repo / entry.path
        if entry.path not in seen_readable and _is_readable_regular_file(current):
            seen_readable.add(entry.path)
            readable_paths.append(entry.path)
    return ImplementationChangeSnapshot(
        root=root,
        status_entries=entries,
        all_status_entries=all_status_entries,
        changed_paths=tuple(changed_paths),
        readable_paths=tuple(readable_paths),
    )


def _attributable_entries(
    entries: tuple[GitStatusEntry, ...] | None,
    fingerprint_before: tuple[tuple[str, str, str, str], ...] | None,
    fingerprint_after: tuple[tuple[str, str, str, str], ...] | None,
) -> tuple[GitStatusEntry, ...] | None:
    if entries is None or fingerprint_before is None or fingerprint_after is None:
        return entries
    before_by_path = {entry[1]: entry for entry in fingerprint_before}
    after_by_path = {entry[1]: entry for entry in fingerprint_after}
    return tuple(
        entry
        for entry in entries
        if (after_entry := after_by_path.get(entry.path.as_posix())) is not None
        and (
            (before_entry := before_by_path.get(entry.path.as_posix())) is None
            or before_entry[3] != after_entry[3]
        )
    )


def _is_readable_regular_file(path: Path) -> bool:
    if path.is_symlink() or not path.is_file():
        return False
    try:
        with path.open("rb") as stream:
            stream.read(0)
    except OSError:
        return False
    return True


def _implementation_entries(
    snapshot: ImplementationChangeSnapshot,
    repo: Path,
    issues_dir: Path,
) -> tuple[GitStatusEntry, ...]:
    return _filter_implementation_entries(snapshot.status_entries, repo, issues_dir)


def _all_implementation_entries(
    snapshot: ImplementationChangeSnapshot,
    repo: Path,
    issues_dir: Path,
) -> tuple[GitStatusEntry, ...]:
    """Non-issues-dir status entries, regardless of which run produced them."""

    return _filter_implementation_entries(snapshot.all_status_entries, repo, issues_dir)


def _filter_implementation_entries(
    entries: tuple[GitStatusEntry, ...] | None,
    repo: Path,
    issues_dir: Path,
) -> tuple[GitStatusEntry, ...]:
    return tuple(
        entry
        for entry in entries or ()
        if _entry_is_implementation_change(entry, repo, issues_dir)
    )


def _entry_is_implementation_change(
    entry: GitStatusEntry,
    repo: Path,
    issues_dir: Path,
) -> bool:
    return any(
        not _is_under_issues_dir(repo / path, issues_dir)
        for path in (entry.path, entry.original_path)
        if path is not None
    )


def _is_under_issues_dir(path: Path, issues_dir: Path) -> bool:
    try:
        path.resolve().relative_to(issues_dir.resolve())
        return True
    except ValueError:
        return False

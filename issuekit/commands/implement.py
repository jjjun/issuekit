"""Implementation of the implement command."""

from __future__ import annotations

from pathlib import Path
import subprocess
import sys

from issuekit.agents.runner import AgentRunner, resolve_adapter
from issuekit.commands.generate_indexes import write_index_files
from issuekit.config import load_config
from issuekit.core import (
    find_issue_by_id,
    has_mojibake,
    parse_issue_id_arg,
    read_active_issues,
    read_completed_issues,
)
from issuekit.workflow import WorkflowError, claim_issue, submit_for_review


def run(args) -> int:
    try:
        issue_id = parse_issue_id_arg(args.id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    cwd = Path.cwd()
    config = load_config(cwd)
    issues_dir = config.issues_path(cwd)
    active_issues = read_active_issues(issues_dir)
    issue = find_issue_by_id(active_issues, issue_id)
    if issue is None:
        print(f"Active issue #{issue_id} was not found.", file=sys.stderr)
        return 1
    if issue.decode_error:
        print(
            f"Active issue #{issue_id} is not valid UTF-8: {issue.relative_path}",
            file=sys.stderr,
        )
        return 1

    reviewer_prompt = (
        _review_feedback_prompt(issue.frontmatter.body)
        if issue.stage == "changes_requested"
        else None
    )
    try:
        issue = claim_issue(issues_dir, issue.id or issue_id, args.agent, config=config)
        adapter = resolve_adapter(args.agent, config=config, model=args.model)
        result = AgentRunner().run(
            adapter,
            issue.file_path,
            cwd,
            timeout=float(args.timeout_sec),
            agent_name=args.agent,
            issue_id=issue.id,
            follow=getattr(args, "follow", False),
            tracker_dir=issues_dir,
            prompt_suffix=reviewer_prompt,
        )
    except (FileNotFoundError, RuntimeError, ValueError, TimeoutError, WorkflowError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"issue={issue.id} file={issue.relative_path} agent={args.agent}")
    print(
        "exit_code={exit_code} timed_out={timed_out} elapsed_sec={elapsed:.2f}".format(
            exit_code=result.exit_code,
            timed_out=str(result.timed_out).lower(),
            elapsed=result.elapsed_sec,
        )
    )
    print(f"stdout_log={result.stdout_path}")
    print(f"agent_log={result.agent_log_path}")
    if result.status_path:
        print(f"status_file={result.status_path}")
    if result.parsed:
        for key, value in sorted(result.parsed.items()):
            if key in {"stdout", "stderr"} or not value:
                continue
            print(f"{key}={value}")

    print("--- git status --short ---")
    if result.status_short:
        print(result.status_short)
    elif result.status_short == "":
        print("No changes.")
    else:
        print("Unavailable.")

    if result.timed_out:
        return 124
    if result.exit_code != 0:
        return result.exit_code if result.exit_code >= 0 else 1
    if result.status_short and not _has_recorded_implementation_commit(result.parsed):
        print(
            "WARNING: implementation changes are unstaged and not committed. "
            "Review the diff, then stage and commit the changes after review."
        )

    if _git_root(cwd) == cwd.resolve() and not _touched_implementation_paths(cwd, issues_dir):
        print(
            "ERROR: agent produced no implementation changes; not submitting for review. "
            "The issue remains claimed in implementation.",
            file=sys.stderr,
        )
        return 1

    run_config = dict(config.agents).get(args.agent)
    if run_config is not None and run_config.diff_shape_warn_deletions is not None:
        _warn_heavy_deletions(
            cwd,
            issues_dir,
            deletion_threshold=run_config.diff_shape_warn_deletions,
        )
    if run_config is not None and run_config.mojibake_gate:
        mojibake_files = _mojibake_touched_files(cwd, issues_dir)
        if mojibake_files:
            print(
                "ERROR: mojibake gate blocked submit_for_review. "
                "Fix the following touched files before submitting:",
                file=sys.stderr,
            )
            for path in mojibake_files:
                print(f"- {path}", file=sys.stderr)
            return 1

    try:
        reviewed_issue = submit_for_review(
            issues_dir,
            issue.id or issue_id,
            summary=f"Implemented by {args.agent} via issuekit implement.",
            assignee=args.agent,
            config=config,
        )
    except (TimeoutError, WorkflowError) as exc:
        print(_submit_for_review_error(issues_dir, issue.id or issue_id, exc), file=sys.stderr)
        return 1

    if not config.api_url:
        write_index_files(issues_dir, config.recent_count)
    print(
        f"submitted_review id={reviewed_issue.id} file={reviewed_issue.relative_path} "
        f"assignee={reviewed_issue.assignee} stage={reviewed_issue.stage}"
    )
    return 0


def _has_recorded_implementation_commit(parsed: dict[str, str] | None) -> bool:
    if not parsed:
        return False
    return bool(parsed.get("implementation_commit") or parsed.get("commit"))


def _review_feedback_prompt(issue_body: str) -> str | None:
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
) -> None:
    if _git_root(repo) != repo.resolve():
        return
    try:
        result = subprocess.run(
            ["git", "--no-pager", "diff", "--numstat", "HEAD", "--"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
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
            file=sys.stderr,
        )


def _touched_paths(repo: Path) -> tuple[Path, ...]:
    if _git_root(repo) != repo.resolve():
        return ()
    try:
        result = subprocess.run(
            ["git", "--no-pager", "status", "--short", "--untracked-files=all"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return ()
    if result.returncode != 0:
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


def _git_root(repo: Path) -> Path | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return Path(result.stdout.strip()).resolve()


def _is_under_issues_dir(path: Path, issues_dir: Path) -> bool:
    try:
        path.resolve().relative_to(issues_dir.resolve())
        return True
    except ValueError:
        return False


def _submit_for_review_error(issues_dir: Path, issue_id: int, exc: Exception) -> str:
    generic_missing = f"Active issue #{issue_id} was not found."
    if str(exc) != generic_missing:
        return str(exc)

    completed_issues = read_completed_issues(issues_dir)
    completed_issue = next(
        (candidate for candidate in completed_issues if candidate.id == issue_id),
        None,
    )
    if completed_issue is None:
        return str(exc)

    return (
        f"Active issue #{issue_id} was not found because it appears to have been "
        f"moved to {completed_issue.relative_path} during implementation. "
        "Implementers must not mutate docs/issues/ tracker state; restore the "
        "issue to active/ and submit it for review."
    )

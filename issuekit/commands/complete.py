"""Implementation of the complete command."""

from __future__ import annotations

from pathlib import Path
import sys

from issuekit.config import IssuekitConfig, load_config
from issuekit.core import (
    Issue,
    parse_issue_id_arg,
    has_non_ascii,
)
from issuekit.workflow import WorkflowError


def run(args) -> int:
    summary = args.summary or ""
    verification = args.verification or ""

    try:
        issue_id = parse_issue_id_arg(args.id)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    config = load_config(Path.cwd())
    issues_dir = config.issues_path(Path.cwd())
    try:
        completed_issue = complete_issue(
            issues_dir,
            issue_id,
            summary=summary,
            verification=verification,
            reviewer=None,
            force=args.force,
            config=config,
        )
    except (ValueError, WorkflowError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except LookupError:
        print(f"Active issue #{issue_id} was not found.", file=sys.stderr)
        return 1
    except UnicodeError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(f"Completed issue #{completed_issue.id}: {completed_issue.relative_path}")
    return 0


def complete_issue(
    issues_dir: Path | str,
    issue_id: int,
    *,
    summary: str = "",
    verification: str = "",
    reviewer: str | None = None,
    force: bool = False,
    config: IssuekitConfig | None = None,
) -> Issue:
    if has_non_ascii(summary) or has_non_ascii(verification):
        raise ValueError("--summary and --verification must be ASCII-only.")

    config = config or IssuekitConfig()
    from issuekit.store import get_store

    store = get_store(config, issues_dir)
    return store.complete_issue(  # type: ignore[attr-defined]
        issue_id,
        summary=summary,
        verification=verification,
        force=force,
    )

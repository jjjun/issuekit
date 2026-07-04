"""Implementation of the author command."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re

from issuekit.author_guard import create_author_guard, stop_message
from issuekit.commands._common import require_ascii, run_command
from issuekit.config import IssuekitConfig, load_config
from issuekit.core import (
    Issue,
    VALID_ISSUE_PRIORITIES,
    is_valid_workflow_token,
)
from issuekit.refs import RefError, current_repo_ref, list_effective_refs
from issuekit.workflow import WorkflowError


def register(subparsers: argparse._SubParsersAction) -> None:
    author_parser = subparsers.add_parser(
        "author",
        help="Create an active issue authored by an agent.",
    )
    author_parser.add_argument("--title", required=True, help="Issue title.")
    body_group = author_parser.add_mutually_exclusive_group(required=True)
    body_group.add_argument("--body", help="Inline issue body.")
    body_group.add_argument("--body-file", help="File containing the issue body.")
    author_parser.add_argument(
        "--priority",
        choices=("high", "medium", "low"),
        default="medium",
        help="Issue priority.",
    )
    author_parser.add_argument("--agent", required=True, help="Configured author agent.")
    author_parser.add_argument("--assign", help="Optional implementer assignee.")
    author_parser.add_argument(
        "--direct-local-author",
        action="store_true",
        help=(
            "Bypass the cross-project proposal preflight when deliberately "
            "creating a local issue."
        ),
    )
    author_parser.add_argument(
        "--origin-project",
        help=(
            "Optional project where this request originated. If it differs from "
            "the current project, use propose unless --direct-local-author is set."
        ),
    )
    author_parser.set_defaults(func=run)


def run(args) -> int:
    def action() -> int:
        config = load_config(Path.cwd())
        authored = author_issue(
            title=args.title,
            body=args.body,
            body_file=args.body_file,
            priority=args.priority,
            agent=args.agent,
            assign=args.assign,
            config=config,
            cwd=Path.cwd(),
            direct_local_author=args.direct_local_author,
            origin_project=args.origin_project,
        )
        guard = create_author_guard(
            Path.cwd(),
            config=config,
            kind="issue",
            item_id=authored.id,
            ref=authored.ref,
            author_agent=args.agent,
        )

        print(f"Authored issue: {_authored_ref(authored)}")
        print(stop_message(guard))
        return 0

    return run_command(
        action,
        errors=(OSError, UnicodeError, ValueError, WorkflowError),
    )


def author_issue(
    *,
    title: str,
    body: str | None,
    body_file: str | None,
    priority: str,
    agent: str,
    assign: str | None = None,
    config: IssuekitConfig | None = None,
    cwd: Path | None = None,
    direct_local_author: bool = False,
    origin_project: str | None = None,
) -> Issue:
    config = config or IssuekitConfig()
    cwd = cwd or Path.cwd()
    _validate_author_input(
        title=title,
        priority=priority,
        agent=agent,
        assign=assign,
        config=config,
    )
    issue_body = _read_body(body=body, body_file=body_file)
    require_ascii(issue_body, message="--body and --body-file must be ASCII-only.")
    _require_local_author_context(
        title=title,
        body=issue_body,
        config=config,
        cwd=cwd,
        direct_local_author=direct_local_author,
        origin_project=origin_project,
    )
    from issuekit.store import get_store

    store = get_store(config)
    return store.create_issue(  # type: ignore[attr-defined]
        title=title.strip(),
        body=issue_body.strip(),
        priority=priority,
        author=agent,
        assignee=assign,
    )


def _validate_author_input(
    *,
    title: str,
    priority: str,
    agent: str,
    assign: str | None,
    config: IssuekitConfig,
) -> None:
    if not title.strip():
        raise ValueError("--title is required.")
    require_ascii(title, message="--title must be ASCII-only.")
    if priority not in VALID_ISSUE_PRIORITIES:
        raise ValueError(f"Invalid priority: {priority}")
    _validate_agent_token(agent, "--agent", config)
    if assign:
        _validate_agent_token(assign, "--assign", config)


def _validate_agent_token(value: str, label: str, config: IssuekitConfig) -> None:
    if not is_valid_workflow_token(value):
        raise WorkflowError(f"Invalid {label} token: {value}")
    if value not in config.assignees:
        raise WorkflowError(f"Unknown {label}: {value}")


def _read_body(*, body: str | None, body_file: str | None) -> str:
    if body is not None:
        return body.strip()
    if body_file:
        return Path(body_file).read_text(encoding="utf-8-sig").strip()
    raise ValueError("--body or --body-file is required.")


def _authored_ref(authored: Issue) -> str:
    return authored.ref


def _require_local_author_context(
    *,
    title: str,
    body: str,
    config: IssuekitConfig,
    cwd: Path,
    direct_local_author: bool,
    origin_project: str | None,
) -> None:
    if direct_local_author:
        return
    warning = _cross_project_author_warning(
        title=title,
        body=body,
        config=config,
        cwd=cwd,
        origin_project=origin_project,
    )
    if warning:
        raise WorkflowError(warning)


def _cross_project_author_warning(
    *,
    title: str,
    body: str,
    config: IssuekitConfig,
    cwd: Path,
    origin_project: str | None,
) -> str | None:
    related_refs = _related_ref_names(cwd)
    current_project = config.project
    # "Current project" is the configured API project key, not the git-derived
    # worker repo_id. The worker repo_id is a worker identity that may legitimately
    # differ from the project key, so it must not be treated as a cross-project
    # signal (that produced false positives on every local author).
    local_names = {current_project, _safe_current_ref(cwd)}
    explicit_origin = _origin_project_context(origin_project)
    if explicit_origin and explicit_origin not in local_names:
        return _author_proposal_warning(
            target_project=current_project,
            origin_project=explicit_origin,
            title=title,
            reason=f"--origin-project points at {explicit_origin}",
        )

    candidate_refs = sorted(name for name in related_refs if name not in local_names)
    mentioned = _mentioned_related_refs(f"{title}\n\n{body}", candidate_refs)
    if not mentioned:
        return None
    return _author_proposal_warning(
        target_project=current_project,
        origin_project=mentioned[0],
        title=title,
        reason=f"issue text mentions related project {mentioned[0]}",
    )


def _related_ref_names(cwd: Path) -> set[str]:
    try:
        return set(list_effective_refs(cwd))
    except RefError:
        return set()


def _safe_current_ref(cwd: Path) -> str:
    try:
        return current_repo_ref(cwd)
    except RefError:
        return ""


def _origin_project_context(origin_project: str | None) -> str:
    return (origin_project or os.environ.get("ISSUEKIT_ORIGIN_PROJECT", "")).strip()


def _mentioned_related_refs(text: str, refs: list[str]) -> list[str]:
    return [name for name in refs if _contains_ref_name(text, name)]


def _contains_ref_name(text: str, ref_name: str) -> bool:
    variants = {ref_name, ref_name.replace("-", " "), ref_name.replace("_", " ")}
    return any(_contains_tokenish_phrase(text, variant) for variant in variants)


def _contains_tokenish_phrase(text: str, phrase: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9_-]){re.escape(phrase)}(?![A-Za-z0-9_-])"
    return bool(re.search(pattern, text, flags=re.IGNORECASE))


def _author_proposal_warning(
    *,
    target_project: str,
    origin_project: str,
    title: str,
    reason: str,
) -> str:
    return (
        "Cross-project author preflight stopped direct issue creation: "
        f"{reason}. If you are acting from project {origin_project} and the "
        f"change belongs to {target_project}, run this from {origin_project} "
        "instead:\n\n"
        f"issuekit propose --to {target_project} --title {_quote_command_value(title.strip())} "
        "--body-file <proposal.md>\n\n"
        "If this is genuinely a local issue for the current project, rerun "
        "`issuekit author` with `--direct-local-author`."
    )


def _quote_command_value(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'

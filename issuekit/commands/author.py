"""Implementation of the author command."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from issuekit.commands._common import (
    load_config_for_project_mutation,
    print_json,
    require_ascii,
    run_command,
)
from issuekit.config import IssuekitConfig
from issuekit.config.refs import RefError, current_repo_ref, list_effective_refs
from issuekit.core import (
    VALID_ISSUE_PRIORITIES,
    Issue,
    is_valid_workflow_token,
    issue_dict,
)
from issuekit.guards.author import (
    STOP_SENTINEL,
    create_author_guard,
    guard_dict,
    stop_message,
)
from issuekit.issues.dependencies import bare_ref_collision_warnings, dependency_refs
from issuekit.issues.session import resolved_or_new_session_token
from issuekit.workflow import WorkflowError

_MIN_BARE_REF_NAME_LENGTH = 4
_INVOCATION_PREFIX_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])(?:uv\s+run|uvx|python\s+-m|npx|pnpm)\s*$",
    flags=re.IGNORECASE,
)
_DEPENDS_ON_LINE_PATTERN = re.compile(r"^[ \t]*Depends-On:", flags=re.IGNORECASE)


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
        "--depends-on",
        action="append",
        dest="depends_on",
        help="Attach an upstream dependency reference such as project#proposal:123.",
    )
    author_parser.add_argument(
        "--project",
        help=(
            "Explicit API project to author into when running outside a local "
            "issuekit project root."
        ),
    )
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
    author_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    author_parser.set_defaults(func=run)


def run(args) -> int:
    def action() -> int:
        config = load_config_for_project_mutation(
            Path.cwd(),
            command="author",
            project=args.project,
        )
        session = resolved_or_new_session_token("cli")
        authored = author_issue(
            title=args.title,
            body=args.body,
            body_file=args.body_file,
            priority=args.priority,
            agent=args.agent,
            assign=args.assign,
            depends_on=args.depends_on,
            config=config,
            cwd=Path.cwd(),
            direct_local_author=args.direct_local_author,
            origin_project=args.origin_project,
            session=session,
        )
        for warning in _author_warnings(authored):
            print(warning, file=sys.stderr)
        guard = create_author_guard(
            Path.cwd(),
            config=config,
            kind="issue",
            item_id=authored.id,
            ref=authored.ref,
            author_agent=args.agent,
            author_session=session,
        )

        if args.json:
            output = issue_dict(authored, include_body=True)
            output["authorGuard"] = guard_dict(guard)
            output["stop"] = STOP_SENTINEL
            print_json(output)
            return 0
        print(f"Authored issue: {authored.ref}")
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
    depends_on: list[str] | None = None,
    config: IssuekitConfig | None = None,
    cwd: Path | None = None,
    direct_local_author: bool = False,
    origin_project: str | None = None,
    session: str | None = None,
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
    dependency_refs = _depends_on(depends_on)
    require_ascii(issue_body, message="--body and --body-file must be ASCII-only.")
    _require_local_author_context(
        title=title,
        body=issue_body,
        config=config,
        cwd=cwd,
        direct_local_author=direct_local_author,
        origin_project=origin_project,
    )
    try:
        session = session or resolved_or_new_session_token("cli")
    except ValueError as exc:
        raise WorkflowError(str(exc), code="invalid_session") from exc
    from issuekit.store import get_store

    with get_store(config) as store:
        return store.create_issue(  # type: ignore[attr-defined]
            title=title.strip(),
            body=issue_body.strip(),
            priority=priority,
            author=agent,
            assignee=assign,
            session=session,
            depends_on=dependency_refs or None,
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


def _depends_on(value: list[str] | None) -> tuple[str, ...]:
    try:
        return dependency_refs(value)
    except ValueError as exc:
        raise WorkflowError(str(exc)) from exc


def _author_warnings(authored: Issue) -> tuple[str, ...]:
    warnings: list[str] = []
    if authored.warning:
        warnings.extend(line for line in authored.warning.splitlines() if line.strip())
    warnings.extend(bare_ref_collision_warnings(authored.dependencies))
    return _dedupe_warnings(warnings)


def _dedupe_warnings(warnings: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for warning in warnings:
        if warning in seen:
            continue
        seen.add(warning)
        deduped.append(warning)
    return tuple(deduped)


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
        reason=_related_ref_match_reason(mentioned[0]),
    )


def _related_ref_names(cwd: Path) -> set[str]:
    try:
        return set(list_effective_refs(cwd))
    except RefError as exc:
        raise WorkflowError(
            "Cross-project author preflight could not determine related projects: "
            f"{exc}. If this is genuinely a local issue for the current project, "
            "rerun `issuekit author` with `--direct-local-author`."
        ) from exc


def _safe_current_ref(cwd: Path) -> str:
    try:
        return current_repo_ref(cwd)
    except RefError:
        # The related-ref lookup already loaded and validated the same ref config.
        return ""


def _origin_project_context(origin_project: str | None) -> str:
    return (origin_project or os.environ.get("ISSUEKIT_ORIGIN_PROJECT", "")).strip()


def _mentioned_related_refs(text: str, refs: list[str]) -> list[str]:
    return [name for name in refs if _contains_ref_name(text, name)]


def _related_ref_match_reason(ref_name: str) -> str:
    if len(ref_name) < _MIN_BARE_REF_NAME_LENGTH:
        return (
            f"issue text contains a ref-style reference to related project {ref_name}; "
            f"bare project names shorter than {_MIN_BARE_REF_NAME_LENGTH} characters "
            "are ignored"
        )
    return f"issue text mentions related project {ref_name}"


def _tokenish_pattern(phrase: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<![A-Za-z0-9_-]){re.escape(phrase)}(?![A-Za-z0-9_-])",
        flags=re.IGNORECASE,
    )


def _contains_ref_name(text: str, ref_name: str) -> bool:
    text = _without_dependency_refs(text, ref_name)
    if _contains_ref_style(text, ref_name):
        return True
    if len(ref_name) < _MIN_BARE_REF_NAME_LENGTH:
        return False
    prose = _without_markdown_code(text)
    return any(
        _INVOCATION_PREFIX_PATTERN.search(prose, 0, match.start()) is None
        for match in _tokenish_pattern(ref_name).finditer(prose)
    )


def _contains_ref_style(text: str, phrase: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9_-]){re.escape(phrase)}#"
    return bool(re.search(pattern, text, flags=re.IGNORECASE))


def _without_dependency_refs(text: str, ref_name: str) -> str:
    ref_pattern = re.compile(
        rf"(?<![A-Za-z0-9_-]){re.escape(ref_name)}#"
        r"(?:(?:issue|proposal):)?[0-9]+(?![A-Za-z0-9_-])",
        flags=re.IGNORECASE,
    )
    lines = []
    for line in text.splitlines(keepends=True):
        if _DEPENDS_ON_LINE_PATTERN.match(line):
            line = ref_pattern.sub(lambda match: " " * len(match.group()), line)
        lines.append(line)
    return "".join(lines)


def _without_markdown_code(text: str) -> str:
    masked = list(text)
    fence_pattern = re.compile(r"(?m)^ {0,3}(?P<fence>`{3,}|~{3,})[^\r\n]*(?:\r?\n|$)")
    position = 0
    inline_limit = len(text)
    fenced_ranges: list[tuple[int, int]] = []

    while opening := fence_pattern.search(text, position):
        fence = opening.group("fence")
        closing_pattern = re.compile(
            rf"(?m)^ {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*(?:\r?\n|$)"
        )
        closing = closing_pattern.search(text, opening.end())
        if closing is None:
            inline_limit = opening.start()
            break
        fenced_ranges.append((opening.start(), closing.end()))
        position = closing.end()

    for start, end in fenced_ranges:
        _mask_code_range(masked, text, start, end)

    cursor = 0
    for start, end in fenced_ranges:
        _mask_inline_code(masked, text, cursor, min(start, inline_limit))
        cursor = end
        if cursor >= inline_limit:
            return "".join(masked)
    _mask_inline_code(masked, text, cursor, inline_limit)
    return "".join(masked)


def _mask_inline_code(masked: list[str], text: str, start: int, end: int) -> None:
    delimiter_pattern = re.compile(r"(?<!`)`+(?!`)")
    position = start
    while opening := delimiter_pattern.search(text, position, end):
        delimiter = opening.group()
        closing_pattern = re.compile(rf"(?<!`){re.escape(delimiter)}(?!`)")
        closing = closing_pattern.search(text, opening.end(), end)
        if closing is None:
            position = opening.end()
            continue
        _mask_code_range(masked, text, opening.start(), closing.end())
        position = closing.end()


def _mask_code_range(masked: list[str], text: str, start: int, end: int) -> None:
    for index in range(start, end):
        if text[index] not in "\r\n":
            masked[index] = " "


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

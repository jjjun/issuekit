"""Build and validate proposals from local issuekit inputs."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import date
from pathlib import Path

from issuekit.config import IssuekitConfig, load_config
from issuekit.config.refs import RefError, list_effective_refs
from issuekit.core import Issue, parse_issue_id_arg, parse_target_address
from issuekit.gitutil import git_short_head
from issuekit.inputs import require_ascii, resolve_text
from issuekit.issues.dependencies import dependency_refs
from issuekit.store import get_store

from .model import Proposal, ProposalError, origin_destination

DEPENDENCY_REF_TOKEN_PATTERN = re.compile(
    r"\b[A-Za-z0-9_.-]+#(?:(?:issue|proposal):)?[0-9]+\b"
)


STRUCTURED_DEPENDENCY_PATTERN = re.compile(
    r"(?im)^\s*(?:depends[-_ ]?on|upstream[-_ ]?dependency|dependency|"
    r"blocked[-_ ]?by|prerequisite)\s*:\s*(?P<refs>[^\n]+)$"
)


DEPENDENCY_LINE_PATTERN = re.compile(
    r"(?i)\b(depends?\s+on|requires?|prerequisite|blocked\s+by|upstream)\b"
)


def _proposal_text(value: object) -> str:
    return str(value or "").strip()


def build_proposal(
    cwd: Path,
    *,
    to: str | None,
    title: str | None,
    body: str | None,
    body_file: str | None,
    from_issue: str | None,
    reply: str | None,
    blocking: bool = False,
    depends_on: str | Sequence[str] | None = None,
    config: IssuekitConfig | None = None,
) -> Proposal:
    config = config or load_config(cwd)
    if not config.api_url:
        raise ProposalError(
            "Proposal commands require api_url in issuekit.toml/[tool.issuekit] or ISSUEKIT_API_URL."
        )

    source_issue: Issue | None = None
    reply_to = ""
    if reply is not None:
        source_issue = _get_issue(config, reply)
        reply_to = source_issue.metadata.get("origin", "").strip()
        if not reply_to:
            raise ProposalError(f"Issue #{source_issue.id} has no origin field.")
        to = to or origin_destination(reply_to)
    elif from_issue is not None:
        source_issue = _get_issue(config, from_issue)

    if not to:
        raise ProposalError("--to is required unless --reply is used.")
    try:
        target = parse_target_address(to, label="--to")
    except ValueError as exc:
        raise ProposalError(str(exc)) from exc
    to = target.repo

    title = title or (source_issue.title if source_issue is not None else "")
    if not title:
        raise ProposalError("--title is required unless --from-issue or --reply provides one.")

    proposal_body = _proposal_body(body, body_file, source_issue)
    require_ascii(
        title,
        proposal_body,
        message="--title/--body must be ASCII-only.",
        error=ProposalError,
    )
    dependency_refs = _proposal_dependency_refs(depends_on, proposal_body)
    origin_id = str(source_issue.id) if source_issue is not None and source_issue.id is not None else "0"
    origin_project = config.project
    origin = f"{origin_project}#{origin_id}@{_git_commit(cwd)}"
    warnings = proposal_preflight_warnings(
        origin_project=origin_project,
        target_project=to,
        body=proposal_body,
        depends_on=dependency_refs,
        is_reply=bool(reply_to),
        known_projects=_related_project_names(cwd),
    )
    return Proposal(
        origin=origin,
        to=to,
        target_worker=target.directed_worker,
        reply_to=reply_to,
        created=date.today().isoformat(),
        title=title,
        body=proposal_body,
        blocking=blocking,
        depends_on=dependency_refs,
        warnings=warnings,
    )


def proposal_preflight_warnings(
    *,
    origin_project: str,
    target_project: str,
    body: str,
    depends_on: Sequence[str],
    is_reply: bool,
    known_projects: Sequence[str] = (),
) -> tuple[str, ...]:
    warnings: list[str] = []
    if target_project == origin_project and not is_reply:
        warnings.append(
            "Self-target proposal preflight: this proposal targets the current "
            "project. Use `issuekit author` for local work unless this is a "
            "reply or cross-project handoff."
        )
    if not depends_on:
        dependency_projects = _dependency_project_mentions(body, known_projects=known_projects)
        upstream_projects = [
            project
            for project in dependency_projects
            if project not in {origin_project, target_project}
        ]
        if upstream_projects:
            project_list = ", ".join(upstream_projects)
            warnings.append(
                "Dependency preflight: proposal body appears to depend on "
                f"{project_list}, but no upstream reference was supplied. "
                "Create or propose the upstream owner work first, then pass "
                "`--depends-on <project#proposal:N>` or add a "
                "`Depends-On: <project#proposal:N>` body line. Use explicit "
                "project#issue:N or project#proposal:N refs when both could exist."
            )
    return tuple(warnings)


def _get_issue(config: IssuekitConfig, raw_id: str) -> Issue:
    issue_id = parse_issue_id_arg(raw_id)
    with get_store(config) as store:
        issue = store.get_issue(issue_id)
    if issue is None:
        raise LookupError(f"Issue #{issue_id} was not found.")
    return issue


def _proposal_body(body: str | None, body_file: str | None, source_issue: Issue | None) -> str:
    resolved = resolve_text(body, body_file)
    if resolved is not None:
        return resolved
    if source_issue is not None:
        return source_issue.body.strip()
    return "## Context\n\n## Suggested Change\n\n## Rationale"


def _proposal_dependency_refs(
    explicit: str | Sequence[str] | None,
    body: str,
) -> tuple[str, ...]:
    refs = [*_dependency_tuple(explicit), *_structured_dependency_refs(body)]
    return tuple(dict.fromkeys(refs))


def _dependency_tuple(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    try:
        if isinstance(value, str | Sequence):
            return dependency_refs(value)
        return dependency_refs(str(value))
    except ValueError as exc:
        raise ProposalError(str(exc)) from exc


def _structured_dependency_refs(body: str) -> tuple[str, ...]:
    refs: list[str] = []
    for match in STRUCTURED_DEPENDENCY_PATTERN.finditer(body):
        refs.extend(DEPENDENCY_REF_TOKEN_PATTERN.findall(match.group("refs")))
    return tuple(dict.fromkeys(refs))


def _related_project_names(cwd: Path) -> tuple[str, ...]:
    try:
        return tuple(list_effective_refs(cwd))
    except RefError:
        return ()


def _dependency_project_mentions(body: str, *, known_projects: Sequence[str]) -> tuple[str, ...]:
    projects: list[str] = []
    candidates = sorted(set(known_projects))
    for line in body.splitlines():
        if not DEPENDENCY_LINE_PATTERN.search(line):
            continue
        for project in candidates:
            if not _contains_project_name(line, project):
                continue
            if project not in projects:
                projects.append(project)
    return tuple(projects)


def _contains_project_name(text: str, project: str) -> bool:
    pattern = rf"(?<![A-Za-z0-9_-]){re.escape(project)}(?![A-Za-z0-9_-])"
    return bool(re.search(pattern, text, flags=re.IGNORECASE))


def _git_commit(cwd: Path) -> str:
    return git_short_head(cwd) or "unknown"

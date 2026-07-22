"""Public helpers for API-backed cross-repository proposals."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
import re
from typing import Any

from issuekit.api import IssuekitClient
from issuekit.config import IssuekitConfig, load_config
from issuekit.core import Issue, parse_issue_id_arg, parse_target_address
from issuekit.encoding import ASCII_ONLY_HINT, has_non_ascii
from issuekit.issues.dependencies import (
    DEPENDENCY_REF_PATTERN,
    bare_ref_collision_warnings,
    dependency_refs,
)
from issuekit.gitutil import git_short_head
from . import Proposal, ProposalError, origin_destination
from issuekit.config.refs import RefError, list_effective_refs
from issuekit.store import get_store
from issuekit.workflow import WorkflowError


OUTGOING_PROPOSAL_STATUSES = ("pending", "adopted", "discarded")
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
PROJECT_CATALOG_UNSUPPORTED_CODES = {
    "http_404",
    "http_405",
    "not_found",
    "method_not_allowed",
}


@dataclass(frozen=True)
class ProjectCatalog:
    projects: tuple[str, ...]
    source: str | None
    supported: bool


def adopt_outcome(proposal_id: str | int, project: str, issue: dict) -> dict:
    raw_issue_id = issue.get("id")
    try:
        issue_id = int(raw_issue_id)
    except (TypeError, ValueError):
        issue_id = None
    created_api_issue = issue_id is not None and issue_id > 0
    issue_ref = f"{project}#{issue_id}" if created_api_issue else None
    next_command = (
        f"issuekit claim --id {issue_id} --assignee <agent>"
        if created_api_issue
        else None
    )
    instruction = (
        f"Use issue #{issue_id} next."
        if created_api_issue
        else (
            "Adoption did not return a created API issue. Run `issuekit author` "
            "from the adopted proposal content to create an active API issue."
        )
    )
    outcome = dict(issue)
    outcome.update(
        {
            "api_result": "created_issue" if created_api_issue else "no_issue_created",
            "created_api_issue": created_api_issue,
            "proposal_id": str(proposal_id),
            "issue_id": issue_id if created_api_issue else None,
            "issue_ref": issue_ref,
            "next_command": next_command,
            "instruction": instruction,
            "issue": issue,
        }
    )
    return outcome


class ProposalAppendError(ProposalError):
    """Raised after adoption succeeds but appending extra issue content fails."""

    def __init__(self, message: str, *, outcome: dict, append_error: str) -> None:
        super().__init__(message)
        self.outcome = outcome
        self.append_error = append_error


def api_client(config: IssuekitConfig, *, project: str | None = None) -> IssuekitClient:
    if not config.api_url:
        raise ProposalError(
            "Proposal commands require api_url in issuekit.toml/[tool.issuekit] or ISSUEKIT_API_URL."
        )
    return IssuekitClient(
        config.api_url,
        project=project or config.project,
        timeout=config.api_timeout,
    )


def send_proposal(config: IssuekitConfig, proposal: Proposal) -> dict:
    """Create a proposal and annotate idempotent payload conflicts."""
    target_warnings = validate_target_project(config, proposal.to)
    with api_client(config, project=proposal.to) as client:
        created = client.create_proposal(
            origin=proposal.origin,
            title=proposal.title,
            body=proposal.body,
            reply_to=proposal.reply_to or None,
            blocking=True if proposal.blocking else None,
            depends_on=list(proposal.depends_on) or None,
            target_worker=proposal.target_worker or None,
        )
    result = dict(created)
    dependency_ref = proposal_dependency_ref(proposal.to, result.get("id"))
    if dependency_ref is not None:
        result["dependency_ref"] = dependency_ref
    if proposal.depends_on and "depends_on" not in result:
        result["depends_on"] = list(proposal.depends_on)
    warnings = [
        *target_warnings,
        *proposal.warnings,
        *bare_ref_collision_warnings(_dependency_rows_from_response(result)),
    ]
    if warnings:
        result["warnings"] = list(_dedupe_refs(warnings))
    mismatched = proposal_payload_mismatch(proposal, created)
    result["payload_mismatch"] = bool(mismatched)
    if mismatched:
        result["idempotent_existing"] = True
        result["payload_mismatch_fields"] = mismatched
        result["warning"] = payload_mismatch_guidance(proposal, created, mismatched)
    return result


def proposal_dependency_ref(project: str, proposal_id: object) -> str | None:
    try:
        raw_id = int(str(proposal_id).strip())
    except (TypeError, ValueError):
        return None
    if raw_id <= 0:
        return None
    return f"{project}#proposal:{raw_id}"


def adopt_proposal_with_append(
    config: IssuekitConfig,
    proposal_id: str | int,
    *,
    priority: str | None,
    append_text: str | None = None,
    append_file: str | None = None,
) -> dict:
    if append_text is not None and append_file is not None:
        raise ValueError("append_text and append_file are mutually exclusive.")
    raw_id = proposal_id_arg(str(proposal_id))
    with api_client(config) as client:
        issue = client.adopt_proposal(raw_id, priority=priority)
        if append_text is not None or append_file is not None:
            issue_id = _adopted_issue_id(issue)
            outcome = adopt_outcome(proposal_id, config.project, issue)
            if issue_id is None:
                message = (
                    "Adopted proposal, but no API issue id was returned; cannot append file."
                    if append_file is not None
                    else "Adoption did not return a created API issue; cannot append to the issue body."
                )
                raise ProposalAppendError(message, outcome=outcome, append_error=message)
            try:
                from issuekit.commands.edit import edit_issue
                from issuekit.store import ApiStore

                edit_issue(
                    issue_id,
                    append=append_text,
                    append_file=append_file,
                    config=config,
                    store=ApiStore(config, client=client),
                )
                issue = client.get_issue(issue_id)
            except (OSError, UnicodeError, ValueError, WorkflowError) as exc:
                raise ProposalAppendError(
                    f"Adopted proposal as issue #{issue_id}, but append failed: {exc}",
                    outcome=outcome,
                    append_error=str(exc),
                ) from exc
    return adopt_outcome(proposal_id, config.project, issue)


def proposal_payload_mismatch(proposal: Proposal, created: Mapping[str, Any]) -> list[str]:
    """Fields where an idempotent same-origin response differs from the request."""
    if created.get("origin") != proposal.origin:
        return []
    mismatched = []
    if _proposal_text(created.get("title")) != _proposal_text(proposal.title):
        mismatched.append("title")
    if _proposal_text(created.get("body")) != _proposal_text(proposal.body):
        mismatched.append("body")
    if (created.get("reply_to") or None) != (proposal.reply_to or None):
        mismatched.append("reply_to")
    if bool(created.get("blocking", False)) != proposal.blocking:
        mismatched.append("blocking")
    if "depends_on" in created and _dependency_tuple(created.get("depends_on")) != proposal.depends_on:
        mismatched.append("depends_on")
    if _proposal_text(created.get("target_worker")) != proposal.target_worker:
        mismatched.append("target_worker")
    return mismatched


def auto_adopt_incoming_proposals(config: IssuekitConfig) -> list[dict]:
    """Adopt pending inbox proposals that match this target project's policy."""
    policy = config.triage
    if not policy.trusted_origins:
        return []
    adopted: list[dict] = []
    with api_client(config) as client:
        for proposal in client.list_proposals(status="pending"):
            if len(adopted) >= policy.max_adoptions_per_cycle:
                break
            if not matches_triage_policy(proposal, config):
                continue
            issue = client.adopt_proposal(
                int(proposal["id"]),
                priority=policy.default_priority,
            )
            outcome = adopt_outcome(proposal["id"], config.project, issue)
            outcome["auto_adopted"] = True
            outcome["blocking"] = bool(proposal.get("blocking", False))
            adopted.append(outcome)
    return adopted


def payload_mismatch_guidance(
    proposal: Proposal,
    created: Mapping[str, Any],
    mismatched: Sequence[str],
) -> str:
    return (
        f"Proposal was not sent: {proposal.to} already has pending proposal "
        f"#{created.get('id')} with origin {proposal.origin} but different "
        f"{', '.join(mismatched)}. Use --from-issue <id> to derive a distinct "
        f"origin, or adopt/discard the stale pending proposal in {proposal.to}. "
        "Avoid reusing the implicit #0 origin for unrelated proposals from one commit."
    )


def list_outgoing_proposals(
    config: IssuekitConfig,
    *,
    to: str,
    status: str | None = None,
) -> list[dict]:
    """List proposals this project sent to another project's inbox (read-only)."""
    to = _target_repo(to, label="--to")
    validate_target_project(config, to)
    if status is not None and status not in OUTGOING_PROPOSAL_STATUSES:
        raise ProposalError(
            f"Invalid proposal status: {status}. "
            f"Expected one of {', '.join(OUTGOING_PROPOSAL_STATUSES)}."
        )
    statuses = (status,) if status else OUTGOING_PROPOSAL_STATUSES
    outgoing = []
    with api_client(config, project=to) as client:
        for candidate_status in statuses:
            outgoing.extend(
                proposal
                for proposal in client.list_proposals(status=candidate_status)
                if _is_own_origin(proposal.get("origin"), config.project)
            )
    outgoing.sort(key=lambda proposal: int(proposal.get("id", 0)))
    return outgoing


def get_outgoing_proposal(config: IssuekitConfig, *, to: str, proposal_id: int) -> dict:
    """Read one proposal this project sent to another project's inbox."""
    to = _target_repo(to, label="--to")
    validate_target_project(config, to)
    with api_client(config, project=to) as client:
        proposal = client.get_proposal(int(proposal_id))
    if not _is_own_origin(proposal.get("origin"), config.project):
        raise ProposalError(
            f"Proposal #{proposal_id} in {to} was not sent by {config.project}."
        )
    return proposal


def _is_own_origin(origin: object, project: str) -> bool:
    return isinstance(origin, str) and origin.startswith(f"{project}#")


def matches_triage_policy(proposal: Mapping[str, Any], config: IssuekitConfig) -> bool:
    origin = proposal.get("origin")
    if not isinstance(origin, str):
        return False
    try:
        origin_project = origin_destination(origin)
    except ProposalError:
        return False
    if origin_project not in config.triage.trusted_origins:
        return False
    if config.triage.require_blocking and not bool(proposal.get("blocking", False)):
        return False
    return True


def validate_target_project(config: IssuekitConfig, target_project: str) -> tuple[str, ...]:
    """Validate proposal targets against the API's project catalog when available."""
    target_project = _target_repo(target_project, label="target project")
    catalog = fetch_project_catalog(config)
    if catalog.supported:
        if target_project in catalog.projects:
            return ()
        raise ProposalError(_unknown_target_project_message(target_project, catalog.projects))
    if catalog.source is not None:
        return ()
    return (
        "Target project preflight: API server did not expose a project catalog; "
        f"cannot validate target project {target_project}.",
    )


def fetch_project_catalog(config: IssuekitConfig) -> ProjectCatalog:
    if not config.api_url:
        raise ProposalError(
            "Proposal commands require api_url in issuekit.toml/[tool.issuekit] or ISSUEKIT_API_URL."
        )
    with api_client(config) as client:
        # A supported-but-empty profile catalog must still fail closed, so track
        # whether any catalog endpoint responded rather than treating an empty
        # profile list as compatibility success. Fall through to the worker
        # registry before deciding when project profiles are empty.
        profile_source: str | None = None
        try:
            profile_projects = _project_names_from_rows(client.list_project_profiles())
        except WorkflowError as exc:
            if exc.code not in PROJECT_CATALOG_UNSUPPORTED_CODES:
                raise
        else:
            profile_source = "project profiles"
            if profile_projects:
                return ProjectCatalog(profile_projects, profile_source, True)

        try:
            worker_projects = _project_names_from_rows(client.list_workers())
        except WorkflowError as exc:
            if exc.code not in PROJECT_CATALOG_UNSUPPORTED_CODES:
                raise
        else:
            if worker_projects:
                return ProjectCatalog(worker_projects, "worker registry", True)
            # Worker registry responded but is empty; the API is catalog-aware,
            # so report a supported empty catalog and fail closed.
            return ProjectCatalog((), profile_source or "worker registry", True)

    if profile_source is not None:
        # Project profiles responded (empty) but the worker registry endpoint is
        # unsupported. The API still exposes a catalog, so fail closed.
        return ProjectCatalog((), profile_source, True)
    return ProjectCatalog((), None, False)


def _project_names_from_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    projects: list[str] = []
    for row in rows:
        project = str(row.get("project") or "").strip()
        if project and project not in projects:
            projects.append(project)
    return tuple(sorted(projects))


def _unknown_target_project_message(target_project: str, known_projects: Sequence[str]) -> str:
    if not known_projects:
        return f"Unknown target project '{target_project}'. No registered API projects were returned."
    preview = ", ".join(known_projects[:8])
    if len(known_projects) > 8:
        return (
            f"Unknown target project '{target_project}'. "
            f"{len(known_projects)} registered API projects are available; "
            f"first projects: {preview}."
        )
    return (
        f"Unknown target project '{target_project}'. "
        f"Registered API projects: {preview}."
    )


def _target_repo(value: str, *, label: str) -> str:
    try:
        return parse_target_address(value, label=label).repo
    except ValueError as exc:
        raise ProposalError(str(exc)) from exc


def _proposal_text(value: object) -> str:
    return str(value or "").strip()


def _adopted_issue_id(issue: dict) -> int | None:
    try:
        issue_id = int(issue.get("id"))
    except (TypeError, ValueError):
        return None
    return issue_id if issue_id > 0 else None


def proposal_id_arg(value: str) -> int:
    try:
        proposal_id = int(value)
    except ValueError as exc:
        raise ProposalError(f"Proposal id must be an integer in API mode: {value}") from exc
    if proposal_id <= 0:
        raise ProposalError(f"Proposal id must be positive: {value}")
    return proposal_id


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
    if has_non_ascii(title) or has_non_ascii(proposal_body):
        raise ProposalError(
            f"--title/--body must be ASCII-only. {ASCII_ONLY_HINT}"
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
    if body is not None:
        return body.strip()
    if body_file:
        return Path(body_file).read_text(encoding="utf-8-sig").strip()
    if source_issue is not None:
        return source_issue.body.strip()
    return "## Context\n\n## Suggested Change\n\n## Rationale"


def _proposal_dependency_refs(
    explicit: str | Sequence[str] | None,
    body: str,
) -> tuple[str, ...]:
    refs = [*_dependency_tuple(explicit), *_structured_dependency_refs(body)]
    return _dedupe_refs(refs)


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
    return _dedupe_refs(refs)


def _dedupe_refs(refs: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    deduped: list[str] = []
    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        deduped.append(ref)
    return tuple(deduped)


def _dependency_rows_from_response(raw: Mapping[str, Any]) -> tuple[dict[str, object], ...]:
    rows: list[dict[str, object]] = []
    _extend_dependency_rows(rows, raw)
    issue = raw.get("issue")
    if isinstance(issue, Mapping):
        _extend_dependency_rows(rows, issue)
    proposal = raw.get("proposal")
    if isinstance(proposal, Mapping):
        _extend_dependency_rows(rows, proposal)
    return tuple(rows)


def _extend_dependency_rows(rows: list[dict[str, object]], raw: Mapping[str, Any]) -> None:
    for key in ("dependencies", "dependency_resolutions", "resolved_dependencies"):
        value = raw.get(key)
        if not isinstance(value, list):
            continue
        rows.extend(dict(item) for item in value if isinstance(item, Mapping))


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

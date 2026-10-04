"""Public helpers for API-backed cross-repository proposals."""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from issuekit.api import IssuekitClient
from issuekit.api.factory import client_for, require_api_url
from issuekit.config import IssuekitConfig, load_config
from issuekit.config.refs import RefError, list_effective_refs
from issuekit.core import Issue, parse_issue_id_arg, parse_target_address
from issuekit.errors import WorkflowError
from issuekit.gitutil import git_short_head
from issuekit.inputs import active_issue_not_found, read_text_file, require_ascii, resolve_text
from issuekit.issues.dependencies import (
    bare_ref_collision_warnings,
    dependency_refs,
)
from issuekit.store import get_store
from issuekit.timestamps import parse_timestamp

from .model import Proposal, ProposalError, origin_destination

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
ADOPT_APPEND_RETRY_DELAYS = (0.1, 0.2, 0.4, 0.8, 1.6)
_sleep = time.sleep


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


class AdoptedIssueHoldError(WorkflowError):
    """Raised when an adopted issue could not be held for human release."""

    def __init__(
        self,
        issue_id: int,
        *,
        origin: str,
        reason: str,
        error: Exception,
    ) -> None:
        super().__init__(
            f"Could not hold adopted issue #{issue_id} for human release: {error}",
            code="hold_failed",
        )
        self.issue_id = issue_id
        self.origin = origin
        self.reason = reason


def hold_adopted_issue(
    config: IssuekitConfig,
    issue_id: int,
    *,
    origin: str,
    reason: str,
) -> bool:
    """Keep an automatically adopted issue out of the implement pool."""
    if not config.triage.hold_auto_adopted:
        return False
    note = (
        f"Held for human release: adopted automatically from {origin} by {reason}. "
        f"Release with: issuekit plan {issue_id} --stage todo"
    )
    try:
        with api_client(config) as client:
            client.plan(issue_id, stage="planned", note=note)
    except (WorkflowError, ProposalError, ValueError) as exc:
        raise AdoptedIssueHoldError(
            issue_id,
            origin=origin,
            reason=reason,
            error=exc,
        ) from exc
    return True


def api_client(config: IssuekitConfig, *, project: str | None = None) -> IssuekitClient:
    require_api_url(config, "Proposal command", error=ProposalError)
    return client_for(
        config,
        project=project,
    )


def send_proposal(config: IssuekitConfig, proposal: Proposal) -> dict:
    """Create a proposal and annotate idempotent payload conflicts."""
    validate_target_project(config, proposal.to)
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
    deduplicated = result.pop("was_created", None) is False
    result["deduplicated"] = deduplicated
    dependency_ref = proposal_dependency_ref(proposal.to, result.get("id"))
    if dependency_ref is not None:
        result["dependency_ref"] = dependency_ref
    if proposal.depends_on and "depends_on" not in result:
        result["depends_on"] = list(proposal.depends_on)
    warnings = [
        *proposal.warnings,
        *bare_ref_collision_warnings(_dependency_rows_from_response(result)),
    ]
    if warnings:
        result["warnings"] = list(dict.fromkeys(warnings))
    if deduplicated:
        result["idempotent_existing"] = True
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
    appended_text: str | None = None
    if append_text is not None or append_file is not None:
        appended_text = _append_text(append_text, append_file)
    with api_client(config) as client:
        issue = client.adopt_proposal(raw_id, priority=priority)
        if appended_text is not None:
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
                from issuekit.issues.service import edit_issue
                from issuekit.store import ApiStore

                issue = _append_and_verify_adopted_issue(
                    issue_id,
                    appended_text,
                    config=config,
                    client=client,
                    edit_issue=edit_issue,
                    store=ApiStore(config, client=client),
                )
            except (OSError, UnicodeError, ValueError, WorkflowError) as exc:
                raise ProposalAppendError(
                    _append_failure_message(issue_id, exc),
                    outcome=outcome,
                    append_error=str(exc),
                ) from exc
    outcome = adopt_outcome(proposal_id, config.project, issue)
    if appended_text is not None:
        outcome["append_applied"] = True
        outcome["appended_chars"] = len(appended_text)
    return outcome


def _append_text(append_text: str | None, append_file: str | None) -> str:
    if append_text is not None:
        appended_text = append_text.strip()
    elif append_file is not None:
        try:
            appended_text = read_text_file(append_file).strip()
        except OSError as exc:
            raise ProposalError(f"Could not read append file '{append_file}'.") from exc
    else:
        raise ValueError("append text or append file is required.")
    require_ascii(appended_text, message="--append and --append-file must be ASCII-only.")
    if not appended_text:
        raise ValueError("Append text is empty; nothing to append.")
    return appended_text


def _append_and_verify_adopted_issue(
    issue_id: int,
    appended_text: str,
    *,
    config: IssuekitConfig,
    client: IssuekitClient,
    edit_issue,
    store,
) -> dict:
    retry_delays = iter(ADOPT_APPEND_RETRY_DELAYS)
    while True:
        try:
            edit_issue(
                issue_id,
                append=appended_text,
                config=config,
                store=store,
            )
        except (OSError, UnicodeError, ValueError, WorkflowError) as exc:
            if not _is_adopted_issue_not_found(exc, issue_id) or not _sleep_for_retry(retry_delays):
                raise
        else:
            break

    while True:
        try:
            issue = client.get_issue(issue_id)
        except WorkflowError as exc:
            if not _is_adopted_issue_not_found(exc, issue_id):
                raise
        else:
            body = issue.get("body")
            if isinstance(body, str) and appended_text in body:
                return issue
        if not _sleep_for_retry(retry_delays):
            raise ValueError(f"Issue #{issue_id} does not contain the appended text after retrying.")


def _is_adopted_issue_not_found(exc: Exception, issue_id: int) -> bool:
    return (isinstance(exc, WorkflowError) and exc.code == "not_found") or (
        type(exc) is ValueError and str(exc) == active_issue_not_found(issue_id)
    )


def _sleep_for_retry(retry_delays: Iterator[float]) -> bool:
    try:
        delay = next(retry_delays)
    except StopIteration:
        return False
    _sleep(delay)
    return True


def _append_failure_message(issue_id: int, exc: Exception) -> str:
    if str(exc) == f"Issue #{issue_id} does not contain the appended text after retrying.":
        return (
            f"Adopted proposal as issue #{issue_id}. The append update was accepted but could "
            f"not be confirmed. Check with `issuekit show {issue_id} --json` before re-appending."
        )
    return (
        f"Adopted proposal as issue #{issue_id}, but append failed: {exc} "
        "The issue is already in the open implement pool without the appended text. "
        f"Recover with `issuekit edit {issue_id} --append-file <file>` or the MCP `update_issue` tool."
    )


def proposal_payload_mismatch(proposal: Proposal, created: Mapping[str, Any]) -> list[str]:
    """Fields where a deduplicated response differs from the request."""
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


def _proposal_origin_issue_id(origin: str) -> int | None:
    match = re.fullmatch(r"[^#]+#(?P<issue_id>[0-9]+)@[^@]+", origin)
    return int(match.group("issue_id")) if match is not None else None


def auto_adopt_incoming_proposals(
    config: IssuekitConfig,
    *,
    on_adoption_error: Callable[[object, Exception], None] | None = None,
) -> list[dict]:
    """Adopt pending inbox proposals that match this target project's policy."""
    policy = config.triage
    if not policy.trusted_origins:
        return []
    from issuekit.proposals.service import list_incoming_proposals

    pending = list_incoming_proposals(config)
    adopted: list[dict] = []
    with api_client(config) as client:
        for proposal in pending:
            if len(adopted) >= policy.max_adoptions_per_cycle:
                break
            if not matches_triage_policy(proposal, config):
                continue
            proposal_id = proposal.get("id")
            try:
                issue = client.adopt_proposal(
                    int(proposal_id),
                    priority=policy.default_priority,
                )
            except Exception as exc:
                if on_adoption_error is None:
                    raise
                on_adoption_error(proposal_id, exc)
                continue
            outcome = adopt_outcome(proposal["id"], config.project, issue)
            outcome["auto_adopted"] = True
            outcome["blocking"] = bool(proposal.get("blocking", False))
            if outcome.get("issue_id") is not None and policy.hold_auto_adopted:
                origin = str(proposal.get("origin", ""))
                outcome["origin"] = origin
                reason = "serve auto-adopt"
                outcome["next_command"] = (
                    f"issuekit plan {outcome['issue_id']} --stage todo"
                )
                outcome["instruction"] = (
                    f"Issue #{outcome['issue_id']} requires human release. "
                    "Release it with the next command."
                )
                try:
                    outcome["held"] = hold_adopted_issue(
                        config,
                        int(outcome["issue_id"]),
                        origin=origin,
                        reason=reason,
                    )
                except AdoptedIssueHoldError as exc:
                    outcome["hold_error"] = str(exc)
                    outcome["hold_origin"] = exc.origin
                    outcome["hold_reason"] = exc.reason
            adopted.append(outcome)
    return adopted


def payload_mismatch_guidance(
    proposal: Proposal,
    created: Mapping[str, Any],
    mismatched: Sequence[str],
) -> str:
    source_issue_id = _proposal_origin_issue_id(proposal.origin)
    if source_issue_id not in (None, 0):
        proposal_id = created.get("id")
        return (
            f"Proposal was not sent: source issue #{source_issue_id} already has pending proposal "
            f"#{proposal_id} in {proposal.to} from this commit with different {', '.join(mismatched)}. "
            "One source issue can have only "
            "one pending proposal per target project per commit. Send a separate proposal "
            "without --from-issue (implicit #0 origin; dropping --reply also drops the reply link), "
            "or adopt/discard "
            f"pending proposal #{proposal_id} first."
        )
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
    with api_client(config, project=to) as client:
        validate_target_project(config, to, client=client)
        if status is not None and status not in OUTGOING_PROPOSAL_STATUSES:
            raise ProposalError(
                f"Invalid proposal status: {status}. "
                f"Expected one of {', '.join(OUTGOING_PROPOSAL_STATUSES)}."
            )
        statuses = (status,) if status else OUTGOING_PROPOSAL_STATUSES
        outgoing = list_outgoing_proposal_rows(
            client,
            project=config.project,
            statuses=statuses,
        )
        return _enrich_outgoing_proposals(outgoing, client)


def list_outgoing_proposal_rows(
    client: IssuekitClient,
    *,
    project: str,
    statuses: Sequence[str],
) -> list[dict]:
    """List raw proposals sent by a project without fetching enrichment data."""
    outgoing = [
        proposal
        for status in statuses
        for proposal in client.list_proposals(status=status)
        if _is_own_origin(proposal.get("origin"), project)
    ]
    outgoing.sort(key=lambda proposal: int(proposal.get("id", 0)))
    return outgoing


def get_outgoing_proposal(config: IssuekitConfig, *, to: str, proposal_id: int) -> dict:
    """Read one proposal this project sent to another project's inbox."""
    to = _target_repo(to, label="--to")
    with api_client(config, project=to) as client:
        validate_target_project(config, to, client=client)
        proposal = client.get_proposal(int(proposal_id))
        if not _is_own_origin(proposal.get("origin"), config.project):
            raise ProposalError(
                f"Proposal #{proposal_id} in {to} was not sent by {config.project}."
            )
        return _enrich_outgoing_proposals([proposal], client)[0]


def discard_outgoing_proposal(config: IssuekitConfig, *, to: str, proposal_id: int) -> dict:
    """Discard one pending proposal this project sent to another project's inbox."""
    to = _target_repo(to, label="--to")
    validate_target_project(config, to)
    with api_client(config, project=to) as client:
        proposal = client.get_proposal(int(proposal_id))
        if not _is_own_origin(proposal.get("origin"), config.project):
            raise ProposalError(
                f"Proposal #{proposal_id} in {to} was not sent by {config.project}; "
                "refusing to discard it."
            )
        return client.discard_proposal(int(proposal_id))


def _with_adopted_issue_state(proposal: Mapping[str, Any], client: IssuekitClient) -> dict:
    enriched = dict(proposal)
    enriched["adopted_issue_status"] = None
    enriched["adopted_issue_stage"] = None
    enriched["proposal_checks"] = [
        _with_proposal_check_waiting_time(check)
        for check in client.list_proposal_checks_for_proposal(int(proposal["id"]))
    ]
    try:
        adopted_issue_number = int(proposal.get("adopted_issue_number"))
    except (TypeError, ValueError):
        return enriched
    if adopted_issue_number <= 0:
        return enriched
    try:
        issue = client.get_issue(adopted_issue_number)
    except WorkflowError:
        return enriched
    enriched["adopted_issue_status"] = issue.get("status")
    enriched["adopted_issue_stage"] = issue.get("stage")
    return enriched


def _enrich_outgoing_proposals(
    proposals: Sequence[Mapping[str, Any]],
    client: IssuekitClient,
) -> list[dict]:
    if not proposals:
        return []
    with ThreadPoolExecutor(max_workers=min(4, len(proposals))) as executor:
        futures = [
            executor.submit(_with_adopted_issue_state, proposal, client)
            for proposal in proposals
        ]
        return [future.result() for future in futures]


def _with_proposal_check_waiting_time(check: Mapping[str, Any]) -> dict:
    enriched = dict(check)
    if check.get("status") != "pending":
        return enriched
    created_at = parse_timestamp(check.get("created_at"))
    if created_at is not None:
        enriched["waiting_seconds"] = max(
            0,
            int((datetime.now(UTC) - created_at).total_seconds()),
        )
    return enriched


def _is_own_origin(origin: object, project: str) -> bool:
    return isinstance(origin, str) and origin.startswith(f"{project}#")


def matches_triage_policy(proposal: Mapping[str, Any], config: IssuekitConfig) -> bool:
    if proposal.get("thread_id") is not None:
        return False
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


def validate_target_project(
    config: IssuekitConfig,
    target_project: str,
    *,
    client: IssuekitClient | None = None,
) -> None:
    """Validate proposal targets against the API's project catalog."""
    target_project = _target_repo(target_project, label="target project")
    projects = fetch_project_catalog(config, client=client)
    if target_project not in projects:
        raise ProposalError(_unknown_target_project_message(target_project, projects))


def fetch_project_catalog(
    config: IssuekitConfig,
    *,
    client: IssuekitClient | None = None,
) -> tuple[str, ...]:
    if client is not None:
        return _fetch_project_catalog(client)
    with api_client(config) as catalog_client:
        return _fetch_project_catalog(catalog_client)


def _fetch_project_catalog(client: IssuekitClient) -> tuple[str, ...]:
    profile_projects = _project_names_from_rows(client.list_project_profiles())
    worker_projects = _project_names_from_rows(client.list_workers())
    return tuple(sorted(set(profile_projects) | set(worker_projects)))


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
        raise ProposalError(f"Proposal id must be an integer: {value}") from exc
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

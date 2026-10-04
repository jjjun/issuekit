"""Adopt incoming proposals and apply adoption helpers."""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator, Mapping
from typing import Any

from issuekit.api import IssuekitClient
from issuekit.config import IssuekitConfig
from issuekit.errors import WorkflowError
from issuekit.inputs import active_issue_not_found, read_text_file, require_ascii

from .client import api_client, proposal_id_arg
from .model import ProposalError, origin_destination

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


def _adopted_issue_id(issue: dict) -> int | None:
    try:
        issue_id = int(issue.get("id"))
    except (TypeError, ValueError):
        return None
    return issue_id if issue_id > 0 else None

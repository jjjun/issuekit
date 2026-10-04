"""Agent-refined triage of incoming proposals (triage-author mode).

When ``[tool.issuekit.triage] author_agent`` is configured, ``serve --triage``
and ``issuekit triage --once`` run the configured agent against each
policy-matching pending proposal and let it adopt the proposal as an
implementation-ready issue (appending an authored spec), reply to the origin
project for clarification, discard it as out of scope, or adopt and send a
targeted follow-up to the origin.

The agent run is an author-type session: it inspects the checkout read-only and
never implements or mutates tracker state. This module applies the parsed
decision through the existing proposal helpers; the agent only emits JSON.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, TextIO

from issuekit.agentrun import AgentRunner
from issuekit.agents.proposal_eval import (
    proposal_dependencies_text,
    run_readonly_proposal_evaluation,
)
from issuekit.agents.readonly import prompt_from_spec
from issuekit.agents.registry import resolve_adapter
from issuekit.agents.triage_state import (
    load_state,
    now,
    save_state,
)
from issuekit.config import IssuekitConfig
from issuekit.encoding import has_non_ascii
from issuekit.errors import WorkflowError
from issuekit.prompts import (
    TRIAGE_PROMPT,
    TriageAuthorParseError,
    canonical_contract_token,
    fence_untrusted,
)
from issuekit.proposals import origin_destination
from issuekit.proposals.api import (
    AdoptedIssueHoldError,
    ProposalError,
    adopt_proposal_with_append,
    api_client,
    build_proposal,
    hold_adopted_issue,
    matches_triage_policy,
    send_proposal,
)

_DECISIONS = {"adopt", "adopt_and_reply", "reply", "discard"}
_DECISION_FIELD = {
    "adopt": "spec_markdown",
    "adopt_and_reply": "spec_markdown",
    "reply": "question",
    "discard": "reason",
}
_SUPERSEDES_LINE_PATTERN = re.compile(
    r"^\s*Supersedes:\s*(?P<project>[A-Za-z0-9_.-]+)#(?P<id>[1-9][0-9]*)\s*$"
)

LogFn = Callable[..., None]


@dataclass(frozen=True)
class TriageDecision:
    """A parsed and applied triage-author decision for one proposal."""

    proposal_id: int
    origin: str
    decision: str
    detail: str
    issue_id: int | None = None
    reply_ref: str | None = None
    error: str | None = None
    held: bool = False
    hold_error: bool = False

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "proposal_id": self.proposal_id,
            "origin": self.origin,
            "decision": self.decision,
            "detail": self.detail,
        }
        if self.issue_id is not None:
            data["issue_id"] = self.issue_id
        if self.reply_ref is not None:
            data["reply_ref"] = self.reply_ref
        if self.error is not None:
            data["error"] = self.error
        if self.held:
            data["held"] = True
        if self.hold_error:
            data["hold_error"] = True
        return data


def parse_triage_output(stdout: str) -> dict[str, str]:
    """Parse the newest well-formed ```triage``` block from agent stdout."""

    raw = TRIAGE_PROMPT.parse_json(stdout)
    return _decision_from_json(raw)


def _decision_from_json(raw: dict[str, object]) -> dict[str, str]:
    raw_decision = raw.get("decision")
    decision = canonical_contract_token(raw_decision, _DECISIONS)
    if decision is None:
        raise TriageAuthorParseError(f"Invalid triage decision: {raw_decision!r}")
    field = _DECISION_FIELD[decision]
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise TriageAuthorParseError(
            f"Triage decision '{decision}' requires a non-empty '{field}'."
        )
    if has_non_ascii(f"{decision}\n{value}"):
        raise TriageAuthorParseError("Triage fields must be ASCII-only.")
    parsed = {"decision": decision, field: value.strip()}
    if decision == "adopt_and_reply":
        reply_markdown = raw.get("reply_markdown")
        if not isinstance(reply_markdown, str) or not reply_markdown.strip():
            raise TriageAuthorParseError(
                "Triage decision 'adopt_and_reply' requires a non-empty "
                "'reply_markdown'."
            )
        if has_non_ascii(reply_markdown):
            raise TriageAuthorParseError("Triage fields must be ASCII-only.")
        parsed["reply_markdown"] = reply_markdown.strip()
    return parsed


def run_triage_author_cycle(
    config: IssuekitConfig,
    cwd: Path,
    *,
    timeout: float = 600.0,
    model: str | None = None,
    reasoning_effort: str | None = None,
    runner_factory=None,
    log: LogFn | None = None,
    err: TextIO | None = None,
    abort_event: threading.Event | None = None,
) -> list[TriageDecision]:
    """Run one agent-backed triage cycle over policy-matching pending proposals."""

    agent = config.triage.author_agent
    if not agent:
        raise WorkflowError(
            "triage-author mode requires [tool.issuekit.triage] author_agent."
        )
    err = err or sys.stderr
    emit = log or (lambda *args, **kwargs: None)
    runner_factory = runner_factory or AgentRunner

    adapter = resolve_adapter(
        agent,
        config=config,
        model=model,
        reasoning_effort=reasoning_effort,
        role="triage",
    )
    state = load_state(cwd)
    decisions: list[TriageDecision] = []
    evaluated = 0
    limit = config.triage.max_adoptions_per_cycle

    with api_client(config) as client:
        pending = client.list_proposals(status="pending")

    for proposal in pending:
        if abort_event is not None and abort_event.is_set():
            break
        if evaluated >= limit:
            break
        if not matches_triage_policy(proposal, config):
            continue
        proposal_id = int(proposal["id"])
        fingerprint = _proposal_fingerprint(proposal)
        body_sha = _body_sha(proposal.get("body", ""))
        if _skip_replied(state, proposal_id, fingerprint, body_sha):
            reason = (
                "reply_suppressed"
                if "suppressed_at" in state.get(str(proposal_id), {})
                else "replied"
            )
            emit("triage_author_skip", proposal=proposal_id, reason=reason)
            continue

        evaluated += 1
        try:
            parsed = _evaluate_proposal(
                proposal,
                agent=agent,
                adapter=adapter,
                cwd=cwd,
                timeout=timeout,
                runner_factory=runner_factory,
                err=err,
                abort_event=abort_event,
            )
        except (
            FileNotFoundError,
            RuntimeError,
            ValueError,
            TimeoutError,
            WorkflowError,
            TriageAuthorParseError,
        ) as exc:
            emit("triage_author_error", proposal=proposal_id, error=str(exc))
            decisions.append(
                TriageDecision(
                    proposal_id=proposal_id,
                    origin=str(proposal.get("origin", "")),
                    decision="error",
                    detail="",
                    error=str(exc),
                )
            )
            continue

        decision = _apply_decision(
            proposal,
            parsed,
            config=config,
            cwd=cwd,
            state=state,
            fingerprint=fingerprint,
            emit=emit,
        )
        decisions.append(decision)
        if decision.error is not None:
            emit(
                "triage_author_error",
                proposal=proposal_id,
                error=decision.error,
            )
        else:
            emit(
                "triage_author_decision",
                proposal=proposal_id,
                decision=decision.decision,
                issue=decision.issue_id if decision.issue_id is not None else decision.detail,
            )

    save_state(cwd, state)
    return decisions


def _evaluate_proposal(
    proposal: Mapping[str, Any],
    *,
    agent: str,
    adapter,
    cwd: Path,
    timeout: float,
    runner_factory,
    err: TextIO,
    abort_event: threading.Event | None,
) -> dict[str, str]:
    proposal_id = int(proposal["id"])
    stdout = run_readonly_proposal_evaluation(
        proposal,
        agent=agent,
        adapter=adapter,
        cwd=cwd,
        timeout=timeout,
        runner_factory=runner_factory,
        err=err,
        prompt=prompt_from_spec(
            TRIAGE_PROMPT,
            cwd=cwd,
            filename=f"triage-proposal-{proposal_id}.md",
            body=_render_triage_prompt(proposal),
        ),
        label="Triage",
        mutation_log_message=(
            "ERROR: triage-author run modified repository state; ignoring its output."
        ),
        abort_event=abort_event,
    )
    return parse_triage_output(stdout)


def _apply_decision(
    proposal: Mapping[str, Any],
    parsed: dict[str, str],
    *,
    config: IssuekitConfig,
    cwd: Path,
    state: dict[str, dict[str, str]],
    fingerprint: str,
    emit: LogFn,
) -> TriageDecision:
    proposal_id = int(proposal["id"])
    origin = str(proposal.get("origin", ""))
    decision = parsed["decision"]
    try:
        if decision in {"adopt", "adopt_and_reply"}:
            spec = parsed["spec_markdown"]
            outcome = adopt_proposal_with_append(
                config,
                proposal_id,
                priority=config.triage.default_priority,
                append_text=spec,
            )
            issue_id = outcome.get("issue_id")
            held = False
            if issue_id is not None:
                held = hold_adopted_issue(
                    config,
                    int(issue_id),
                    origin=origin,
                    reason="triage author",
                )
            state.pop(str(proposal_id), None)
            _discard_superseded_pending_proposal(
                proposal,
                config=config,
                state=state,
                emit=emit,
            )
            reply_ref = None
            if decision == "adopt_and_reply" and not proposal.get("reply_to"):
                try:
                    reply_ref = _send_reply(
                        proposal,
                        parsed["reply_markdown"],
                        config=config,
                        cwd=cwd,
                        from_issue=str(outcome["issue_id"]),
                    )
                except ProposalError as exc:
                    return TriageDecision(
                        proposal_id=proposal_id,
                        origin=origin,
                        decision=decision,
                        detail=str(outcome.get("issue_ref") or ""),
                        issue_id=outcome.get("issue_id"),
                        error=str(exc),
                        held=held,
                    )
            elif decision == "adopt_and_reply":
                emit("triage_author_reply_suppressed", proposal=proposal_id)
            return TriageDecision(
                proposal_id=proposal_id,
                origin=origin,
                decision="adopt" if reply_ref is None else decision,
                detail=str(outcome.get("issue_ref") or ""),
                issue_id=issue_id,
                reply_ref=reply_ref,
                held=held,
            )
        if decision == "reply":
            if proposal.get("reply_to"):
                emit("triage_author_reply_suppressed", proposal=proposal_id)
                state[str(proposal_id)] = {
                    "fingerprint": fingerprint,
                    "suppressed_at": now(),
                }
                save_state(cwd, state)
                return TriageDecision(
                    proposal_id=proposal_id,
                    origin=origin,
                    decision="reply_suppressed",
                    detail="Automatic replies to replies are suppressed.",
                )
            question = parsed["question"]
            issue_ref = _send_reply(proposal, question, config=config, cwd=cwd)
            state[str(proposal_id)] = {
                "fingerprint": fingerprint,
                "replied_at": now(),
            }
            save_state(cwd, state)
            return TriageDecision(
                proposal_id=proposal_id,
                origin=origin,
                decision="reply",
                detail=issue_ref,
            )
        reason = parsed["reason"]
        with api_client(config) as client:
            client.discard_proposal(proposal_id)
        state.pop(str(proposal_id), None)
        return TriageDecision(
            proposal_id=proposal_id,
            origin=origin,
            decision="discard",
            detail=reason,
        )
    except AdoptedIssueHoldError as exc:
        return TriageDecision(
            proposal_id=proposal_id,
            origin=origin,
            decision=decision,
            detail=f"{config.project}#{exc.issue_id}",
            issue_id=exc.issue_id,
            error=str(exc),
            hold_error=True,
        )
    except (ProposalError, WorkflowError, ValueError, TimeoutError) as exc:
        return TriageDecision(
            proposal_id=proposal_id,
            origin=origin,
            decision=decision,
            detail="",
            error=str(exc),
        )


def _discard_superseded_pending_proposal(
    proposal: Mapping[str, Any],
    *,
    config: IssuekitConfig,
    state: dict[str, dict[str, str]],
    emit: LogFn,
) -> None:
    proposal_id = int(proposal["id"])
    superseded_id, ignored_reason, ref = _local_supersedes_ref(
        str(proposal.get("body", "")),
        project=config.project,
    )
    if superseded_id is None:
        if ignored_reason is not None:
            emit(
                "triage_author_superseded_ignored",
                new_proposal=proposal_id,
                ref=ref,
                reason=ignored_reason,
            )
        return

    try:
        with api_client(config) as client:
            superseded = client.get_proposal(superseded_id)
            try:
                same_origin = origin_destination(str(superseded.get("origin", ""))) == (
                    origin_destination(str(proposal.get("origin", "")))
                )
            except ProposalError:
                same_origin = False
            if not same_origin:
                emit(
                    "triage_author_superseded_ignored",
                    old_proposal=superseded_id,
                    new_proposal=proposal_id,
                    ref=ref,
                    reason="origin_mismatch",
                )
                return
            if superseded.get("status") != "pending":
                emit(
                    "triage_author_superseded_ignored",
                    old_proposal=superseded_id,
                    new_proposal=proposal_id,
                    ref=ref,
                    reason="not_pending",
                    status=superseded.get("status"),
                )
                return
            client.discard_proposal(superseded_id)
    except (ProposalError, WorkflowError, ValueError, TimeoutError) as exc:
        reason = (
            "missing"
            if isinstance(exc, WorkflowError)
            and exc.code in {"not_found", "http_404"}
            else "discard_failed"
        )
        emit(
            "triage_author_superseded_ignored",
            old_proposal=superseded_id,
            new_proposal=proposal_id,
            ref=ref,
            reason=reason,
            error=str(exc),
        )
        return

    state.pop(str(superseded_id), None)
    emit(
        "triage_author_superseded",
        old_proposal=superseded_id,
        new_proposal=proposal_id,
    )


def _local_supersedes_ref(
    body: str,
    *,
    project: str,
) -> tuple[int | None, str | None, str | None]:
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith("supersedes:"):
            continue
        match = _SUPERSEDES_LINE_PATTERN.match(stripped)
        if match is None:
            return None, "malformed", stripped
        ref_project = match.group("project")
        ref = f"{ref_project}#{match.group('id')}"
        if ref_project != project:
            return None, "foreign_project", ref
        return int(match.group("id")), None, ref
    return None, None, None


def _send_reply(
    proposal: Mapping[str, Any],
    question: str,
    *,
    config: IssuekitConfig,
    cwd: Path,
    from_issue: str | None = None,
) -> str:
    origin_project = origin_destination(str(proposal.get("origin", "")))
    original_title = str(proposal.get("title", "")).strip() or f"proposal #{proposal['id']}"
    title = f"Re: {config.project}#{proposal['id']}: {original_title}"
    reply = build_proposal(
        cwd,
        to=origin_project,
        title=title,
        body=question,
        body_file=None,
        from_issue=from_issue,
        reply=None,
    )
    commit = reply.origin.rsplit("@", maxsplit=1)[1]
    sent = send_proposal(
        config,
        replace(
            reply,
            origin=f"{config.project}#triage-reply-{proposal['id']}@{commit}",
            reply_to=str(proposal.get("origin", "")),
        ),
    )
    if sent.get("idempotent_existing") or sent.get("payload_mismatch"):
        raise ProposalError(str(sent.get("warning") or "Reply proposal was not sent."))
    return f"{origin_project}#{sent.get('id')}"


def _render_triage_prompt(proposal: Mapping[str, Any]) -> str:
    return TRIAGE_PROMPT.render(
        proposal_id=proposal["id"],
        origin=fence_untrusted("origin", str(proposal.get("origin", ""))),
        reply_to=fence_untrusted(
            "reply_to", str(proposal.get("reply_to") or "(none)")
        ),
        title=fence_untrusted("title", str(proposal.get("title", ""))),
        blocking=bool(proposal.get("blocking", False)),
        depends_on=fence_untrusted("depends_on", proposal_dependencies_text(proposal)),
        proposal_body=fence_untrusted(
            "proposal_body", str(proposal.get("body", ""))
        ),
    )




def _skip_replied(
    state: dict[str, dict[str, str]],
    proposal_id: int,
    fingerprint: str,
    body_sha: str,
) -> bool:
    prior = state.get(str(proposal_id))
    if not prior:
        return False
    if prior.get("fingerprint") == fingerprint:
        return True
    if "fingerprint" not in prior and prior.get("body_sha") == body_sha:
        state[str(proposal_id)] = {
            "fingerprint": fingerprint,
            "replied_at": prior.get("replied_at", ""),
        }
        return True
    return False


def _body_sha(body: object) -> str:
    return hashlib.sha256(str(body).encode("utf-8")).hexdigest()


def _proposal_fingerprint(proposal: Mapping[str, Any]) -> str:
    depends_on = proposal.get("depends_on") or []
    if isinstance(depends_on, (list, tuple)):
        dependencies: object = [str(ref) for ref in depends_on]
    else:
        dependencies = str(depends_on)
    prompt_fields = {
        "origin": str(proposal.get("origin", "")),
        "reply_to": str(proposal.get("reply_to") or ""),
        "title": str(proposal.get("title", "")),
        "blocking": bool(proposal.get("blocking", False)),
        "depends_on": dependencies,
        "body": str(proposal.get("body", "")),
    }
    canonical = json.dumps(
        prompt_fields,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

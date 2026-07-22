"""Agent-refined triage of incoming proposals (triage-author mode).

When ``[tool.issuekit.triage] author_agent`` is configured, ``serve --triage``
and ``issuekit triage --once`` run the configured agent against each
policy-matching pending proposal and let it make a three-way decision: adopt
the proposal as an implementation-ready issue (appending an authored spec),
reply to the origin project for clarification, or discard it as out of scope.

The agent run is an author-type session: it inspects the checkout read-only and
never implements or mutates tracker state. This module applies the parsed
decision through the existing proposal helpers; the agent only emits JSON.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
import hashlib
import re
import sys
import threading
from typing import Any, TextIO

from issuekit.agents.proposal_eval import (
    run_readonly_proposal_evaluation,
)
from issuekit.agents.readonly import prompt_from_spec
from issuekit.agents.registry import resolve_adapter
from issuekit.agentrun import AgentRunner
from issuekit.agents.triage_state import (
    STATE_FILENAME,
    _load_state,
    _now,
    _save_state,
    _state_path,
)
from issuekit.config import IssuekitConfig
from issuekit.encoding import has_non_ascii
from issuekit.prompts import TRIAGE_PROMPT, TriageAuthorParseError
from issuekit.proposals import origin_destination
from issuekit.proposals.api import (
    ProposalError,
    adopt_proposal_with_append,
    api_client,
    build_proposal,
    matches_triage_policy,
    send_proposal,
)
from issuekit.workflow import WorkflowError


TRIAGE_BLOCK_LANGUAGE = TRIAGE_PROMPT.block_language
_DECISIONS = {"adopt", "reply", "discard"}
_DECISION_FIELD = {
    "adopt": "spec_markdown",
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
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "proposal_id": self.proposal_id,
            "origin": self.origin,
            "decision": self.decision,
            "detail": self.detail,
        }
        if self.issue_id is not None:
            data["issue_id"] = self.issue_id
        if self.error is not None:
            data["error"] = self.error
        return data


def parse_triage_output(stdout: str) -> dict[str, str]:
    """Parse the newest well-formed ```triage``` block from agent stdout."""

    raw = TRIAGE_PROMPT.parse_json(stdout)
    return _decision_from_json(raw)


def _decision_from_json(raw: dict[str, object]) -> dict[str, str]:
    decision = raw.get("decision")
    if not isinstance(decision, str) or decision not in _DECISIONS:
        raise TriageAuthorParseError(f"Invalid triage decision: {decision!r}")
    field = _DECISION_FIELD[decision]
    value = raw.get(field)
    if not isinstance(value, str) or not value.strip():
        raise TriageAuthorParseError(
            f"Triage decision '{decision}' requires a non-empty '{field}'."
        )
    if has_non_ascii(f"{decision}\n{value}"):
        raise TriageAuthorParseError("Triage fields must be ASCII-only.")
    return {"decision": decision, field: value.strip()}


def run_triage_author_cycle(
    config: IssuekitConfig,
    cwd: Path,
    *,
    timeout: float = 600.0,
    model: str | None = None,
    reasoning_effort: str | None = None,
    runner_factory=None,
    log: LogFn | None = None,
    out: TextIO | None = None,
    err: TextIO | None = None,
) -> list[TriageDecision]:
    """Run one agent-backed triage cycle over policy-matching pending proposals."""

    agent = config.triage.author_agent
    if not agent:
        raise WorkflowError(
            "triage-author mode requires [tool.issuekit.triage] author_agent."
        )
    out = out or sys.stdout
    err = err or sys.stderr
    emit = log or (lambda *args, **kwargs: None)
    runner_factory = runner_factory or AgentRunner

    adapter = resolve_adapter(
        agent,
        config=config,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    state = _load_state(cwd)
    decisions: list[TriageDecision] = []
    evaluated = 0
    limit = config.triage.max_adoptions_per_cycle

    with api_client(config) as client:
        pending = client.list_proposals(status="pending")

    for proposal in pending:
        if evaluated >= limit:
            break
        if not matches_triage_policy(proposal, config):
            continue
        proposal_id = int(proposal["id"])
        body_sha = _body_sha(proposal.get("body", ""))
        if _skip_replied(state, proposal_id, body_sha):
            emit("triage_author_skip", proposal=proposal_id, reason="replied")
            continue

        evaluated += 1
        try:
            parsed = _evaluate_proposal(
                proposal,
                agent=agent,
                adapter=adapter,
                config=config,
                cwd=cwd,
                timeout=timeout,
                runner_factory=runner_factory,
                err=err,
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
            body_sha=body_sha,
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

    _save_state(cwd, state)
    return decisions


def _evaluate_proposal(
    proposal: Mapping[str, Any],
    *,
    agent: str,
    adapter,
    config: IssuekitConfig,
    cwd: Path,
    timeout: float,
    runner_factory,
    err: TextIO,
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
            "ERROR: triage-author run modified the worktree; ignoring its output."
        ),
    )
    return parse_triage_output(stdout)


def _apply_decision(
    proposal: Mapping[str, Any],
    parsed: dict[str, str],
    *,
    config: IssuekitConfig,
    cwd: Path,
    state: dict[str, dict[str, str]],
    body_sha: str,
    emit: LogFn,
) -> TriageDecision:
    proposal_id = int(proposal["id"])
    origin = str(proposal.get("origin", ""))
    decision = parsed["decision"]
    try:
        if decision == "adopt":
            spec = parsed["spec_markdown"]
            outcome = adopt_proposal_with_append(
                config,
                proposal_id,
                priority=config.triage.default_priority,
                append_text=spec,
            )
            state.pop(str(proposal_id), None)
            _discard_superseded_pending_proposal(
                proposal,
                config=config,
                state=state,
                emit=emit,
            )
            return TriageDecision(
                proposal_id=proposal_id,
                origin=origin,
                decision="adopt",
                detail=str(outcome.get("issue_ref") or ""),
                issue_id=outcome.get("issue_id"),
            )
        if decision == "reply":
            question = parsed["question"]
            issue_ref = _send_reply(proposal, question, config=config, cwd=cwd)
            state[str(proposal_id)] = {"body_sha": body_sha, "replied_at": _now()}
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
        from_issue=None,
        reply=None,
    )
    sent = send_proposal(config, reply)
    return f"{origin_project}#{sent.get('id')}"


def _render_triage_prompt(proposal: Mapping[str, Any]) -> str:
    depends_on = proposal.get("depends_on") or []
    if isinstance(depends_on, (list, tuple)):
        depends_text = ", ".join(str(ref) for ref in depends_on) or "(none)"
    else:
        depends_text = str(depends_on)
    return TRIAGE_PROMPT.render(
        proposal_id=proposal["id"],
        origin=proposal.get("origin", ""),
        title=proposal.get("title", ""),
        blocking=bool(proposal.get("blocking", False)),
        depends_on=depends_text,
        proposal_body=proposal.get("body", ""),
    )




def _skip_replied(
    state: Mapping[str, Mapping[str, str]],
    proposal_id: int,
    body_sha: str,
) -> bool:
    prior = state.get(str(proposal_id))
    return bool(prior) and prior.get("body_sha") == body_sha


def _body_sha(body: object) -> str:
    return hashlib.sha256(str(body).encode("utf-8")).hexdigest()

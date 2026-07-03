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
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import re
import sys
import threading
from typing import Any, TextIO

from issuekit.agents.runner import AgentResult, AgentRunner, resolve_adapter
from issuekit.config import IssuekitConfig
from issuekit.core import has_non_ascii
from issuekit.gitutil import git_status_short
from issuekit.proposals import origin_destination
from issuekit.proposals_api import (
    ProposalError,
    adopt_proposal_with_append,
    api_client,
    build_proposal,
    matches_triage_policy,
    send_proposal,
)
from issuekit.workflow import WorkflowError


TRIAGE_BLOCK_LANGUAGE = "triage"
_TRIAGE_BLOCK_PATTERN = re.compile(
    r"```triage[ \t]*\r?\n(?P<body>.*?)\r?\n```",
    re.DOTALL,
)
_DECISIONS = {"adopt", "reply", "discard"}
_DECISION_FIELD = {
    "adopt": "spec_markdown",
    "reply": "question",
    "discard": "reason",
}
STATE_FILENAME = "triage-author-state.json"

LogFn = Callable[..., None]


class TriageAuthorParseError(RuntimeError):
    """Raised when a triage-author agent response cannot be parsed."""


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

    blocks = [match.group("body") for match in _TRIAGE_BLOCK_PATTERN.finditer(stdout)]
    if not blocks:
        raise TriageAuthorParseError("No ```triage``` block found in agent output.")

    last_json_error: TriageAuthorParseError | None = None
    for block in reversed(blocks):
        try:
            raw = json.loads(block.strip())
        except json.JSONDecodeError as exc:
            last_json_error = TriageAuthorParseError(
                f"Triage block was not valid JSON: {exc.msg}."
            )
            continue
        if not isinstance(raw, dict):
            raise TriageAuthorParseError("Triage block JSON must be an object.")
        return _decision_from_json(raw)

    if last_json_error is not None:
        raise last_json_error
    raise TriageAuthorParseError("No well-formed ```triage``` block found.")


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

    adapter = resolve_adapter(agent, config=config)
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
    run_dir = cwd / ".agent-runs"
    run_dir.mkdir(exist_ok=True)
    prompt_path = run_dir / f"triage-proposal-{proposal_id}.md"
    prompt_path.write_text(
        _render_triage_prompt(proposal),
        encoding="utf-8",
        newline="\n",
    )
    fingerprint_before = _worktree_fingerprint(cwd)

    result = runner_factory().run(
        adapter,
        prompt_path,
        cwd,
        timeout=float(timeout),
        agent_name=agent,
        prompt_override=_prompt_pointer(prompt_path),
    )
    if result.timed_out:
        raise TimeoutError(f"Triage agent timed out for proposal #{proposal_id}.")
    if result.exit_code != 0:
        raise RuntimeError(
            f"Triage agent exited {result.exit_code} for proposal #{proposal_id}."
        )
    fingerprint_after = _worktree_fingerprint(cwd)
    if fingerprint_before != fingerprint_after:
        print(
            "ERROR: triage-author run modified the worktree; ignoring its output.",
            file=err,
        )
        raise WorkflowError(
            f"Triage agent modified the worktree for proposal #{proposal_id}."
        )
    return parse_triage_output(_stdout_text(result))


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
    return "\n".join(
        [
            f"# Triage proposal {proposal.get('origin', '')} (id {proposal['id']})",
            "",
            "You are triaging one incoming cross-project proposal for this project.",
            "Inspect this repository read-only to judge whether the request belongs",
            "here and how it should be specified. Do NOT edit files, run git commit or",
            "push, and do NOT run issuekit claim, submit-review, request-changes,",
            "approve, complete, or otherwise mutate tracker or issue lifecycle state.",
            "",
            f"Proposal title: {proposal.get('title', '')}",
            f"Origin: {proposal.get('origin', '')}",
            f"Blocking: {bool(proposal.get('blocking', False))}",
            f"Depends-on: {depends_text}",
            "",
            "Proposal body:",
            "",
            str(proposal.get("body", "")),
            "",
            "Decide exactly one of:",
            "- adopt: the request belongs to this project. Write an",
            "  implementation-ready spec (background, scope, acceptance criteria,",
            "  affected files) as spec_markdown; it is appended to the adopted issue.",
            "- reply: the request intent is unclear. Ask one concrete question that",
            "  the origin project must answer before this can be adopted.",
            "- discard: the request does not belong to this project. Explain why so",
            "  the sender can re-route.",
            "",
            "Output contract:",
            "Emit exactly one fenced block and no other response text.",
            "Everything outside the block is ignored by the parser.",
            "All text must be ASCII-only (English; no em dashes or curly quotes).",
            "```triage",
            "{",
            '  "decision": "adopt-or-reply-or-discard",',
            '  "spec_markdown": "Implementation-ready spec when decision is adopt.",',
            '  "question": "One clarifying question when decision is reply.",',
            '  "reason": "Why it does not belong here when decision is discard."',
            "}",
            "```",
            "",
        ]
    )


def _prompt_pointer(prompt_path: Path) -> str:
    return (
        f"Read the triage prompt at: {prompt_path} and respond with exactly one "
        "fenced triage block per its instructions. Inspect the repo read-only; do "
        "not modify files or mutate the tracker."
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


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _state_path(cwd: Path) -> Path:
    return cwd / ".agent-runs" / STATE_FILENAME


def _load_state(cwd: Path) -> dict[str, dict[str, str]]:
    path = _state_path(cwd)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    state: dict[str, dict[str, str]] = {}
    for key, value in raw.items():
        if isinstance(value, dict) and isinstance(value.get("body_sha"), str):
            state[str(key)] = {
                "body_sha": value["body_sha"],
                "replied_at": str(value.get("replied_at", "")),
            }
    return state


def _save_state(cwd: Path, state: Mapping[str, Mapping[str, str]]) -> None:
    path = _state_path(cwd)
    path.parent.mkdir(exist_ok=True)
    path.write_text(
        json.dumps(dict(state), indent=2, sort_keys=True),
        encoding="utf-8",
        newline="\n",
    )


def _worktree_fingerprint(cwd: Path) -> tuple[tuple[str, str], ...] | None:
    status = git_status_short(cwd, strip=False, untracked_files="all")
    if status is None:
        return None
    entries: list[tuple[str, str]] = []
    for line in status.splitlines():
        if len(line) < 4:
            continue
        raw_path = line[3:]
        if " -> " in raw_path:
            raw_path = raw_path.rsplit(" -> ", 1)[1]
        raw_path = raw_path.strip('"')
        path = Path(raw_path)
        if path.parts and path.parts[0] == ".agent-runs":
            continue
        entries.append((line[:2], path.as_posix()))
    return tuple(sorted(entries))


def _stdout_text(result: AgentResult) -> str:
    if result.parsed and "stdout" in result.parsed:
        return result.parsed["stdout"]
    return result.stdout_path.read_text(encoding="utf-8", errors="replace")

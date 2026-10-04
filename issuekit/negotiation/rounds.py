"""Agent and store helpers for bounded negotiation rounds."""

from __future__ import annotations

import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

from issuekit.agentrun import AgentAdapter, AgentPrompt, AgentResult, AgentRunner
from issuekit.agents.readonly import require_clean_run, run_readonly_evaluation
from issuekit.coerce import last_nonempty_line
from issuekit.config import IssuekitConfig
from issuekit.core import Issue
from issuekit.errors import WorkflowError
from issuekit.negotiation.model import NegotiationEntry, NegotiationStore, ThreadStatus
from issuekit.negotiation.prompts import (
    ParsedRound,
    parse_round_output,
    render_round_prompt,
)
from issuekit.negotiation.thread import _evaluate_convergence, _latest_contract
from issuekit.prompts import render_negotiation_round_pointer


@dataclass(frozen=True)
class RoundRun:
    round_number: int
    side: str
    agent: str
    run_id: str | None
    session_id: str | None = None


@dataclass(frozen=True)
class NegotiationResult:
    thread_id: str
    outcome: str
    round_count: int
    final_contract: str | None
    run_ids: tuple[str, ...]
    round_runs: tuple[RoundRun, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "thread_id": self.thread_id,
            "outcome": self.outcome,
            "round_count": self.round_count,
            "final_contract": self.final_contract,
            "run_ids": list(self.run_ids),
            "rounds": [
                {
                    "round": run.round_number,
                    "side": run.side,
                    "agent": run.agent,
                    "run_id": run.run_id,
                    "session_id": run.session_id,
                }
                for run in self.round_runs
            ],
        }


@dataclass(frozen=True)
class _TurnResult:
    parsed: ParsedRound
    run: RoundRun


class _SideSessions:
    """Hold one agent session per side so later rounds resume it.

    Sessions live for a single negotiate invocation. A thread resumed by a
    later invocation starts fresh sessions because the store does not record
    session ids and the counterpart may run on another machine.
    """

    def __init__(self, adapters: dict[str, AgentAdapter]) -> None:
        self._adapters = adapters
        self._sessions: dict[str, str] = {}

    def next_round(self, side: str) -> tuple[str | None, bool]:
        """Return the session id for this side's next round and whether to resume."""
        adapter = self._adapters[side]
        if not adapter.supports_session_resume():
            return None, False
        existing = self._sessions.get(side)
        if existing is not None and adapter.supports_session_continuation():
            return existing, True
        session_id = str(uuid.uuid4())
        self._sessions[side] = session_id
        return session_id, False


def _run_side_turn(
    *,
    round_number: int,
    side: str,
    agent: str,
    adapter: AgentAdapter,
    session: tuple[str | None, bool],
    seed: str,
    thread: list[NegotiationEntry],
    issue: Issue,
    cwd: Path,
    timeout: float,
    runner: AgentRunner,
) -> _TurnResult:
    session_id, resume_session = session
    prompt = render_round_prompt(
        side=side,
        seed=seed,
        thread=thread,
        resolved_contract=_latest_contract(thread),
    )
    issue_token = str(issue.id) if issue.id is not None else "unknown"
    plan_path = (
        cwd
        / ".agent-runs"
        / f"negotiate-issue-{issue_token}-round-{round_number}-{side}.md"
    )
    agent_prompt = AgentPrompt(
        path=plan_path,
        body=prompt,
        pointer=render_negotiation_round_pointer(plan_path),
    )
    readonly_run = run_readonly_evaluation(
        agent=agent,
        adapter=adapter,
        cwd=cwd,
        timeout=timeout,
        runner_factory=lambda: runner,
        prompt=agent_prompt,
        label=f"Negotiation round {round_number}",
        subject=side,
        issue_id=issue.id,
        session_id=session_id,
        resume_session=resume_session,
    )
    result = readonly_run.result
    run_id = _run_id(result)
    print(
        f"round={round_number} side={side} agent={agent} run_id={run_id or '-'} "
        f"session_id={session_id or '-'}{' resumed' if resume_session else ''}",
        file=sys.stderr,
    )

    if result.timed_out:
        raise TimeoutError(
            f"Negotiation round {round_number} timed out for {side} "
            f"(agent={agent}, run_id={run_id or '-'}, session_id={session_id or '-'})."
        )
    if result.exit_code != 0:
        reason = _failure_reason(result)
        suffix = f": {reason}" if reason else "."
        raise WorkflowError(
            f"Negotiation round {round_number} failed for {side} "
            f"(agent={agent}, run_id={run_id or '-'}, session_id={session_id or '-'}) "
            f"with exit code {result.exit_code}{suffix}",
            code="agent_failed",
        )

    output = require_clean_run(
        readonly_run,
        err=sys.stderr,
        mutation_log_message=(
            f"ERROR: negotiation round {round_number} {side} run modified "
            "repository state; ignoring its output."
        ),
    )
    parsed = parse_round_output(output)
    if parsed.side != side:
        raise WorkflowError(
            f"Negotiation round {round_number} returned side {parsed.side}, expected {side}.",
            code="invalid_negotiation_side",
        )
    return _TurnResult(
        parsed=parsed,
        run=RoundRun(
            round_number=round_number,
            side=side,
            agent=agent,
            run_id=run_id,
            session_id=session_id,
        ),
    )


def _failure_reason(result: AgentResult) -> str | None:
    if result.parsed:
        for key in ("stderr", "stdout"):
            value = result.parsed.get(key)
            line = last_nonempty_line(value)
            if line:
                return line
    for path in (result.agent_log_path, result.stdout_path):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        line = last_nonempty_line(text)
        if line:
            return line
    return None


def _set_terminal_status(
    store: NegotiationStore,
    thread_id: str,
    outcome: str,
    thread: list[NegotiationEntry],
) -> None:
    if outcome == "agreed":
        store.set_status(thread_id, ThreadStatus.agreed, agreed_contract=_latest_contract(thread))
    elif outcome == "blocked":
        store.set_status(thread_id, ThreadStatus.blocked)
        store.settle_thread_members(thread_id)


def _set_terminal_status_if_converged(
    store: NegotiationStore,
    thread_id: str,
    thread: list[NegotiationEntry],
) -> None:
    if store.get_status(thread_id) is not ThreadStatus.negotiating:
        return
    outcome = _evaluate_convergence(thread)
    if outcome != "negotiating":
        _set_terminal_status(store, thread_id, outcome, thread)


def _find_resumable_thread_id(
    store: NegotiationStore,
    *,
    issue: Issue,
    config: IssuekitConfig,
) -> str | None:
    candidates: list[str] = []
    for summary in store.list_threads(status=ThreadStatus.negotiating):
        thread = store.get_thread(summary.thread_id)
        if _thread_matches_origin_issue(thread, issue=issue, config=config):
            candidates.append(summary.thread_id)
    if not candidates:
        return None
    if len(candidates) > 1:
        raise WorkflowError(
            "Multiple negotiating threads match "
            f"{config.project}#{issue.id}: {', '.join(candidates)}. "
            "Inspect them with `issuekit threads` before resuming.",
            code="ambiguous_negotiation_thread",
        )
    return candidates[0]


def _thread_matches_origin_issue(
    thread: list[NegotiationEntry],
    *,
    issue: Issue,
    config: IssuekitConfig,
) -> bool:
    issue_id = issue.id if issue.id is not None else "unknown"
    prefix = f"{config.project}#{issue_id}@"
    return any(entry.origin.startswith(prefix) for entry in thread)


def _result(
    thread: list[NegotiationEntry],
    *,
    outcome: str,
    runs: list[RoundRun],
) -> NegotiationResult:
    run_ids = tuple(run.run_id for run in runs if run.run_id)
    return NegotiationResult(
        thread_id=thread[0].thread_id,
        outcome=outcome,
        round_count=len(thread),
        final_contract=_latest_contract(thread),
        run_ids=run_ids,
        round_runs=tuple(runs),
    )


def _seed_text(issue: Issue, *, config: IssuekitConfig, to_project: str) -> str:
    source_label = (
        "Source proposal"
        if issue.metadata.get("source_type") == "proposal"
        else "Origin issue"
    )
    return "\n".join(
        [
            f"Origin project: {config.project}",
            f"Target project: {to_project}",
            f"{source_label}: {issue.ref}",
            f"Title: {issue.title}",
            "",
            issue.body,
        ]
    )


def _run_id(result: AgentResult) -> str | None:
    if result.status_path is not None:
        name = result.status_path.name
        if name.endswith(".status.json"):
            return name[: -len(".status.json")]
        return result.status_path.stem
    name = result.stdout_path.name
    if name.endswith(".out.log"):
        return name[: -len(".out.log")]
    return result.stdout_path.stem if result.stdout_path else None

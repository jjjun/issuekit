"""Negotiation orchestration engine."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from issuekit.agentrun import AgentAdapter, AgentRunner
from issuekit.agents.registry import resolve_adapter
from issuekit.config import IssuekitConfig
from issuekit.core import Issue
from issuekit.errors import WorkflowError
from issuekit.negotiation.model import NegotiationEntry, NegotiationStore, ThreadStatus
from issuekit.negotiation.prompts import NegotiationParseError
from issuekit.negotiation.rounds import (
    NegotiationResult,
    RoundRun,
    _find_resumable_thread_id,
    _result,
    _run_side_turn,
    _seed_text,
    _set_terminal_status,
    _set_terminal_status_if_converged,
    _SideSessions,
)
from issuekit.negotiation.thread import (
    CONSUMER_SIDE,
    PROVIDER_SIDE,
    NegotiationThreadInspection,
    _entry_title,
    _evaluate_convergence,
    _next_side,
    _other_side,
    _require_supported_thread,
    entry_origin,
    inspect_thread,
)

DEFAULT_MAX_ROUNDS = 4


def load_thread_inspection(
    store: NegotiationStore,
    thread_id: str,
) -> NegotiationThreadInspection:
    thread = store.get_thread(thread_id)
    status = store.get_status(thread_id)
    issue_refs = store.get_issue_refs(thread_id)
    agreed_contract = store.get_agreed_contract(thread_id)
    return inspect_thread(
        thread_id,
        thread=thread,
        status=status,
        issue_refs=issue_refs,
        agreed_contract=agreed_contract,
    )


@dataclass(frozen=True)
class _NegotiationContext:
    issue: Issue
    config: IssuekitConfig
    seed: str
    adapters: dict[str, AgentAdapter]
    agents: dict[str, str]
    side_cwds: dict[str, Path]
    sessions: _SideSessions
    timeout: float
    runner: AgentRunner


def run_negotiation(
    *,
    issue: Issue,
    to_project: str,
    config: IssuekitConfig,
    cwd: Path,
    store: NegotiationStore,
    initiator_side: str,
    provider_agent: str,
    consumer_agent: str,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    timeout: float = 120.0,
    model: str | None = None,
    reasoning_effort: str | None = None,
    counterpart_cwd: Path | None = None,
    runner: AgentRunner | None = None,
) -> NegotiationResult:
    """Drive a bounded provider/consumer negotiation to a terminal outcome."""
    if max_rounds < 1:
        raise ValueError("max_rounds must be at least 1.")
    context = _create_context(
        issue=issue,
        to_project=to_project,
        config=config,
        cwd=cwd,
        initiator_side=initiator_side,
        provider_agent=provider_agent,
        consumer_agent=consumer_agent,
        counterpart_cwd=counterpart_cwd,
        model=model,
        reasoning_effort=reasoning_effort,
        timeout=timeout,
        runner=runner,
    )
    run_records: list[RoundRun] = []
    thread_id, thread, early_result = _open_or_resume_thread(
        store,
        issue=context.issue,
        config=context.config,
        initiator_side=initiator_side,
    )
    if early_result is not None:
        return early_result
    if not thread:
        thread_id, thread, first_run = _run_initial_turn(
            thread_id=thread_id,
            side=initiator_side,
            store=store,
            context=context,
        )
        run_records.append(first_run)
    return _run_rounds(
        thread_id=thread_id,
        thread=thread,
        store=store,
        max_rounds=max_rounds,
        runs=run_records,
        context=context,
    )


def _create_context(
    *,
    issue: Issue,
    to_project: str,
    config: IssuekitConfig,
    cwd: Path,
    initiator_side: str,
    provider_agent: str,
    consumer_agent: str,
    counterpart_cwd: Path | None,
    model: str | None,
    reasoning_effort: str | None,
    timeout: float,
    runner: AgentRunner | None,
) -> _NegotiationContext:
    if initiator_side not in (PROVIDER_SIDE, CONSUMER_SIDE):
        raise ValueError("initiator_side must be provider or consumer.")
    adapters, agents, side_cwds = _resolve_sides(
        initiator_side=initiator_side,
        provider_agent=provider_agent,
        consumer_agent=consumer_agent,
        config=config,
        cwd=cwd,
        counterpart_cwd=counterpart_cwd,
        model=model,
        reasoning_effort=reasoning_effort,
    )
    return _NegotiationContext(
        issue=issue,
        config=config,
        seed=_seed_text(issue, config=config, to_project=to_project),
        adapters=adapters,
        agents=agents,
        side_cwds=side_cwds,
        sessions=_SideSessions(adapters),
        timeout=timeout,
        runner=runner or AgentRunner(),
    )


def _resolve_sides(
    *,
    initiator_side: str,
    provider_agent: str,
    consumer_agent: str,
    config: IssuekitConfig,
    cwd: Path,
    counterpart_cwd: Path | None,
    model: str | None,
    reasoning_effort: str | None,
) -> tuple[dict[str, AgentAdapter], dict[str, str], dict[str, Path]]:
    adapters = {
        PROVIDER_SIDE: resolve_adapter(
            provider_agent,
            config=config,
            model=model,
            reasoning_effort=reasoning_effort,
            role="negotiation",
        ),
        CONSUMER_SIDE: resolve_adapter(
            consumer_agent,
            config=config,
            model=model,
            reasoning_effort=reasoning_effort,
            role="negotiation",
        ),
    }
    agents = {
        PROVIDER_SIDE: provider_agent,
        CONSUMER_SIDE: consumer_agent,
    }
    side_cwds = {
        initiator_side: cwd,
        _other_side(initiator_side): counterpart_cwd or cwd,
    }
    return adapters, agents, side_cwds


def _open_or_resume_thread(
    store: NegotiationStore,
    *,
    issue: Issue,
    config: IssuekitConfig,
    initiator_side: str,
) -> tuple[str | None, list[NegotiationEntry], NegotiationResult | None]:
    thread_id = _find_resumable_thread_id(store, issue=issue, config=config)
    if thread_id is None:
        return None, [], None

    thread = store.get_thread(thread_id)
    _require_supported_thread(thread_id, thread)
    stored_status = store.get_status(thread_id)
    if stored_status is ThreadStatus.cancelled:
        raise WorkflowError(
            f"Negotiation thread {thread_id} is cancelled and cannot be resumed.",
            code="invalid_transition",
        )
    if thread and stored_status in {ThreadStatus.agreed, ThreadStatus.blocked}:
        return thread_id, thread, _result(thread, outcome=stored_status.value, runs=[])
    if thread and thread[0].side != initiator_side:
        raise WorkflowError(
            f"Negotiation thread {thread_id} was initiated by the {thread[0].side} "
            f"side, not the requested {initiator_side} side.",
            code="invalid_negotiation_side",
        )
    return thread_id, thread, None


def _run_initial_turn(
    *,
    thread_id: str | None,
    side: str,
    store: NegotiationStore,
    context: _NegotiationContext,
) -> tuple[str, list[NegotiationEntry], RoundRun]:
    turn = _run_side_turn(
        round_number=1,
        side=side,
        agent=context.agents[side],
        adapter=context.adapters[side],
        session=context.sessions.next_round(side),
        seed=context.seed,
        thread=[],
        issue=context.issue,
        cwd=context.side_cwds[side],
        timeout=context.timeout,
        runner=context.runner,
    )
    entry_values = {
        "side": side,
        "verdict": turn.parsed.verdict,
        "title": _entry_title(side, turn.parsed),
        "body": turn.parsed.notes,
        "origin": entry_origin(
            context.issue,
            config=context.config,
            side=side,
            round_number=1,
        ),
        "contract": turn.parsed.contract,
    }
    if thread_id is None:
        first_entry = store.create_thread(**entry_values)
        thread_id = first_entry.thread_id
    else:
        store.append_initial_entry(thread_id, **entry_values)
    thread = store.get_thread(thread_id)
    _require_supported_thread(thread_id, thread)
    return thread_id, thread, turn.run


def _run_rounds(
    *,
    thread_id: str,
    thread: list[NegotiationEntry],
    store: NegotiationStore,
    max_rounds: int,
    runs: list[RoundRun],
    context: _NegotiationContext,
) -> NegotiationResult:
    outcome = _evaluate_convergence(thread)
    if outcome != "negotiating":
        _set_terminal_status(store, thread_id, outcome, thread)
        return _result(thread, outcome=outcome, runs=runs)

    while len(thread) < max_rounds:
        side = _next_side(thread)
        round_number = len(thread) + 1
        try:
            turn = _run_side_turn(
                round_number=round_number,
                side=side,
                agent=context.agents[side],
                adapter=context.adapters[side],
                session=context.sessions.next_round(side),
                seed=context.seed,
                thread=thread,
                issue=context.issue,
                cwd=context.side_cwds[side],
                timeout=context.timeout,
                runner=context.runner,
            )
        except (TimeoutError, WorkflowError, NegotiationParseError):
            _set_terminal_status_if_converged(store, thread_id, thread)
            raise
        store.append_entry(
            thread_id,
            side=side,
            verdict=turn.parsed.verdict,
            title=_entry_title(side, turn.parsed),
            body=turn.parsed.notes,
            origin=entry_origin(
                context.issue,
                config=context.config,
                side=side,
                round_number=round_number,
            ),
            contract=turn.parsed.contract,
        )
        runs.append(turn.run)
        thread = store.get_thread(thread_id)
        _require_supported_thread(thread_id, thread)

        outcome = _evaluate_convergence(thread)
        if outcome != "negotiating":
            _set_terminal_status(store, thread_id, outcome, thread)
            return _result(thread, outcome=outcome, runs=runs)

    return _result(thread, outcome="escalate", runs=runs)

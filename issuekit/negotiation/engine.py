"""Negotiation orchestration engine."""

from __future__ import annotations

import hashlib
import sys
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from issuekit.agentrun import AgentAdapter, AgentPrompt, AgentResult, AgentRunner
from issuekit.agents.readonly import require_clean_run, run_readonly_evaluation
from issuekit.agents.registry import resolve_adapter
from issuekit.config import IssuekitConfig
from issuekit.core import Issue, last_nonempty_line
from issuekit.negotiation.model import (
    NegotiationEntry,
    NegotiationIssueRefs,
    NegotiationStore,
    ThreadStatus,
    Verdict,
)
from issuekit.negotiation.prompts import (
    NegotiationParseError,
    ParsedRound,
    consumer_issue_body,
    parse_round_output,
    provider_issue_body,
    render_round_prompt,
)
from issuekit.prompts import render_negotiation_round_pointer
from issuekit.store import get_store
from issuekit.workflow import WorkflowError

PROVIDER_SIDE = "provider"
CONSUMER_SIDE = "consumer"
# Legacy threads used positional sides, so frontend maps to consumer and backend to provider.
LEGACY_SIDES = {"frontend": CONSUMER_SIDE, "backend": PROVIDER_SIDE}
DEFAULT_MAX_ROUNDS = 4


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
class NegotiationFinalizationResult:
    thread_id: str
    backend_issue_ref: str
    frontend_issue_ref: str
    created: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "thread_id": self.thread_id,
            "backend_issue_ref": self.backend_issue_ref,
            "frontend_issue_ref": self.frontend_issue_ref,
            "created": self.created,
        }


@dataclass(frozen=True)
class NegotiationThreadInspection:
    thread_id: str
    status: ThreadStatus
    outcome: str
    final_contract: str | None
    agreed_contract: str | None
    issue_refs: NegotiationIssueRefs | None
    source_proposal_ref: str | None
    entries: tuple[NegotiationEntry, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "thread_id": self.thread_id,
            "status": self.status.value,
            "outcome": self.outcome,
            "final_contract": self.final_contract,
            "agreed_contract": self.agreed_contract,
            "issue_refs": self.issue_refs.to_dict() if self.issue_refs else None,
            "source_proposal_ref": self.source_proposal_ref,
            "entries": [
                {
                    "id": entry.id,
                    "side": entry.side,
                    "verdict": entry.verdict.value,
                    "contract": entry.contract,
                    "title": entry.title,
                    "body": entry.body,
                    "origin": entry.origin,
                    "created": entry.created,
                }
                for entry in self.entries
            ],
            "finalize_refusal": finalize_refusal_reason(self.status, list(self.entries)),
        }


class IssueCreator(Protocol):
    def create_issue(
        self,
        *,
        project: str,
        title: str,
        body: str,
        priority: str,
        author: str,
        depends_on: tuple[str, ...] | None = None,
    ) -> Issue:
        """Create one implementation issue in a project."""

    def update_issue_body(self, *, project: str, issue_id: int, body: str) -> Issue:
        """Update one issue body after both cross-linked refs are known."""


class ApiIssueCreator:
    def __init__(self, config: IssuekitConfig) -> None:
        self.config = config

    def create_issue(
        self,
        *,
        project: str,
        title: str,
        body: str,
        priority: str,
        author: str,
        depends_on: tuple[str, ...] | None = None,
    ) -> Issue:
        project_config = replace(self.config, project=project)
        with get_store(project_config) as store:
            return store.create_issue(  # type: ignore[attr-defined]
                title=title,
                body=body,
                priority=priority,
                author=author,
                depends_on=depends_on,
            )

    def update_issue_body(self, *, project: str, issue_id: int, body: str) -> Issue:
        project_config = replace(self.config, project=project)
        with get_store(project_config) as store:
            return store.update_issue_body(issue_id, body=body)  # type: ignore[attr-defined]


class MockIssueCreator:
    def __init__(self) -> None:
        self._next_ids: dict[str, int] = {}
        self.issues: dict[str, Issue] = {}

    def create_issue(
        self,
        *,
        project: str,
        title: str,
        body: str,
        priority: str,
        author: str,
        depends_on: tuple[str, ...] | None = None,
    ) -> Issue:
        issue_id = self._next_ids.get(project, 1)
        self._next_ids[project] = issue_id + 1
        issue = Issue(
            id=issue_id,
            ref=f"{project}#{issue_id}",
            title=title,
            issue_status="active",
            created="",
            completed="",
            priority=priority,
            assignee="",
            stage="todo",
            implementer="",
            author=author,
            body=body,
            metadata={"title": title, "depends_on": list(depends_on or ())},
            depends_on=depends_on or (),
        )
        self.issues[issue.ref] = issue
        return issue

    def update_issue_body(self, *, project: str, issue_id: int, body: str) -> Issue:
        ref = f"{project}#{issue_id}"
        issue = self.issues.get(ref)
        if issue is None:
            raise WorkflowError(f"Issue {ref} was not found.", code="not_found")
        updated = replace(issue, body=body)
        self.issues[ref] = updated
        return updated


def finalize_negotiation(
    *,
    thread_id: str,
    to_project: str,
    author_agent: str,
    priority: str,
    config: IssuekitConfig,
    store: NegotiationStore,
    issue_creator: IssueCreator,
) -> NegotiationFinalizationResult:
    """Create cross-linked implementation issues for an agreed negotiation."""

    status = store.get_status(thread_id)
    thread = store.get_thread(thread_id)
    if status is ThreadStatus.negotiating:
        outcome = _evaluate_convergence(thread)
        if outcome != "negotiating":
            _set_terminal_status(store, thread_id, outcome, thread)
            status = store.get_status(thread_id)

    if status is not ThreadStatus.agreed:
        raise WorkflowError(
            f"Negotiation thread {thread_id} is {status.value}, not agreed: "
            f"{finalize_refusal_reason(status, thread)}",
            code="invalid_transition",
        )

    existing_refs = store.get_issue_refs(thread_id)
    if existing_refs is not None:
        store.settle_thread_members(thread_id)
        return NegotiationFinalizationResult(
            thread_id=thread_id,
            backend_issue_ref=existing_refs.backend_issue_ref,
            frontend_issue_ref=existing_refs.frontend_issue_ref,
            created=False,
        )

    contract = store.get_agreed_contract(thread_id) or _latest_contract(thread)
    if not contract:
        raise WorkflowError(
            f"Negotiation thread {thread_id} has no agreed contract.",
            code="invalid_transition",
        )

    origin_issue_ref = origin_issue_ref_from_thread(thread)
    initiator_side = _normalized_side(thread[0].side)
    counterpart_side = _other_side(initiator_side)
    projects = {initiator_side: config.project, counterpart_side: to_project}
    titles = {
        PROVIDER_SIDE: f"Implement agreed contract from negotiation {thread_id}",
        CONSUMER_SIDE: f"Integrate agreed contract from negotiation {thread_id}",
    }
    source_proposal_ref = (
        store.get_source_proposal_ref(thread_id)
        or source_proposal_ref_from_thread(thread)
    )
    if source_proposal_ref is not None:
        if initiator_side != CONSUMER_SIDE:
            raise WorkflowError(
                "Proposal-seeded negotiation requires the initiating project to be "
                "the consumer so the target proposal can become the provider issue.",
                code="invalid_negotiation_side",
            )
        refs = store.finalize_proposal_thread(
            thread_id,
            consumer_project=projects[CONSUMER_SIDE],
            author=author_agent,
            priority=priority,
            provider_title=titles[PROVIDER_SIDE],
            provider_body=provider_issue_body(
                thread_id=thread_id,
                origin_issue_ref=source_proposal_ref,
                consumer_issue_ref="pending",
                contract=contract,
            ),
            consumer_title=titles[CONSUMER_SIDE],
            consumer_body=consumer_issue_body(
                thread_id=thread_id,
                origin_issue_ref=source_proposal_ref,
                provider_issue_ref="pending",
                contract=contract,
            ),
        )
        store.settle_thread_members(thread_id)
        return NegotiationFinalizationResult(
            thread_id=thread_id,
            backend_issue_ref=refs.backend_issue_ref,
            frontend_issue_ref=refs.frontend_issue_ref,
            created=True,
        )

    provider = issue_creator.create_issue(
        project=projects[PROVIDER_SIDE],
        title=titles[PROVIDER_SIDE],
        body=provider_issue_body(
            thread_id=thread_id,
            origin_issue_ref=origin_issue_ref,
            consumer_issue_ref="pending",
            contract=contract,
        ),
        priority=priority,
        author=author_agent,
    )
    consumer = issue_creator.create_issue(
        project=projects[CONSUMER_SIDE],
        title=titles[CONSUMER_SIDE],
        body=consumer_issue_body(
            thread_id=thread_id,
            origin_issue_ref=origin_issue_ref,
            provider_issue_ref=provider.ref,
            contract=contract,
        ),
        priority=priority,
        author=author_agent,
        depends_on=(f"{projects[PROVIDER_SIDE]}#issue:{_require_issue_id(provider)}",),
    )
    issue_creator.update_issue_body(
        project=projects[PROVIDER_SIDE],
        issue_id=_require_issue_id(provider),
        body=provider_issue_body(
            thread_id=thread_id,
            origin_issue_ref=origin_issue_ref,
            consumer_issue_ref=consumer.ref,
            contract=contract,
        ),
    )

    refs = NegotiationIssueRefs(
        backend_issue_ref=provider.ref,
        frontend_issue_ref=consumer.ref,
    )
    store.set_issue_refs(thread_id, refs)
    store.settle_thread_members(thread_id)
    return NegotiationFinalizationResult(
        thread_id=thread_id,
        backend_issue_ref=refs.backend_issue_ref,
        frontend_issue_ref=refs.frontend_issue_ref,
        created=True,
    )


def run_negotiation(
    *,
    issue: Issue,
    to_project: str,
    config: IssuekitConfig,
    cwd: Path,
    store: NegotiationStore,
    initiator_side: str | None = None,
    provider_agent: str | None = None,
    consumer_agent: str | None = None,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    timeout: float = 120.0,
    model: str | None = None,
    reasoning_effort: str | None = None,
    counterpart_cwd: Path | None = None,
    runner: AgentRunner | None = None,
    frontend_agent: str | None = None,
    backend_agent: str | None = None,
    backend_cwd: Path | None = None,
    proposal_thread_id: str | None = None,
) -> NegotiationResult:
    """Drive a bounded provider/consumer negotiation to a terminal outcome."""

    if max_rounds < 1:
        raise ValueError("max_rounds must be at least 1.")
    runner = runner or AgentRunner()
    seed = _seed_text(issue, config=config, to_project=to_project)
    run_records: list[RoundRun] = []
    legacy_mode = initiator_side is None
    if legacy_mode:
        initiator_side = "frontend"
        provider_agent = backend_agent
        consumer_agent = frontend_agent
        counterpart_cwd = backend_cwd
    if initiator_side not in (PROVIDER_SIDE, CONSUMER_SIDE, "frontend"):
        raise ValueError("initiator_side must be provider or consumer.")
    if provider_agent is None or consumer_agent is None:
        raise ValueError("provider_agent and consumer_agent are required.")
    provider_side = "backend" if legacy_mode else PROVIDER_SIDE
    consumer_side = "frontend" if legacy_mode else CONSUMER_SIDE
    if legacy_mode:
        initiator_side = consumer_side
    adapters = {
        provider_side: resolve_adapter(
            provider_agent,
            config=config,
            model=model,
            reasoning_effort=reasoning_effort,
        ),
        consumer_side: resolve_adapter(
            consumer_agent,
            config=config,
            model=model,
            reasoning_effort=reasoning_effort,
        ),
    }
    agents = {
        provider_side: provider_agent,
        consumer_side: consumer_agent,
    }
    side_cwds = {
        initiator_side: cwd,
        _other_side(initiator_side) if not legacy_mode else provider_side: counterpart_cwd or cwd,
    }
    resume_thread_id = proposal_thread_id or _find_resumable_thread_id(
        store,
        issue=issue,
        config=config,
    )
    if resume_thread_id is None:
        first = _run_side_turn(
            round_number=1,
            side=initiator_side,
            agent=agents[initiator_side],
            adapter=adapters[initiator_side],
            session_id=_new_round_session_id(adapters[initiator_side]),
            seed=seed,
            thread=[],
            issue=issue,
            cwd=side_cwds[initiator_side],
            timeout=timeout,
            runner=runner,
        )
        first_entry = store.create_thread(
            side=initiator_side,
            verdict=first.parsed.verdict,
            title=_entry_title(initiator_side, first.parsed),
            body=first.parsed.notes,
            origin=entry_origin(issue, config=config, side=initiator_side, round_number=1),
            contract=first.parsed.contract,
        )
        run_records.append(first.run)
        thread_id = first_entry.thread_id
        thread = _thread_for_run(store.get_thread(thread_id), normalize_legacy=not legacy_mode)
    else:
        thread_id = resume_thread_id
        thread = _thread_for_run(store.get_thread(thread_id), normalize_legacy=not legacy_mode)
        stored_status = store.get_status(thread_id)
        if stored_status is ThreadStatus.cancelled:
            raise WorkflowError(
                f"Negotiation thread {thread_id} is cancelled and cannot be resumed.",
                code="invalid_transition",
            )
        if thread and stored_status in {ThreadStatus.agreed, ThreadStatus.blocked}:
            return _result(
                thread,
                outcome=stored_status.value,
                runs=run_records,
            )
        if not thread and proposal_thread_id is not None:
            first = _run_side_turn(
                round_number=1,
                side=initiator_side,
                agent=agents[initiator_side],
                adapter=adapters[initiator_side],
                session_id=_new_round_session_id(adapters[initiator_side]),
                seed=seed,
                thread=[],
                issue=issue,
                cwd=side_cwds[initiator_side],
                timeout=timeout,
                runner=runner,
            )
            store.append_initial_entry(
                thread_id,
                side=initiator_side,
                verdict=first.parsed.verdict,
                title=_entry_title(initiator_side, first.parsed),
                body=first.parsed.notes,
                origin=entry_origin(issue, config=config, side=initiator_side, round_number=1),
                contract=first.parsed.contract,
            )
            run_records.append(first.run)
            thread = _thread_for_run(
                store.get_thread(thread_id),
                normalize_legacy=not legacy_mode,
            )
        if not legacy_mode and thread[0].side != initiator_side:
            raise WorkflowError(
                f"Negotiation thread {thread_id} was initiated by the {thread[0].side} "
                f"side, not the requested {initiator_side} side.",
                code="invalid_negotiation_side",
            )

    outcome = _evaluate_convergence(thread)
    if outcome != "negotiating":
        _set_terminal_status(store, thread_id, outcome, thread)
        return _result(thread, outcome=outcome, runs=run_records)

    while len(thread) < max_rounds:
        side = _next_side(thread)
        round_number = len(thread) + 1
        try:
            turn = _run_side_turn(
                round_number=round_number,
                side=side,
                agent=agents[side],
                adapter=adapters[side],
                session_id=_new_round_session_id(adapters[side]),
                seed=seed,
                thread=thread,
                issue=issue,
                cwd=side_cwds[side],
                timeout=timeout,
                runner=runner,
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
            origin=entry_origin(issue, config=config, side=side, round_number=round_number),
            contract=turn.parsed.contract,
        )
        run_records.append(turn.run)
        thread = _thread_for_run(store.get_thread(thread_id), normalize_legacy=not legacy_mode)

        outcome = _evaluate_convergence(thread)
        if outcome != "negotiating":
            _set_terminal_status(store, thread_id, outcome, thread)
            return _result(thread, outcome=outcome, runs=run_records)

    return _result(thread, outcome="escalate", runs=run_records)


def inspect_thread(thread_id: str, *, store: NegotiationStore) -> NegotiationThreadInspection:
    thread = store.get_thread(thread_id)
    status = store.get_status(thread_id)
    try:
        issue_refs = store.get_issue_refs(thread_id)
    except WorkflowError as exc:
        if exc.code != "server_schema_drift":
            raise
        issue_refs = None
    source_proposal_ref = store.get_source_proposal_ref(thread_id)
    return NegotiationThreadInspection(
        thread_id=thread_id,
        status=status,
        outcome=_evaluate_convergence(thread),
        final_contract=_latest_contract(thread),
        agreed_contract=store.get_agreed_contract(thread_id),
        issue_refs=issue_refs,
        source_proposal_ref=source_proposal_ref or source_proposal_ref_from_thread(thread),
        entries=tuple(_normalize_thread(thread)),
    )


@dataclass(frozen=True)
class _TurnResult:
    parsed: ParsedRound
    run: RoundRun


def _run_side_turn(
    *,
    round_number: int,
    side: str,
    agent: str,
    adapter: AgentAdapter,
    session_id: str | None,
    seed: str,
    thread: list[NegotiationEntry],
    issue: Issue,
    cwd: Path,
    timeout: float,
    runner: AgentRunner,
) -> _TurnResult:
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
    )
    result = readonly_run.result
    run_id = _run_id(result)
    print(
        f"round={round_number} side={side} agent={agent} run_id={run_id or '-'} "
        f"session_id={session_id or '-'}",
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


def _new_round_session_id(adapter: AgentAdapter) -> str | None:
    if not adapter.supports_session_resume():
        return None
    return str(uuid.uuid4())


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


def _evaluate_convergence(thread: list[NegotiationEntry]) -> str:
    if any(entry.verdict is Verdict.blocked for entry in thread):
        return "blocked"
    if not thread:
        return "negotiating"

    latest = thread[-1]
    latest_hash = _contract_hash(latest.contract)
    if latest.verdict is Verdict.agree and latest_hash is not None:
        for entry in reversed(thread[:-1]):
            if entry.side == latest.side:
                continue
            if _contract_hash(entry.contract) == latest_hash:
                return "agreed"

    agreements: dict[str, str] = {}
    for entry in thread:
        if entry.verdict is not Verdict.agree:
            continue
        contract_hash = _contract_hash(entry.contract)
        if contract_hash is None:
            continue
        agreements[entry.side] = contract_hash
    if len(agreements) >= 2 and len(set(agreements.values())) == 1:
        return "agreed"
    return "negotiating"


def _contract_hash(contract: str | None) -> str | None:
    if contract is None:
        return None
    normalized = " ".join(contract.split()).strip()
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


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


def _next_side(thread: list[NegotiationEntry]) -> str:
    if not thread:
        raise ValueError("A negotiation thread needs an initiating entry.")
    if thread[-1].side == "frontend":
        return "backend"
    if thread[-1].side == "backend":
        return "frontend"
    return _other_side(_normalized_side(thread[-1].side))


def _thread_for_run(
    thread: list[NegotiationEntry], *, normalize_legacy: bool
) -> list[NegotiationEntry]:
    return _normalize_thread(thread) if normalize_legacy else thread


def _normalize_thread(thread: list[NegotiationEntry]) -> list[NegotiationEntry]:
    return [replace(entry, side=_normalized_side(entry.side)) for entry in thread]


def _normalized_side(side: str) -> str:
    return LEGACY_SIDES.get(side, side)


def _other_side(side: str) -> str:
    if side == PROVIDER_SIDE:
        return CONSUMER_SIDE
    if side == CONSUMER_SIDE:
        return PROVIDER_SIDE
    raise ValueError(f"Unknown negotiation side: {side}")


def _latest_contract(thread: list[NegotiationEntry]) -> str | None:
    for entry in reversed(thread):
        if entry.contract:
            return entry.contract
    return None


def finalize_refusal_reason(status: ThreadStatus, thread: list[NegotiationEntry]) -> str | None:
    if status is ThreadStatus.agreed:
        return None
    if status is ThreadStatus.blocked:
        return "thread is blocked"
    if not thread:
        return "thread has no entries"
    if any(entry.verdict is Verdict.blocked for entry in thread):
        return "at least one turn is blocked"
    latest = thread[-1]
    latest_hash = _contract_hash(latest.contract)
    if latest.verdict is not Verdict.agree:
        return f"latest verdict is {latest.verdict.value}, not agree"
    if latest_hash is None:
        return "latest agree turn has no contract"
    counterpart_entries = [entry for entry in thread[:-1] if entry.side != latest.side]
    if not counterpart_entries:
        return "no counterpart turn exists"
    if any(_contract_hash(entry.contract) == latest_hash for entry in counterpart_entries):
        return None
    return "latest agree contract does not match a counterpart contract"


def _entry_title(side: str, parsed: ParsedRound) -> str:
    return f"{side} {parsed.verdict.value}"


def entry_origin(
    issue: Issue,
    *,
    config: IssuekitConfig,
    side: str,
    round_number: int,
) -> str:
    if issue.metadata.get("source_type") == "proposal":
        source_ref = issue.ref
    else:
        issue_id = issue.id if issue.id is not None else "unknown"
        source_ref = f"{config.project}#{issue_id}"
    return f"{source_ref}@{side}:round-{round_number}"


def origin_issue_ref_from_thread(thread: list[NegotiationEntry]) -> str | None:
    if not thread:
        return None
    origin = thread[0].origin.split("@", 1)[0].strip()
    return origin or None


def source_proposal_ref_from_thread(thread: list[NegotiationEntry]) -> str | None:
    origin_ref = origin_issue_ref_from_thread(thread)
    if origin_ref is None or "#proposal:" not in origin_ref:
        return None
    return origin_ref


def _require_issue_id(issue: Issue) -> int:
    if issue.id is None:
        raise WorkflowError(f"Created issue {issue.ref} has no id.", code="invalid_response")
    return issue.id


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

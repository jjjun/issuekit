"""Implementation of the negotiate command."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import sys
import uuid
from typing import Protocol

from issuekit.agents.runner import AgentAdapter, AgentResult, AgentRunner, resolve_adapter
from issuekit.commands._common import run_command
from issuekit.config import IssuekitConfig, load_config
from issuekit.core import Issue, parse_issue_id_arg
from issuekit.negotiation import (
    NegotiationIssueRefs,
    NegotiationEntry,
    NegotiationStore,
    NegotiationThreadSummary,
    ThreadStatus,
    Verdict,
    get_negotiation_store,
)
from issuekit.negotiation_prompts import (
    NegotiationParseError,
    ParsedRound,
    parse_round_output,
    render_round_prompt,
)
from issuekit.store import get_store
from issuekit.workflow import WorkflowError


FRONTEND_SIDE = "frontend"
BACKEND_SIDE = "backend"
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
    entries: tuple[NegotiationEntry, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "thread_id": self.thread_id,
            "status": self.status.value,
            "outcome": self.outcome,
            "final_contract": self.final_contract,
            "agreed_contract": self.agreed_contract,
            "issue_refs": self.issue_refs.to_dict() if self.issue_refs else None,
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
            "finalize_refusal": _finalize_refusal_reason(self.status, list(self.entries)),
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
    ) -> Issue:
        project_config = replace(self.config, project=project)
        store = get_store(project_config)
        return store.create_issue(  # type: ignore[attr-defined]
            title=title,
            body=body,
            priority=priority,
            author=author,
        )

    def update_issue_body(self, *, project: str, issue_id: int, body: str) -> Issue:
        project_config = replace(self.config, project=project)
        store = get_store(project_config)
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
            metadata={"title": title},
        )
        self.issues[issue.ref] = issue
        return issue

    def update_issue_body(self, *, project: str, issue_id: int, body: str) -> Issue:
        ref = f"{project}#{issue_id}"
        issue = self.issues.get(ref)
        if issue is None:
            raise WorkflowError(f"Issue {ref} was not found.", code="not_found")
        updated = Issue(
            id=issue.id,
            ref=issue.ref,
            title=issue.title,
            issue_status=issue.issue_status,
            created=issue.created,
            completed=issue.completed,
            priority=issue.priority,
            assignee=issue.assignee,
            stage=issue.stage,
            implementer=issue.implementer,
            author=issue.author,
            body=body,
            metadata=issue.metadata,
            worker=issue.worker,
        )
        self.issues[ref] = updated
        return updated


def run(args) -> int:
    def action() -> int:
        cwd = Path.cwd()
        config = load_config(cwd)
        if args.finalize:
            _require_finalize_args(args)
            store = get_negotiation_store(config, use_mock=bool(args.mock))
            creator: IssueCreator = MockIssueCreator() if args.mock else ApiIssueCreator(config)
            result = finalize_negotiation(
                thread_id=args.finalize,
                to_project=args.to,
                author_agent=args.author_agent,
                priority=args.priority,
                config=config,
                store=store,
                issue_creator=creator,
            )
            if args.json:
                print(json.dumps(result.to_dict(), indent=2))
            else:
                _print_human_finalization_result(result)
            return 0

        _require_round_args(args)
        issue_id = parse_issue_id_arg(args.from_issue)
        max_rounds = int(args.max_rounds)
        if max_rounds < 1:
            raise ValueError("--max-rounds must be at least 1.")

        issue = get_store(config).get_issue(issue_id)
        if issue is None:
            print(f"Active issue #{issue_id} was not found.", file=sys.stderr)
            return 1

        store = get_negotiation_store(config, use_mock=bool(args.mock))
        result = run_negotiation(
            issue=issue,
            to_project=args.to,
            frontend_agent=args.frontend_agent,
            backend_agent=args.backend_agent,
            max_rounds=max_rounds,
            timeout=float(args.timeout_sec),
            model=args.model,
            config=config,
            cwd=cwd,
            store=store,
        )
        if args.json:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            _print_human_result(result)
        return 0

    return run_command(
        action,
        errors=(
            FileNotFoundError,
            RuntimeError,
            ValueError,
            TimeoutError,
            WorkflowError,
            NegotiationParseError,
        ),
    )


def run_threads(args) -> int:
    def action() -> int:
        config = load_config(Path.cwd())
        store = get_negotiation_store(config, use_mock=bool(args.mock))
        status = ThreadStatus(args.status) if args.status else None
        if args.thread_id:
            inspection = inspect_thread(args.thread_id, store=store)
            if args.json:
                print(json.dumps(inspection.to_dict(), indent=2))
            else:
                _print_human_thread_inspection(inspection)
            return 0

        summaries = store.list_threads(status=status)
        if args.json:
            print(json.dumps([_thread_summary_to_dict(summary) for summary in summaries], indent=2))
        else:
            _print_human_thread_summaries(summaries)
        return 0

    return run_command(
        action,
        errors=(
            FileNotFoundError,
            RuntimeError,
            ValueError,
            WorkflowError,
        ),
    )


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
            f"{_finalize_refusal_reason(status, thread)}",
            code="invalid_transition",
        )

    existing_refs = store.get_issue_refs(thread_id)
    if existing_refs is not None:
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

    origin_issue_ref = _origin_issue_ref(thread)
    frontend_project = config.project
    frontend_title = f"Integrate agreed contract from negotiation {thread_id}"
    backend_title = f"Implement agreed contract from negotiation {thread_id}"

    frontend = issue_creator.create_issue(
        project=frontend_project,
        title=frontend_title,
        body=_frontend_issue_body(
            thread_id=thread_id,
            origin_issue_ref=origin_issue_ref,
            backend_issue_ref="pending",
            contract=contract,
        ),
        priority=priority,
        author=author_agent,
    )
    backend = issue_creator.create_issue(
        project=to_project,
        title=backend_title,
        body=_backend_issue_body(
            thread_id=thread_id,
            origin_issue_ref=origin_issue_ref,
            frontend_issue_ref=frontend.ref,
            contract=contract,
        ),
        priority=priority,
        author=author_agent,
    )
    issue_creator.update_issue_body(
        project=frontend_project,
        issue_id=_require_issue_id(frontend),
        body=_frontend_issue_body(
            thread_id=thread_id,
            origin_issue_ref=origin_issue_ref,
            backend_issue_ref=backend.ref,
            contract=contract,
        ),
    )

    refs = NegotiationIssueRefs(
        backend_issue_ref=backend.ref,
        frontend_issue_ref=frontend.ref,
    )
    store.set_issue_refs(thread_id, refs)
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
    frontend_agent: str,
    backend_agent: str,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    timeout: float = 120.0,
    model: str | None = None,
    config: IssuekitConfig,
    cwd: Path,
    store: NegotiationStore,
    runner: AgentRunner | None = None,
) -> NegotiationResult:
    """Drive a bounded frontend/backend negotiation to a terminal outcome."""

    if max_rounds < 1:
        raise ValueError("max_rounds must be at least 1.")
    runner = runner or AgentRunner()
    seed = _seed_text(issue, config=config, to_project=to_project)
    run_records: list[RoundRun] = []
    adapters = {
        FRONTEND_SIDE: resolve_adapter(frontend_agent, config=config, model=model),
        BACKEND_SIDE: resolve_adapter(backend_agent, config=config, model=model),
    }
    agents = {
        FRONTEND_SIDE: frontend_agent,
        BACKEND_SIDE: backend_agent,
    }
    resume_thread_id = _find_resumable_thread_id(store, issue=issue, config=config)
    if resume_thread_id is None:
        first = _run_side_turn(
            round_number=1,
            side=FRONTEND_SIDE,
            agent=agents[FRONTEND_SIDE],
            adapter=adapters[FRONTEND_SIDE],
            session_id=_new_round_session_id(adapters[FRONTEND_SIDE]),
            seed=seed,
            thread=[],
            issue=issue,
            cwd=cwd,
            timeout=timeout,
            runner=runner,
        )
        first_entry = store.create_thread(
            side=FRONTEND_SIDE,
            verdict=first.parsed.verdict,
            title=_entry_title(FRONTEND_SIDE, first.parsed),
            body=first.parsed.notes,
            origin=_entry_origin(issue, config=config, side=FRONTEND_SIDE, round_number=1),
            contract=first.parsed.contract,
        )
        run_records.append(first.run)
        thread_id = first_entry.thread_id
        thread = store.get_thread(thread_id)
    else:
        thread_id = resume_thread_id
        thread = store.get_thread(thread_id)

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
                cwd=cwd,
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
            origin=_entry_origin(issue, config=config, side=side, round_number=round_number),
            contract=turn.parsed.contract,
        )
        run_records.append(turn.run)
        thread = store.get_thread(thread_id)

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
    return NegotiationThreadInspection(
        thread_id=thread_id,
        status=status,
        outcome=_evaluate_convergence(thread),
        final_contract=_latest_contract(thread),
        agreed_contract=store.get_agreed_contract(thread_id),
        issue_refs=issue_refs,
        entries=tuple(thread),
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
    plan_path = _write_round_prompt(
        cwd,
        issue_id=issue.id,
        round_number=round_number,
        side=side,
        prompt=prompt,
    )
    result = runner.run(
        adapter,
        plan_path,
        cwd,
        timeout=float(timeout),
        agent_name=agent,
        issue_id=issue.id,
        prompt_override=_round_prompt_pointer(plan_path),
        session_id=session_id,
    )
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

    parsed = parse_round_output(_stdout_text(result))
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
            line = _last_nonempty_line(value)
            if line:
                return line
    for path in (result.agent_log_path, result.stdout_path):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        line = _last_nonempty_line(text)
        if line:
            return line
    return None


def _last_nonempty_line(text: str | None) -> str | None:
    if not text:
        return None
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if stripped:
            return stripped
    return None


def _write_round_prompt(
    cwd: Path,
    *,
    issue_id: int | None,
    round_number: int,
    side: str,
    prompt: str,
) -> Path:
    run_dir = cwd / ".agent-runs"
    run_dir.mkdir(exist_ok=True)
    issue_token = str(issue_id) if issue_id is not None else "unknown"
    path = run_dir / f"negotiate-issue-{issue_token}-round-{round_number}-{side}.md"
    path.write_text(prompt, encoding="utf-8", newline="\n")
    return path


def _round_prompt_pointer(plan_path: Path) -> str:
    return (
        f"Read the negotiation round prompt at: {plan_path} and respond with exactly "
        "one fenced negotiation block per its instructions. Do not implement code; "
        "do not modify the tracker."
    )


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
    return "\n".join(
        [
            f"Origin project: {config.project}",
            f"Target project: {to_project}",
            f"Origin issue: {issue.ref}",
            f"Title: {issue.title}",
            "",
            issue.body,
        ]
    )


def _next_side(thread: list[NegotiationEntry]) -> str:
    if not thread or thread[-1].side == BACKEND_SIDE:
        return FRONTEND_SIDE
    return BACKEND_SIDE


def _latest_contract(thread: list[NegotiationEntry]) -> str | None:
    for entry in reversed(thread):
        if entry.contract:
            return entry.contract
    return None


def _finalize_refusal_reason(status: ThreadStatus, thread: list[NegotiationEntry]) -> str | None:
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
    if not any(entry.side != latest.side for entry in thread[:-1]):
        return "no counterpart turn exists"
    return "latest agree contract does not match a counterpart contract"


def _entry_title(side: str, parsed: ParsedRound) -> str:
    return f"{side} {parsed.verdict.value}"


def _entry_origin(
    issue: Issue,
    *,
    config: IssuekitConfig,
    side: str,
    round_number: int,
) -> str:
    issue_id = issue.id if issue.id is not None else "unknown"
    return f"{config.project}#{issue_id}@{side}:round-{round_number}"


def _backend_issue_body(
    *,
    thread_id: str,
    origin_issue_ref: str | None,
    frontend_issue_ref: str,
    contract: str,
) -> str:
    lines = [
        "## Implementation Task",
        "",
        "Implement the backend/API side of the agreed cross-repository contract.",
        "",
        "## Links",
        "",
        f"- Negotiation thread: {thread_id}",
        f"- Frontend/origin issue: {frontend_issue_ref}",
    ]
    if origin_issue_ref:
        lines.append(f"- Originating issue: {origin_issue_ref}")
    lines.extend(
        [
            "",
            "## Agreed Contract",
            "",
            "```",
            contract,
            "```",
            "",
            "## Acceptance Criteria",
            "",
            "- The API behavior described in the agreed contract is implemented.",
            "- The contract is covered by focused tests.",
            "- Any documented request/response shape remains compatible with the frontend issue.",
        ]
    )
    return "\n".join(lines)


def _frontend_issue_body(
    *,
    thread_id: str,
    origin_issue_ref: str | None,
    backend_issue_ref: str,
    contract: str,
) -> str:
    lines = [
        "## Implementation Task",
        "",
        "Integrate the frontend/origin project with the agreed backend contract.",
        "",
        "## Links",
        "",
        f"- Negotiation thread: {thread_id}",
        f"- Backend/API issue: {backend_issue_ref}",
    ]
    if origin_issue_ref:
        lines.append(f"- Originating issue: {origin_issue_ref}")
    lines.extend(
        [
            "",
            "## Agreed Contract",
            "",
            "```",
            contract,
            "```",
            "",
            "## Acceptance Criteria",
            "",
            "- The integration consumes the agreed contract.",
            "- User-facing behavior from the originating issue is covered.",
            "- The implementation handles backend errors or unavailable data clearly.",
        ]
    )
    return "\n".join(lines)


def _origin_issue_ref(thread: list[NegotiationEntry]) -> str | None:
    if not thread:
        return None
    origin = thread[0].origin.split("@", 1)[0].strip()
    return origin or None


def _require_issue_id(issue: Issue) -> int:
    if issue.id is None:
        raise WorkflowError(f"Created issue {issue.ref} has no id.", code="invalid_response")
    return issue.id


def _require_finalize_args(args) -> None:
    if not args.to:
        raise ValueError("--to is required with --finalize.")


def _require_round_args(args) -> None:
    missing = [
        name
        for name, value in (
            ("--from-issue", args.from_issue),
            ("--to", args.to),
            ("--frontend-agent", args.frontend_agent),
            ("--backend-agent", args.backend_agent),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"{', '.join(missing)} required unless --finalize is used.")


def _stdout_text(result: AgentResult) -> str:
    if result.parsed and "stdout" in result.parsed:
        return result.parsed["stdout"]
    return result.stdout_path.read_text(encoding="utf-8", errors="replace")


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


def _print_human_result(result: NegotiationResult) -> None:
    print(
        f"negotiation thread={result.thread_id} outcome={result.outcome} "
        f"rounds={result.round_count}"
    )
    if result.final_contract:
        print("final_contract:")
        print(result.final_contract)
    if result.run_ids:
        print(f"run_ids={','.join(result.run_ids)}")


def _print_human_finalization_result(result: NegotiationFinalizationResult) -> None:
    action = "created" if result.created else "already finalized"
    print(
        f"negotiation thread={result.thread_id} {action} "
        f"backend={result.backend_issue_ref} frontend={result.frontend_issue_ref}"
    )


def _thread_summary_to_dict(summary: NegotiationThreadSummary) -> dict[str, object]:
    return {
        "thread_id": summary.thread_id,
        "status": summary.status.value,
        "agreed_contract": summary.agreed_contract,
        "issue_refs": summary.issue_refs.to_dict() if summary.issue_refs else None,
        "updated": summary.updated,
    }


def _print_human_thread_summaries(summaries: list[NegotiationThreadSummary]) -> None:
    if not summaries:
        print("no negotiation threads")
        return
    print("thread\tstatus\tupdated\tissue_refs")
    for summary in summaries:
        refs = "-"
        if summary.issue_refs is not None:
            refs = f"{summary.issue_refs.backend_issue_ref},{summary.issue_refs.frontend_issue_ref}"
        print(f"{summary.thread_id}\t{summary.status.value}\t{summary.updated or '-'}\t{refs}")


def _print_human_thread_inspection(inspection: NegotiationThreadInspection) -> None:
    print(
        f"negotiation thread={inspection.thread_id} status={inspection.status.value} "
        f"outcome={inspection.outcome} entries={len(inspection.entries)}"
    )
    if inspection.final_contract:
        print("final_contract:")
        print(inspection.final_contract)
    refusal = _finalize_refusal_reason(inspection.status, list(inspection.entries))
    if refusal:
        print(f"finalize_refusal={refusal}")
    for entry in inspection.entries:
        print(
            f"- id={entry.id or '-'} side={entry.side} verdict={entry.verdict.value} "
            f"origin={entry.origin} contract={entry.contract or '-'}"
        )

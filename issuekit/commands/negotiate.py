"""Implementation of the negotiate command."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys

from issuekit.agents.runner import AgentResult, AgentRunner, resolve_adapter
from issuekit.commands._common import run_command
from issuekit.config import IssuekitConfig, load_config
from issuekit.core import Issue, parse_issue_id_arg
from issuekit.negotiation import (
    NegotiationEntry,
    NegotiationStore,
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
                }
                for run in self.round_runs
            ],
        }


def run(args) -> int:
    def action() -> int:
        issue_id = parse_issue_id_arg(args.from_issue)
        max_rounds = int(args.max_rounds)
        if max_rounds < 1:
            raise ValueError("--max-rounds must be at least 1.")

        cwd = Path.cwd()
        config = load_config(cwd)
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

    first = _run_side_turn(
        round_number=1,
        side=FRONTEND_SIDE,
        agent=frontend_agent,
        seed=seed,
        thread=[],
        issue=issue,
        config=config,
        cwd=cwd,
        timeout=timeout,
        model=model,
        runner=runner,
    )
    first_entry = store.create_thread(
        side=FRONTEND_SIDE,
        verdict=first.parsed.verdict,
        title=_entry_title(FRONTEND_SIDE, first.parsed),
        body=first.parsed.notes,
        origin=_entry_origin(issue, config=config, side=FRONTEND_SIDE),
        contract=first.parsed.contract,
    )
    run_records.append(first.run)
    thread_id = first_entry.thread_id
    thread = store.get_thread(thread_id)

    outcome = _evaluate_convergence(thread)
    if outcome != "negotiating":
        _set_terminal_status(store, thread_id, outcome)
        return _result(thread, outcome=outcome, runs=run_records)

    while len(thread) < max_rounds:
        side = _next_side(thread)
        agent = frontend_agent if side == FRONTEND_SIDE else backend_agent
        turn = _run_side_turn(
            round_number=len(thread) + 1,
            side=side,
            agent=agent,
            seed=seed,
            thread=thread,
            issue=issue,
            config=config,
            cwd=cwd,
            timeout=timeout,
            model=model,
            runner=runner,
        )
        store.append_entry(
            thread_id,
            side=side,
            verdict=turn.parsed.verdict,
            title=_entry_title(side, turn.parsed),
            body=turn.parsed.notes,
            origin=_entry_origin(issue, config=config, side=side),
            contract=turn.parsed.contract,
        )
        run_records.append(turn.run)
        thread = store.get_thread(thread_id)

        outcome = _evaluate_convergence(thread)
        if outcome != "negotiating":
            _set_terminal_status(store, thread_id, outcome)
            return _result(thread, outcome=outcome, runs=run_records)

    return _result(thread, outcome="escalate", runs=run_records)


@dataclass(frozen=True)
class _TurnResult:
    parsed: ParsedRound
    run: RoundRun


def _run_side_turn(
    *,
    round_number: int,
    side: str,
    agent: str,
    seed: str,
    thread: list[NegotiationEntry],
    issue: Issue,
    config: IssuekitConfig,
    cwd: Path,
    timeout: float,
    model: str | None,
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
    adapter = resolve_adapter(agent, config=config, model=model)
    result = runner.run(
        adapter,
        plan_path,
        cwd,
        timeout=float(timeout),
        agent_name=agent,
        issue_id=issue.id,
        prompt_override=prompt,
    )
    run_id = _run_id(result)
    print(
        f"round={round_number} side={side} agent={agent} run_id={run_id or '-'}",
        file=sys.stderr,
    )

    if result.timed_out:
        raise TimeoutError(f"Negotiation round {round_number} timed out for {side}.")
    if result.exit_code != 0:
        raise WorkflowError(
            f"Negotiation round {round_number} failed for {side} "
            f"with exit code {result.exit_code}.",
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
        ),
    )


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


def _evaluate_convergence(thread: list[NegotiationEntry]) -> str:
    if any(entry.verdict is Verdict.blocked for entry in thread):
        return "blocked"

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


def _set_terminal_status(store: NegotiationStore, thread_id: str, outcome: str) -> None:
    if outcome == "agreed":
        store.set_status(thread_id, ThreadStatus.agreed)
    elif outcome == "blocked":
        store.set_status(thread_id, ThreadStatus.blocked)


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


def _entry_title(side: str, parsed: ParsedRound) -> str:
    return f"{side} {parsed.verdict.value}"


def _entry_origin(issue: Issue, *, config: IssuekitConfig, side: str) -> str:
    issue_id = issue.id if issue.id is not None else "unknown"
    return f"{config.project}#{issue_id}:{side}"


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

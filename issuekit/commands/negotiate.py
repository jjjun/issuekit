"""Implementation of the negotiate command."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from issuekit.agents.runner import AgentRunner
from issuekit.commands._common import run_command
from issuekit.config import load_config
from issuekit.core import parse_issue_id_arg
from issuekit.negotiation import (
    NegotiationThreadSummary,
    ThreadStatus,
    get_negotiation_store,
)
from issuekit.negotiation.engine import (
    ApiIssueCreator,
    DEFAULT_MAX_ROUNDS,
    IssueCreator,
    MockIssueCreator,
    NegotiationFinalizationResult,
    NegotiationResult,
    NegotiationThreadInspection,
    _entry_origin,
    _finalize_refusal_reason,
    _origin_issue_ref,
    finalize_negotiation,
    inspect_thread,
    run_negotiation,
)
from issuekit.negotiation_prompts import NegotiationParseError
from issuekit.store import get_store
from issuekit.workflow import WorkflowError


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
            runner=AgentRunner(),
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

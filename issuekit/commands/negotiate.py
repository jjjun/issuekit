"""Implementation of the negotiate command."""

from __future__ import annotations

import argparse
import sys
import tomllib
from dataclasses import replace
from pathlib import Path

from issuekit.agentrun import AgentRunner
from issuekit.commands._common import print_json, run_agent_command, run_command
from issuekit.config import load_config
from issuekit.config.refs import list_effective_refs
from issuekit.core import parse_issue_id_arg
from issuekit.errors import AGENT_RUN_ERRORS, WorkflowError
from issuekit.gitutil import git_status_short
from issuekit.inputs import active_issue_not_found
from issuekit.negotiation import (
    NegotiationThreadSummary,
    ThreadStatus,
    get_negotiation_store,
)
from issuekit.negotiation.engine import (
    DEFAULT_MAX_ROUNDS,
    ApiIssueCreator,
    IssueCreator,
    MockIssueCreator,
    NegotiationFinalizationResult,
    NegotiationResult,
    NegotiationThreadInspection,
    finalize_negotiation,
    finalize_refusal_reason,
    inspect_thread,
    run_negotiation,
)
from issuekit.proposals.api import validate_target_project
from issuekit.store import get_store
from issuekit.workflow import require_implementer


def register(subparsers: argparse._SubParsersAction) -> None:
    negotiate_parser = subparsers.add_parser(
        "negotiate",
        help="Drive a bounded cross-repository design negotiation.",
    )
    negotiate_parser.add_argument("--from-issue", help="Originating issue id.")
    negotiate_parser.add_argument("--to", help="Target project name.")
    negotiate_parser.add_argument(
        "--finalize",
        metavar="THREAD_ID",
        help="Create cross-linked implementation issues for an agreed thread.",
    )
    negotiate_parser.add_argument(
        "--cancel",
        metavar="THREAD_ID",
        help="Cancel a negotiation thread.",
    )
    negotiate_parser.add_argument(
        "--provider-agent",
        help="Configured agent representing the provider side.",
    )
    negotiate_parser.add_argument(
        "--consumer-agent",
        help="Configured agent representing the consumer side.",
    )
    negotiate_parser.add_argument(
        "--counterpart-ref",
        help="Effective ref whose checkout the counterpart agent inspects.",
    )
    negotiate_parser.add_argument(
        "--initiator-side",
        choices=("provider", "consumer"),
        help="Role held by the initiating checkout.",
    )
    negotiate_parser.add_argument(
        "--max-rounds",
        type=int,
        default=DEFAULT_MAX_ROUNDS,
        help="Maximum total agent turns, including the opening turn.",
    )
    negotiate_parser.add_argument(
        "--mock",
        action="store_true",
        help="Use the local mock negotiation store.",
    )
    negotiate_parser.add_argument("--model", help="Optional model name passed to both agents.")
    negotiate_parser.add_argument(
        "--reasoning-effort", help="Optional reasoning effort passed to both agents."
    )
    negotiate_parser.add_argument(
        "--timeout-sec",
        type=float,
        default=120.0,
        help="Hard timeout for each negotiation turn in seconds.",
    )
    negotiate_parser.add_argument(
        "--author-agent",
        help="Author agent for issues created by --finalize.",
    )
    negotiate_parser.add_argument(
        "--priority",
        choices=("high", "medium", "low"),
        default="medium",
        help="Priority for issues created by --finalize.",
    )
    negotiate_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    negotiate_parser.set_defaults(func=run)

    threads_parser = subparsers.add_parser(
        "threads",
        help="Inspect negotiation thread status.",
    )
    threads_parser.add_argument("thread_id", nargs="?", help="Negotiation thread id to inspect.")
    threads_parser.add_argument(
        "--status",
        choices=("negotiating", "agreed", "blocked", "cancelled"),
        help="Filter listed threads by status.",
    )
    threads_parser.add_argument(
        "--mock",
        action="store_true",
        help="Use the local mock negotiation store.",
    )
    threads_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    threads_parser.set_defaults(func=run_threads)


def run(args) -> int:
    def action() -> int:
        cwd = Path.cwd()
        config = load_config(cwd)
        if args.cancel:
            _require_cancel_args(args)
            store_config = replace(config, project=args.to)
            with get_negotiation_store(store_config, use_mock=False) as store:
                status = store.get_status(args.cancel)
                if status is not ThreadStatus.cancelled:
                    store.cancel_thread(args.cancel)
                store.settle_thread_members(args.cancel)
            if args.json:
                print_json({"thread_id": args.cancel, "status": "cancelled"})
            else:
                print(f"negotiation thread={args.cancel} status=cancelled")
            return 0
        if args.finalize:
            _require_finalize_args(args)
            author_agent = require_implementer(
                args.author_agent,
                config,
                flag="--author-agent",
            )
            if not args.mock:
                validate_target_project(config, args.to)
            creator: IssueCreator = MockIssueCreator() if args.mock else ApiIssueCreator(config)
            with get_negotiation_store(config, use_mock=bool(args.mock)) as store:
                result = finalize_negotiation(
                    thread_id=args.finalize,
                    to_project=args.to,
                    author_agent=author_agent,
                    priority=args.priority,
                    config=config,
                    store=store,
                    issue_creator=creator,
                )
            if args.json:
                print_json(result.to_dict())
            else:
                _print_human_finalization_result(result)
            return 0

        _require_round_args(args)
        counterpart_cwd = _resolve_counterpart_cwd(args.counterpart_ref, args.to, cwd)
        max_rounds = int(args.max_rounds)
        if max_rounds < 1:
            raise ValueError("--max-rounds must be at least 1.")
        if not args.mock:
            validate_target_project(config, args.to)

        issue_id = parse_issue_id_arg(args.from_issue)
        with get_store(config) as issue_store:
            issue = issue_store.get_issue(issue_id)
            if issue is None:
                print(active_issue_not_found(issue_id), file=sys.stderr)
                return 1

        with get_negotiation_store(config, use_mock=bool(args.mock)) as store:
            result = run_negotiation(
                issue=issue,
                to_project=args.to,
                initiator_side=args.initiator_side,
                provider_agent=args.provider_agent,
                consumer_agent=args.consumer_agent,
                max_rounds=max_rounds,
                timeout=float(args.timeout_sec),
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                config=config,
                cwd=cwd,
                counterpart_cwd=counterpart_cwd,
                store=store,
                runner=AgentRunner(),
            )
        if args.json:
            print_json(result.to_dict())
        else:
            _print_human_result(result)
        return 0

    return run_agent_command(
        action,
        errors=AGENT_RUN_ERRORS,
    )


def run_threads(args) -> int:
    def action() -> int:
        config = load_config(Path.cwd())
        with get_negotiation_store(config, use_mock=bool(args.mock)) as store:
            status = ThreadStatus(args.status) if args.status else None
            if args.thread_id:
                inspection = inspect_thread(args.thread_id, store=store)
                if args.json:
                    print_json(inspection.to_dict())
                else:
                    _print_human_thread_inspection(inspection)
                return 0

            summaries = store.list_threads(status=status)
            if args.json:
                print_json([summary.to_dict() for summary in summaries])
            else:
                _print_human_thread_summaries(summaries)
            return 0

    return run_command(
        action,
        errors=(
            FileNotFoundError,
            RuntimeError,
            ValueError,
        ),
    )


def _require_finalize_args(args) -> None:
    if not args.to:
        raise ValueError("--to is required with --finalize.")
    if args.from_issue:
        raise ValueError("--from-issue cannot be used with --finalize.")


def _require_cancel_args(args) -> None:
    if not args.to:
        raise ValueError("--to is required with --cancel.")
    if args.mock:
        raise ValueError("--cancel requires the API negotiation store.")


def _require_round_args(args) -> None:
    if not args.from_issue:
        raise ValueError(
            "--from-issue is required unless --finalize or --cancel is used."
        )
    missing = [
        name
        for name, value in (
            ("--initiator-side", args.initiator_side),
            ("--provider-agent", args.provider_agent),
            ("--consumer-agent", args.consumer_agent),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"{', '.join(missing)} required unless --finalize is used.")
    if not args.to:
        raise ValueError("--to is required with --from-issue.")


def _resolve_counterpart_cwd(
    counterpart_ref: str | None,
    to_project: str,
    cwd: Path,
) -> Path:
    refs = list_effective_refs(cwd)
    if counterpart_ref is not None:
        entry = refs.get(counterpart_ref)
        if entry is None:
            known_refs = ", ".join(refs) or "(none)"
            raise WorkflowError(
                f"Unknown counterpart ref {counterpart_ref!r}. Known refs: {known_refs}."
            )
        return _validated_counterpart_cwd(counterpart_ref, entry.path, to_project, required=True)

    for ref_name, entry in refs.items():
        if _counterpart_project(entry.path) == to_project:
            try:
                return _validated_counterpart_cwd(ref_name, entry.path, to_project, required=False)
            except WorkflowError as exc:
                print(
                    f"Ignoring automatically resolved counterpart ref {ref_name!r}: {exc}",
                    file=sys.stderr,
                )
                return cwd
    return cwd


def _validated_counterpart_cwd(
    counterpart_ref: str,
    counterpart_cwd: Path,
    to_project: str,
    *,
    required: bool,
) -> Path:
    counterpart_project = _counterpart_project(counterpart_cwd, required=required)
    if counterpart_project != to_project:
        raise WorkflowError(
            f"Counterpart ref {counterpart_ref!r} points to project {counterpart_project!r}, "
            f"not requested project {to_project!r}."
        )

    if git_status_short(counterpart_cwd):
        raise WorkflowError(
            f"Counterpart ref {counterpart_ref!r} points to a dirty checkout: {counterpart_cwd}."
        )
    return counterpart_cwd


def _counterpart_project(counterpart_cwd: Path, *, required: bool = False) -> str | None:
    try:
        config_data = _counterpart_config_data(counterpart_cwd)
    except (OSError, TypeError, ValueError) as exc:
        if not required:
            return None
        raise WorkflowError(
            f"Could not read issuekit configuration for counterpart checkout {counterpart_cwd}: {exc}"
        ) from exc

    if config_data is None:
        if not required:
            return None
        raise WorkflowError(f"Counterpart ref checkout {counterpart_cwd} has no readable issuekit configuration.")

    project = config_data.get("project")
    if isinstance(project, str) and project.strip():
        return project.strip()
    if required:
        raise WorkflowError(
            f"Counterpart ref checkout {counterpart_cwd} does not declare a project in its "
            "issuekit configuration."
        )
    return None


def _counterpart_config_data(counterpart_cwd: Path) -> dict[str, object] | None:
    # load_config validates all settings and reads machine-local state; this only
    # needs the counterpart's declared project and must not inherit either.
    pyproject_path = counterpart_cwd / "pyproject.toml"
    if pyproject_path.exists():
        with pyproject_path.open("rb") as config_file:
            data = tomllib.load(config_file)
        project_config = data.get("tool", {}).get("issuekit")
        if project_config is not None:
            return dict(project_config)

    issuekit_path = counterpart_cwd / "issuekit.toml"
    if not issuekit_path.exists():
        return None
    with issuekit_path.open("rb") as config_file:
        return tomllib.load(config_file)


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
        f"provider={result.backend_issue_ref} consumer={result.frontend_issue_ref}"
    )


def _print_human_thread_summaries(summaries: list[NegotiationThreadSummary]) -> None:
    if not summaries:
        print("no negotiation threads")
        return
    print("thread\tstatus\tupdated\tissue_refs")
    for summary in summaries:
        refs = "-"
        if summary.issue_refs is not None:
            refs = f"{summary.issue_refs.backend_issue_ref},{summary.issue_refs.frontend_issue_ref}"
        print(
            f"{summary.thread_id}\t{summary.status.value}\t{summary.updated or '-'}\t{refs}"
        )


def _print_human_thread_inspection(inspection: NegotiationThreadInspection) -> None:
    print(
        f"negotiation thread={inspection.thread_id} status={inspection.status.value} "
        f"outcome={inspection.outcome} entries={len(inspection.entries)}"
    )
    if inspection.final_contract:
        print("final_contract:")
        print(inspection.final_contract)
    refusal = finalize_refusal_reason(inspection.status, list(inspection.entries))
    if refusal:
        print(f"finalize_refusal={refusal}")
    for entry in inspection.entries:
        print(
            f"- id={entry.id or '-'} side={entry.side} verdict={entry.verdict.value} "
            f"origin={entry.origin} contract={entry.contract or '-'}"
        )

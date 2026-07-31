"""Implementation of the negotiate command."""

from __future__ import annotations

import argparse
import re
import sys
import tomllib
from dataclasses import replace
from pathlib import Path

from issuekit.agentrun import AgentRunner
from issuekit.commands._common import print_json, run_command
from issuekit.config import IssuekitConfig, load_config
from issuekit.config.refs import RefError, list_effective_refs
from issuekit.core import Issue, parse_issue_id_arg
from issuekit.gitutil import git_status_short
from issuekit.negotiation import (
    NegotiationThreadSummary,
    ProposalNegotiationSource,
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
from issuekit.negotiation.prompts import NegotiationParseError
from issuekit.proposals import ProposalError
from issuekit.proposals.api import validate_target_project
from issuekit.store import get_store
from issuekit.workflow import WorkflowError, resolve_implementer


def register(subparsers: argparse._SubParsersAction) -> None:
    negotiate_parser = subparsers.add_parser(
        "negotiate",
        help="Drive a bounded cross-repository design negotiation.",
    )
    negotiate_parser.add_argument("--from-issue", help="Originating issue id.")
    negotiate_parser.add_argument(
        "--from-proposal",
        help="Pending proposal ref, for example mine-py#proposal:531.",
    )
    negotiate_parser.add_argument("--to", help="Target project name.")
    negotiate_parser.add_argument(
        "--finalize",
        metavar="THREAD_ID",
        help="Create cross-linked implementation issues for an agreed thread.",
    )
    negotiate_parser.add_argument(
        "--cancel",
        metavar="THREAD_ID",
        help="Cancel a proposal-seeded negotiation and unlock its pending proposal.",
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
            target_project = _proposal_target(args.from_proposal, args.to)
            target_config = replace(config, project=target_project)
            with get_negotiation_store(target_config, use_mock=False) as store:
                store.cancel_thread(args.cancel)
            if args.json:
                print_json({"thread_id": args.cancel, "status": "cancelled"})
            else:
                print(f"negotiation thread={args.cancel} status=cancelled")
            return 0
        if args.finalize:
            _require_finalize_args(args)
            if args.from_proposal:
                args.to = _proposal_target(args.from_proposal, args.to)
            author_agent = resolve_implementer(args.author_agent, config)
            if author_agent is None:
                raise WorkflowError(
                    "No implementer is configured. Pass --author-agent, set "
                    "default_implementer, or configure exactly one enabled assignee."
                )
            if not args.mock:
                _warn_target_validation(config, args.to)
            creator: IssueCreator = MockIssueCreator() if args.mock else ApiIssueCreator(config)
            store_config = config
            if args.from_proposal:
                store_config = replace(
                    config,
                    project=_proposal_target(args.from_proposal, args.to),
                )
            with get_negotiation_store(store_config, use_mock=bool(args.mock)) as store:
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
        if args.from_proposal:
            args.to = _proposal_target(args.from_proposal, args.to)
        counterpart_cwd = _resolve_counterpart_cwd(args.counterpart_ref, args.to, cwd)
        max_rounds = int(args.max_rounds)
        if max_rounds < 1:
            raise ValueError("--max-rounds must be at least 1.")
        if not args.mock:
            _warn_target_validation(config, args.to)

        proposal_thread_id = None
        store_config = config
        if args.from_proposal:
            if args.mock:
                raise ValueError("--from-proposal requires the API negotiation store.")
            if args.initiator_side != "consumer":
                raise ValueError(
                    "--from-proposal requires --initiator-side consumer because the "
                    "target proposal becomes the provider issue."
                )
            proposal_id = _proposal_ref_parts(args.from_proposal)[1]
            store_config = replace(config, project=args.to)
            with get_negotiation_store(store_config, use_mock=False) as proposal_store:
                source = proposal_store.begin_proposal_thread(
                    proposal_id,
                    initiator_project=config.project,
                    initiator_side=args.initiator_side,
                )
            issue = _proposal_source_issue(source)
            proposal_thread_id = source.thread_id
        else:
            issue_id = parse_issue_id_arg(args.from_issue)
            with get_store(config) as issue_store:
                issue = issue_store.get_issue(issue_id)
            if issue is None:
                print(f"Active issue #{issue_id} was not found.", file=sys.stderr)
                return 1

        with get_negotiation_store(store_config, use_mock=bool(args.mock)) as store:
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
                proposal_thread_id=proposal_thread_id,
            )
        if args.json:
            print_json(result.to_dict())
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
            ProposalError,
            RefError,
            WorkflowError,
            NegotiationParseError,
        ),
    )


def _warn_target_validation(config: IssuekitConfig, target_project: str) -> None:
    for warning in validate_target_project(config, target_project):
        print(warning, file=sys.stderr)


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
                print_json([_thread_summary_to_dict(summary) for summary in summaries])
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
    if not args.to and not args.from_proposal:
        raise ValueError("--to or --from-proposal is required with --finalize.")
    if args.from_issue:
        raise ValueError("--from-issue cannot be used with --finalize.")


def _require_cancel_args(args) -> None:
    if not args.from_proposal and not args.to:
        raise ValueError("--from-proposal or --to is required with --cancel.")
    if args.mock:
        raise ValueError("--cancel requires the API negotiation store.")


def _require_round_args(args) -> None:
    if bool(args.from_issue) == bool(args.from_proposal):
        raise ValueError(
            "Exactly one of --from-issue or --from-proposal is required unless "
            "--finalize or --cancel is used."
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
    if args.from_issue and not args.to:
        raise ValueError("--to is required with --from-issue.")


def _proposal_ref_parts(value: str) -> tuple[str, int]:
    match = re.fullmatch(r"([A-Za-z0-9_.-]+)#proposal:([1-9][0-9]*)", value.strip())
    if match is None:
        raise ValueError(
            f"Invalid proposal ref {value!r}; expected <project>#proposal:<id>."
        )
    return match.group(1), int(match.group(2))


def _proposal_target(from_proposal: str | None, to_project: str | None) -> str:
    if from_proposal:
        proposal_project, _ = _proposal_ref_parts(from_proposal)
        if to_project and to_project != proposal_project:
            raise ValueError(
                f"--to {to_project!r} does not match proposal target {proposal_project!r}."
            )
        return proposal_project
    if not to_project:
        raise ValueError("--to is required.")
    return to_project


def _proposal_source_issue(source: ProposalNegotiationSource) -> Issue:
    return Issue(
        id=source.proposal_id,
        ref=source.proposal_ref,
        title=source.title,
        issue_status="active",
        created="",
        completed="",
        priority="medium",
        assignee="",
        stage="todo",
        implementer="",
        author="",
        body=source.body,
        metadata={"origin": source.origin, "source_type": "proposal"},
    )


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


def _thread_summary_to_dict(summary: NegotiationThreadSummary) -> dict[str, object]:
    return {
        "thread_id": summary.thread_id,
        "status": summary.status.value,
        "agreed_contract": summary.agreed_contract,
        "issue_refs": summary.issue_refs.to_dict() if summary.issue_refs else None,
        "source_proposal_ref": summary.source_proposal_ref,
        "updated": summary.updated,
    }


def _print_human_thread_summaries(summaries: list[NegotiationThreadSummary]) -> None:
    if not summaries:
        print("no negotiation threads")
        return
    print("thread\tstatus\tupdated\tissue_refs\tsource_proposal")
    for summary in summaries:
        refs = "-"
        if summary.issue_refs is not None:
            refs = f"{summary.issue_refs.backend_issue_ref},{summary.issue_refs.frontend_issue_ref}"
        print(
            f"{summary.thread_id}\t{summary.status.value}\t{summary.updated or '-'}\t"
            f"{refs}\t{summary.source_proposal_ref or '-'}"
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

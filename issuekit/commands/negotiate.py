"""Implementation of the negotiate command."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tomllib

from issuekit.commands._common import print_json
from issuekit.agentrun import AgentRunner
from issuekit.commands._common import run_command
from issuekit.config import IssuekitConfig, load_config
from issuekit.config.refs import RefError, list_effective_refs
from issuekit.core import parse_issue_id_arg
from issuekit.gitutil import git_status_short
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
    entry_origin,
    finalize_refusal_reason,
    origin_issue_ref_from_thread,
    finalize_negotiation,
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
    negotiate_parser.add_argument("--to", help="Target project name.")
    negotiate_parser.add_argument(
        "--finalize",
        metavar="THREAD_ID",
        help="Create cross-linked implementation issues for an agreed thread.",
    )
    negotiate_parser.add_argument(
        "--frontend-agent",
        help="Configured agent representing the frontend side.",
    )
    negotiate_parser.add_argument(
        "--backend-agent",
        help="Configured agent representing the backend side.",
    )
    negotiate_parser.add_argument(
        "--backend-ref",
        help="Effective ref whose checkout the backend agent inspects.",
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
        choices=("negotiating", "agreed", "blocked"),
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
        if args.finalize:
            _require_finalize_args(args)
            author_agent = resolve_implementer(args.author_agent, config)
            if author_agent is None:
                raise WorkflowError(
                    "No implementer is configured. Pass --author-agent, set "
                    "default_implementer, or configure exactly one enabled assignee."
                )
            if not args.mock:
                _warn_target_validation(config, args.to)
            store = get_negotiation_store(config, use_mock=bool(args.mock))
            creator: IssueCreator = MockIssueCreator() if args.mock else ApiIssueCreator(config)
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
        backend_cwd = _resolve_backend_cwd(args.backend_ref, args.to, cwd)
        issue_id = parse_issue_id_arg(args.from_issue)
        max_rounds = int(args.max_rounds)
        if max_rounds < 1:
            raise ValueError("--max-rounds must be at least 1.")
        if not args.mock:
            _warn_target_validation(config, args.to)

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
            reasoning_effort=args.reasoning_effort,
            config=config,
            cwd=cwd,
            backend_cwd=backend_cwd,
            store=store,
            runner=AgentRunner(),
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
        store = get_negotiation_store(config, use_mock=bool(args.mock))
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


def _resolve_backend_cwd(
    backend_ref: str | None,
    to_project: str,
    cwd: Path,
) -> Path:
    refs = list_effective_refs(cwd)
    if backend_ref is not None:
        entry = refs.get(backend_ref)
        if entry is None:
            known_refs = ", ".join(refs) or "(none)"
            raise WorkflowError(
                f"Unknown backend ref {backend_ref!r}. Known refs: {known_refs}."
            )
        return _validated_backend_cwd(backend_ref, entry.path, to_project, required=True)

    for ref_name, entry in refs.items():
        if _backend_project(entry.path) == to_project:
            try:
                return _validated_backend_cwd(ref_name, entry.path, to_project, required=False)
            except WorkflowError as exc:
                print(
                    f"Ignoring automatically resolved backend ref {ref_name!r}: {exc}",
                    file=sys.stderr,
                )
                return cwd
    return cwd


def _validated_backend_cwd(
    backend_ref: str,
    backend_cwd: Path,
    to_project: str,
    *,
    required: bool,
) -> Path:
    backend_project = _backend_project(backend_cwd, required=required)
    if backend_project != to_project:
        raise WorkflowError(
            f"Backend ref {backend_ref!r} points to project {backend_project!r}, "
            f"not requested project {to_project!r}."
        )

    if git_status_short(backend_cwd):
        raise WorkflowError(
            f"Backend ref {backend_ref!r} points to a dirty checkout: {backend_cwd}."
        )
    return backend_cwd


def _backend_project(backend_cwd: Path, *, required: bool = False) -> str | None:
    try:
        config_data = _backend_config_data(backend_cwd)
    except (OSError, TypeError, ValueError) as exc:
        if not required:
            return None
        raise WorkflowError(
            f"Could not read issuekit configuration for backend checkout {backend_cwd}: {exc}"
        ) from exc

    if config_data is None:
        if not required:
            return None
        raise WorkflowError(
            f"Backend ref checkout {backend_cwd} has no readable issuekit configuration."
        )

    project = config_data.get("project")
    if isinstance(project, str) and project.strip():
        return project.strip()
    if required:
        raise WorkflowError(
            f"Backend ref checkout {backend_cwd} does not declare a project in its "
            "issuekit configuration."
        )
    return None


def _backend_config_data(backend_cwd: Path) -> dict[str, object] | None:
    # load_config validates all settings and reads machine-local state; this only
    # needs the counterpart's declared project and must not inherit either.
    pyproject_path = backend_cwd / "pyproject.toml"
    if pyproject_path.exists():
        with (backend_cwd / "pyproject.toml").open("rb") as config_file:
            data = tomllib.load(config_file)
        project_config = data.get("tool", {}).get("issuekit")
        if project_config is not None:
            return dict(project_config)

    issuekit_path = backend_cwd / "issuekit.toml"
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
    refusal = finalize_refusal_reason(inspection.status, list(inspection.entries))
    if refusal:
        print(f"finalize_refusal={refusal}")
    for entry in inspection.entries:
        print(
            f"- id={entry.id or '-'} side={entry.side} verdict={entry.verdict.value} "
            f"origin={entry.origin} contract={entry.contract or '-'}"
        )

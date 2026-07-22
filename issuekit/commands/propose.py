"""Commands for cross-repository proposals."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from issuekit.commands._common import print_json
from issuekit.guards.author import STOP_SENTINEL, create_author_guard, guard_dict, stop_message
from issuekit.commands._common import load_config_for_project_mutation
from issuekit.config import load_config
from issuekit.core import VALID_ISSUE_PRIORITIES
from issuekit.proposals import ProposalError
from issuekit.proposals.api import (
    ProposalAppendError,
    adopt_proposal_with_append,
    api_client,
    build_proposal,
    get_outgoing_proposal,
    list_outgoing_proposals,
    proposal_id_arg,
    send_proposal,
)
from issuekit.config.refs import (
    RefError,
    add_ref,
    add_workspace_ref,
    list_effective_refs,
)
from issuekit.issues.session import resolved_or_new_session_token
from issuekit.workflow import WorkflowError


def register(subparsers: argparse._SubParsersAction) -> None:
    add_ref_parser = subparsers.add_parser(
        "add-ref",
        help="Register a machine-local related repository ref.",
    )
    add_ref_parser.add_argument("name", help="Short ref name.")
    add_ref_parser.add_argument("--path", required=True, help="Absolute or relative repository path.")
    add_ref_parser.add_argument(
        "--scope",
        choices=("local", "workspace"),
        default="local",
        help="Write to issuekit.local.toml or issuekit.workspace.toml.",
    )
    add_ref_parser.add_argument(
        "--path-to-workspace",
        help="Explicit workspace registry file for --scope workspace.",
    )
    add_ref_parser.set_defaults(func=run_add_ref)

    list_refs_parser = subparsers.add_parser(
        "list-refs",
        help="List effective related repository refs.",
    )
    list_refs_parser.set_defaults(func=run_list_refs)

    propose_parser = subparsers.add_parser(
        "propose",
        help="Send a cross-repository proposal to a related repository.",
    )
    propose_parser.add_argument("--to", help="Target related repository ref.")
    propose_parser.add_argument("--title", help="Proposal title.")
    propose_parser.add_argument("--body", help="Inline proposal body.")
    propose_parser.add_argument("--body-file", help="File containing proposal body.")
    propose_parser.add_argument("--from-issue", help="Local issue id to propose from.")
    propose_parser.add_argument("--reply", help="Local adopted issue id to reply from.")
    propose_parser.add_argument("--agent", help="Optional author agent for the local STOP guard.")
    propose_parser.add_argument(
        "--project",
        help=(
            "Explicit origin API project when running outside a local issuekit "
            "project root."
        ),
    )
    propose_parser.add_argument(
        "--blocking",
        action="store_true",
        help="Mark the proposal as a hard dependency for the origin project.",
    )
    propose_parser.add_argument(
        "--depends-on",
        action="append",
        dest="depends_on",
        help="Attach an upstream dependency reference such as project#proposal:123.",
    )
    propose_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    propose_parser.set_defaults(func=run_propose)

    incoming_parser = subparsers.add_parser(
        "incoming",
        help="List incoming cross-repository proposals.",
    )
    incoming_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    incoming_parser.set_defaults(func=run_incoming)

    outgoing_parser = subparsers.add_parser(
        "outgoing",
        help="List proposals this project sent to a target project's inbox.",
    )
    outgoing_parser.add_argument(
        "--to",
        required=True,
        help="Target project whose inbox holds the outgoing proposals.",
    )
    outgoing_parser.add_argument("--id", type=int, help="Look up a single proposal id.")
    outgoing_parser.add_argument(
        "--status",
        help="Filter by proposal status (pending, adopted, or discarded).",
    )
    outgoing_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    outgoing_parser.set_defaults(func=run_outgoing)

    adopt_parser = subparsers.add_parser(
        "adopt",
        help="Adopt an incoming proposal as a local active issue.",
    )
    adopt_parser.add_argument("proposal", help="Proposal id.")
    adopt_parser.add_argument(
        "--priority",
        choices=("high", "medium", "low"),
        default="medium",
        help="Priority for the adopted issue.",
    )
    adopt_parser.add_argument(
        "--append-file",
        help="File containing text to append to the adopted issue body.",
    )
    adopt_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    adopt_parser.set_defaults(func=run_adopt)

    discard_parser = subparsers.add_parser(
        "discard",
        help="Discard an incoming proposal.",
    )
    discard_parser.add_argument("proposal", help="Proposal id.")
    discard_parser.add_argument("--json", action="store_true", help="Print JSON output.")
    discard_parser.set_defaults(func=run_discard)


def run_add_ref(args) -> int:
    try:
        if args.scope == "workspace":
            refs = add_workspace_ref(
                args.name,
                args.path,
                Path.cwd(),
                workspace_path=args.path_to_workspace,
            )
        else:
            refs = add_ref(args.name, args.path, Path.cwd())
    except RefError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Added {args.scope} ref {args.name}: {refs[args.name]}")
    return 0


def run_list_refs(_args) -> int:
    cwd = Path.cwd().resolve()
    try:
        refs = list_effective_refs(cwd)
    except RefError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for name, entry in refs.items():
        source = "self" if entry.path.resolve() == cwd else entry.source
        print(f"{name}\t{source}\t{entry.path.as_posix()}")
    return 0


def run_propose(args) -> int:
    try:
        config = load_config_for_project_mutation(
            Path.cwd(),
            command="propose",
            project=args.project,
        )
        session = resolved_or_new_session_token("cli")
        proposal = build_proposal(
            Path.cwd(),
            to=args.to,
            title=args.title,
            body=args.body,
            body_file=args.body_file,
            from_issue=args.from_issue,
            reply=args.reply,
            blocking=args.blocking,
            depends_on=args.depends_on,
            config=config,
        )
        created = send_proposal(config, proposal)
    except (LookupError, ProposalError, RefError, ValueError, WorkflowError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    warning = created.get("warning")
    mismatched = bool(created.get("payload_mismatch"))
    if args.json:
        output = dict(created)
        output.pop("warning", None)
    if mismatched:
        if args.json:
            print_json(output)
        print(warning, file=sys.stderr)
        return 1
    for preflight_warning in created.get("warnings", []):
        print(preflight_warning, file=sys.stderr)
    try:
        guard = create_author_guard(
            Path.cwd(),
            config=config,
            kind="proposal",
            item_id=created.get("id"),
            ref=f"{proposal.to}#{created.get('id')}",
            target_project=proposal.to,
            author_agent=args.agent,
            author_session=session,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        output["authorGuard"] = guard_dict(guard)
        output["stop"] = STOP_SENTINEL
        print_json(output)
    if not args.json:
        print(f"Sent proposal #{created.get('id')}: {created.get('title', proposal.title)}")
        dependency_ref = created.get("dependency_ref")
        if dependency_ref:
            print(f"Dependency ref: {dependency_ref}")
        print(stop_message(guard))
    return 0


def run_incoming(args) -> int:
    config = load_config(Path.cwd())
    try:
        with api_client(config) as client:
            incoming = client.list_proposals(status="pending")
    except (ProposalError, WorkflowError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print_json(incoming)
        return 0
    if not incoming:
        print("No incoming proposals.")
        return 0
    for proposal in incoming:
        prefix = "reply" if proposal.get("reply_to") else "proposal"
        blocking = "blocking" if proposal.get("blocking") else "-"
        print(
            f"{proposal['id']}\t{prefix}\t{blocking}\t"
            f"{proposal['origin']}\t{proposal['title']}"
        )
    return 0


def run_outgoing(args) -> int:
    config = load_config(Path.cwd())
    try:
        if args.id is not None:
            outgoing = [get_outgoing_proposal(config, to=args.to, proposal_id=args.id)]
        else:
            outgoing = list_outgoing_proposals(config, to=args.to, status=args.status)
    except (ProposalError, WorkflowError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print_json(outgoing)
        return 0
    if not outgoing:
        print(f"No outgoing proposals in {args.to}.")
        return 0
    for proposal in outgoing:
        adopted = proposal.get("adopted_issue_number")
        adopted_ref = f"{args.to}#{adopted}" if adopted else "-"
        blocking = "blocking" if proposal.get("blocking") else "-"
        print(
            f"{proposal['id']}\t{proposal.get('status')}\t"
            f"{adopted_ref}\t{blocking}\t{proposal.get('title')}"
        )
    return 0


def run_adopt(args) -> int:
    if args.priority not in VALID_ISSUE_PRIORITIES:
        print(f"Invalid priority: {args.priority}", file=sys.stderr)
        return 1
    config = load_config(Path.cwd())
    try:
        outcome = adopt_proposal_with_append(
            config,
            args.proposal,
            priority=args.priority,
            append_file=args.append_file,
        )
    except ProposalAppendError as exc:
        if args.json:
            output = dict(exc.outcome)
            output["append_error"] = exc.append_error
            print_json(output)
        print(str(exc), file=sys.stderr)
        return 1
    except (ProposalError, WorkflowError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print_json(outcome)
        return 0
    if outcome["created_api_issue"]:
        print(
            f"Adopted proposal #{args.proposal} as API issue "
            f"#{outcome['issue_id']} ({outcome['issue_ref']})."
        )
        print(f"Next: {outcome['next_command']}")
    else:
        print(f"Adopted proposal #{args.proposal}, but no API issue id was returned.")
        print(outcome["instruction"])
    return 0


def run_discard(args) -> int:
    config = load_config(Path.cwd())
    try:
        with api_client(config) as client:
            discarded = client.discard_proposal(proposal_id_arg(args.proposal))
    except (ProposalError, WorkflowError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print_json(discarded)
        return 0
    print(f"Discarded proposal #{discarded.get('id')}.")
    return 0

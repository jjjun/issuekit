"""Commands for cross-repository proposals."""

from __future__ import annotations

import json
from pathlib import Path
import sys

from issuekit.config import load_config
from issuekit.core import VALID_ISSUE_PRIORITIES
from issuekit.proposals import ProposalError
from issuekit.proposals_api import (
    ProposalAppendError,
    adopt_proposal_with_append,
    api_client,
    build_proposal,
    get_outgoing_proposal,
    list_outgoing_proposals,
    proposal_id_arg,
    send_proposal,
)
from issuekit.refs import (
    RefError,
    add_ref,
    add_workspace_ref,
    list_effective_refs,
)
from issuekit.workflow import WorkflowError


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
    config = load_config(Path.cwd())
    try:
        proposal = build_proposal(
            Path.cwd(),
            to=args.to,
            title=args.title,
            body=args.body,
            body_file=args.body_file,
            from_issue=args.from_issue,
            reply=args.reply,
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
        print(json.dumps(output, indent=2))
    if mismatched:
        print(warning, file=sys.stderr)
        return 1
    if not args.json:
        print(f"Sent proposal #{created.get('id')}: {created.get('title', proposal.title)}")
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
        print(json.dumps(incoming, indent=2))
        return 0
    if not incoming:
        print("No incoming proposals.")
        return 0
    for proposal in incoming:
        prefix = "reply" if proposal.get("reply_to") else "proposal"
        print(f"{proposal['id']}\t{prefix}\t{proposal['origin']}\t{proposal['title']}")
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
        print(json.dumps(outgoing, indent=2))
        return 0
    if not outgoing:
        print(f"No outgoing proposals in {args.to}.")
        return 0
    for proposal in outgoing:
        adopted = proposal.get("adopted_issue_number")
        adopted_ref = f"{args.to}#{adopted}" if adopted else "-"
        print(f"{proposal['id']}\t{proposal.get('status')}\t{adopted_ref}\t{proposal.get('title')}")
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
            print(json.dumps(output, indent=2))
        print(str(exc), file=sys.stderr)
        return 1
    except (ProposalError, WorkflowError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(outcome, indent=2))
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
        print(json.dumps(discarded, indent=2))
        return 0
    print(f"Discarded proposal #{discarded.get('id')}.")
    return 0

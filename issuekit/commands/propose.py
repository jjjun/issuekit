"""Commands for cross-repository proposals."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import subprocess
import sys

from issuekit.commands.generate_indexes import write_index_files
from issuekit.config import load_config
from issuekit.core import Issue, VALID_ISSUE_PRIORITIES, read_all_issues
from issuekit.proposals import (
    Proposal,
    ProposalError,
    adopt_proposal,
    discard_proposal,
    list_incoming,
    origin_destination,
    proposal_dict,
    write_proposal,
)
from issuekit.refs import RefError, add_ref, default_repo_ref, list_refs, resolve_ref


def run_add_ref(args) -> int:
    try:
        refs = add_ref(args.name, args.path, Path.cwd())
    except RefError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Added ref {args.name}: {refs[args.name]}")
    return 0


def run_list_refs(_args) -> int:
    for name, path in list_refs(Path.cwd()).items():
        print(f"{name}\t{path}")
    return 0


def run_propose(args) -> int:
    try:
        proposal = build_proposal(
            Path.cwd(),
            to=args.to,
            title=args.title,
            body=None,
            body_file=args.body_file,
            from_issue=args.from_issue,
            reply=args.reply,
        )
        target = resolve_ref(proposal.to, Path.cwd())
        path = write_proposal(target.issues_dir, proposal)
    except (LookupError, ProposalError, RefError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Wrote proposal: {path}")
    return 0


def run_incoming(args) -> int:
    config = load_config(Path.cwd())
    incoming = [proposal_dict(proposal) for proposal in list_incoming(config.issues_path(Path.cwd()))]
    if args.json:
        print(json.dumps(incoming, indent=2))
        return 0
    if not incoming:
        print("No incoming proposals.")
        return 0
    for proposal in incoming:
        prefix = "reply" if proposal["reply_to"] else "proposal"
        print(
            f"{proposal['file']}\t{prefix}\t{proposal['origin']}\t"
            f"{proposal['title']}"
        )
    return 0


def run_adopt(args) -> int:
    if args.priority not in VALID_ISSUE_PRIORITIES:
        print(f"Invalid priority: {args.priority}", file=sys.stderr)
        return 1
    config = load_config(Path.cwd())
    issues_dir = config.issues_path(Path.cwd())
    try:
        path = adopt_proposal(issues_dir, args.proposal_file, priority=args.priority)
    except ProposalError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    write_index_files(issues_dir, config.recent_count)
    print(f"Adopted proposal as: {path}")
    return 0


def run_discard(args) -> int:
    config = load_config(Path.cwd())
    try:
        path = discard_proposal(config.issues_path(Path.cwd()), args.proposal_file)
    except ProposalError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(f"Discarded proposal: {path}")
    return 0


def build_proposal(
    cwd: Path,
    *,
    to: str | None,
    title: str | None,
    body: str | None,
    body_file: str | None,
    from_issue: str | None,
    reply: str | None,
) -> Proposal:
    config = load_config(cwd)
    issues_dir = config.issues_path(cwd)
    _, _, all_issues = read_all_issues(issues_dir)

    source_issue: Issue | None = None
    reply_to = ""
    if reply is not None:
        source_issue = _find_issue(all_issues, reply)
        reply_to = source_issue.frontmatter.data.get("origin", "").strip()
        if not reply_to:
            raise ProposalError(f"Issue #{source_issue.id} has no origin field.")
        to = to or origin_destination(reply_to)
    elif from_issue is not None:
        source_issue = _find_issue(all_issues, from_issue)

    if not to:
        raise ProposalError("--to is required unless --reply is used.")

    title = title or (source_issue.title if source_issue is not None else "")
    if not title:
        raise ProposalError("--title is required unless --from-issue or --reply provides one.")

    proposal_body = _proposal_body(body, body_file, source_issue)
    origin_id = str(source_issue.id) if source_issue is not None and source_issue.id is not None else "0"
    origin = f"{default_repo_ref(cwd)}#{origin_id}@{_git_commit(cwd)}"
    return Proposal(
        origin=origin,
        to=to,
        reply_to=reply_to,
        created=date.today().isoformat(),
        title=title,
        body=proposal_body,
    )


def _find_issue(issues: list[Issue], raw_id: str) -> Issue:
    try:
        issue_id = int(raw_id)
    except ValueError as exc:
        raise ValueError(f"Invalid issue id: {raw_id}") from exc
    issue = next((candidate for candidate in issues if candidate.id == issue_id), None)
    if issue is None:
        raise LookupError(f"Issue #{issue_id} was not found.")
    return issue


def _proposal_body(body: str | None, body_file: str | None, source_issue: Issue | None) -> str:
    if body is not None:
        return body.strip()
    if body_file:
        return Path(body_file).read_text(encoding="utf-8-sig").strip()
    if source_issue is not None:
        return source_issue.frontmatter.body.strip()
    return "## Context\n\n## Suggested Change\n\n## Rationale"


def _git_commit(cwd: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return result.stdout.strip() or "unknown"

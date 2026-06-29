"""Commands for cross-repository proposals."""

from __future__ import annotations

from datetime import date
import json
from pathlib import Path
import subprocess
import sys

from issuekit.client import IssuekitClient
from issuekit.commands.generate_indexes import write_index_files
from issuekit.config import load_config
from issuekit.core import (
    Issue,
    VALID_ISSUE_PRIORITIES,
    find_issue_by_id,
    issue_dict,
    parse_issue_id_arg,
    read_all_issues,
)
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
from issuekit.refs import (
    RefError,
    add_ref,
    add_workspace_ref,
    current_repo_ref,
    list_effective_refs,
    resolve_ref,
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
        config = load_config(Path.cwd())
        if _use_api(config):
            created = _api_client(config, project=proposal.to).create_proposal(
                origin=proposal.origin,
                title=proposal.title,
                body=proposal.body,
                reply_to=proposal.reply_to or None,
            )
        else:
            target = resolve_ref(proposal.to, Path.cwd())
            path = write_proposal(target.issues_dir, proposal)
    except (LookupError, ProposalError, RefError, ValueError, WorkflowError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if args.json:
        if _use_api(load_config(Path.cwd())):
            print(json.dumps(created, indent=2))
        else:
            print(json.dumps({**proposal_dict(proposal), "path": path.as_posix()}, indent=2))
        return 0
    if _use_api(load_config(Path.cwd())):
        print(f"Sent proposal #{created.get('id')}: {created.get('title', proposal.title)}")
    else:
        print(f"Wrote proposal: {path}")
    return 0


def run_incoming(args) -> int:
    config = load_config(Path.cwd())
    try:
        if _use_api(config):
            incoming = _api_client(config).list_proposals(status="pending")
        else:
            incoming = [proposal_dict(proposal) for proposal in list_incoming(config.issues_path(Path.cwd()))]
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
        if _use_api(config):
            prefix = "reply" if proposal.get("reply_to") else "proposal"
            print(f"{proposal['id']}\t{prefix}\t{proposal['origin']}\t{proposal['title']}")
        else:
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
        if _use_api(config):
            issue = _api_client(config).adopt_proposal(
                _proposal_id_arg(args.proposal),
                priority=args.priority,
            )
        else:
            path = adopt_proposal(issues_dir, args.proposal, priority=args.priority)
    except (ProposalError, WorkflowError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if _use_api(config):
        if args.json:
            print(json.dumps(issue, indent=2))
            return 0
        print(f"Adopted proposal #{args.proposal} as issue #{issue.get('id')}.")
        return 0

    write_index_files(issues_dir, config.recent_count)
    if args.json:
        _, _, issues = read_all_issues(issues_dir)
        issue = next(candidate for candidate in issues if candidate.file_path == path)
        print(json.dumps(issue_dict(issue, include_body=True), indent=2))
        return 0
    print(f"Adopted proposal as: {path}")
    return 0


def run_discard(args) -> int:
    config = load_config(Path.cwd())
    try:
        if _use_api(config):
            discarded = _api_client(config).discard_proposal(_proposal_id_arg(args.proposal))
        else:
            path = discard_proposal(config.issues_path(Path.cwd()), args.proposal)
    except (ProposalError, WorkflowError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    if getattr(args, "json", False):
        print(json.dumps(discarded if _use_api(config) else {"path": path.as_posix()}, indent=2))
        return 0
    if _use_api(config):
        print(f"Discarded proposal #{discarded.get('id')}.")
    else:
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

    source_issue: Issue | None = None
    reply_to = ""
    if reply is not None:
        all_issues = _read_all_issues(cwd, config)
        source_issue = _find_issue(all_issues, reply)
        reply_to = source_issue.frontmatter.data.get("origin", "").strip()
        if not reply_to:
            raise ProposalError(f"Issue #{source_issue.id} has no origin field.")
        to = to or origin_destination(reply_to)
    elif from_issue is not None:
        all_issues = _read_all_issues(cwd, config)
        source_issue = _find_issue(all_issues, from_issue)

    if not to:
        raise ProposalError("--to is required unless --reply is used.")

    title = title or (source_issue.title if source_issue is not None else "")
    if not title:
        raise ProposalError("--title is required unless --from-issue or --reply provides one.")

    proposal_body = _proposal_body(body, body_file, source_issue)
    origin_id = str(source_issue.id) if source_issue is not None and source_issue.id is not None else "0"
    origin_project = config.project if _use_api(config) else current_repo_ref(cwd)
    origin = f"{origin_project}#{origin_id}@{_git_commit(cwd)}"
    return Proposal(
        origin=origin,
        to=to,
        reply_to=reply_to,
        created=date.today().isoformat(),
        title=title,
        body=proposal_body,
    )


def _read_all_issues(cwd: Path, config) -> list[Issue]:
    issues_dir = config.issues_path(cwd)
    if _use_api(config):
        from issuekit.store import get_store

        _, _, all_issues = get_store(config, issues_dir).read_all_issues()
        return all_issues
    _, _, all_issues = read_all_issues(issues_dir)
    return all_issues


def _use_api(config) -> bool:
    return bool(config.api_url) and not config.use_filesystem_store


def _api_client(config, *, project: str | None = None) -> IssuekitClient:
    return IssuekitClient(
        config.api_url,
        project=project or config.project,
        timeout=config.api_timeout,
    )


def _proposal_id_arg(value: str) -> int:
    try:
        proposal_id = int(value)
    except ValueError as exc:
        raise ProposalError(f"Proposal id must be an integer in API mode: {value}") from exc
    if proposal_id <= 0:
        raise ProposalError(f"Proposal id must be positive: {value}")
    return proposal_id


def _find_issue(issues: list[Issue], raw_id: str) -> Issue:
    issue_id = parse_issue_id_arg(raw_id)
    issue = find_issue_by_id(issues, issue_id)
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
        # stdin must be redirected: when this runs inside the issuekit-mcp stdio
        # server, an inherited stdin pipe makes `git` block until the timeout.
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
            stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return result.stdout.strip() or "unknown"

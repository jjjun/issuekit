"""Cross-repository proposal files."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import re
import shutil
from pathlib import Path

from issuekit.core import (
    get_next_issue_id,
    has_non_ascii,
    parse_issue_frontmatter,
    read_all_issues,
    write_issue_atomic,
)


class ProposalError(RuntimeError):
    """Raised when proposal IO cannot be completed."""


@dataclass(frozen=True)
class Proposal:
    origin: str
    to: str
    reply_to: str
    created: str
    title: str
    body: str
    file_path: Path | None = None

    @property
    def file_name(self) -> str:
        return "" if self.file_path is None else self.file_path.name


def write_proposal(target_issues_dir: Path | str, proposal: Proposal) -> Path:
    _validate_proposal(proposal)
    incoming_dir = Path(target_issues_dir) / "incoming"
    incoming_dir.mkdir(parents=True, exist_ok=True)

    for existing in list_incoming(target_issues_dir):
        if existing.origin == proposal.origin:
            return existing.file_path or incoming_dir / _proposal_file_name(proposal)

    path = incoming_dir / _proposal_file_name(proposal)
    if path.exists():
        raise ProposalError(f"Proposal file already exists: {path}")
    write_issue_atomic(path, format_proposal(proposal))
    return path


def list_incoming(issues_dir: Path | str) -> list[Proposal]:
    incoming_dir = Path(issues_dir) / "incoming"
    if not incoming_dir.exists():
        return []
    proposals: list[Proposal] = []
    for path in sorted(incoming_dir.glob("*.md")):
        proposals.append(read_proposal(path))
    return proposals


def read_proposal(path: Path | str) -> Proposal:
    proposal_path = Path(path)
    content = proposal_path.read_text(encoding="utf-8-sig")
    frontmatter = parse_issue_frontmatter(content)
    if not frontmatter.has_frontmatter:
        raise ProposalError(f"Proposal is missing frontmatter: {proposal_path}")
    data = frontmatter.data
    proposal = Proposal(
        origin=data.get("origin", "").strip(),
        to=data.get("to", "").strip(),
        reply_to=data.get("reply_to", "").strip(),
        created=data.get("created", "").strip(),
        title=data.get("title", "").strip(),
        body=frontmatter.body.strip("\n"),
        file_path=proposal_path,
    )
    _validate_proposal(proposal)
    return proposal


def format_proposal(proposal: Proposal) -> str:
    _validate_proposal(proposal)
    return (
        "---\n"
        f"origin: {proposal.origin}\n"
        f"to: {proposal.to}\n"
        f"reply_to: {proposal.reply_to}\n"
        f"created: {proposal.created}\n"
        f"title: {proposal.title}\n"
        "---\n\n"
        f"# Proposal: {proposal.title}\n\n"
        f"{proposal.body.strip()}\n"
    )


def adopt_proposal(
    issues_dir: Path | str,
    proposal_file: Path | str,
    *,
    priority: str = "medium",
) -> Path:
    issues_path = Path(issues_dir)
    proposal_path = _resolve_proposal_path(issues_path, proposal_file)
    proposal = read_proposal(proposal_path)
    _, _, all_issues = read_all_issues(issues_path)
    issue_id = get_next_issue_id(all_issues)
    slug = slugify(proposal.title)
    target_path = issues_path / "active" / f"{issue_id:03d}_{slug}.md"
    if target_path.exists():
        raise ProposalError(f"Active issue already exists: {target_path}")

    write_issue_atomic(
        target_path,
        _adopted_issue_content(
            issue_id=issue_id,
            title=proposal.title,
            priority=priority,
            origin=proposal.origin,
            body=proposal.body,
        ),
    )
    _move_consumed(proposal_path, issues_path / "incoming" / "adopted")
    return target_path


def discard_proposal(issues_dir: Path | str, proposal_file: Path | str) -> Path:
    issues_path = Path(issues_dir)
    proposal_path = _resolve_proposal_path(issues_path, proposal_file)
    read_proposal(proposal_path)
    return _move_consumed(proposal_path, issues_path / "incoming" / "discarded")


def proposal_dict(proposal: Proposal) -> dict[str, str]:
    return {
        "file": proposal.file_name,
        "origin": proposal.origin,
        "to": proposal.to,
        "reply_to": proposal.reply_to,
        "created": proposal.created,
        "title": proposal.title,
    }


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower())
    slug = re.sub(r"_+", "_", slug).strip("_")
    return slug[:64] or "proposal"


def origin_destination(origin: str) -> str:
    match = re.match(r"^([^#]+)#.+@.+$", origin)
    if not match:
        raise ProposalError(f"Invalid proposal origin: {origin}")
    return match.group(1)


def _proposal_file_name(proposal: Proposal) -> str:
    source, source_id = _origin_parts(proposal.origin)
    return f"{slugify(source)}__{slugify(source_id)}__{slugify(proposal.title)}.md"


def _origin_parts(origin: str) -> tuple[str, str]:
    match = re.match(r"^([^#]+)#([^@]+)@(.+)$", origin)
    if not match:
        raise ProposalError(f"Invalid proposal origin: {origin}")
    return match.group(1), match.group(2)


def _validate_proposal(proposal: Proposal) -> None:
    text = "\n".join(
        [
            proposal.origin,
            proposal.to,
            proposal.reply_to,
            proposal.created,
            proposal.title,
            proposal.body,
        ]
    )
    if has_non_ascii(text):
        raise ProposalError("Proposal text must be ASCII-only.")
    required = {
        "origin": proposal.origin,
        "to": proposal.to,
        "created": proposal.created,
        "title": proposal.title,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise ProposalError(f"Proposal is missing required field: {', '.join(missing)}")
    for field in (proposal.origin, proposal.to, proposal.reply_to, proposal.created, proposal.title):
        if "\n" in field or "\r" in field:
            raise ProposalError("Proposal frontmatter fields must be single-line.")
    _origin_parts(proposal.origin)
    if proposal.reply_to:
        _origin_parts(proposal.reply_to)


def _resolve_proposal_path(issues_dir: Path, proposal_file: Path | str) -> Path:
    path = Path(proposal_file)
    if path.is_absolute():
        return path
    incoming = issues_dir / "incoming"
    if (incoming / path).exists():
        return incoming / path
    return Path.cwd() / path


def _adopted_issue_content(
    *,
    issue_id: int,
    title: str,
    priority: str,
    origin: str,
    body: str,
) -> str:
    created = date.today().isoformat()
    return (
        "---\n"
        f"id: {issue_id}\n"
        "status: active\n"
        f"priority: {priority}\n"
        f"created: {created}\n"
        "completed:\n"
        f"origin: {origin}\n"
        f"title: {title}\n"
        "---\n\n"
        f"# Issue #{issue_id}: {title}\n\n"
        "## Problem\n\n"
        "Adopted from an incoming cross-project proposal.\n\n"
        "## Proposed Solution\n\n"
        f"{body.strip()}\n\n"
        "## Impact\n\n"
        "- Adopted proposal content should be reviewed locally.\n\n"
        "## Implementation Plan\n\n"
        "1. Triage the adopted proposal into local implementation steps.\n\n"
        "## Test Plan\n\n"
        "- Run the relevant local verification commands.\n\n"
        "## Related Resources\n\n"
        f"- Origin: `{origin}`\n"
    )


def _move_consumed(path: Path, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / path.name
    if target.exists():
        target = directory / f"{path.stem}_1{path.suffix}"
    shutil.move(str(path), str(target))
    return target

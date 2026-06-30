"""Cross-repository proposal helpers."""

from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path

from issuekit.core import has_non_ascii


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


def origin_destination(origin: str) -> str:
    match = re.match(r"^([^#]+)#.+@.+$", origin)
    if not match:
        raise ProposalError(f"Invalid proposal origin: {origin}")
    return match.group(1)


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

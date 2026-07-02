"""Cross-repository proposal helpers."""

from __future__ import annotations

from dataclasses import dataclass
import re


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
    blocking: bool = False


def origin_destination(origin: str) -> str:
    match = re.match(r"^([^#]+)#.+@.+$", origin)
    if not match:
        raise ProposalError(f"Invalid proposal origin: {origin}")
    return match.group(1)

"""Cross-repository proposal data model and validation helpers."""

from __future__ import annotations

import re
from dataclasses import dataclass


class ProposalError(RuntimeError):
    """Raised when proposal IO cannot be completed."""


@dataclass(frozen=True)
class Proposal:
    origin: str
    to: str
    target_worker: str
    reply_to: str
    created: str
    title: str
    body: str
    blocking: bool = False
    depends_on: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def origin_destination(origin: str) -> str:
    match = re.match(r"^([^#]+)#.+@.+$", origin)
    if not match:
        raise ProposalError(f"Invalid proposal origin: {origin}")
    return match.group(1)

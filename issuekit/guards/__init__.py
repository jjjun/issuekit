"""Public guards for issuekit handoff and checkout policies."""

from .author import (
    ENFORCE_AUTHOR_HANDOFF_ENV,
    REQUIRED_NEXT_ACTION,
    STOP_SENTINEL,
    AuthorGuard,
    AuthorOrchestrationContext,
    author_handoff_enforced,
    clear_author_guard,
    create_author_guard,
    enforce_no_author_guard,
    guard_dict,
    read_author_guard,
    stop_message,
)
from .branch import enforce_work_branch
from .claim_sync import FETCH_TIMEOUT_SEC, enforce_claim_sync
from .separation import AUTHOR_GUARD_HELP, SEPARATION_GUARD_REFERENCE, separation_guard_note

__all__ = [
    "AUTHOR_GUARD_HELP",
    "ENFORCE_AUTHOR_HANDOFF_ENV",
    "FETCH_TIMEOUT_SEC",
    "REQUIRED_NEXT_ACTION",
    "SEPARATION_GUARD_REFERENCE",
    "STOP_SENTINEL",
    "AuthorGuard",
    "AuthorOrchestrationContext",
    "author_handoff_enforced",
    "clear_author_guard",
    "create_author_guard",
    "enforce_claim_sync",
    "enforce_no_author_guard",
    "enforce_work_branch",
    "guard_dict",
    "read_author_guard",
    "separation_guard_note",
    "stop_message",
]

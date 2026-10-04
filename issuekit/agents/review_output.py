"""Parse and validate structured output from reviewer agents."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TextIO

from issuekit.agentrun import AgentResult
from issuekit.encoding import ASCII_ONLY_HINT, has_non_ascii
from issuekit.prompts import REVIEW_PROMPT, ReviewParseError, canonical_contract_token
from issuekit.prompts.fields import require_str, sanitize_ascii_field

REVIEW_OUTPUT_KEYS = REVIEW_PROMPT.required_keys
_REVIEW_VERDICTS = {"approve", "request-changes"}


@dataclass(frozen=True)
class ReviewVerdict:
    verdict: str
    verification: str
    notes: str


class ReviewRunParseError(ReviewParseError):
    """A review parse error that retains the completed agent run result."""

    def __init__(self, error: ReviewParseError, result: AgentResult) -> None:
        super().__init__(str(error))
        self.result = result


def _empty_verdict() -> ReviewVerdict:
    return ReviewVerdict(verdict="", verification="", notes="")


def parse_review_output(stdout: str, *, err: TextIO | None = None) -> ReviewVerdict:
    """Parse the newest well-formed review block from agent stdout."""

    return _review_verdict_from_json(
        REVIEW_PROMPT.parse_json(stdout),
        err=err or sys.stderr,
    )


def _review_verdict_from_json(
    raw: dict[str, object],
    *,
    err: TextIO,
) -> ReviewVerdict:
    missing = [key for key in REVIEW_OUTPUT_KEYS if key not in raw]
    if missing:
        raise ReviewParseError(f"Review block is missing required key: {', '.join(missing)}.")

    raw_verdict = require_str(
        raw["verdict"],
        error=ReviewParseError,
        message="Review key verdict must be a string.",
    )
    verdict = canonical_contract_token(raw_verdict, _REVIEW_VERDICTS)
    if verdict is None:
        raise ReviewParseError(f"Invalid review verdict: {raw_verdict}")
    verification = sanitize_ascii_field(
        "verification",
        _required_review_text(raw["verification"], "verification").strip(),
        err=err,
        actor="reviewer",
        recording="verdict",
    )
    notes = sanitize_ascii_field(
        "notes",
        _required_review_text(raw["notes"], "notes").strip(),
        err=err,
        actor="reviewer",
        recording="verdict",
    )
    if verdict == "approve" and not verification:
        raise ReviewParseError("Approved review verdict requires verification.")
    if verdict == "request-changes" and not notes:
        raise ReviewParseError("Request-changes review verdict requires notes.")
    _validate_ascii_review_field("verdict", verdict)
    return ReviewVerdict(verdict=verdict, verification=verification, notes=notes)


def _required_review_text(value: object, key: str) -> str:
    if isinstance(value, list):
        if not all(isinstance(item, str) for item in value):
            raise ReviewParseError(
                f"Review key {key} must be a string or a list of strings."
            )
        return "\n".join(value)
    if isinstance(value, dict):
        for entry_key, entry_value in value.items():
            if not isinstance(entry_value, str):
                raise ReviewParseError(
                    f"Review key {key} entry {entry_key} must be a string."
                )
        return "\n".join(
            f"{entry_key}: {entry_value}" for entry_key, entry_value in value.items()
        )
    return require_str(
        value,
        error=ReviewParseError,
        message=f"Review key {key} must be a string.",
    )


def _validate_ascii_review_field(key: str, value: str) -> None:
    if has_non_ascii(value):
        raise ReviewParseError(
            f"Review field {key} must be ASCII-only. {ASCII_ONLY_HINT}"
        )

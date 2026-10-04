"""Prompt rendering and structured output parsing for negotiation rounds."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, TextIO

from issuekit.encoding import has_non_ascii
from issuekit.negotiation import NegotiationEntry, Verdict
from issuekit.prompts import (
    NEGOTIATION_ROUND_PROMPT,
    NegotiationParseError,
    fence_untrusted,
)
from issuekit.prompts.fields import require_str, sanitize_ascii_field

NEGOTIATION_OUTPUT_KEYS = NEGOTIATION_ROUND_PROMPT.required_keys


@dataclass(frozen=True)
class ParsedRound:
    side: str
    verdict: Verdict
    contract: str | None
    notes: str


def render_round_prompt(
    *,
    side: str,
    seed: str,
    thread: Sequence[NegotiationEntry],
    resolved_contract: str | None = None,
) -> str:
    """Render a bounded negotiation prompt for one side of a design round."""

    thread_summary = _render_thread_summary(thread)
    resolved = resolved_contract if resolved_contract is not None else "(none yet)"
    verdict_values = ", ".join(verdict.value for verdict in Verdict)

    return NEGOTIATION_ROUND_PROMPT.render(
        side=side,
        seed=fence_untrusted("seed", seed),
        resolved_contract=fence_untrusted("resolved_contract", resolved),
        thread_summary=fence_untrusted("thread_summary", thread_summary),
        output_keys=", ".join(NEGOTIATION_OUTPUT_KEYS),
        verdict_values=verdict_values,
    )


def parse_round_output(stdout: str, *, err: TextIO | None = None) -> ParsedRound:
    """Parse the newest well-formed negotiation block from agent stdout."""

    return _parsed_round_from_json(
        NEGOTIATION_ROUND_PROMPT.parse_json(stdout),
        err=err or sys.stderr,
    )


def _render_thread_summary(thread: Sequence[NegotiationEntry]) -> str:
    if not thread:
        return "- (no prior entries)"
    return "\n".join(_format_thread_entry(index, entry) for index, entry in enumerate(thread, 1))


def _format_thread_entry(index: int, entry: NegotiationEntry) -> str:
    contract = entry.contract if entry.contract is not None else "null"
    return f"- {index}. {entry.title} | verdict={entry.verdict.value} | contract={contract}"


def _parsed_round_from_json(raw: dict[str, Any], *, err: TextIO) -> ParsedRound:
    missing = [key for key in NEGOTIATION_OUTPUT_KEYS if key not in raw]
    if missing:
        raise NegotiationParseError(
            f"Negotiation block is missing required key: {', '.join(missing)}."
        )

    side = require_str(
        raw["side"],
        error=NegotiationParseError,
        message="Negotiation key side must be a string.",
    )
    verdict_raw = require_str(
        raw["verdict"],
        error=NegotiationParseError,
        message="Negotiation key verdict must be a string.",
    )
    contract = _optional_string(raw["contract"], "contract")
    notes = require_str(
        raw["notes"],
        error=NegotiationParseError,
        message="Negotiation key notes must be a string.",
    )

    try:
        verdict = Verdict(verdict_raw)
    except ValueError as exc:
        raise NegotiationParseError(f"Invalid negotiation verdict: {verdict_raw}") from exc

    if has_non_ascii(side) or has_non_ascii(verdict.value):
        raise NegotiationParseError("Negotiation fields must be ASCII-only.")
    if contract is not None:
        contract = sanitize_ascii_field(
            "contract",
            contract,
            err=err,
            actor="negotiation",
            recording="round",
        )
    notes = sanitize_ascii_field(
        "notes",
        notes,
        err=err,
        actor="negotiation",
        recording="round",
    )

    return ParsedRound(side=side, verdict=verdict, contract=contract, notes=notes)


def _optional_string(value: object, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise NegotiationParseError(f"Negotiation key {key} must be a string or null.")
    return require_str(
        value,
        error=NegotiationParseError,
        message=f"Negotiation key {key} must be a string or null.",
    )


def provider_issue_body(
    *,
    thread_id: str,
    origin_issue_ref: str | None,
    consumer_issue_ref: str,
    contract: str,
) -> str:
    lines = [
        "## Implementation Task",
        "",
        "Implement the contract this project owns and now exposes.",
        "",
        "## Links",
        "",
        f"- Negotiation thread: {thread_id}",
        f"- Consumer issue: {consumer_issue_ref}",
    ]
    if origin_issue_ref:
        lines.append(f"- Originating issue: {origin_issue_ref}")
    fence = _markdown_fence_for(contract)
    lines.extend(
        [
            "",
            "## Agreed Contract",
            "",
            fence,
            contract,
            fence,
            "",
            "## Acceptance Criteria",
            "",
            "- The contract behavior described in the agreed contract is implemented.",
            "- The contract is covered by focused tests.",
            "- Any documented request/response shape remains compatible with the consumer issue.",
        ]
    )
    return "\n".join(lines)


def consumer_issue_body(
    *,
    thread_id: str,
    origin_issue_ref: str | None,
    provider_issue_ref: str,
    contract: str,
) -> str:
    lines = [
        "## Implementation Task",
        "",
        "Integrate this project against the agreed contract.",
        "",
        "## Links",
        "",
        f"- Negotiation thread: {thread_id}",
        f"- Provider issue: {provider_issue_ref}",
    ]
    if origin_issue_ref:
        lines.append(f"- Originating issue: {origin_issue_ref}")
    fence = _markdown_fence_for(contract)
    lines.extend(
        [
            "",
            "## Agreed Contract",
            "",
            fence,
            contract,
            fence,
            "",
            "## Acceptance Criteria",
            "",
            "- The integration consumes the agreed contract.",
            "- Behavior from the originating issue is covered.",
            "- The implementation handles provider errors or unavailable data clearly.",
        ]
    )
    return "\n".join(lines)


def _markdown_fence_for(content: str) -> str:
    longest_run = 0
    current_run = 0
    for char in content:
        if char == "`":
            current_run += 1
            longest_run = max(longest_run, current_run)
        else:
            current_run = 0
    return "`" * max(3, longest_run + 1)

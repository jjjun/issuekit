"""Prompt rendering and structured output parsing for negotiation rounds."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from issuekit.encoding import has_non_ascii
from issuekit.negotiation import NegotiationEntry, Verdict
from issuekit.prompts import (
    NEGOTIATION_ROUND_PROMPT,
    NegotiationParseError,
)


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
        seed=seed,
        resolved_contract=resolved,
        thread_summary=thread_summary,
        output_keys=", ".join(NEGOTIATION_OUTPUT_KEYS),
        verdict_values=verdict_values,
    )


def parse_round_output(stdout: str) -> ParsedRound:
    """Parse the newest well-formed negotiation block from agent stdout."""

    return _parsed_round_from_json(NEGOTIATION_ROUND_PROMPT.parse_json(stdout))


def _render_thread_summary(thread: Sequence[NegotiationEntry]) -> str:
    if not thread:
        return "- (no prior entries)"
    return "\n".join(_format_thread_entry(index, entry) for index, entry in enumerate(thread, 1))


def _format_thread_entry(index: int, entry: NegotiationEntry) -> str:
    contract = entry.contract if entry.contract is not None else "null"
    return f"- {index}. {entry.title} | verdict={entry.verdict.value} | contract={contract}"


def _parsed_round_from_json(raw: dict[str, Any]) -> ParsedRound:
    missing = [key for key in NEGOTIATION_OUTPUT_KEYS if key not in raw]
    if missing:
        raise NegotiationParseError(
            f"Negotiation block is missing required key: {', '.join(missing)}."
        )

    side = _required_string(raw["side"], "side")
    verdict_raw = _required_string(raw["verdict"], "verdict")
    contract = _optional_string(raw["contract"], "contract")
    notes = _required_string(raw["notes"], "notes")

    try:
        verdict = Verdict(verdict_raw)
    except ValueError as exc:
        raise NegotiationParseError(f"Invalid negotiation verdict: {verdict_raw}") from exc

    ascii_text = "\n".join(value for value in (side, verdict.value, contract, notes) if value)
    if has_non_ascii(ascii_text):
        raise NegotiationParseError("Negotiation fields must be ASCII-only.")

    return ParsedRound(side=side, verdict=verdict, contract=contract, notes=notes)


def _required_string(value: object, key: str) -> str:
    if not isinstance(value, str):
        raise NegotiationParseError(f"Negotiation key {key} must be a string.")
    return value


def _optional_string(value: object, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise NegotiationParseError(f"Negotiation key {key} must be a string or null.")
    return value


def backend_issue_body(
    *,
    thread_id: str,
    origin_issue_ref: str | None,
    frontend_issue_ref: str,
    contract: str,
) -> str:
    lines = [
        "## Implementation Task",
        "",
        "Implement the backend/API side of the agreed cross-repository contract.",
        "",
        "## Links",
        "",
        f"- Negotiation thread: {thread_id}",
        f"- Frontend/origin issue: {frontend_issue_ref}",
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
            "- The API behavior described in the agreed contract is implemented.",
            "- The contract is covered by focused tests.",
            "- Any documented request/response shape remains compatible with the frontend issue.",
        ]
    )
    return "\n".join(lines)


def frontend_issue_body(
    *,
    thread_id: str,
    origin_issue_ref: str | None,
    backend_issue_ref: str,
    contract: str,
) -> str:
    lines = [
        "## Implementation Task",
        "",
        "Integrate the frontend/origin project with the agreed backend contract.",
        "",
        "## Links",
        "",
        f"- Negotiation thread: {thread_id}",
        f"- Backend/API issue: {backend_issue_ref}",
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
            "- User-facing behavior from the originating issue is covered.",
            "- The implementation handles backend errors or unavailable data clearly.",
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

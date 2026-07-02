"""Prompt rendering and structured output parsing for negotiation rounds."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import json
import re
from typing import Any

from issuekit.core import has_non_ascii
from issuekit.negotiation import NegotiationEntry, Verdict


NEGOTIATION_BLOCK_LANGUAGE = "negotiation"
NEGOTIATION_OUTPUT_KEYS = ("side", "verdict", "contract", "notes")
_NEGOTIATION_BLOCK_PATTERN = re.compile(
    r"```negotiation[ \t]*\r?\n(?P<body>.*?)\r?\n```",
    re.DOTALL,
)


class NegotiationParseError(RuntimeError):
    """Raised when an agent negotiation response cannot be parsed."""


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

    return "\n".join(
        [
            "You are participating in an issuekit cross-repo design negotiation.",
            f"Perspective: you represent the {side} side.",
            "Round job: propose, counter, agree, or block the current contract.",
            "",
            "Seed:",
            str(seed),
            "",
            "Resolved contract so far:",
            resolved,
            "",
            "Compact thread so far:",
            thread_summary,
            "",
            "Read budget:",
            (
                "Read only the files needed to judge this specific contract; do not "
                "implement code; do not modify the tracker."
            ),
            "Do not read or include whole-repo dumps.",
            "",
            "Output contract:",
            "Emit exactly one fenced block and no other response text.",
            "Everything outside the block is ignored by the parser.",
            f"The JSON keys must be: {', '.join(NEGOTIATION_OUTPUT_KEYS)}.",
            f"The verdict must be one of: {verdict_values}.",
            "The contract value must be a string or null.",
            "The notes value must be short free text.",
            "```negotiation",
            '{',
            f'  "side": "{side}",',
            '  "verdict": "propose",',
            '  "contract": "Small proposed contract text, or null",',
            '  "notes": "Short rationale."',
            '}',
            "```",
            "",
        ]
    )


def render_resumed_round_prompt(
    *,
    side: str,
    latest_counterpart: NegotiationEntry,
    resolved_contract: str | None = None,
) -> str:
    """Render a compact prompt for an already-resumed side session."""

    resolved = resolved_contract if resolved_contract is not None else "(none yet)"
    verdict_values = ", ".join(verdict.value for verdict in Verdict)

    return "\n".join(
        [
            "You are continuing an issuekit cross-repo design negotiation.",
            f"Perspective: you represent the {side} side.",
            "Round job: propose, counter, agree, or block the current contract.",
            "",
            "Resolved contract so far:",
            resolved,
            "",
            "Latest counterpart entry:",
            _format_thread_entry(1, latest_counterpart),
            "",
            "Read budget:",
            (
                "Read only the files needed to judge this specific contract; do not "
                "implement code; do not modify the tracker."
            ),
            "Do not read or include whole-repo dumps.",
            "",
            "Output contract:",
            "Emit exactly one fenced block and no other response text.",
            "Everything outside the block is ignored by the parser.",
            f"The JSON keys must be: {', '.join(NEGOTIATION_OUTPUT_KEYS)}.",
            f"The verdict must be one of: {verdict_values}.",
            "The contract value must be a string or null.",
            "The notes value must be short free text.",
            "```negotiation",
            '{',
            f'  "side": "{side}",',
            '  "verdict": "propose",',
            '  "contract": "Small proposed contract text, or null",',
            '  "notes": "Short rationale."',
            '}',
            "```",
            "",
        ]
    )


def parse_round_output(stdout: str) -> ParsedRound:
    """Parse the newest well-formed negotiation block from agent stdout."""

    blocks = [match.group("body") for match in _NEGOTIATION_BLOCK_PATTERN.finditer(stdout)]
    if not blocks:
        raise NegotiationParseError("No ```negotiation``` block found in agent output.")

    last_json_error: NegotiationParseError | None = None
    for block in reversed(blocks):
        try:
            raw = json.loads(block.strip())
        except json.JSONDecodeError as exc:
            last_json_error = NegotiationParseError(
                f"Negotiation block was not valid JSON: {exc.msg}."
            )
            continue
        if not isinstance(raw, dict):
            raise NegotiationParseError("Negotiation block JSON must be an object.")
        return _parsed_round_from_json(raw)

    if last_json_error is not None:
        raise last_json_error
    raise NegotiationParseError("No well-formed ```negotiation``` block found.")


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


def _backend_issue_body(
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


def _frontend_issue_body(
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

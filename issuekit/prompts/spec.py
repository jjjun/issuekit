"""Agent prompt templates and paired structured-output contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import resources
import json
import re
from string import Template
from types import MappingProxyType
from typing import TypeVar

from issuekit.encoding import has_non_ascii


ParseErrorT = TypeVar("ParseErrorT", bound=RuntimeError)


class TriageAuthorParseError(RuntimeError):
    """Raised when a triage-author agent response cannot be parsed."""


class ProposalCheckParseError(RuntimeError):
    """Raised when a proposal-check agent response cannot be parsed."""


class ReviewParseError(RuntimeError):
    """Raised when a reviewer response cannot be parsed."""


class RouterParseError(RuntimeError):
    """Raised when a router agent response cannot be parsed or validated."""


class NegotiationParseError(RuntimeError):
    """Raised when an agent negotiation response cannot be parsed."""


SHARED_PARTIALS = MappingProxyType(
    {
        "single_fenced_block_instruction": (
            "Emit exactly one fenced block and no other response text."
        ),
        "ignored_text_instruction": "Everything outside the block is ignored by the parser.",
        "ascii_only_rule": (
            "All text must be ASCII-only (English; no em dashes or curly quotes)."
        ),
        "negotiation_read_budget": (
            "Read only the files needed to judge this specific contract; do not "
            "implement code; do not modify the tracker."
        ),
    }
)


@dataclass(frozen=True)
class PromptSpec:
    """Template plus output contract for one structured agent prompt."""

    template_name: str
    block_language: str
    parse_error_type: type[RuntimeError]
    block_label: str
    required_keys: tuple[str, ...] = ()
    branch_key: str | None = None
    required_keys_by_branch: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict
    )
    pointer_template_name: str | None = None
    reject_non_ascii_block: bool = False
    non_ascii_block_message: str | None = None

    def render(self, **context: object) -> str:
        return render_template(self.template_name, **context)

    def render_pointer(self, **context: object) -> str:
        if self.pointer_template_name is None:
            raise ValueError(f"{self.template_name} has no pointer template.")
        return render_template(self.pointer_template_name, **context).removesuffix("\n")

    def parse_json(self, stdout: str) -> dict[str, object]:
        raw = parse_newest_json_block(
            stdout,
            language=self.block_language,
            block_label=self.block_label,
            error_factory=self.parse_error_type,
            reject_non_ascii=self.reject_non_ascii_block,
            non_ascii_message=self.non_ascii_block_message,
        )
        self.validate_required_keys(raw)
        return raw

    def validate_required_keys(self, raw: Mapping[str, object]) -> None:
        required_keys = self.required_keys
        if self.branch_key is not None:
            branch = raw.get(self.branch_key)
            if isinstance(branch, str) and branch in self.required_keys_by_branch:
                required_keys = self.required_keys_by_branch[branch]
        missing = [key for key in required_keys if key not in raw]
        if missing:
            raise self.parse_error_type(
                f"{self.block_label} is missing required key: {', '.join(missing)}."
            )


TRIAGE_PROMPT = PromptSpec(
    template_name="triage.md",
    pointer_template_name="triage_pointer.md",
    block_language="triage",
    block_label="Triage block",
    parse_error_type=TriageAuthorParseError,
    branch_key="decision",
    required_keys_by_branch={
        "adopt": ("decision", "spec_markdown"),
        "reply": ("decision", "question"),
        "discard": ("decision", "reason"),
    },
)

PROPOSAL_CHECK_PROMPT = PromptSpec(
    template_name="proposal_check.md",
    pointer_template_name="proposal_check_pointer.md",
    block_language="proposal-check",
    block_label="Proposal-check block",
    parse_error_type=ProposalCheckParseError,
    required_keys=("verdict", "comment"),
)

REVIEW_PROMPT = PromptSpec(
    template_name="review.md",
    pointer_template_name="review_pointer.md",
    block_language="review",
    block_label="Review block",
    parse_error_type=ReviewParseError,
    required_keys=("verdict", "verification", "notes"),
)

ROUTER_PROMPT = PromptSpec(
    template_name="router.md",
    pointer_template_name="router_pointer.md",
    block_language="route",
    block_label="Route block",
    parse_error_type=RouterParseError,
    branch_key="decision",
    required_keys_by_branch={
        "route": ("decision", "targets"),
        "clarify": ("decision", "question"),
        "reject": ("decision", "reason"),
    },
    reject_non_ascii_block=True,
    non_ascii_block_message="Route block must be ASCII-only.",
)

NEGOTIATION_ROUND_PROMPT = PromptSpec(
    template_name="negotiation_round.md",
    pointer_template_name="negotiation_round_pointer.md",
    block_language="negotiation",
    block_label="Negotiation block",
    parse_error_type=NegotiationParseError,
    required_keys=("side", "verdict", "contract", "notes"),
)

NEGOTIATION_RESUMED_ROUND_PROMPT = PromptSpec(
    template_name="negotiation_round_resumed.md",
    block_language="negotiation",
    block_label="Negotiation block",
    parse_error_type=NegotiationParseError,
    required_keys=("side", "verdict", "contract", "notes"),
)

PROMPT_SPECS = MappingProxyType(
    {
        "triage": TRIAGE_PROMPT,
        "proposal_check": PROPOSAL_CHECK_PROMPT,
        "review": REVIEW_PROMPT,
        "router": ROUTER_PROMPT,
        "negotiation_round": NEGOTIATION_ROUND_PROMPT,
        "negotiation_round_resumed": NEGOTIATION_RESUMED_ROUND_PROMPT,
    }
)

TEMPLATE_NAMES = (
    "triage.md",
    "triage_pointer.md",
    "proposal_check.md",
    "proposal_check_pointer.md",
    "review.md",
    "review_pointer.md",
    "router.md",
    "router_pointer.md",
    "review_feedback.md",
    "negotiation_round.md",
    "negotiation_round_resumed.md",
    "negotiation_round_pointer.md",
)


def render_template(template_name: str, **context: object) -> str:
    template = Template(load_template(template_name))
    values = {**SHARED_PARTIALS, **{key: str(value) for key, value in context.items()}}
    return template.substitute(values)


def load_template(template_name: str) -> str:
    template = resources.files(__package__).joinpath("templates", template_name)
    return template.read_text(encoding="utf-8")


def render_review_feedback_prompt(notes: str) -> str:
    return render_template("review_feedback.md", notes=notes).removesuffix("\n")


def render_negotiation_round_pointer(prompt_path: object) -> str:
    return NEGOTIATION_ROUND_PROMPT.render_pointer(prompt_path=prompt_path)


def parse_newest_json_block(
    stdout: str,
    *,
    language: str,
    block_label: str,
    error_factory: type[ParseErrorT],
    reject_non_ascii: bool = False,
    non_ascii_message: str | None = None,
) -> dict[str, object]:
    """Parse the newest well-formed fenced JSON block for an agent contract."""

    pattern = re.compile(
        rf"```{re.escape(language)}[ \t]*\r?\n(?P<body>.*?)\r?\n```",
        re.DOTALL,
    )
    blocks = [match.group("body") for match in pattern.finditer(stdout)]
    if not blocks:
        raise error_factory(f"No ```{language}``` block found in agent output.")

    last_json_error: ParseErrorT | None = None
    for block in reversed(blocks):
        if reject_non_ascii and has_non_ascii(block):
            raise error_factory(
                non_ascii_message or f"{block_label} must be ASCII-only."
            )
        try:
            raw = json.loads(block.strip())
        except json.JSONDecodeError as exc:
            last_json_error = error_factory(
                f"{block_label} was not valid JSON: {exc.msg}."
            )
            continue
        if not isinstance(raw, dict):
            raise error_factory(f"{block_label} JSON must be an object.")
        return raw

    if last_json_error is not None:
        raise last_json_error
    raise error_factory(f"No well-formed ```{language}``` block found.")

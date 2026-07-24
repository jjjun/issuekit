from __future__ import annotations

import pytest

from issuekit.encoding import ASCII_ONLY_HINT
from issuekit.prompts import (
    PROMPT_SPECS,
    ROUTER_PROMPT,
    TEMPLATE_NAMES,
    RouterParseError,
    load_template,
    render_review_feedback_prompt,
)


SPEC_CONTEXTS = {
    "triage": {
        "proposal_id": 1,
        "origin": "source#1@abc",
        "reply_to": "(none)",
        "title": "Add endpoint",
        "blocking": False,
        "depends_on": "(none)",
        "proposal_body": "Please add the endpoint.",
    },
    "proposal_check": {
        "check_id": 2,
        "target_project": "api",
        "proposal_id": 1,
        "title": "Add endpoint",
        "origin": "source#1@abc",
        "blocking": True,
        "depends_on": "source#4",
        "proposal_body": "Please add the endpoint.",
    },
    "review": {
        "issue_ref": "api#3",
        "review_target": "the implementation diff",
        "issue_body": "# Issue\n\nBuild it.",
        "implementation_context": "git diff HEAD --:\n+value = 2",
        "readability_hints": "Automated readability hints: none.",
        "output_keys": "verdict, verification, notes",
        "ascii_only_hint": ASCII_ONLY_HINT,
    },
    "router": {
        "max_targets": 2,
        "final_instruction": "If the request cannot be routed safely, ask one concrete clarification question.",
        "request_text": "Add export.",
        "qa_text": "(none)",
        "profile_text": "## Project: api\nSummary: API\nTags: python\n\nOwns APIs.",
    },
    "negotiation_round": {
        "side": "frontend",
        "seed": "Origin issue body.",
        "resolved_contract": "(none yet)",
        "thread_summary": "- (no prior entries)",
        "output_keys": "side, verdict, contract, notes",
        "verdict_values": "propose, counter, agree, blocked",
    },
    "negotiation_round_resumed": {
        "side": "backend",
        "resolved_contract": "GET /items",
        "latest_counterpart": "- 1. Proposal | verdict=propose | contract=GET /items",
        "output_keys": "side, verdict, contract, notes",
        "verdict_values": "propose, counter, agree, blocked",
    },
}


def test_every_prompt_template_loads() -> None:
    for template_name in TEMPLATE_NAMES:
        assert load_template(template_name).strip()


def test_every_prompt_spec_renders_ascii_representative_context() -> None:
    for name, spec in PROMPT_SPECS.items():
        rendered = spec.render(**SPEC_CONTEXTS[name])

        assert rendered.endswith("\n")
        assert spec.block_language in rendered
        assert rendered.isascii()


def test_prompt_render_fails_on_missing_context_key() -> None:
    with pytest.raises(KeyError):
        PROMPT_SPECS["triage"].render(proposal_id=1)


def test_triage_prompt_requires_verified_claims_and_design_decisions() -> None:
    rendered = PROMPT_SPECS["triage"].render(**SPEC_CONTEXTS["triage"])
    rendered_words = " ".join(rendered.split())

    assert "verified or corrected factual claims about this codebase" in rendered
    assert "resolved design decisions for any open choices" in rendered
    assert "implementation order when several pending proposals interact" in rendered_words
    assert "spec_markdown" in rendered
    rendered.encode("ascii")


def test_pointer_templates_render_ascii() -> None:
    for spec in PROMPT_SPECS.values():
        if spec.pointer_template_name is None:
            continue
        rendered = spec.render_pointer(prompt_path=".agent-runs/prompt.md")

        assert "prompt" in rendered
        assert rendered.isascii()


def test_review_feedback_template_renders_ascii() -> None:
    rendered = render_review_feedback_prompt("Add focused tests.")

    assert "Address ONLY these notes" in rendered
    assert rendered.endswith("Add focused tests.")
    assert rendered.isascii()


def test_prompt_spec_validates_branch_required_keys() -> None:
    with pytest.raises(RouterParseError, match="missing required key: targets"):
        ROUTER_PROMPT.parse_json('```route\n{"decision":"route"}\n```')

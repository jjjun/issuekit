import pytest

from issuekit.negotiation import NegotiationEntry, Verdict
from issuekit.negotiation.prompts import (
    NegotiationParseError,
    ParsedRound,
    parse_round_output,
    render_round_prompt,
)


def _entry(
    *,
    side: str = "frontend",
    verdict: Verdict = Verdict.propose,
    title: str = "Initial contract",
    body: str = "Repo file contents should not be copied into prompts.",
    contract: str | None = "GET /items",
) -> NegotiationEntry:
    return NegotiationEntry(
        thread_id="1",
        side=side,
        verdict=verdict,
        contract=contract,
        title=title,
        body=body,
        origin=f"{side}#1",
        created="2026-01-01",
    )


def test_render_round_prompt_includes_side_thread_budget_and_contract() -> None:
    prompt = render_round_prompt(
        side="frontend",
        seed="Negotiate the item list contract.",
        thread=[
            _entry(title="Initial contract", verdict=Verdict.propose, contract="GET /items"),
            _entry(
                side="backend",
                title="Pagination counter",
                verdict=Verdict.counter,
                contract="GET /items?page=1",
            ),
        ],
        resolved_contract="GET /items?page=1",
    )

    assert "Perspective: you represent the frontend side." in prompt
    assert "Round job: propose, counter, agree, or block" in prompt
    assert "Initial contract | verdict=propose | contract=GET /items" in prompt
    assert "Pagination counter | verdict=counter | contract=GET /items?page=1" in prompt
    assert (
        "Read only the files needed to judge this specific contract; do not implement code; "
        "do not modify the tracker."
    ) in prompt
    assert "```negotiation" in prompt
    assert '"side"' in prompt
    assert '"verdict"' in prompt
    assert '"contract"' in prompt
    assert '"notes"' in prompt
    assert "All text must be ASCII-only" in prompt


def test_render_round_prompt_excludes_entry_bodies_and_repo_dumps() -> None:
    prompt = render_round_prompt(
        side="backend",
        seed="Review a small API contract.",
        thread=[_entry(body="SECRET_REPO_FILE_CONTENTS")],
    )

    assert "SECRET_REPO_FILE_CONTENTS" not in prompt
    assert "Do not read or include whole-repo dumps." in prompt


def test_parse_round_output_parses_clean_block() -> None:
    parsed = parse_round_output(
        """```negotiation
{"side":"frontend","verdict":"propose","contract":"GET /items","notes":"Looks viable."}
```"""
    )

    assert parsed == ParsedRound(
        side="frontend",
        verdict=Verdict.propose,
        contract="GET /items",
        notes="Looks viable.",
    )


def test_parse_round_output_picks_last_well_formed_block() -> None:
    parsed = parse_round_output(
        """Ignoring preamble.
```negotiation
{"side":"frontend","verdict":"propose","contract":"GET /items","notes":"First."}
```
More stdout.
```negotiation
{"side":"backend","verdict":"agree","contract":"GET /items","notes":"Accepted."}
```"""
    )

    assert parsed.side == "backend"
    assert parsed.verdict is Verdict.agree
    assert parsed.notes == "Accepted."


def test_parse_round_output_raises_on_missing_block() -> None:
    with pytest.raises(NegotiationParseError, match="No ```negotiation``` block"):
        parse_round_output('{"side":"frontend","verdict":"propose"}')


def test_parse_round_output_raises_on_invalid_verdict() -> None:
    with pytest.raises(NegotiationParseError, match="Invalid negotiation verdict"):
        parse_round_output(
            """```negotiation
{"side":"frontend","verdict":"maybe","contract":"GET /items","notes":"Unsure."}
```"""
        )


def test_parse_round_output_round_trips_null_contract() -> None:
    parsed = parse_round_output(
        """```negotiation
{"side":"backend","verdict":"blocked","contract":null,"notes":"Need auth decision."}
```"""
    )

    assert parsed.contract is None
    assert parsed.verdict is Verdict.blocked


def test_parse_round_output_sanitizes_non_ascii_fields(capsys) -> None:
    parsed = parse_round_output(
        """```negotiation
{"side":"frontend","verdict":"agree","contract":"GET /caf\u00e9","notes":"\u627f\u8a8d"}
```"""
    )

    assert parsed.contract == "GET /cafe\n\n[contract sanitized from non-ASCII]"
    assert parsed.notes == "[notes sanitized from non-ASCII]"
    captured = capsys.readouterr()
    assert "negotiation agent field contract contained non-ASCII text" in captured.err
    assert "negotiation agent field notes contained non-ASCII text" in captured.err


def test_parse_round_output_rejects_non_ascii_side() -> None:
    with pytest.raises(NegotiationParseError, match="ASCII-only"):
        parse_round_output(
            """```negotiation
{"side":"caf\u00e9","verdict":"agree","contract":"GET /items","notes":"Accepted."}
```"""
        )

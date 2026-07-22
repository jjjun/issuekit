"""Formatting helpers for encoding detection reports."""

from __future__ import annotations

from typing import TextIO


def code_point_text(text: str) -> str:
    return " ".join(code_point(character) for character in text)


def code_point_context(text: str, index: int, character: str) -> str:
    before = text[max(0, index - 5) : index]
    after = text[index + 1 : index + 6]
    return " ".join(
        [
            "...",
            *(code_point(value) for value in before),
            f"[{code_point(character)}]",
            *(code_point(value) for value in after),
            "...",
        ]
    )


def code_point(character: str) -> str:
    return f"U+{ord(character):04X}"


def print_mojibake_hit(
    hit: dict[str, int | str],
    stream: TextIO,
    *,
    prefix: str,
    context_prefix: str,
) -> None:
    print(
        f"{prefix}{hit['file']}:{hit['line']}:{hit['column']}: {hit['code_point']}",
        file=stream,
    )
    print(f"{context_prefix}{hit['context']}", file=stream)

from pathlib import Path

import pytest

from issuekit.errors import WorkflowError
from issuekit.inputs import read_text_file, require_ascii, resolve_text


def test_read_text_file_strips_utf8_bom_and_surrounding_whitespace(tmp_path: Path) -> None:
    text_file = tmp_path / "input.txt"
    text_file.write_text("\ufeff  body  \n", encoding="utf-8", newline="\n")

    assert read_text_file(str(text_file)) == "body"


def test_resolve_text_prefers_inline_and_strips_it_by_default(tmp_path: Path) -> None:
    text_file = tmp_path / "input.txt"
    text_file.write_text("file body", encoding="utf-8", newline="\n")

    assert resolve_text("  inline body  ", str(text_file)) == "inline body"


def test_resolve_text_can_prefer_file_or_preserve_inline_whitespace(tmp_path: Path) -> None:
    text_file = tmp_path / "input.txt"
    text_file.write_text("file body", encoding="utf-8", newline="\n")

    assert resolve_text(
        "  inline body  ",
        str(text_file),
        strip_inline=False,
        prefer="file",
    ) == "file body"
    assert resolve_text("  inline body  ", None, strip_inline=False) == "  inline body  "


def test_resolve_text_returns_none_when_no_input_is_given() -> None:
    assert resolve_text(None, None) is None


def test_require_ascii_uses_requested_exception_type() -> None:
    with pytest.raises(WorkflowError, match="--notes must be ASCII-only"):
        require_ascii("\u3042", message="--notes must be ASCII-only.", error=WorkflowError)

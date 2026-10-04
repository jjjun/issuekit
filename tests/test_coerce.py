from issuekit.coerce import first_text, strip_or_empty


def test_first_text_returns_first_nonempty_stripped_value() -> None:
    assert first_text(
        {"first": "  ", "second": None, "third": 42},
        "first",
        "second",
        "third",
    ) == "42"
    assert first_text({"first": "  "}, "first") == ""


def test_strip_or_empty_converts_and_strips_values() -> None:
    assert strip_or_empty(None) == ""
    assert strip_or_empty("  text  ") == "text"
    assert strip_or_empty(42) == "42"

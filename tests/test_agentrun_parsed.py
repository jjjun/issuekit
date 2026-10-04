from issuekit.agentrun.parsed import (
    encode_usage,
    int_counts,
    parsed_int,
    parsed_is_error,
    parsed_usage,
)


def test_int_counts_keeps_string_keys_and_non_boolean_integers() -> None:
    assert int_counts({"input": 10, "output": 2, "invalid": True, 1: 5}) == {
        "input": 10,
        "output": 2,
    }


def test_usage_encoding_and_parsing_preserve_parsed_field_format() -> None:
    assert encode_usage({"input_tokens": 30, "total_tokens": 42}) == {
        "usage_input_tokens": "30",
        "usage_total_tokens": "42",
    }
    assert parsed_usage(
        {"usage_input_tokens": "30", "usage_total_tokens": "bad", "other": "9"}
    ) == {"input_tokens": 30}


def test_parsed_error_and_integer_decoders() -> None:
    assert parsed_is_error({"is_error": "true"}) is True
    assert parsed_is_error({"is_error": "false"}) is False
    assert parsed_is_error({"is_error": "yes"}) is None
    assert parsed_int({"num_turns": "2"}, "num_turns") == 2
    assert parsed_int({"num_turns": "bad"}, "num_turns") is None
    assert parsed_int(None, "num_turns") is None

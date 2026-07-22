"""Public encoding and mojibake detection API."""

from issuekit.encoding.detect import (
    ASCII_ONLY_HINT,
    CP932_DOUBLE_ENCODING_LEAD_CHARACTER_PATTERN,
    ENCODING_ARTIFACT_PATTERN,
    HALFWIDTH_KATAKANA_PATTERN,
    NON_ASCII_PATTERN,
    confirmed_mojibake_hits,
    find_encoding_artifacts,
    has_encoding_artifacts,
    has_non_ascii,
    is_encoding_excluded_path,
)
from issuekit.encoding.report import (
    code_point,
    code_point_context,
    code_point_text,
    print_mojibake_hit,
)

__all__ = [
    "ASCII_ONLY_HINT",
    "CP932_DOUBLE_ENCODING_LEAD_CHARACTER_PATTERN",
    "ENCODING_ARTIFACT_PATTERN",
    "HALFWIDTH_KATAKANA_PATTERN",
    "NON_ASCII_PATTERN",
    "confirmed_mojibake_hits",
    "code_point",
    "code_point_context",
    "code_point_text",
    "find_encoding_artifacts",
    "has_encoding_artifacts",
    "has_non_ascii",
    "is_encoding_excluded_path",
    "print_mojibake_hit",
]

from issuekit import encoding


def test_mojibake_detection() -> None:
    assert encoding.has_mojibake("\u7e67")
    assert encoding.has_mojibake("\ufffd")
    assert not encoding.has_mojibake("plain ascii")


def test_encoding_artifact_detection() -> None:
    assert encoding.has_encoding_artifacts("\u0080")
    assert encoding.has_encoding_artifacts("\u8389")
    assert encoding.has_encoding_artifacts("\u8711")
    assert encoding.has_encoding_artifacts("\u8700")
    assert encoding.has_encoding_artifacts("\ue000")
    assert encoding.has_encoding_artifacts("\uff71")
    assert not encoding.has_encoding_artifacts("\uff71", include_halfwidth_katakana=False)
    assert not encoding.has_encoding_artifacts("plain ascii")


def test_encoding_artifact_reverted_generated_file_exclusions() -> None:
    """Keep issuekit#229's restoration of previously excluded mojibake characters."""
    assert encoding.has_encoding_artifacts("\u83f4")
    assert encoding.has_encoding_artifacts("\u873f")
    assert encoding.has_encoding_artifacts("\u9015")
    assert encoding.has_encoding_artifacts("\u95ad")

from issuekit.issues.session import resolved_or_new_session_token


def test_resolved_or_new_session_token_uses_configured_session() -> None:
    assert resolved_or_new_session_token("cli", {"ISSUEKIT_SESSION": "author-123"}) == "author-123"


def test_resolved_or_new_session_token_generates_prefixed_session() -> None:
    token = resolved_or_new_session_token("cli", {})

    assert token.startswith("cli-")

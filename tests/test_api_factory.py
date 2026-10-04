from issuekit.api.client import BURST_HTTP_LIMITS
from issuekit.api.factory import client_for
from issuekit.config import IssuekitConfig


def test_client_for_uses_configured_insecure_api_url_opt_out() -> None:
    with client_for(
        IssuekitConfig(
            api_url="http://mine.example",
            allow_insecure_api_url=True,
        )
    ) as client:
        assert client.allow_insecure_api_url is True


def test_client_for_uses_burst_limits_when_requested(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class RecordingClient:
        def __init__(self, *args, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("issuekit.api.factory.IssuekitClient", RecordingClient)

    client_for(IssuekitConfig(api_url="https://mine.example"), keepalive=True)

    assert captured["http_limits"] is BURST_HTTP_LIMITS


def test_client_for_preserves_explicit_limits_with_keepalive(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class RecordingClient:
        def __init__(self, *args, **kwargs) -> None:
            captured.update(kwargs)

    monkeypatch.setattr("issuekit.api.factory.IssuekitClient", RecordingClient)
    limits = BURST_HTTP_LIMITS

    client_for(
        IssuekitConfig(api_url="https://mine.example"),
        keepalive=True,
        http_limits=limits,
    )

    assert captured["http_limits"] is limits

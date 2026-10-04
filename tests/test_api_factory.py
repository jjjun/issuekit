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

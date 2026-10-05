import pytest

from majsoul_client_data.discovery import DEFAULT_CLIENT_URL, discover

HTML = """<script src="Build/jp-WebGL-release-4.0.0.loader.js"></script>
          <script>const config = {productVersion: "4.0.0"};</script>"""
SETTINGS_URL = "https://appstatic.mahjongsoul.com/v4/jp/clientbundlesettings/jp-release.json"


class FakeClient:
    def __init__(self, responses: dict[str, str | Exception]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get_text(self, url: str) -> str:
        self.calls.append(url)
        response = self.responses[url]
        if isinstance(response, Exception):
            raise response
        return response


def test_default_only_gets_html_and_reports_default_settings() -> None:
    client = FakeClient({DEFAULT_CLIENT_URL: HTML})
    report = discover(client)
    assert client.calls == [DEFAULT_CLIENT_URL]
    assert report["client"]["product_version"] == "4.0.0"
    assert report["client_settings"]["url"] == SETTINGS_URL
    assert report["client_settings"]["status"] == "not_requested"
    assert "source" not in report["client_settings"]
    assert report["loader_url"] == (
        "https://game.mahjongsoul.com/Build/jp-WebGL-release-4.0.0.loader.js"
    )
    assert report["requested_urls"] == client.calls
    assert "bundle_base_url" not in report


def test_changed_html_stops_after_first_request() -> None:
    client = FakeClient({DEFAULT_CLIENT_URL: "<html></html>"})
    with pytest.raises(ValueError, match="loader"):
        discover(client)
    assert client.calls == [DEFAULT_CLIENT_URL]

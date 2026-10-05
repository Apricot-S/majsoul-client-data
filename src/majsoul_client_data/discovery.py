from dataclasses import asdict
from typing import Any, Protocol
from urllib.parse import urljoin

from .parsing import parse_client_html

DEFAULT_CLIENT_URL = "https://game.mahjongsoul.com/"
DEFAULT_CLIENT_SETTINGS_URL = "https://appstatic.mahjongsoul.com/v4/jp/clientbundlesettings/jp-release.json"
TEXTURE_PROFILES = ("ASTC", "DXT")


class MetadataClient(Protocol):
    def get_text(self, url: str) -> str: ...


def discover(client: MetadataClient) -> dict[str, Any]:
    requested: list[str] = []

    def fetch(url: str) -> str:
        requested.append(url)
        return client.get_text(url)

    info = parse_client_html(fetch(DEFAULT_CLIENT_URL))
    report: dict[str, Any] = {
        "client_url": DEFAULT_CLIENT_URL,
        "client": asdict(info),
        "loader_url": urljoin(DEFAULT_CLIENT_URL, info.loader),
        "client_settings": {
            "url": DEFAULT_CLIENT_SETTINGS_URL,
            "status": "not_requested",
        },
        "requested_urls": requested,
    }
    return report

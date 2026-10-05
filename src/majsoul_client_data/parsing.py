import re
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class ClientInfo:
    issuer: str
    loader: str
    product_version: str | None


class MetadataError(ValueError):
    pass


def validate_url(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        msg = f"Expected an HTTPS URL without credentials: {url}"
        raise MetadataError(msg)
    if parts.fragment:
        msg = f"URL must not contain a fragment: {url}"
        raise MetadataError(msg)
    return url


def parse_client_html(html: str) -> ClientInfo:
    loader_match = re.search(
        r"""["'](Build/([^/"']+?)-WebGL-release-[^/"']+\.loader\.js)["']""",
        html,
    )
    if not loader_match:
        msg = (
            "JP Unity loader was not found; the client format may have changed"
        )
        raise MetadataError(msg)
    if loader_match[2] != "jp":
        msg = f"Expected a JP client, found issuer {loader_match[2]!r}"
        raise MetadataError(msg)
    version_match = re.search(
        r"""productVersion\s*:\s*["']([^"']+)["']""", html
    )
    return ClientInfo(
        "jp", loader_match[1], version_match[1] if version_match else None
    )


def _object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = f"{label} must be an object"
        raise MetadataError(msg)
    return value


def _nonempty_list(value: object, label: str) -> list[object]:
    if not isinstance(value, list) or not value:
        msg = f"{label} must be a nonempty list"
        raise MetadataError(msg)
    return value


def _url_priority(entry: dict[str, object]) -> tuple[int | float, int | float]:
    priority = entry.get("Priority", 0)
    weight = entry.get("weight", 0)
    if not isinstance(priority, (int, float)):
        msg = "Priority must be numeric"
        raise MetadataError(msg)
    if not isinstance(weight, (int, float)):
        msg = "weight must be numeric"
        raise MetadataError(msg)
    return priority, weight


def _selected_url(settings: dict[str, object]) -> str:
    entries = [
        _object(entry, "URL entry")
        for entry in _nonempty_list(settings.get("urls"), "urls")
    ]
    chosen = max(entries, key=_url_priority)
    url = chosen.get("url")
    if not isinstance(url, str):
        msg = "URL entry is missing url"
        raise MetadataError(msg)
    return validate_url(url)


def _append_path(base: str, path: object, label: str) -> str:
    if not isinstance(path, str) or not path.strip("/"):
        msg = f"{label} must be a nonempty path"
        raise MetadataError(msg)
    parts = urlsplit(path)
    if (
        parts.scheme
        or parts.netloc
        or parts.query
        or parts.fragment
        or path.startswith("//")
        or "\\" in path
        or any(part in (".", "..") for part in path.split("/"))
    ):
        msg = f"{label} must be a relative distribution path"
        raise MetadataError(msg)
    if urlsplit(base).query:
        msg = "Distribution base URL must not contain a query"
        raise MetadataError(msg)
    return base.rstrip("/") + "/" + path.lstrip("/")


def warehouse_settings_url(settings: object) -> str:
    settings = _object(settings, "Client settings")
    warehouse = _object(
        _nonempty_list(settings.get("warehouses"), "warehouses")[0],
        "Warehouse",
    )
    return _append_path(
        _selected_url(warehouse),
        warehouse.get("warehouseSettingPath"),
        "warehouseSettingPath",
    )


def bundle_base_url(settings: object) -> str:
    settings = _object(settings, "Warehouse settings")
    return (
        _append_path(
            _selected_url(settings), settings.get("bundlePath"), "bundlePath"
        ).rstrip("/")
        + "/"
    )

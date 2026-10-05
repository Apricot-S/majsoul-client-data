import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NotRequired, Protocol, TypedDict
from uuid import uuid4

from .artifacts import FileRecord
from .bundles import (
    BUNDLE_LIMIT,
    INDEX_LIMIT,
    TOTAL_BUNDLE_LIMIT,
    SavedBundle,
    SelectedBundle,
    parse_bundle_index,
    select_bundles,
)
from .discovery import TEXTURE_PROFILES
from .parsing import bundle_base_url, validate_url, warehouse_settings_url
from .storage import publish, validate_destination, validate_output_dir

METADATA_LIMIT = 1024 * 1024


class DownloadedFile(FileRecord):
    url: str


class FetchManifest(TypedDict):
    schema_version: int
    snapshot_id: str
    tool_version: str
    stages: dict[str, dict[str, str]]
    retrieved_at: str
    platform: str
    texture_profile: str
    bundle_base_url: str
    profile_base_url: str
    bundle_hash: str
    files: list[DownloadedFile]
    selected_bundles: NotRequired[list[SavedBundle]]


class BinaryClient(Protocol):
    def get_bytes(self, url: str, *, max_bytes: int) -> bytes: ...


class DownloadResult(TypedDict):
    snapshot_dir: str
    manifest: FetchManifest


def _fetch_selected_bundles(
    index: bytes,
    profile_base: str,
    fetch: Callable[[str, str, int], bytes],
) -> list[SelectedBundle]:
    selected = select_bundles(*parse_bundle_index(index))
    remaining = TOTAL_BUNDLE_LIMIT
    for bundle in selected:
        if remaining <= 0:
            msg = "Total bundle size limit exhausted"
            raise ValueError(msg)
        body = fetch(
            "bundles/" + bundle["name"],
            profile_base + bundle["name"],
            min(BUNDLE_LIMIT, remaining),
        )
        if not body.startswith((b"UnityFS\0", b"UnityWeb\0", b"UnityRaw\0")):
            msg = f"Invalid Unity bundle header: {bundle['name']}"
            raise ValueError(msg)
        remaining -= len(body)
    return selected


def download_snapshot(  # noqa: PLR0913
    client: BinaryClient,
    *,
    client_settings_url: str,
    output_dir: Path = Path("fetched"),
    texture_profile: str = "DXT",
    metadata_only: bool = False,
    overwrite: bool = False,
) -> DownloadResult:
    output = validate_output_dir(output_dir)
    validate_destination(output, overwrite=overwrite)
    validate_url(client_settings_url)
    if texture_profile not in TEXTURE_PROFILES:
        msg = f"Unsupported texture profile: {texture_profile}"
        raise ValueError(msg)
    payloads: list[tuple[str, str, bytes]] = []

    def fetch(name: str, url: str, limit: int = METADATA_LIMIT) -> bytes:
        body = client.get_bytes(url, max_bytes=limit)
        if len(body) > limit:
            msg = f"Response size limit exceeded at {url}"
            raise ValueError(msg)
        payloads.append((name, url, body))
        return body

    settings = json.loads(
        fetch(
            "metadata/client-bundle-settings.json",
            client_settings_url,
        )
    )
    warehouse_url = warehouse_settings_url(settings)
    warehouse = json.loads(
        fetch("metadata/warehouse-settings.json", warehouse_url)
    )
    base = bundle_base_url(warehouse)
    profile_base = f"{base}WebGL/{texture_profile}/"
    bundle_hash = (
        fetch(
            "metadata/bundle_hash.txt",
            profile_base + "bundle_hash.txt",
        )
        .decode("utf-8-sig")
        .strip()
    )
    if not bundle_hash:
        msg = "Bundle hash is empty"
        raise ValueError(msg)
    index = fetch(
        "metadata/bundle_info_so.majset",
        profile_base + "bundle_info_so.majset",
        INDEX_LIMIT,
    )
    if not index.startswith((b"UnityFS\0", b"UnityWeb\0", b"UnityRaw\0")):
        msg = "Bundle index does not have a supported Unity bundle header"
        raise ValueError(msg)

    selected = []
    if not metadata_only:
        selected = _fetch_selected_bundles(index, profile_base, fetch)

    observed_at = datetime.now(UTC)
    snapshot_id = observed_at.strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:12]
    manifest: FetchManifest = {
        "schema_version": 1,
        "snapshot_id": snapshot_id,
        "tool_version": version("majsoul-client-data"),
        "stages": {
            "fetch": {
                "status": "complete",
                "scope": "metadata" if metadata_only else "bundles",
            }
        },
        "retrieved_at": observed_at.isoformat(),
        "platform": "WebGL",
        "texture_profile": texture_profile,
        "bundle_base_url": base,
        "profile_base_url": profile_base,
        "bundle_hash": bundle_hash,
        "files": [
            {
                "name": name,
                "url": url,
                "size": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
            for name, url, body in payloads
        ],
    }
    if not metadata_only:
        manifest["selected_bundles"] = [
            {**bundle, "file": "bundles/" + bundle["name"]}
            for bundle in selected
        ]
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix=".partial-", dir=output.parent
    ) as temporary:
        staging = Path(temporary)
        for name, _, body in payloads:
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        with publish(staging, output, overwrite=overwrite):
            pass
    return {"snapshot_dir": str(output), "manifest": manifest}

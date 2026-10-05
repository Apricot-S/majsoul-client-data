import hashlib
import json
from importlib.metadata import version
from pathlib import Path
from typing import NotRequired, TypedDict

import pytest

from majsoul_client_data import download
from majsoul_client_data.download import download_snapshot

SETTINGS = "https://config.example.test/jp-v1.json"
WAREHOUSE = "https://cdn.example.test/warehouse.json"
BASE = "https://cdn.example.test/bundles/WebGL/DXT/"


class FetchOptions(TypedDict):
    client_settings_url: str
    metadata_only: bool
    output_dir: Path
    texture_profile: NotRequired[str]


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.limits: list[int] = []
        self.responses: dict[str, bytes | Exception] = {
            SETTINGS: json.dumps(
                {
                    "warehouses": [
                        {
                            "urls": [{"url": "https://cdn.example.test"}],
                            "warehouseSettingPath": "/warehouse.json",
                        }
                    ]
                }
            ).encode(),
            WAREHOUSE: json.dumps(
                {
                    "urls": [{"url": "https://cdn.example.test"}],
                    "bundlePath": "/bundles/",
                }
            ).encode(),
            BASE + "bundle_hash.txt": b"sample-version\n",
            BASE + "bundle_info_so.majset": b"UnityFS\x00synthetic-index",
        }

    def get_bytes(self, url: str, *, max_bytes: int) -> bytes:
        assert max_bytes > 0
        self.calls.append(url)
        self.limits.append(max_bytes)
        value = self.responses[url]
        if isinstance(value, Exception):
            raise value
        return value


@pytest.fixture
def bundle_client(monkeypatch: pytest.MonkeyPatch) -> FakeClient:
    monkeypatch.setattr(
        download,
        "parse_bundle_index",
        lambda _body: (
            [{"name": "config.majset"}, {"name": "protocol.majset"}],
            [
                {
                    "assetPath": "LuaByte/Lua/Excels/a.lua.bytes",
                    "ownerBundleIndex": 0,
                },
                {
                    "assetPath": "LuaByte/Lua/Excels/Langs/name_jp.lua.bytes",
                    "ownerBundleIndex": 0,
                },
                {
                    "assetPath": "LuaByte/Lua/Protol/a.lua.bytes",
                    "ownerBundleIndex": 1,
                },
                {
                    "assetPath": "MyAssets/docs/proto_config.bytes",
                    "ownerBundleIndex": 1,
                },
            ],
        ),
    )
    client = FakeClient()
    client.responses[BASE + "config.majset"] = b"UnityFS\0config"
    client.responses[BASE + "protocol.majset"] = b"UnityFS\0protocol"
    return client


def test_default_fetch_saves_selected_bundles_and_mapping(
    tmp_path: Path,
    bundle_client: FakeClient,
) -> None:
    result = download_snapshot(
        bundle_client,
        client_settings_url=SETTINGS,
        output_dir=tmp_path / "out",
    )
    snapshot = Path(result["snapshot_dir"])
    manifest = result["manifest"]
    assert len(bundle_client.calls) == 6
    assert manifest["stages"]["fetch"] == {
        "status": "complete",
        "scope": "bundles",
    }
    assert len(manifest["selected_bundles"]) == 2
    assert manifest["selected_bundles"][0]["file"] == "bundles/config.majset"
    assert len(manifest["selected_bundles"][0]["assets"]) == 2
    assert len(manifest["files"]) == 6
    for item in manifest["files"]:
        body = (snapshot / item["name"]).read_bytes()
        assert item["size"] == len(body)
        assert item["sha256"] == hashlib.sha256(body).hexdigest()
    assert bundle_client.limits[-2:] == [64 * 1024 * 1024] * 2


def test_bundle_failure_publishes_nothing_and_stops(
    tmp_path: Path,
    bundle_client: FakeClient,
) -> None:
    bundle_client.responses[BASE + "config.majset"] = ValueError("HTTP 429")
    with pytest.raises(ValueError, match="429"):
        download_snapshot(
            bundle_client,
            client_settings_url=SETTINGS,
            output_dir=tmp_path / "out",
        )
    assert bundle_client.calls[-1] == BASE + "config.majset"
    assert not (tmp_path / "out").exists()


def test_aggregate_budget_limits_the_next_request(
    tmp_path: Path,
    bundle_client: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(download, "TOTAL_BUNDLE_LIMIT", 20)
    with pytest.raises(ValueError, match="size limit"):
        download_snapshot(
            bundle_client,
            client_settings_url=SETTINGS,
            output_dir=tmp_path / "out",
        )
    assert bundle_client.limits[-2:] == [20, 6]
    assert not (tmp_path / "out").exists()


def test_invalid_selection_stops_before_bundle_get(
    tmp_path: Path,
    bundle_client: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "majsoul_client_data.download.parse_bundle_index",
        lambda _body: ([], []),
    )
    with pytest.raises(ValueError, match="No target"):
        download_snapshot(
            bundle_client,
            client_settings_url=SETTINGS,
            output_dir=tmp_path / "out",
        )
    assert len(bundle_client.calls) == 4


def test_metadata_only_does_not_parse_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(_body: bytes) -> None:
        pytest.fail("Metadata-only mode parsed the index")

    monkeypatch.setattr(download, "parse_bundle_index", fail)
    client = FakeClient()
    download_snapshot(
        client,
        client_settings_url=SETTINGS,
        metadata_only=True,
        output_dir=tmp_path / "out",
    )
    assert len(client.calls) == 4


@pytest.mark.parametrize("count", [64, 65])
def test_bundle_count_budget_is_enforced_before_additional_gets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    count: int,
) -> None:
    index = (
        [{"name": f"{number}.majset"} for number in range(count)],
        [
            {
                "assetPath": f"LuaByte/Lua/Excels/{number}.lua.bytes",
                "ownerBundleIndex": number,
            }
            for number in range(count)
        ],
    )
    monkeypatch.setattr(download, "parse_bundle_index", lambda _body: index)
    client = FakeClient()
    client.responses.update(
        {
            BASE + f"{number}.majset": b"UnityFS\0bundle"
            for number in range(count)
        }
    )
    if count == 65:
        with pytest.raises(ValueError, match="count exceeds 64"):
            download_snapshot(
                client,
                client_settings_url=SETTINGS,
                output_dir=tmp_path / "out",
            )
        assert len(client.calls) == 4
        assert not (tmp_path / "out").exists()
    else:
        result = download_snapshot(
            client, client_settings_url=SETTINGS, output_dir=tmp_path / "out"
        )
        assert len(client.calls) == 68
        assert len(result["manifest"]["selected_bundles"]) == 64


@pytest.mark.parametrize("limit", [0, 14])
def test_total_budget_exhaustion_stops_before_next_bundle(
    tmp_path: Path,
    bundle_client: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
    limit: int,
) -> None:
    monkeypatch.setattr(download, "TOTAL_BUNDLE_LIMIT", limit)
    with pytest.raises(ValueError, match="size limit exhausted"):
        download_snapshot(
            bundle_client,
            client_settings_url=SETTINGS,
            output_dir=tmp_path / "out",
        )
    assert len(bundle_client.calls) == (4 if limit == 0 else 5)
    assert not (tmp_path / "out").exists()


def test_individual_bundle_size_limit_stops_before_next_bundle(
    tmp_path: Path,
    bundle_client: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(download, "BUNDLE_LIMIT", 8)
    with pytest.raises(ValueError, match="size limit"):
        download_snapshot(
            bundle_client,
            client_settings_url=SETTINGS,
            output_dir=tmp_path / "out",
        )
    assert len(bundle_client.calls) == 5
    assert not (tmp_path / "out").exists()


def test_non_bundle_response_is_rejected(
    tmp_path: Path, bundle_client: FakeClient
) -> None:
    bundle_client.responses[BASE + "config.majset"] = b"<html>error</html>"
    with pytest.raises(ValueError, match="Unity bundle header"):
        download_snapshot(
            bundle_client,
            client_settings_url=SETTINGS,
            output_dir=tmp_path / "out",
        )
    assert len(bundle_client.calls) == 5
    assert not (tmp_path / "out").exists()


def test_bundle_disk_failure_preserves_previous_snapshot(
    tmp_path: Path,
    bundle_client: FakeClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = download_snapshot(
        bundle_client,
        client_settings_url=SETTINGS,
        output_dir=tmp_path / "out",
    )
    snapshot = Path(result["snapshot_dir"])
    before = (snapshot / "manifest.json").read_bytes()
    original = Path.write_bytes

    def fail_bundle_write(path: Path, data: bytes) -> int:
        if path.parent.name == "bundles":
            msg = "disk full"
            raise OSError(msg)
        return original(path, data)

    monkeypatch.setattr(Path, "write_bytes", fail_bundle_write)
    with pytest.raises(OSError, match="disk full"):
        download_snapshot(
            bundle_client,
            client_settings_url=SETTINGS,
            output_dir=tmp_path / "out",
            overwrite=True,
        )
    assert snapshot == tmp_path / "out"
    assert (snapshot / "manifest.json").read_bytes() == before


def test_saves_four_exact_payloads_and_manifest_without_bulk_bundles(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    result = download_snapshot(
        client,
        metadata_only=True,
        client_settings_url=SETTINGS,
        output_dir=tmp_path / "downloads",
        texture_profile="DXT",
    )
    snapshot = Path(result["snapshot_dir"])
    assert snapshot == tmp_path / "downloads"
    assert client.calls == list(client.responses)
    names = [
        "client-bundle-settings.json",
        "warehouse-settings.json",
        "bundle_hash.txt",
        "bundle_info_so.majset",
    ]
    for name, url in zip(names, client.calls, strict=True):
        assert (snapshot / "metadata" / name).read_bytes() == client.responses[
            url
        ]
    manifest = json.loads((snapshot / "manifest.json").read_text())
    assert manifest["bundle_hash"] == "sample-version"
    assert manifest["platform"] == "WebGL"
    assert manifest["texture_profile"] == "DXT"
    assert manifest["schema_version"] == 1
    assert manifest["snapshot_id"]
    assert manifest["tool_version"] == version("majsoul-client-data")
    assert manifest["stages"] == {
        "fetch": {"status": "complete", "scope": "metadata"}
    }
    assert {path.name for path in snapshot.iterdir()} == {
        "metadata",
        "manifest.json",
    }
    assert len(manifest["files"]) == 4
    for item in manifest["files"]:
        assert item["name"].startswith("metadata/")
        body = (snapshot / item["name"]).read_bytes()
        assert item["size"] == len(body)
        assert item["sha256"] == hashlib.sha256(body).hexdigest()


def test_each_run_keeps_existing_snapshot(tmp_path: Path) -> None:
    options: FetchOptions = {
        "client_settings_url": SETTINGS,
        "metadata_only": True,
        "output_dir": tmp_path / "downloads",
        "texture_profile": "DXT",
    }
    first = download_snapshot(FakeClient(), **options)
    second = download_snapshot(FakeClient(), **options, overwrite=True)
    assert first["snapshot_dir"] == second["snapshot_dir"]
    assert Path(first["snapshot_dir"]).is_dir()


@pytest.mark.parametrize(
    "url", [SETTINGS, WAREHOUSE, BASE + "bundle_info_so.majset"]
)
def test_failed_request_creates_no_partial_snapshot(
    tmp_path: Path,
    url: str,
) -> None:
    client = FakeClient()
    client.responses[url] = ValueError("HTTP 403")
    with pytest.raises(ValueError, match="403"):
        download_snapshot(
            client,
            metadata_only=True,
            client_settings_url=SETTINGS,
            output_dir=tmp_path / "downloads",
            texture_profile="DXT",
        )
    assert client.calls[-1] == url
    assert not (tmp_path / "downloads").exists()


@pytest.mark.parametrize("payload", [b"", b"<html>not a bundle</html>"])
def test_invalid_index_is_not_published(
    tmp_path: Path, payload: bytes
) -> None:
    client = FakeClient()
    client.responses[BASE + "bundle_info_so.majset"] = payload
    with pytest.raises(ValueError, match="Unity"):
        download_snapshot(
            client,
            metadata_only=True,
            client_settings_url=SETTINGS,
            output_dir=tmp_path / "downloads",
            texture_profile="DXT",
        )
    assert not (tmp_path / "downloads").exists()


def test_file_output_is_rejected_before_any_request(
    tmp_path: Path,
) -> None:
    client = FakeClient()
    output = tmp_path / "file"
    output.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="directory"):
        download_snapshot(
            client,
            metadata_only=True,
            client_settings_url=SETTINGS,
            output_dir=output,
        )
    assert client.calls == []


def test_empty_hash_stops_before_index_request(tmp_path: Path) -> None:
    client = FakeClient()
    client.responses[BASE + "bundle_hash.txt"] = b"\n"
    with pytest.raises(ValueError, match="hash is empty"):
        download_snapshot(
            client,
            metadata_only=True,
            client_settings_url=SETTINGS,
            output_dir=tmp_path / "downloads",
            texture_profile="DXT",
        )
    assert len(client.calls) == 3
    assert not (tmp_path / "downloads").exists()


def test_disk_failure_removes_staging_and_publishes_nothing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_write(_path: Path, _data: bytes) -> int:
        msg = "disk full"
        raise OSError(msg)

    monkeypatch.setattr(Path, "write_bytes", fail_write)
    output = tmp_path / "downloads"
    with pytest.raises(OSError, match="disk full"):
        download_snapshot(
            FakeClient(),
            metadata_only=True,
            client_settings_url=SETTINGS,
            output_dir=output,
            texture_profile="DXT",
        )
    assert not output.exists()


def test_explicit_output_root_can_be_outside_source_tree(
    tmp_path: Path,
) -> None:
    output = tmp_path / "custom-data"
    result = download_snapshot(
        FakeClient(),
        metadata_only=True,
        client_settings_url=SETTINGS,
        output_dir=output,
    )
    assert Path(result["snapshot_dir"]) == output


def test_default_output_is_relative_to_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = download_snapshot(
        FakeClient(), metadata_only=True, client_settings_url=SETTINGS
    )
    assert Path(result["snapshot_dir"]) == tmp_path / "fetched"


def test_file_ancestor_is_rejected_before_network(tmp_path: Path) -> None:
    parent = tmp_path / "file"
    parent.write_text("keep", encoding="utf-8")
    client = FakeClient()
    with pytest.raises(ValueError, match="directory"):
        download_snapshot(
            client,
            metadata_only=True,
            client_settings_url=SETTINGS,
            output_dir=parent / "child",
        )
    assert client.calls == []


def test_manifest_failure_does_not_publish_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_write(_path: Path, _data: str, **_kwargs) -> int:
        msg = "manifest write failed"
        raise OSError(msg)

    monkeypatch.setattr(Path, "write_text", fail_write)
    output = tmp_path / "downloads"
    with pytest.raises(OSError, match="manifest write failed"):
        download_snapshot(
            FakeClient(),
            metadata_only=True,
            client_settings_url=SETTINGS,
            output_dir=output,
        )
    assert not output.exists()


def test_failed_run_preserves_existing_snapshot(tmp_path: Path) -> None:
    output = tmp_path / "downloads"
    first = download_snapshot(
        FakeClient(),
        metadata_only=True,
        client_settings_url=SETTINGS,
        output_dir=output,
    )
    snapshot = Path(first["snapshot_dir"])
    manifest = (snapshot / "manifest.json").read_bytes()
    client = FakeClient()
    client.responses[WAREHOUSE] = ValueError("HTTP 429")
    with pytest.raises(ValueError, match="429"):
        download_snapshot(
            client,
            metadata_only=True,
            client_settings_url=SETTINGS,
            output_dir=output,
            overwrite=True,
        )
    assert snapshot == output
    assert (snapshot / "manifest.json").read_bytes() == manifest


def test_fetch_overwrite_replaces_whole_directory(tmp_path: Path) -> None:
    output = tmp_path / "out"
    options: FetchOptions = {
        "client_settings_url": SETTINGS,
        "metadata_only": True,
        "output_dir": output,
    }
    first = download_snapshot(FakeClient(), **options)
    (output / "stale").write_bytes(b"old")
    client = FakeClient()
    with pytest.raises(ValueError, match="already exists"):
        download_snapshot(client, **options)
    assert not client.calls
    second = download_snapshot(client, **options, overwrite=True)
    assert first["snapshot_dir"] == second["snapshot_dir"] == str(output)
    assert not (output / "stale").exists()
    assert (
        first["manifest"]["snapshot_id"] != second["manifest"]["snapshot_id"]
    )

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from majsoul_client_data import extract
from majsoul_client_data.extract import _asset_payload, _match_text

pytestmark = pytest.mark.usefixtures("working_directory")

LUA = "LuaByte/Lua/Excels/Data/item.lua.bytes"
RPC = "MyAssets/docs/proto_config.bytes"
SOURCE = b"local item = {}\nreturn item\n"


@pytest.mark.parametrize(
    "body",
    [
        b"-- local return =\nnot lua",
        b'local a = "unterminated',
        b"return {1, 2",
        b"local a={}\n" + b" " * 5000 + b"\x00",
    ],
)
def test_lua_detection_checks_lexical_structure_of_entire_input(
    body: bytes,
) -> None:
    with pytest.raises(ValueError, match="Lua"):
        extract.decode_lua(body)


def test_lua_detection_skips_long_comments_and_string_delimiters() -> None:
    body = b"--[=[ documentation ]=]\nreturn {text=[==[ } ( ]==]}"
    assert extract.decode_lua(body) == body


@pytest.mark.parametrize("limit", ["MAX_DEPTH", "MAX_TOKENS", "TEXT_LIMIT"])
def test_lua_detection_enforces_resource_limits(
    limit: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(extract, limit, 1)
    with pytest.raises(ValueError, match="Lua"):
        extract.decode_lua(b"return {{1}}")


def test_text_asset_aliases_must_resolve_without_precedence() -> None:
    with pytest.raises(ValueError, match="Ambiguous TextAsset"):
        _match_text({"item.lua": [SOURCE], "item": [b"other"]}, LUA)


@pytest.mark.parametrize("name", ["item", "item.lua", "item.lua.bytes"])
def test_text_asset_names_share_a_canonical_identity(name: str) -> None:
    assert _match_text({name: [SOURCE], "unrelated": [b"x"]}, LUA) == (
        name,
        SOURCE,
    )


def test_version_json_is_validated_and_preserved() -> None:
    path = "MyAssets/docs_version/version.json"
    body = b'{"version":"0.16.283"}'
    assert _asset_payload(body, path) == body
    with pytest.raises(ValueError, match="docs version"):
        _asset_payload(b'{"version":47}', path)


def test_extraction_records_version_and_original_json(
    snapshot: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = "MyAssets/docs_version/version.json"
    body = b'{"version":"0.16.283"}'
    manifest_path = snapshot / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["selected_bundles"][0]["assets"].append(path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        extract,
        "parse_bundle_index",
        lambda _: (
            [{"name": "config"}],
            [
                {"assetPath": name, "ownerBundleIndex": 0}
                for name in [LUA, RPC, path]
            ],
        ),
    )
    monkeypatch.setattr(
        extract,
        "read_text_assets",
        lambda _: {
            "item.lua": [SOURCE],
            "proto_config": [b"{}"],
            "version": [body],
        },
    )
    result = extract.extract_snapshot(snapshot)
    output = Path(result["extracted_dir"])
    assert (output / path).read_bytes() == body
    assert (
        json.loads((output / "manifest.json").read_bytes())["docs_version"]
        == "0.16.283"
    )


@pytest.fixture
def snapshot(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "source"
    files = {
        "metadata/bundle_info_so.majset": b"UnityFS\0index",
        "bundles/config": b"UnityFS\0bundle",
    }
    for name, body in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    manifest = {
        "schema_version": 1,
        "snapshot_id": "source",
        "bundle_hash": "hash",
        "stages": {"fetch": {"status": "complete", "scope": "bundles"}},
        "files": [
            {
                "name": name,
                "size": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
            for name, body in files.items()
        ],
        "selected_bundles": [
            {"name": "config", "file": "bundles/config", "assets": [LUA, RPC]}
        ],
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        extract,
        "parse_bundle_index",
        lambda _body: (
            [{"name": "config"}],
            [
                {"assetPath": path, "ownerBundleIndex": 0}
                for path in [LUA, RPC]
            ],
        ),
    )
    monkeypatch.setattr(
        extract,
        "read_text_assets",
        lambda _body: {
            "item.lua": [SOURCE],
            "proto_config": [b'{"services": []}'],
        },
    )
    return root


def test_extract_preserves_inputs_and_records_verified_outputs(
    snapshot: Path,
) -> None:
    before = (snapshot / "bundles/config").read_bytes()
    result = extract.extract_snapshot(snapshot)
    extracted = Path(result["extracted_dir"])
    assert extracted == snapshot.parent / "extracted"
    assert (
        extracted / "LuaByte/Lua/Excels/Data/item.lua"
    ).read_bytes() == SOURCE
    assert (
        extracted / "MyAssets/docs/proto_config.bytes"
    ).read_bytes() == b'{"services": []}'
    assert (snapshot / "bundles/config").read_bytes() == before
    manifest = json.loads(
        (extracted / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["source_snapshot_id"] == "source"
    assert manifest["source_bundle_hash"] == "hash"
    assert len(manifest["files"]) == 2
    for item in manifest["files"]:
        body = (extracted / item["name"]).read_bytes()
        assert item["sha256"] == hashlib.sha256(body).hexdigest()
        assert item["size"] == len(body)
    parent_manifest = json.loads(
        (snapshot / "manifest.json").read_text(encoding="utf-8")
    )
    assert parent_manifest["stages"]["fetch"]["status"] == "complete"
    assert parent_manifest["stages"]["extract"]["status"] == "complete"
    assert parent_manifest["stages"]["extract"]["file_count"] == 2


def test_rerun_keeps_previous_extraction(snapshot: Path) -> None:
    first = extract.extract_snapshot(snapshot)
    second = extract.extract_snapshot(snapshot, overwrite=True)
    assert first["extracted_dir"] == second["extracted_dir"]
    assert Path(first["extracted_dir"]).is_dir()


@pytest.mark.parametrize("encrypted", [False, True])
def test_lua_is_preserved_or_xor_decoded(encrypted: bool) -> None:  # noqa: FBT001
    body = SOURCE
    if encrypted:
        key = b"wrelupqezdfrqdsd"
        body = bytes(
            value ^ key[index % len(key)] for index, value in enumerate(SOURCE)
        )
    assert extract.decode_lua(body) == SOURCE


def test_unicode_lua_is_recognized_as_plaintext() -> None:
    body = 'return {name = "一姫"}\n'.encode()
    assert extract.decode_lua(body) == body


@pytest.mark.parametrize("separator", ["\u200b", "\u00a0", "\u2028"])
def test_translation_lua_accepts_unicode_format_characters(
    separator: str,
) -> None:
    body = ('local a={"' + ("一姫" + separator) * 30 + '"}\nreturn a').encode()
    key = b"wrelupqezdfrqdsd"
    encrypted = bytes(
        value ^ key[index % len(key)] for index, value in enumerate(body)
    )
    assert extract.decode_lua(body) == body
    assert extract.decode_lua(encrypted) == body


def test_invalid_lua_reports_asset_path(
    snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        extract, "read_text_assets", lambda _body: {"item.lua": [b"invalid"]}
    )
    with pytest.raises(ValueError, match=LUA):
        extract.extract_snapshot(snapshot)


@pytest.mark.parametrize("body", [b"", b"\x1bLua\x00", b"\x00\xffbroken"])
def test_unknown_lua_format_is_rejected(body: bytes) -> None:
    with pytest.raises(ValueError, match="Lua"):
        extract.decode_lua(body)


def test_metadata_only_snapshot_is_rejected(snapshot: Path) -> None:
    path = snapshot / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["stages"]["fetch"]["scope"] = "metadata"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="bundles"):
        extract.extract_snapshot(snapshot)
    assert not (snapshot.parent / "extracted").exists()


def test_modified_bundle_is_rejected(snapshot: Path) -> None:
    (snapshot / "bundles/config").write_bytes(b"modified")
    before = (snapshot / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="integrity"):
        extract.extract_snapshot(snapshot)
    assert (snapshot / "manifest.json").read_bytes() == before
    assert not list((snapshot.parent / "extracted").glob("*"))


@pytest.mark.parametrize("texts", [{}, {"item.lua": [SOURCE, SOURCE]}])
def test_missing_or_ambiguous_asset_is_not_published(
    snapshot: Path, monkeypatch: pytest.MonkeyPatch, texts: dict
) -> None:
    before = (snapshot / "manifest.json").read_bytes()
    monkeypatch.setattr(extract, "read_text_assets", lambda _body: texts)
    with pytest.raises(ValueError, match="TextAsset"):
        extract.extract_snapshot(snapshot, overwrite=True)
    assert (snapshot / "manifest.json").read_bytes() == before
    assert not (snapshot.parent / "extracted").exists()


def test_explicit_output_root(snapshot: Path, tmp_path: Path) -> None:
    root = tmp_path / "external"
    result = extract.extract_snapshot(snapshot, output_dir=root)
    assert Path(result["extracted_dir"]) == root


def test_text_asset_bytes_preserve_surrogateescaped_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    obj = SimpleNamespace(
        type=SimpleNamespace(name="TextAsset"),
        read=Mock(
            return_value=SimpleNamespace(
                m_Name="a",
                m_Script=b"\xff\x00".decode("utf-8", "surrogateescape"),
            )
        ),
    )
    monkeypatch.setattr(
        extract, "load_bundle_objects", Mock(return_value=[obj])
    )
    assert extract.read_text_assets(b"bundle") == {"a": [b"\xff\x00"]}


def test_text_asset_validation_stops_before_reading_the_next_asset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid = SimpleNamespace(
        type=SimpleNamespace(name="TextAsset"),
        read=Mock(return_value=SimpleNamespace(m_Name="")),
    )
    later = SimpleNamespace(
        type=SimpleNamespace(name="TextAsset"),
        read=Mock(side_effect=EOFError("later failure")),
    )
    monkeypatch.setattr(
        extract, "load_bundle_objects", lambda _: [invalid, later]
    )

    with pytest.raises(
        ValueError, match="TextAsset parsing failed: Invalid TextAsset name"
    ):
        extract.read_text_assets(b"bundle")
    later.read.assert_not_called()


def test_text_asset_library_failure_keeps_its_cause(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    error = EOFError("truncated")
    monkeypatch.setattr(
        extract, "load_bundle_objects", Mock(side_effect=error)
    )

    with pytest.raises(
        ValueError, match="TextAsset parsing failed: truncated"
    ) as caught:
        extract.read_text_assets(b"bundle")
    assert caught.value.__cause__ is error


def test_snapshot_manifest_write_failure_rolls_back_new_output(
    snapshot: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = extract.extract_snapshot(snapshot)
    before = (snapshot / "manifest.json").read_bytes()

    def fail_replace(_source: Path, _target: Path) -> Path:
        msg = "manifest write failed"
        raise OSError(msg)

    monkeypatch.setattr(Path, "replace", fail_replace)
    with pytest.raises(OSError, match="manifest write failed"):
        extract.extract_snapshot(snapshot, overwrite=True)
    assert (snapshot / "manifest.json").read_bytes() == before
    assert Path(first["manifest_path"]).is_file()
    assert not list(snapshot.glob(".partial-*"))


def test_partial_asset_write_preserves_input_and_previous_result(
    snapshot: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = extract.extract_snapshot(snapshot)
    before = (snapshot / "manifest.json").read_bytes()
    original_write = Path.write_bytes

    def fail_rpc(path: Path, body: bytes) -> int:
        if path.name == "proto_config.bytes":
            msg = "disk full"
            raise OSError(msg)
        return original_write(path, body)

    monkeypatch.setattr(Path, "write_bytes", fail_rpc)
    with pytest.raises(OSError, match="disk full"):
        extract.extract_snapshot(snapshot, overwrite=True)
    assert (snapshot / "manifest.json").read_bytes() == before
    assert Path(first["manifest_path"]).is_file()


def test_input_hash_and_output_limits_are_checked(
    snapshot: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(extract, "EXTRACTED_LIMIT", len(SOURCE) - 1)
    before = (snapshot / "manifest.json").read_bytes()
    with pytest.raises(ValueError, match="Extracted size limit"):
        extract.extract_snapshot(snapshot, overwrite=True)
    assert (snapshot / "manifest.json").read_bytes() == before
    assert not (snapshot.parent / "extracted").exists()


def test_modified_index_is_rejected_before_unity_parse(
    snapshot: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = Mock()
    monkeypatch.setattr(extract, "parse_bundle_index", reader)
    (snapshot / "metadata/bundle_info_so.majset").write_bytes(b"broken")
    with pytest.raises(ValueError, match="integrity"):
        extract.extract_snapshot(snapshot)
    reader.assert_not_called()
    assert not (snapshot.parent / "extracted").exists()


@pytest.mark.parametrize("field", ["file", "assets"])
def test_selection_tampering_is_rejected(snapshot: Path, field: str) -> None:
    path = snapshot / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["selected_bundles"][0][field] = "../outside"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="do not match"):
        extract.extract_snapshot(snapshot)
    assert not (snapshot.parent / "extracted").exists()


def test_escaping_input_symlink_is_rejected(
    snapshot: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside"
    outside.write_bytes((snapshot / "bundles/config").read_bytes())
    target = snapshot / "bundles/config"
    target.unlink()
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks is unavailable in this environment")
    with pytest.raises(ValueError, match="escapes snapshot"):
        extract.extract_snapshot(snapshot)
    assert not list((snapshot.parent / "extracted").glob("*"))


@pytest.mark.parametrize("protected", ["bundles", "metadata"])
def test_output_cannot_pollute_fetch_inputs(
    snapshot: Path, protected: str
) -> None:
    with pytest.raises(ValueError, match="inside input"):
        extract.extract_snapshot(
            snapshot, output_dir=snapshot / protected / "out"
        )
    assert not (snapshot / protected / "out").exists()


def test_same_basename_does_not_reuse_one_text_asset_for_two_paths(
    snapshot: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    second = "LuaByte/Lua/Excels/Other/item.lua.bytes"
    path = snapshot / "manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["selected_bundles"][0]["assets"].append(second)
    path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        extract,
        "parse_bundle_index",
        lambda _body: (
            [{"name": "config"}],
            [
                {"assetPath": asset, "ownerBundleIndex": 0}
                for asset in [LUA, RPC, second]
            ],
        ),
    )
    with pytest.raises(ValueError, match=r"TextAsset.*multiple paths"):
        extract.extract_snapshot(snapshot)

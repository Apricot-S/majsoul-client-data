import struct
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from UnityPy import config
from UnityPy.helpers import CompressionHelper

from majsoul_client_data import bundles as bundle_module
from majsoul_client_data.bundles import parse_bundle_index, select_bundles

EXCEL = "LuaByte/Lua/Excels/Data/item.lua.bytes"
PROTOCOL = "LuaByte/Lua/Protol/liqi.lua.bytes"
RPC = "MyAssets/docs/proto_config.bytes"


@pytest.mark.parametrize(
    "tree",
    [
        {"bundleInfos": [{"name": "a"}], "assetInfos": []},
        {"m_Name": "Other", "bundleInfos": [{"name": "a"}], "assetInfos": []},
        {"m_Name": "BundleInfoSO", "bundleInfos": [{"name": "a"}]},
    ],
)
def test_index_requires_named_object_and_complete_schema(
    tree: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    obj = SimpleNamespace(
        type=SimpleNamespace(name="MonoBehaviour"), read_typetree=lambda: tree
    )
    monkeypatch.setattr(bundle_module, "load_bundle_objects", lambda _: [obj])
    with pytest.raises(ValueError, match=r"BundleInfoSO|assetInfos"):
        parse_bundle_index(b"index")


def test_duplicate_named_indexes_are_rejected_before_schema_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    trees = [
        {"m_Name": "BundleInfoSO", "bundleInfos": [], "assetInfos": []},
        {"m_Name": "BundleInfoSO"},
    ]
    objects = [
        SimpleNamespace(
            type=SimpleNamespace(name="MonoBehaviour"),
            read_typetree=lambda tree=tree: tree,
        )
        for tree in trees
    ]
    monkeypatch.setattr(
        bundle_module, "load_bundle_objects", lambda _: objects
    )
    with pytest.raises(ValueError, match="exactly one BundleInfoSO"):
        parse_bundle_index(b"index")


def test_selects_only_the_exact_docs_version_asset() -> None:
    path = "MyAssets/docs_version/version.json"
    assert select_bundles(
        [{"name": "version"}],
        [
            {"assetPath": path, "ownerBundleIndex": 0},
            {
                "assetPath": "MyAssets/docs_version/other.json",
                "ownerBundleIndex": 0,
            },
        ],
    ) == [{"name": "version", "assets": [path]}]


def test_selection_includes_translations_and_rpc_and_deduplicates() -> None:
    paths = [
        EXCEL,
        "LuaByte/Lua/Excels/Langs/name_jp.lua.bytes",
        PROTOCOL,
        RPC,
    ]
    bundles = [{"name": "config.majset"}, {"name": "protocol.majset"}]
    assets = [
        {"assetPath": path, "ownerBundleIndex": index // 2}
        for index, path in enumerate(paths)
    ]
    assets.append(
        {"assetPath": "MyAssets/images/icon.png", "ownerBundleIndex": 0}
    )
    assert select_bundles(bundles, assets) == [
        {"name": "config.majset", "assets": paths[:2]},
        {"name": "protocol.majset", "assets": paths[2:]},
    ]


@pytest.mark.parametrize("owner", [-1, 1, True, "0", None])
def test_invalid_owner_is_rejected(owner: object) -> None:
    with pytest.raises(ValueError, match="ownerBundleIndex"):
        select_bundles(
            [{"name": "a.majset"}],
            [{"assetPath": EXCEL, "ownerBundleIndex": owner}],
        )


@pytest.mark.parametrize(
    "name",
    [
        "../x",
        "/x",
        "https://other.test/x",
        "a\\b",
        "a?x",
        "a#x",
        "a%2fb",
        "C:x",
        "a//b",
        "CON",
        "a. ",
        "",
    ],
)
def test_unsafe_bundle_name_is_rejected(name: str) -> None:
    with pytest.raises(ValueError, match="path"):
        select_bundles(
            [{"name": name}], [{"assetPath": EXCEL, "ownerBundleIndex": 0}]
        )


def test_distribution_name_can_contain_at_and_dollar() -> None:
    name = "2_tszfz2@ezo$hash"
    assert select_bundles(
        [{"name": name}], [{"assetPath": EXCEL, "ownerBundleIndex": 0}]
    ) == [{"name": name, "assets": [EXCEL]}]


def test_no_target_is_an_error() -> None:
    with pytest.raises(ValueError, match="No target"):
        select_bundles([{"name": "a.majset"}], [])


def test_bundle_count_is_bounded() -> None:
    with pytest.raises(ValueError, match="64"):
        select_bundles(
            [{"name": f"{index}.majset"} for index in range(65)],
            [
                {
                    "assetPath": f"LuaByte/Lua/Excels/{index}.lua.bytes",
                    "ownerBundleIndex": index,
                }
                for index in range(65)
            ],
        )


def test_reads_bundle_info_from_mono_behaviour(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = {
        "m_Name": "BundleInfoSO",
        "bundleInfos": [{"name": "a"}],
        "assetInfos": [],
    }
    obj = SimpleNamespace(
        type=SimpleNamespace(name="MonoBehaviour"),
        read_typetree=Mock(return_value=tree),
    )
    monkeypatch.setattr(
        "majsoul_client_data.bundles.load_bundle_objects",
        Mock(return_value=[obj]),
    )
    assert parse_bundle_index(b"index") == (tree["bundleInfos"], [])


def test_unity_failure_becomes_value_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "majsoul_client_data.bundles.load_bundle_objects",
        Mock(side_effect=EOFError("truncated")),
    )
    with pytest.raises(ValueError, match="Bundle index"):
        parse_bundle_index(b"bad")


@pytest.mark.parametrize(
    "tree", [{}, {"bundleInfos": "bad", "assetInfos": []}]
)
def test_missing_or_malformed_index_is_rejected(tree: dict) -> None:
    with pytest.raises(ValueError, match="list of objects"):
        select_bundles(tree.get("bundleInfos"), tree.get("assetInfos"))


def synthetic_unity_fs(
    block: bytes = b"",
    *,
    compression: int = 0,
    declared_size: int | None = None,
    with_file: bool = False,
    engine_version: str = "2022.3.62f1",
) -> bytes:
    info = b"\0" * 16 + struct.pack(">i", 1)
    info += struct.pack(
        ">IIH",
        len(block) if declared_size is None else declared_size,
        len(block),
        0,
    )
    info += struct.pack(">i", int(with_file))
    if with_file:
        info += struct.pack(">qqI", 0, len(block), 0) + b"entry\0"
    encoded = info
    if compression == 1:
        encoded = CompressionHelper.compress_lzma(info)
    elif compression in {2, 3}:
        encoded = CompressionHelper.compress_lz4(info)
    prefix = (
        b"UnityFS\0"
        + struct.pack(">I", 7)
        + b"5.x.x\0"
        + engine_version.encode()
        + b"\0"
    )
    header_length = len(prefix) + 20
    padding = b"\0" * (-header_length % 16)
    size = header_length + len(padding) + len(encoded) + len(block)
    return (
        prefix
        + struct.pack(">qIII", size, len(encoded), len(info), 64 | compression)
        + padding
        + encoded
        + block
    )


@pytest.mark.parametrize("compression", [0, 1, 2, 3])
def test_real_unity_container_is_read_and_missing_info_is_reported(
    compression: int,
) -> None:
    with pytest.raises(ValueError, match="exactly one BundleInfoSO"):
        parse_bundle_index(synthetic_unity_fs(compression=compression))


def test_advertised_expansion_is_rejected_before_decompression() -> None:
    with pytest.raises(ValueError, match="expansion limit"):
        parse_bundle_index(synthetic_unity_fs(declared_size=129 * 1024 * 1024))


def test_expansion_budget_includes_directory_and_all_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bundle_module, "INDEX_EXPANSION_LIMIT", 40)
    with pytest.raises(ValueError, match="expansion limit"):
        parse_bundle_index(synthetic_unity_fs(b"x" * 16))


def test_lzma_output_larger_than_declared_size_is_rejected() -> None:
    body = synthetic_unity_fs(compression=1)
    offset = body.index(b"2022.3.62f1\0") + len(b"2022.3.62f1\0")
    body = body[: offset + 12] + struct.pack(">I", 1) + body[offset + 16 :]
    with pytest.raises(ValueError, match="block size mismatch"):
        parse_bundle_index(body)


def test_zero_lz4_size_is_rejected_before_library_decoder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    decoder = Mock(side_effect=AssertionError("Unsafe zero-size decode"))
    monkeypatch.setattr(bundle_module.BundleFile, "decompress_data", decoder)
    body = synthetic_unity_fs(compression=2)
    offset = body.index(b"2022.3.62f1\0") + len(b"2022.3.62f1\0")
    body = body[: offset + 12] + struct.pack(">I", 0) + body[offset + 16 :]
    with pytest.raises(ValueError, match="block size mismatch"):
        parse_bundle_index(body)
    decoder.assert_not_called()


@pytest.mark.parametrize("names", [["a", "A"], ["a", "a/b"], ["a/b", "a"]])
def test_conflicting_bundle_paths_are_rejected(names: list[str]) -> None:
    with pytest.raises(ValueError, match="Conflicting bundle path"):
        select_bundles(
            [{"name": name} for name in names],
            [
                {"assetPath": EXCEL, "ownerBundleIndex": 0},
                {"assetPath": PROTOCOL, "ownerBundleIndex": 1},
            ],
        )


def test_repeated_asset_and_conflicting_owner() -> None:
    asset = {"assetPath": EXCEL, "ownerBundleIndex": 0}
    assert select_bundles([{"name": "a"}], [asset, asset])[0]["assets"] == [
        EXCEL
    ]
    with pytest.raises(ValueError, match="Conflicting ownerBundleIndex"):
        select_bundles(
            [{"name": "a"}, {"name": "b"}],
            [asset, {**asset, "ownerBundleIndex": 1}],
        )


def test_unsafe_selected_asset_path_is_rejected() -> None:
    with pytest.raises(ValueError, match="path"):
        select_bundles(
            [{"name": "a"}],
            [
                {
                    "assetPath": "LuaByte/Lua/Excels/../a.lua.bytes",
                    "ownerBundleIndex": 0,
                }
            ],
        )


def test_nested_container_cannot_bypass_expansion_limit() -> None:
    with pytest.raises(ValueError, match="Nested containers"):
        parse_bundle_index(
            synthetic_unity_fs(synthetic_unity_fs(), with_file=True)
        )


@pytest.mark.parametrize("engine_version", ["2022.3.62f1", "0.0.0"])
def test_unknown_serialized_version_uses_bundle_version(
    monkeypatch: pytest.MonkeyPatch,
    engine_version: str,
) -> None:
    monkeypatch.setattr(config, "FALLBACK_UNITY_VERSION", None)
    metadata = b"0.0.0\0" + struct.pack("<i", 20) + b"\0"
    metadata += struct.pack("<iiii", 0, 0, 0, 0) + b"\0"
    data_offset = 20 + len(metadata)
    size = 128
    serialized = (
        struct.pack(">IIII", len(metadata), size, 17, data_offset)
        + b"\0" * 4
        + metadata
        + b"\0" * (size - data_offset)
    )
    body = synthetic_unity_fs(
        serialized, with_file=True, engine_version=engine_version
    )
    with pytest.raises(ValueError, match="exactly one BundleInfoSO"):
        parse_bundle_index(body)
    assert config.FALLBACK_UNITY_VERSION is None

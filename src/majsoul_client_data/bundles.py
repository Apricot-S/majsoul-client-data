import lzma
import struct
from typing import Any, TypedDict

from UnityPy import Environment
from UnityPy.enums import FileType
from UnityPy.files import BundleFile
from UnityPy.helpers.ImportHelper import check_file_type
from UnityPy.helpers.UnityVersion import UnityVersion
from UnityPy.streams import EndianBinaryReader

from .assets import DOCS_VERSION_ASSET, EXCEL_PREFIX, PROTOCOL_PREFIX, RPC_FILE
from .storage import validate_relative_path

MAX_BUNDLES = 64
INDEX_LIMIT = 32 * 1024 * 1024
BUNDLE_LIMIT = 64 * 1024 * 1024
TOTAL_BUNDLE_LIMIT = 256 * 1024 * 1024
INDEX_EXPANSION_LIMIT = 128 * 1024 * 1024
LZMA_DICTIONARY_LIMIT = 64 * 1024 * 1024
FALLBACK_UNITY_VERSION = "2022.3.62f2c1"


class SelectedBundle(TypedDict):
    name: str
    assets: list[str]


class SavedBundle(SelectedBundle):
    file: str


class _BoundedBundleFile(BundleFile):
    def __init__(
        self, reader: EndianBinaryReader, parent: Environment
    ) -> None:
        self._remaining = INDEX_EXPANSION_LIMIT
        # UnityPy accepts Environment; the annotation only names File.
        super().__init__(reader, parent)  # ty: ignore[invalid-argument-type]

    def parse_version(self) -> UnityVersion:
        try:
            parsed = UnityVersion.from_str(self.version_engine)
        except ValueError:
            parsed = None
        if parsed is None or parsed.major == 0:
            # SerializedFile inherits the parent's engine version.
            self.version_engine = FALLBACK_UNITY_VERSION
            return UnityVersion.from_str(FALLBACK_UNITY_VERSION)
        return parsed

    def read_files(self, reader: EndianBinaryReader, files: list) -> None:
        if sum(node.size for node in files) > INDEX_EXPANSION_LIMIT:
            msg = "Bundle index entry size limit exceeded"
            raise ValueError(msg)
        for node in files:
            if (
                node.offset < 0
                or node.size < 0
                or node.offset + node.size > reader.Length
            ):
                msg = "Invalid bundle index entry bounds"
                raise ValueError(msg)
            reader.Position = node.offset
            kind, _ = check_file_type(reader.read(node.size))
            if kind not in {FileType.AssetsFile, FileType.ResourceFile}:
                msg = "Nested containers are not supported in bundle indexes"
                raise ValueError(msg)
        super().read_files(reader, files)

    def decompress_data(
        self,
        compressed_data: bytes,
        uncompressed_size: int,
        flags: int,
        index: int = 0,
    ) -> bytes:
        if not 0 <= uncompressed_size <= self._remaining:
            msg = "Bundle index expansion limit exceeded"
            raise ValueError(msg)
        self._remaining -= uncompressed_size
        compression = flags & 0x3F
        if self.decryptor is not None or compression not in {0, 1, 2, 3}:
            msg = "Unsupported bundle index compression or encryption"
            raise ValueError(msg)
        if compression in {2, 3} and uncompressed_size == 0:
            # lz4 treats zero as a size stored in the input.
            msg = "Bundle index block size mismatch"
            raise ValueError(msg)
        if compression == 1:
            props, dictionary = struct.unpack("<BI", compressed_data[:5])
            if dictionary > LZMA_DICTIONARY_LIMIT:
                msg = "Bundle index LZMA dictionary limit exceeded"
                raise ValueError(msg)
            decoder = lzma.LZMADecompressor(
                format=lzma.FORMAT_RAW,
                filters=[
                    {
                        "id": lzma.FILTER_LZMA1,
                        "dict_size": dictionary,
                        "lc": props % 9,
                        "lp": (props // 9) % 5,
                        "pb": props // 45,
                    }
                ],
            )
            result = decoder.decompress(
                compressed_data[5:], max_length=uncompressed_size + 1
            )
        else:
            result = super().decompress_data(
                compressed_data, uncompressed_size, flags, index
            )
        if len(result) != uncompressed_size:
            msg = "Bundle index block size mismatch"
            raise ValueError(msg)
        return result


def load_bundle_objects(body: bytes) -> list[Any]:
    # Only modern UnityFS bundles use this bounded expansion path.
    if not body.startswith(b"UnityFS\0"):
        msg = "Bundle parsing requires UnityFS"
        raise ValueError(msg)
    env = Environment()
    env.files["bundle.majset"] = _BoundedBundleFile(
        EndianBinaryReader(body), env
    )
    return env.objects


def _read_index_descriptors(body: bytes) -> list[dict]:
    try:
        named = []
        for obj in load_bundle_objects(body):
            if obj.type.name != "MonoBehaviour":
                continue
            descriptor = obj.read_typetree()
            if (
                isinstance(descriptor, dict)
                and descriptor.get("m_Name") == "BundleInfoSO"
            ):
                named.append(descriptor)
    except Exception as exc:
        msg = f"Bundle index parsing failed: {exc}"
        raise ValueError(msg) from exc
    return named


def _index_tables(named: list[dict]) -> tuple[list[dict], list[dict]]:
    if len(named) != 1:
        msg = "Expected exactly one BundleInfoSO"
        raise ValueError(msg)
    descriptor = named[0]
    tables = {
        key: _validate_entries(descriptor.get(key), key)
        for key in ("bundleInfos", "assetInfos")
    }
    return tables["bundleInfos"], tables["assetInfos"]


def parse_bundle_index(body: bytes) -> tuple[list[dict], list[dict]]:
    named = _read_index_descriptors(body)
    try:
        return _index_tables(named)
    except ValueError as exc:
        msg = f"Bundle index parsing failed: {exc}"
        raise ValueError(msg) from exc


def _validate_entries(value: object, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or any(
        not isinstance(entry, dict) for entry in value
    ):
        msg = f"{label} must be a list of objects"
        raise ValueError(msg)
    return value


def _is_target(path: object) -> bool:
    if not isinstance(path, str):
        msg = "Invalid assetPath: expected a string"
        raise ValueError(msg)  # noqa: TRY004 - malformed index data
    return path in {
        RPC_FILE,
        DOCS_VERSION_ASSET,
    } or (
        path.startswith((EXCEL_PREFIX, PROTOCOL_PREFIX))
        and path.endswith(".lua.bytes")
    )


def select_bundles(bundles: object, assets: object) -> list[SelectedBundle]:
    bundles = _validate_entries(bundles, "bundleInfos")
    assets = _validate_entries(assets, "assetInfos")
    grouped: dict[int, list[str]] = {}
    owners: dict[str, int] = {}
    for asset in assets:
        path = asset.get("assetPath")
        if not _is_target(path):
            continue
        path = validate_relative_path(path)
        owner = asset.get("ownerBundleIndex")
        if type(owner) is not int or not 0 <= owner < len(bundles):
            msg = f"Invalid ownerBundleIndex for {path}"
            raise ValueError(msg)
        if path in owners:
            if owners[path] != owner:
                msg = f"Conflicting ownerBundleIndex for {path}"
                raise ValueError(msg)
            continue
        owners[path] = owner
        grouped.setdefault(owner, []).append(path)
    if not grouped:
        msg = "No target assets found in bundle index"
        raise ValueError(msg)
    if len(grouped) > MAX_BUNDLES:
        msg = f"Selected bundle count exceeds {MAX_BUNDLES}"
        raise ValueError(msg)
    selected: list[SelectedBundle] = []
    names: set[str] = set()
    for owner, paths in sorted(grouped.items()):
        name = validate_relative_path(bundles[owner].get("name"))
        key = name.casefold()
        if key in names or any(
            key.startswith(existing + "/") or existing.startswith(key + "/")
            for existing in names
        ):
            msg = f"Conflicting bundle path: {name}"
            raise ValueError(msg)
        names.add(key)
        selected.append({"name": name, "assets": paths})
    return selected

import hashlib
import json
import posixpath
import re
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from .artifacts import (
    EXTRACTED_LIMIT,
    MANIFEST_LIMIT,
    MAX_EXTRACTED_FILES,
    TEXT_LIMIT,
    FileRecord,
    artifact_path,
    file_records,
    read_limited,
    verified_file,
)
from .assets import DOCS_VERSION_ASSET
from .bundles import (
    BUNDLE_LIMIT,
    INDEX_LIMIT,
    TOTAL_BUNDLE_LIMIT,
    SavedBundle,
    load_bundle_objects,
    parse_bundle_index,
    select_bundles,
)
from .lexing import source_tokens
from .lua import MAX_DEPTH, MAX_TOKENS
from .lua import require as _require
from .storage import (
    protect_input,
    publish,
    validate_destination,
    validate_output_dir,
    validate_relative_path,
)
from .versions import parse_docs_version

LUA_XOR_KEY = b"wrelupqezdfrqdsd"


class ExtractedFile(FileRecord):
    source_asset: str
    source_bundle: str
    transform: str


@dataclass(frozen=True)
class _Snapshot:
    root: Path
    manifest: dict[str, Any]
    original: bytes
    records: dict[str, FileRecord]
    selected: list[SavedBundle]


def _looks_like_lua(body: bytes) -> bool:
    try:
        text = body.decode("utf-8-sig")
        _require("\0" not in text, "NUL in Lua source")
        tokens = source_tokens(
            text, max_depth=MAX_DEPTH, max_tokens=MAX_TOKENS
        )
    except (UnicodeDecodeError, ValueError):
        return False
    # Require a data-module statement outside comments and strings.
    statement = " ".join(tokens[:6])
    identifier = r"[A-Za-z_][A-Za-z_0-9]*"
    return bool(
        re.match(
            rf"^(?:local {identifier} (?:=|,|function)|"
            rf"return (?:{identifier}|[0-9]+|STRING|\{{)|"
            rf"module (?:\(|STRING)|function {identifier} (?:\.|\()|"
            rf"{identifier} =)",
            statement,
        )
    )


def decode_lua(body: bytes) -> bytes:
    _require(len(body) <= TEXT_LIMIT, "Lua source size limit exceeded")
    if _looks_like_lua(body):
        return body
    decoded = bytes(
        value ^ LUA_XOR_KEY[index % len(LUA_XOR_KEY)]
        for index, value in enumerate(body)
    )
    _require(
        _looks_like_lua(decoded), "Unsupported Lua source or XOR encoding"
    )
    return decoded


def _text_asset_bytes(script: object) -> bytes:
    if not isinstance(script, (bytes, str)):
        msg = "Unsupported TextAsset script type"
        raise ValueError(msg)  # noqa: TRY004 - malformed TextAsset
    body = (
        script.encode("utf-8", "surrogateescape")
        if isinstance(script, str)
        else script
    )
    _require(len(body) <= TEXT_LIMIT, "TextAsset size limit exceeded")
    return body


def _read_text_asset_scripts(body: bytes) -> Iterator[tuple[str, object]]:
    try:
        for obj in load_bundle_objects(body):
            if obj.type.name == "TextAsset":
                asset = obj.read()
                name = asset.m_Name
                if not isinstance(name, str) or not name:
                    msg = "Invalid TextAsset name"
                    raise ValueError(msg)  # noqa: TRY301 - keep read order
                yield name, asset.m_Script
    except Exception as exc:
        msg = f"TextAsset parsing failed: {exc}"
        raise ValueError(msg) from exc


def read_text_assets(body: bytes) -> dict[str, list[bytes]]:
    texts: dict[str, list[bytes]] = {}
    for name, script in _read_text_asset_scripts(body):
        try:
            texts.setdefault(name, []).append(_text_asset_bytes(script))
        except ValueError as exc:
            msg = f"TextAsset parsing failed: {exc}"
            raise ValueError(msg) from exc
    return texts


def _match_text(
    texts: dict[str, list[bytes]], asset_path: str
) -> tuple[str, bytes]:
    filename = posixpath.basename(asset_path)
    # Resolve all permitted spellings without precedence.
    identity = filename.removesuffix(".bytes")
    accepted = {filename, identity, posixpath.splitext(identity)[0]}
    matches = [
        (name, value)
        for name, values in texts.items()
        if name in accepted
        for value in values
    ]
    _require(bool(matches), f"Missing TextAsset for {asset_path}")
    _require(len(matches) == 1, f"Ambiguous TextAsset for {asset_path}")
    return matches[0]


def _output_name(asset_path: str) -> str:
    validate_relative_path(asset_path)
    if asset_path.endswith(".lua.bytes"):
        return asset_path.removesuffix(".bytes")
    return asset_path


def _asset_payload(source: bytes, asset_path: str) -> bytes:
    if asset_path == DOCS_VERSION_ASSET:
        parse_docs_version(source)
    if not asset_path.endswith(".lua.bytes"):
        return source
    try:
        return decode_lua(source)
    except ValueError as exc:
        msg = f"{asset_path}: {exc}"
        raise ValueError(msg) from exc


def _load_input(root: Path) -> _Snapshot:
    original = read_limited(
        artifact_path(root, "manifest.json"), MANIFEST_LIMIT
    )
    manifest = json.loads(original)
    _require(
        isinstance(manifest, dict) and manifest.get("schema_version") == 1,
        "Unsupported snapshot manifest schema",
    )
    stages = manifest.get("stages")
    _require(isinstance(stages, dict), "Invalid snapshot stages")
    fetch = stages.get("fetch")
    _require(
        isinstance(fetch, dict)
        and fetch.get("status") == "complete"
        and fetch.get("scope") == "bundles",
        "extract requires a complete fetch with scope bundles",
    )
    _require(
        isinstance(manifest.get("snapshot_id"), str)
        and bool(manifest["snapshot_id"]),
        "Missing snapshot_id",
    )
    records = file_records(manifest)
    index = verified_file(
        root, "metadata/bundle_info_so.majset", records, INDEX_LIMIT
    )
    selected = select_bundles(*parse_bundle_index(index))
    expected: list[SavedBundle] = [
        {**item, "file": "bundles/" + item["name"]} for item in selected
    ]
    _require(
        manifest.get("selected_bundles") == expected,
        "Selected bundles do not match the verified index",
    )
    names = [
        _output_name(asset)
        for bundle in selected
        for asset in bundle["assets"]
    ]
    _require(
        len(names) <= MAX_EXTRACTED_FILES,
        "Extracted file count limit exceeded",
    )
    _require(
        len(names) == len({name.casefold() for name in names}),
        "Conflicting extracted paths",
    )
    return _Snapshot(root, manifest, original, records, expected)


@dataclass(frozen=True)
class _ExtractedFiles:
    records: list[ExtractedFile]
    docs_version: str | None


def _extract_files(
    root: Path,
    staging: Path,
    records: dict[str, FileRecord],
    selected: list[SavedBundle],
) -> _ExtractedFiles:
    outputs: list[ExtractedFile] = []
    docs_version = None
    remaining_input = TOTAL_BUNDLE_LIMIT
    remaining_output = EXTRACTED_LIMIT
    for bundle in selected:
        _require(
            remaining_input > 0, "Total bundle input size limit exhausted"
        )
        body = verified_file(
            root, bundle["file"], records, min(BUNDLE_LIMIT, remaining_input)
        )
        remaining_input -= len(body)
        texts = read_text_assets(body)
        used_names: set[str] = set()
        for asset_path in bundle["assets"]:
            text_name, source = _match_text(texts, asset_path)
            _require(
                text_name not in used_names,
                f"TextAsset {text_name} resolves to multiple paths "
                f"in {bundle['file']}",
            )
            used_names.add(text_name)
            if asset_path == DOCS_VERSION_ASSET:
                docs_version = parse_docs_version(source)
                decoded = source
            else:
                decoded = _asset_payload(source, asset_path)
            _require(
                len(decoded) <= TEXT_LIMIT
                and len(decoded) <= remaining_output,
                "Extracted size limit exceeded",
            )
            remaining_output -= len(decoded)
            name = _output_name(asset_path)
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(decoded)
            outputs.append(
                {
                    "name": name,
                    "size": len(decoded),
                    "sha256": hashlib.sha256(decoded).hexdigest(),
                    "source_asset": asset_path,
                    "source_bundle": bundle["file"],
                    "transform": "lua-xor" if decoded != source else "none",
                }
            )
    return _ExtractedFiles(outputs, docs_version)


def _json_write(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _publish(
    snapshot: _Snapshot,
    staging: Path,
    destination: Path,
    count: int,
    *,
    overwrite: bool = False,
) -> None:
    root = snapshot.root
    manifest = snapshot.manifest
    output_manifest = destination / "manifest.json"
    updated = {
        **manifest,
        "stages": {
            **manifest["stages"],
            "extract": {
                "status": "complete",
                "file_count": count,
                "manifest": str(output_manifest),
            },
        },
    }
    with TemporaryDirectory(
        prefix=".partial-extract-manifest-", dir=root
    ) as temporary:
        pending = Path(temporary) / "manifest.json"
        _json_write(pending, updated)
        _require(
            (root / "manifest.json").read_bytes() == snapshot.original,
            "Snapshot manifest changed during extraction",
        )
        with publish(staging, destination, overwrite=overwrite):
            pending.replace(root / "manifest.json")


def extract_snapshot(
    input_dir: Path = Path("fetched"),
    *,
    output_dir: Path | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    root = input_dir.resolve()
    _require(root.is_dir(), "Input snapshot must be a directory")
    snapshot = _load_input(root)
    manifest = snapshot.manifest
    output = validate_output_dir(
        output_dir if output_dir is not None else Path("extracted")
    )
    for protected in (root / "bundles", root / "metadata"):
        _require(
            not output.is_relative_to(protected.resolve()),
            "Output must not be inside input bundles or metadata",
        )
    protect_input(output, root)
    validate_destination(output, overwrite=overwrite)
    observed = datetime.now(UTC)
    extraction_id = observed.strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:12]
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix=".partial-extract-", dir=output.parent
    ) as temporary:
        staging = Path(temporary) / "extracted"
        staging.mkdir()
        extracted = _extract_files(
            root, staging, snapshot.records, snapshot.selected
        )
        files = extracted.records
        extraction_manifest = {
            "schema_version": 1,
            "extraction_id": extraction_id,
            "tool_version": version("majsoul-client-data"),
            "extracted_at": observed.isoformat(),
            "source_snapshot_id": manifest["snapshot_id"],
            "source_bundle_hash": manifest.get("bundle_hash"),
            "docs_version": extracted.docs_version,
            "source_manifest_sha256": hashlib.sha256(
                snapshot.original
            ).hexdigest(),
            "stages": {"extract": {"status": "complete"}},
            "files": files,
        }
        _json_write(staging / "manifest.json", extraction_manifest)
        _publish(snapshot, staging, output, len(files), overwrite=overwrite)
    return {
        "snapshot_dir": str(root),
        "extracted_dir": str(output),
        "manifest_path": str(output / "manifest.json"),
        "file_count": len(files),
    }

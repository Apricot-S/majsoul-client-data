import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .artifacts import (
    MANIFEST_LIMIT,
    MAX_EXTRACTED_FILES,
    TEXT_LIMIT,
    artifact_path,
    file_records,
    read_limited,
    verified_file,
)
from .lua import require
from .storage import (
    protect_input,
    publish,
    validate_destination,
    validate_output_dir,
)

COMMUNICATION_PROTOS = (
    "com_struct.proto",
    "amulet_struct.proto",
    "liqi_struct.proto",
    "cli_game.proto",
    "cli_lobby.proto",
    "cli_route.proto",
    "notify_lobby.proto",
    "services.proto",
)
EXCLUDED_PROTOS = frozenset(
    {"client.proto", "com_const.proto", "config.proto", "excel.proto"}
)
PROTO_PREFIX = "protocol/proto/"


@dataclass(frozen=True)
class _Proto:
    package: str
    imports: list[str]
    body: str


@dataclass(frozen=True)
class _Conversion:
    manifest: dict[str, Any]
    original: bytes
    sources: dict[str, str]


def _split_proto(source: str) -> _Proto:
    lines = source.splitlines()
    require(
        bool(lines) and lines[0] == 'syntax = "proto3";',
        "Merge requires generated proto3 sources",
    )
    position = 1
    package = ""
    imports = []
    while position < len(lines):
        line = lines[position]
        if not line:
            position += 1
            continue
        match = re.fullmatch(r"package ([A-Za-z_][\w.]*);", line)
        if match:
            require(not package and not imports, "Invalid proto header")
            package = match[1]
        else:
            match = re.fullmatch(r'import "([A-Za-z_][\w]*\.proto)";', line)
            if not match:
                break
            imports.append(match[1])
        position += 1
    body = "\n".join(lines[position:]).strip("\n")
    require(
        not re.search(r"(?m)^\s*(?:syntax|package|import)\b", body),
        "Unsupported proto header in definitions",
    )
    return _Proto(package, imports, body)


def _symbols(body: str) -> list[str]:
    names = re.findall(r"(?m)^(?:message|enum|service) (\w+) \{", body)
    for values in re.findall(r"(?ms)^enum \w+ \{\n(.*?)^\}", body):
        names.extend(re.findall(r"(?m)^  (\w+) = -?\d+;", values))
    return names


def merge_protocol(sources: dict[str, str]) -> str:
    unknown = sources.keys() - set(COMMUNICATION_PROTOS) - EXCLUDED_PROTOS
    require(not unknown, f"Unsupported proto module: {sorted(unknown)}")
    missing = set(COMMUNICATION_PROTOS) - sources.keys()
    require(not missing, f"Missing communication proto: {sorted(missing)}")
    parts = ['syntax = "proto3";\npackage lq;']
    symbols: set[str] = set()
    for name in COMMUNICATION_PROTOS:
        proto = _split_proto(sources[name])
        require(proto.package == "lq", f"Merge requires package lq: {name}")
        require(
            set(proto.imports) <= set(COMMUNICATION_PROTOS),
            f"Proto imports outside merge: {name}",
        )
        names = _symbols(proto.body)
        require(bool(names), f"Missing generated proto definitions: {name}")
        for symbol in names:
            require(
                symbol not in symbols, f"Duplicate merged symbol: {symbol}"
            )
            symbols.add(symbol)
        parts.append(proto.body)
    merged = "\n\n".join(parts) + "\n"
    require(
        len(merged.encode("utf-8")) <= TEXT_LIMIT,
        "Merged proto size limit exceeded",
    )
    return merged


def validate_merge_destination(
    output_file: Path, *input_dirs: Path, overwrite: bool
) -> Path:
    output = output_file.resolve()
    validate_output_dir(output.parent)
    require(not output.exists() or output.is_file(), "Output must be a file")
    for root in input_dirs:
        protect_input(output, root.resolve())
    validate_destination(output, overwrite=overwrite)
    return output


def _load_conversion(root: Path) -> _Conversion:
    original = read_limited(
        artifact_path(root, "manifest.json"), MANIFEST_LIMIT
    )
    manifest = json.loads(original)
    require(
        isinstance(manifest, dict) and manifest.get("schema_version") == 1,
        "Unsupported conversion manifest schema",
    )
    stages = manifest.get("stages")
    require(
        isinstance(stages, dict)
        and isinstance(stages.get("convert"), dict)
        and stages["convert"].get("status") == "complete",
        "merge-proto requires a complete conversion",
    )
    require(
        isinstance(manifest.get("conversion_id"), str)
        and bool(manifest["conversion_id"]),
        "Missing conversion ID",
    )
    records = file_records(manifest)
    require(
        0 < len(records) <= MAX_EXTRACTED_FILES,
        "Merge input file count limit exceeded",
    )
    require(
        len({name.casefold() for name in records}) == len(records),
        "Duplicate merge input path",
    )
    sources = {}
    total = 0
    for name in sorted(records):
        if not name.startswith(PROTO_PREFIX) or not name.endswith(".proto"):
            continue
        filename = name[len(PROTO_PREFIX) :]
        require("/" not in filename, "Unsupported nested proto module")
        total += records[name]["size"]
        require(total <= TEXT_LIMIT, "Merge input size limit exceeded")
        sources[filename] = verified_file(
            root, name, records, TEXT_LIMIT
        ).decode("utf-8")
    return _Conversion(manifest, original, sources)


def merge_conversion(
    input_dir: Path = Path("converted"),
    *,
    output_file: Path = Path("liqi.proto"),
    overwrite: bool = False,
) -> dict[str, Any]:
    root = input_dir.resolve()
    require(root.is_dir(), "Input conversion must be a directory")
    output = validate_merge_destination(output_file, root, overwrite=overwrite)
    conversion = _load_conversion(root)
    body = merge_protocol(conversion.sources).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix=".partial-merge-", dir=output.parent
    ) as temporary:
        staging = Path(temporary) / "liqi.proto"
        staging.write_bytes(body)
        require(
            read_limited(root / "manifest.json", MANIFEST_LIMIT)
            == conversion.original,
            "Conversion manifest changed during merge",
        )
        with publish(staging, output, overwrite=overwrite):
            pass
    return {
        "input_dir": str(root),
        "output_file": str(output),
        "source_conversion_id": conversion.manifest["conversion_id"],
        "source_manifest_sha256": hashlib.sha256(
            conversion.original
        ).hexdigest(),
        "included_protos": list(COMMUNICATION_PROTOS),
        "excluded_protos": sorted(conversion.sources.keys() & EXCLUDED_PROTOS),
        "unresolved_rpcs": conversion.manifest["stages"]["convert"].get(
            "unresolved_rpcs"
        ),
        "size": len(body),
        "sha256": hashlib.sha256(body).hexdigest(),
    }

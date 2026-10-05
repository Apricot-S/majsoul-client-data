import hashlib
import json
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
    OutputValue,
    artifact_path,
    file_records,
    read_limited,
    verified_file,
)
from .assets import DOCS_VERSION_ASSET, EXCEL_PREFIX, PROTOCOL_PREFIX, RPC_FILE
from .lua import require
from .protocol import convert_protocol
from .storage import (
    protect_input,
    publish,
    validate_destination,
    validate_output_dir,
)
from .tables import convert_tables
from .versions import parse_docs_version

OUTPUT_LIMIT = 256 * 1024 * 1024


@dataclass(frozen=True)
class _Extraction:
    manifest: dict[str, Any]
    original: bytes
    excel: dict[str, str]
    protocol: dict[str, str]
    rpc: dict[str, Any]
    docs_version: str | None


def _load(root: Path) -> _Extraction:
    original = read_limited(
        artifact_path(root, "manifest.json"), MANIFEST_LIMIT
    )
    manifest = json.loads(original)
    require(
        isinstance(manifest, dict) and manifest.get("schema_version") == 1,
        "Unsupported extraction manifest schema",
    )
    stages = manifest.get("stages")
    require(
        isinstance(stages, dict)
        and isinstance(stages.get("extract"), dict)
        and stages["extract"].get("status") == "complete",
        "convert requires a complete extraction",
    )
    for key in ("extraction_id", "source_snapshot_id"):
        require(
            isinstance(manifest.get(key), str) and bool(manifest[key]),
            f"Missing extraction {key}",
        )
    records = file_records(manifest)
    require(
        0 < len(records) <= MAX_EXTRACTED_FILES,
        "Conversion input file count limit exceeded",
    )
    require(
        len({name.casefold() for name in records}) == len(records),
        "Duplicate conversion input path",
    )
    require(
        sum(record["size"] for record in records.values()) <= EXTRACTED_LIMIT,
        "Conversion input size limit exceeded",
    )
    excel = {}
    protocol = {}
    rpc = None
    docs_version = None
    for name in sorted(records):
        body = verified_file(root, name, records, TEXT_LIMIT)
        if name.startswith(EXCEL_PREFIX) and name.endswith(".lua"):
            module = name[len(EXCEL_PREFIX) : -4].replace("/", ".")
            require(module not in excel, "Duplicate Excel module name")
            excel[module] = body.decode("utf-8-sig")
        elif name.startswith(PROTOCOL_PREFIX) and name.endswith(".lua"):
            module = name[len(PROTOCOL_PREFIX) : -4]
            require("/" not in module, "Unsupported nested protocol module")
            protocol["Protol." + module] = body.decode("utf-8-sig")
        elif name == RPC_FILE:
            rpc = json.loads(body)
        elif name == DOCS_VERSION_ASSET:
            docs_version = parse_docs_version(body)
        else:
            msg = f"Unsupported conversion input: {name}"
            raise ValueError(msg)
    if not isinstance(rpc, dict):
        msg = "Missing RPC config"
        raise ValueError(msg)  # noqa: TRY004 - malformed RPC input
    require(
        "docs_version" not in manifest
        or manifest["docs_version"] == docs_version,
        "Extraction docs version does not match verified asset",
    )
    return _Extraction(manifest, original, excel, protocol, rpc, docs_version)


def _output_bytes(value: object) -> bytes:
    if isinstance(value, str):
        return value.encode("utf-8")
    text = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    return (text + "\n").encode("utf-8")


def _write_outputs(
    staging: Path, outputs: dict[str, OutputValue]
) -> list[FileRecord]:
    require(
        len({name.casefold() for name in outputs}) == len(outputs),
        "Duplicate converted output path",
    )
    records: list[FileRecord] = []
    total = 0
    for name, value in sorted(outputs.items()):
        body = _output_bytes(value)
        total += len(body)
        require(total <= OUTPUT_LIMIT, "Converted output size limit exceeded")
        path = artifact_path(staging, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        records.append(
            {
                "name": name,
                "size": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        )
    return records


def convert_extraction(
    input_dir: Path = Path("extracted"),
    *,
    output_dir: Path | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    root = input_dir.resolve()
    require(root.is_dir(), "Input extraction must be a directory")
    extraction = _load(root)
    manifest = extraction.manifest
    tables, index = convert_tables(extraction.excel)
    protocol_outputs, counts = convert_protocol(
        extraction.protocol, extraction.rpc
    )
    outputs: dict[str, OutputValue] = {
        f"tables/{name}.json": rows for name, rows in tables.items()
    }
    outputs["tables/index.json"] = index
    outputs.update(
        {"protocol/" + name: value for name, value in protocol_outputs.items()}
    )
    counts = {
        "tables": len(tables),
        "rows": sum(map(len, tables.values())),
        **counts,
    }
    output = validate_output_dir(
        output_dir if output_dir is not None else Path("converted")
    )
    for protected in (root / "LuaByte", root / "MyAssets"):
        require(
            not output.is_relative_to(protected.resolve()),
            "Output must not be inside extraction input files",
        )
    protect_input(output, root)
    validate_destination(output, overwrite=overwrite)
    observed = datetime.now(UTC)
    conversion_id = observed.strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:12]
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(
        prefix=".partial-convert-", dir=output.parent
    ) as temporary:
        staging = Path(temporary) / "converted"
        staging.mkdir()
        files = _write_outputs(staging, outputs)
        converted_manifest = {
            "schema_version": 1,
            "conversion_id": conversion_id,
            "tool_version": version("majsoul-client-data"),
            "converted_at": observed.isoformat(),
            "source_snapshot_id": manifest["source_snapshot_id"],
            "source_extraction_id": manifest["extraction_id"],
            "source_bundle_hash": manifest.get("source_bundle_hash"),
            "docs_version": extraction.docs_version,
            "source_manifest_sha256": hashlib.sha256(
                extraction.original
            ).hexdigest(),
            "stages": {"convert": {"status": "complete", **counts}},
            "files": files,
        }
        (staging / "manifest.json").write_text(
            json.dumps(converted_manifest, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )
        require(
            (root / "manifest.json").read_bytes() == extraction.original,
            "Extraction manifest changed during conversion",
        )
        with publish(staging, output, overwrite=overwrite):
            pass
    return {
        "input_dir": str(root),
        "converted_dir": str(output),
        "manifest_path": str(output / "manifest.json"),
        "file_count": len(files),
        **counts,
    }

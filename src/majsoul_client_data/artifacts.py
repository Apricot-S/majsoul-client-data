import hashlib
import re
from pathlib import Path
from typing import Any, TypedDict

from .storage import validate_relative_path

MANIFEST_LIMIT = 4 * 1024 * 1024
TEXT_LIMIT = 16 * 1024 * 1024
EXTRACTED_LIMIT = 256 * 1024 * 1024
MAX_EXTRACTED_FILES = 16384

type OutputValue = str | dict[str, Any] | list[Any]


class FileRecord(TypedDict):
    name: str
    size: int
    sha256: str


def artifact_path(root: Path, name: str) -> Path:
    validate_relative_path(name)
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        msg = f"Input path escapes snapshot: {name}"
        raise ValueError(msg)
    return path


def read_limited(path: Path, limit: int) -> bytes:
    if path.stat().st_size > limit:
        msg = f"Input size limit exceeded: {path}"
        raise ValueError(msg)
    with path.open("rb") as stream:
        body = stream.read(limit + 1)
    if len(body) > limit:
        msg = f"Input size limit exceeded: {path}"
        raise ValueError(msg)
    return body


def verified_file(
    root: Path, name: str, records: dict[str, FileRecord], limit: int
) -> bytes:
    if name not in records:
        msg = f"Missing input integrity record: {name}"
        raise ValueError(msg)
    record = records[name]
    body = read_limited(artifact_path(root, name), limit)
    if (
        len(body) != record["size"]
        or hashlib.sha256(body).hexdigest() != record["sha256"]
    ):
        msg = f"Input integrity mismatch: {name}"
        raise ValueError(msg)
    return body


def file_records(manifest: dict) -> dict[str, FileRecord]:
    entries = manifest.get("files")
    if not isinstance(entries, list):
        msg = "Invalid manifest files"
        raise ValueError(msg)  # noqa: TRY004 - malformed manifest

    records: dict[str, FileRecord] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            msg = "Invalid manifest file record"
            raise ValueError(msg)  # noqa: TRY004 - malformed manifest
        name = validate_relative_path(entry.get("name"))
        if name in records:
            msg = f"Duplicate input file: {name}"
            raise ValueError(msg)

        size = entry.get("size")
        if type(size) is not int or size < 0:
            msg = f"Invalid input size: {name}"
            raise ValueError(msg)
        digest = entry.get("sha256")
        if not isinstance(digest, str) or not re.fullmatch(
            r"[0-9a-f]{64}", digest
        ):
            msg = f"Invalid input SHA-256: {name}"
            raise ValueError(msg)
        records[name] = {"name": name, "size": size, "sha256": digest}
    return records

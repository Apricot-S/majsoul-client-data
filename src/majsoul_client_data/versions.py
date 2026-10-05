import json


def parse_docs_version(body: bytes) -> str:
    value = json.loads(body)
    version = value.get("version") if isinstance(value, dict) else None
    if not isinstance(version, str) or not version.strip():
        msg = "Invalid docs version: expected a non-empty version string"
        raise ValueError(msg)
    return version

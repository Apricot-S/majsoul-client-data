import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from majsoul_client_data.cli import main
from majsoul_client_data.merge import merge_conversion, merge_protocol

MODULES = (
    "com_struct",
    "amulet_struct",
    "liqi_struct",
    "cli_game",
    "cli_lobby",
    "cli_route",
    "notify_lobby",
)


def sources() -> dict[str, str]:
    result = {
        f"{name}.proto": (
            'syntax = "proto3";\npackage lq;\n'
            f"\nmessage Type{index} {{\n  string name = 1;\n}}\n"
        )
        for index, name in enumerate(MODULES)
    }
    result["cli_lobby.proto"] = (
        'syntax = "proto3";\npackage lq;\n'
        'import "amulet_struct.proto";\nimport "com_struct.proto";\n'
        "\nmessage Request {\n  .lq.Type1 value = 1;\n"
        "  message Nested {\n    repeated bytes data = 3;\n  }\n}\n"
    )
    result["services.proto"] = (
        'syntax = "proto3";\npackage lq;\nimport "cli_lobby.proto";\n'
        "\nservice Lobby {\n"
        "  rpc login (.lq.Request) returns (.lq.Type0);\n}\n"
    )
    result["config.proto"] = (
        'syntax = "proto3";\npackage lq.config;\nmessage Config {}\n'
    )
    result["excel.proto"] = (
        'syntax = "proto3";\npackage lqc;\nmessage Sheet {}\n'
    )
    result["client.proto"] = 'syntax = "proto3";\n'
    result["com_const.proto"] = (
        'syntax = "proto3";\npackage lq;\nenum EnErrorCode {\n  OK = 0;\n}\n'
    )
    return result


def conversion(root: Path) -> Path:
    root.mkdir()
    records = []
    for name, source in sources().items():
        body = source.encode()
        path = root / "protocol" / "proto" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        records.append(
            {
                "name": f"protocol/proto/{name}",
                "size": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "conversion_id": "conversion-one",
                "stages": {
                    "convert": {"status": "complete", "unresolved_rpcs": 2}
                },
                "files": records,
            }
        ),
        encoding="utf-8",
    )
    return root


def test_merge_keeps_definitions_and_removes_only_headers() -> None:
    inputs = sources()
    merged = merge_protocol(inputs)
    assert merged.startswith('syntax = "proto3";\npackage lq;\n')
    assert merged.count('syntax = "proto3";') == 1
    assert merged.count("package lq;") == 1
    assert "import " not in merged
    assert ".lq.Type1 value = 1;" in merged
    assert "message Nested {\n    repeated bytes data = 3;" in merged
    assert "rpc login (.lq.Request) returns (.lq.Type0);" in merged
    assert "Config" not in merged
    assert "Sheet" not in merged
    assert "EnErrorCode" not in merged
    assert "message Type6" in merged
    assert merged == merge_protocol(dict(reversed(list(inputs.items()))))


def test_merge_preserves_enums_aliases_and_nested_enum_values() -> None:
    inputs = sources()
    enum = (
        "enum State {\n  option allow_alias = true;\n"
        "  ZERO = 0;\n  ALSO_ZERO = 0;\n}\n"
    )
    nested = (
        "message Container {\n  enum NestedState {\n"
        "    ZERO = 0;\n  }\n  .lq.State state = 2;\n}\n"
    )
    inputs["com_struct.proto"] += enum + nested
    merged = merge_protocol(inputs)
    assert enum in merged
    assert nested in merged


def test_merge_rejects_enum_value_collisions_between_files() -> None:
    inputs = sources()
    inputs["com_struct.proto"] += "enum First {\n  ZERO = 0;\n}\n"
    inputs["cli_game.proto"] += "enum Second {\n  ZERO = 0;\n}\n"
    with pytest.raises(ValueError, match="Duplicate merged symbol: ZERO"):
        merge_protocol(inputs)


@pytest.mark.parametrize(
    ("damage", "error"),
    [
        ("missing", "Missing communication proto"),
        ("package", "package lq"),
        ("syntax", "proto3"),
        ("import", "outside merge"),
        ("duplicate", "Duplicate merged symbol"),
        ("new-module", "Unsupported proto module"),
    ],
)
def test_merge_rejects_inputs_that_cannot_be_safely_concatenated(
    damage: str, error: str
) -> None:
    inputs = sources()
    if damage == "missing":
        del inputs["notify_lobby.proto"]
    elif damage == "package":
        inputs["cli_game.proto"] = inputs["cli_game.proto"].replace(
            "package lq;", "package other;"
        )
    elif damage == "syntax":
        inputs["cli_game.proto"] = inputs["cli_game.proto"].replace(
            '"proto3"', '"proto2"'
        )
    elif damage == "import":
        inputs["cli_game.proto"] = inputs["cli_game.proto"].replace(
            "package lq;", 'package lq;\nimport "config.proto";'
        )
    elif damage == "duplicate":
        inputs["cli_game.proto"] = inputs["cli_game.proto"].replace(
            "Type3", "Type0"
        )
    else:
        inputs["future.proto"] = 'syntax = "proto3";\npackage lq;\n'
    with pytest.raises(ValueError, match=error):
        merge_protocol(inputs)


def test_merge_conversion_defaults_preserves_inputs_and_reports_integrity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    root = conversion(tmp_path / "converted")
    original = (root / "manifest.json").read_bytes()
    result = merge_conversion()
    body = (tmp_path / "liqi.proto").read_bytes()
    assert result["output_file"] == str(tmp_path / "liqi.proto")
    assert result["sha256"] == hashlib.sha256(body).hexdigest()
    assert result["size"] == len(body)
    assert result["source_conversion_id"] == "conversion-one"
    assert (
        result["source_manifest_sha256"]
        == hashlib.sha256(original).hexdigest()
    )
    assert result["unresolved_rpcs"] == 2
    assert len(result["included_protos"]) == 8
    assert (root / "manifest.json").read_bytes() == original


def test_merge_refuses_existing_file_and_overwrites_only_when_requested(
    tmp_path: Path,
) -> None:
    root = conversion(tmp_path / "converted")
    output = tmp_path / "liqi.proto"
    output.write_bytes(b"old")
    with pytest.raises(ValueError, match="already exists"):
        merge_conversion(root, output_file=output)
    assert output.read_bytes() == b"old"
    merge_conversion(root, output_file=output, overwrite=True)
    assert output.read_text().startswith('syntax = "proto3";')


@pytest.mark.parametrize("damage", ["content", "missing", "stage"])
def test_merge_rejects_damaged_conversion_before_replacing_output(
    tmp_path: Path, damage: str
) -> None:
    root = conversion(tmp_path / "converted")
    proto = root / "protocol/proto/cli_lobby.proto"
    if damage == "content":
        proto.write_bytes(b"tampered")
    elif damage == "missing":
        proto.unlink()
    else:
        manifest = root / "manifest.json"
        data = json.loads(manifest.read_bytes())
        data["stages"]["convert"]["status"] = "failed"
        manifest.write_text(json.dumps(data), encoding="utf-8")
    output = tmp_path / "liqi.proto"
    output.write_bytes(b"old")
    with pytest.raises((ValueError, OSError)):
        merge_conversion(root, output_file=output, overwrite=True)
    assert output.read_bytes() == b"old"


def test_failed_publish_restores_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = conversion(tmp_path / "converted")
    output = tmp_path / "liqi.proto"
    output.write_bytes(b"old")
    rename = Path.rename

    def fail_publish(path: Path, target: Path) -> Path:
        if path.name == "liqi.proto" and path.parent.name.startswith(
            ".partial-merge-"
        ):
            msg = "publish failed"
            raise OSError(msg)
        return rename(path, target)

    monkeypatch.setattr(Path, "rename", fail_publish)
    with pytest.raises(OSError, match="publish failed"):
        merge_conversion(root, output_file=output, overwrite=True)
    assert output.read_bytes() == b"old"
    assert not list(tmp_path.glob(".partial-*"))


def test_merge_refuses_output_inside_conversion(
    tmp_path: Path,
) -> None:
    root = conversion(tmp_path / "converted")
    with pytest.raises(ValueError, match="overlap"):
        merge_conversion(root, output_file=root / "liqi.proto")


def test_merge_refuses_directory_as_output_even_with_overwrite(
    tmp_path: Path,
) -> None:
    root = conversion(tmp_path / "converted")
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(ValueError, match="must be a file"):
        merge_conversion(root, output_file=output, overwrite=True)


def test_merge_enforces_total_input_size_before_creating_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = conversion(tmp_path / "converted")
    monkeypatch.setattr("majsoul_client_data.merge.TEXT_LIMIT", 50)
    output = tmp_path / "liqi.proto"
    with pytest.raises(ValueError, match="Merge input size limit exceeded"):
        merge_conversion(root, output_file=output)
    assert not output.exists()


def test_merge_aborts_when_input_manifest_changes_before_publish(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = conversion(tmp_path / "converted")
    output = tmp_path / "liqi.proto"
    output.write_bytes(b"old")
    manifest = root / "manifest.json"
    original_merge = merge_protocol

    def change_manifest(inputs: dict[str, str]) -> str:
        manifest.write_bytes(manifest.read_bytes() + b"\n")
        return original_merge(inputs)

    monkeypatch.setattr(
        "majsoul_client_data.merge.merge_protocol", change_manifest
    )
    with pytest.raises(ValueError, match="manifest changed"):
        merge_conversion(root, output_file=output, overwrite=True)
    assert output.read_bytes() == b"old"
    assert not list(tmp_path.glob(".partial-*"))


def test_merge_cli_is_offline_and_accepts_custom_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = conversion(tmp_path / "converted")
    output = tmp_path / "export" / "liqi.proto"
    factory = Mock()
    monkeypatch.setattr("majsoul_client_data.cli.MetadataHTTPClient", factory)
    assert (
        main(
            ["merge-proto", "--input-dir", str(root), "--output", str(output)]
        )
        == 0
    )
    assert output.is_file()
    factory.assert_not_called()

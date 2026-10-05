import hashlib
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from majsoul_client_data.cli import main
from majsoul_client_data.convert import _write_outputs, convert_extraction

pytestmark = pytest.mark.usefixtures("working_directory")


def test_conversion_records_version_from_verified_json(tmp_path: Path) -> None:
    root = extracted_input(tmp_path / "input")
    name = "MyAssets/docs_version/version.json"
    body = b'{"version":"0.16.283"}'
    path = root / name
    path.parent.mkdir(parents=True)
    path.write_bytes(body)
    manifest_path = root / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    manifest["files"].append(
        {
            "name": name,
            "size": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
        }
    )
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    result = convert_extraction(root)
    converted = json.loads(Path(result["manifest_path"]).read_bytes())
    assert converted["docs_version"] == "0.16.283"
    assert "product_version" not in converted


def extracted_input(root: Path) -> Path:
    root.mkdir()
    files = {
        "LuaByte/Lua/Excels/All.lua": (
            'local a={{TableName="a",SheetName="b"}}'
        ),
        "LuaByte/Lua/Excels/Data/a/b.lua": (
            "local b=a.Pack;local f={id=1}local g={1}local h=function(i,j"
            ")return d(i,j,f,g)end;k[1]=b({false},h)"
        ),
        "LuaByte/Lua/Protol/demo_pb.lua": (
            'local a=require"protobuf.protobuf"M=a.Descriptor()M.name='
            '"Request"M.full_name=".lq.Request"M.fields={}M.nested_types='
            "{}M.enum_types={}"
        ),
        "MyAssets/docs/proto_config.bytes": json.dumps(
            {
                "service": {
                    "Demo": {
                        "get": {"request": "Request", "response": "Request"}
                    }
                }
            }
        ),
    }
    records = []
    for name, source in files.items():
        body = source.encode("utf-8")
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        records.append(
            {
                "name": name,
                "size": len(body),
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        )
    (root / "manifest.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "extraction_id": "extracted-one",
                "source_snapshot_id": "snapshot-one",
                "source_bundle_hash": "abcd",
                "stages": {"extract": {"status": "complete"}},
                "files": records,
            }
        ),
        encoding="utf-8",
    )
    return root


def test_conversion_verifies_inputs_and_preserves_prior_results(
    tmp_path: Path,
) -> None:
    root = extracted_input(tmp_path / "extracted")
    original = (root / "manifest.json").read_bytes()
    first = convert_extraction(root)
    second = convert_extraction(root, overwrite=True)
    assert first["converted_dir"] == second["converted_dir"]
    output = Path(first["converted_dir"])
    assert json.loads((output / "tables/a/b.json").read_bytes()) == [{"id": 1}]
    manifest = json.loads((output / "manifest.json").read_bytes())
    assert manifest["source_extraction_id"] == "extracted-one"
    assert (
        manifest["source_manifest_sha256"]
        == hashlib.sha256(original).hexdigest()
    )
    assert manifest["stages"]["convert"]["status"] == "complete"
    for record in manifest["files"]:
        body = (output / record["name"]).read_bytes()
        assert len(body) == record["size"]
        assert hashlib.sha256(body).hexdigest() == record["sha256"]
    assert (root / "manifest.json").read_bytes() == original


@pytest.mark.parametrize("damage", ["content", "path", "stage", "missing"])
def test_conversion_rejects_invalid_input_before_output(
    tmp_path: Path, damage: str
) -> None:
    root = extracted_input(tmp_path / "extracted")
    manifest = json.loads((root / "manifest.json").read_bytes())
    if damage == "content":
        (root / manifest["files"][0]["name"]).write_bytes(b"broken")
    elif damage == "path":
        manifest["files"][0]["name"] = "../outside"
    elif damage == "stage":
        manifest["stages"]["extract"]["status"] = "partial"
    else:
        (root / manifest["files"][0]["name"]).unlink()
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises((ValueError, OSError)):
        convert_extraction(root, output_dir=tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_conversion_failure_does_not_publish_partial_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = extracted_input(tmp_path / "extracted")
    successful = convert_extraction(root)
    original = (root / "manifest.json").read_bytes()
    monkeypatch.setattr(
        "majsoul_client_data.convert._write_outputs",
        Mock(side_effect=OSError("disk full")),
    )
    with pytest.raises(OSError, match="disk full"):
        convert_extraction(root, overwrite=True)
    assert Path(successful["manifest_path"]).is_file()
    assert (root / "manifest.json").read_bytes() == original


def test_convert_cli_does_not_open_a_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = Mock()
    operation = Mock(return_value={"tables": 1})
    monkeypatch.setattr("majsoul_client_data.cli.MetadataHTTPClient", factory)
    monkeypatch.setattr(
        "majsoul_client_data.cli.convert_extraction", operation
    )
    assert (
        main(
            [
                "convert",
                "--input-dir",
                str(tmp_path),
                "--output-dir",
                str(tmp_path / "out"),
            ]
        )
        == 0
    )
    operation.assert_called_once_with(
        tmp_path, output_dir=tmp_path / "out", overwrite=False
    )
    factory.assert_not_called()


def test_conversion_limits_keep_inputs_and_prior_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = extracted_input(tmp_path / "extracted")
    first = convert_extraction(root, output_dir=tmp_path / "out")
    monkeypatch.setattr("majsoul_client_data.convert.OUTPUT_LIMIT", 1)
    with pytest.raises(ValueError, match="output size limit"):
        convert_extraction(root, output_dir=tmp_path / "out", overwrite=True)
    assert Path(first["manifest_path"]).is_file()
    monkeypatch.setattr("majsoul_client_data.convert.EXTRACTED_LIMIT", 1)
    with pytest.raises(ValueError, match="input size limit"):
        convert_extraction(root)
    assert not (root.parent / "converted").exists()


@pytest.mark.parametrize("source_dir", ["LuaByte", "MyAssets"])
def test_conversion_refuses_output_inside_source_files(
    tmp_path: Path, source_dir: str
) -> None:
    root = extracted_input(tmp_path / "extracted")
    with pytest.raises(ValueError, match="inside extraction"):
        convert_extraction(root, output_dir=root / source_dir / "generated")


def test_conversion_does_not_replace_existing_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = extracted_input(tmp_path / "extracted")
    identifier = Mock()
    identifier.hex = "a" * 32
    monkeypatch.setattr(
        "majsoul_client_data.convert.uuid4", lambda: identifier
    )
    clock = Mock()
    clock.now.return_value.strftime.return_value = "fixed-"
    clock.now.return_value.isoformat.return_value = "fixed"
    monkeypatch.setattr("majsoul_client_data.convert.datetime", clock)
    first = convert_extraction(root)
    before = Path(first["manifest_path"]).read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        convert_extraction(root)
    assert Path(first["manifest_path"]).read_bytes() == before


def test_convert_cli_reports_errors_without_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["convert", "--input-dir", str(tmp_path)]) == 1
    assert "convert stopped:" in capsys.readouterr().err


def test_conversion_rejects_colliding_output_names(tmp_path: Path) -> None:

    with pytest.raises(ValueError, match="Duplicate"):
        _write_outputs(
            tmp_path, {"tables/A/b.json": [], "tables/a/b.json": []}
        )

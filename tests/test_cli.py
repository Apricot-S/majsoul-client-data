import json
from pathlib import Path
from unittest.mock import MagicMock, Mock

import niquests
import pytest

from majsoul_client_data.cli import main
from majsoul_client_data.discovery import DEFAULT_CLIENT_URL


def test_cli_prints_json_without_writing_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    client = Mock()
    factory = Mock()
    factory.return_value.__enter__ = Mock(return_value=client)
    factory.return_value.__exit__ = Mock(return_value=False)
    monkeypatch.setattr("majsoul_client_data.cli.MetadataHTTPClient", factory)
    discover = Mock(return_value={"requested_urls": [DEFAULT_CLIENT_URL]})
    monkeypatch.setattr("majsoul_client_data.cli.discover", discover)

    assert main(["inspect"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "requested_urls": [DEFAULT_CLIENT_URL]
    }
    discover.assert_called_once_with(client)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "error", [niquests.HTTPError("HTTP 403"), ValueError("bad settings")]
)
def test_cli_reports_error_once_without_traceback_or_success_output(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
) -> None:
    monkeypatch.setattr(
        "majsoul_client_data.cli.MetadataHTTPClient", MagicMock()
    )
    monkeypatch.setattr(
        "majsoul_client_data.cli.discover", Mock(side_effect=error)
    )
    assert main(["inspect"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert str(error) in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    "args",
    [
        ["inspect", "--timeout", "0"],
        ["inspect", "--timeout", "nan"],
        ["inspect", "--timeout", "inf"],
        ["inspect", "--client-settings-url", "https://config.test/jp.json"],
        ["inspect", "--client-url", "https://client.test/"],
        ["inspect", "--texture-profile", "DXT"],
        ["inspect", "--resolve-settings"],
        ["inspect", "--output-dir", "downloads"],
        ["fetch", "--resolve-settings"],
        ["inspect", "--metadata-only"],
        ["fetch", "--with-bundles"],
        ["--download"],
        [],
        ["run", "--metadata-only"],
    ],
)
def test_invalid_options_fail_before_opening_session(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    factory = Mock()
    monkeypatch.setattr("majsoul_client_data.cli.MetadataHTTPClient", factory)
    with pytest.raises(SystemExit) as exc:
        main(args)
    assert exc.value.code == 2
    factory.assert_not_called()


def test_fetch_cli_uses_bundle_request_budget_and_explicit_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    factory = MagicMock()
    operation = Mock(return_value={"snapshot_dir": "downloads/example"})
    monkeypatch.setattr("majsoul_client_data.cli.MetadataHTTPClient", factory)
    monkeypatch.setattr("majsoul_client_data.cli.download_snapshot", operation)
    monkeypatch.chdir(tmp_path)
    assert (
        main(
            [
                "fetch",
                "--client-settings-url",
                "https://config.example.test/jp.json",
            ]
        )
        == 0
    )
    factory.assert_called_once_with(timeout=20, max_requests=68)
    assert operation.call_args.kwargs["texture_profile"] == "DXT"
    assert operation.call_args.kwargs["output_dir"] == tmp_path / "fetched"
    assert operation.call_args.kwargs["client_settings_url"] == (
        "https://config.example.test/jp.json"
    )
    assert json.loads(capsys.readouterr().out)["snapshot_dir"] == (
        "downloads/example"
    )


def test_fetch_accepts_explicit_output_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    factory = MagicMock()
    operation = Mock(return_value={"snapshot_dir": "example"})
    monkeypatch.setattr("majsoul_client_data.cli.MetadataHTTPClient", factory)
    monkeypatch.setattr("majsoul_client_data.cli.download_snapshot", operation)
    output = tmp_path / "custom-data"
    assert (
        main(
            [
                "fetch",
                "--client-settings-url",
                "https://config.test/jp.json",
                "--output-dir",
                str(output),
                "--texture-profile",
                "ASTC",
                "--timeout",
                "10",
            ]
        )
        == 0
    )
    factory.assert_called_once_with(timeout=10, max_requests=68)
    assert operation.call_args.kwargs["output_dir"] == output
    assert operation.call_args.kwargs["texture_profile"] == "ASTC"


def test_fetch_rejects_file_output_before_opening_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "file"
    output.write_text("keep", encoding="utf-8")
    factory = Mock()
    monkeypatch.setattr("majsoul_client_data.cli.MetadataHTTPClient", factory)
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "fetch",
                "--client-settings-url",
                "https://config.test/jp.json",
                "--output-dir",
                str(output),
            ]
        )
    assert exc.value.code == 2
    factory.assert_not_called()
    assert output.read_text(encoding="utf-8") == "keep"


@pytest.mark.usefixtures("working_directory")
def test_fetch_failure_has_no_success_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        "majsoul_client_data.cli.MetadataHTTPClient", MagicMock()
    )
    monkeypatch.setattr(
        "majsoul_client_data.cli.download_snapshot",
        Mock(side_effect=OSError("disk full")),
    )
    assert (
        main(
            [
                "fetch",
                "--client-settings-url",
                "https://config.test/jp.json",
            ]
        )
        == 1
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "fetch" in captured.err
    assert "disk full" in captured.err
    assert "Traceback" not in captured.err


def test_inspect_passes_explicit_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = MagicMock()
    operation = Mock(return_value={"requested_urls": []})
    monkeypatch.setattr("majsoul_client_data.cli.MetadataHTTPClient", factory)
    monkeypatch.setattr("majsoul_client_data.cli.discover", operation)
    assert (
        main(
            [
                "inspect",
                "--timeout",
                "10",
            ]
        )
        == 0
    )
    factory.assert_called_once_with(timeout=10)
    operation.assert_called_once_with(
        factory.return_value.__enter__.return_value,
    )


def test_fetch_uses_default_settings_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    factory = MagicMock()
    operation = Mock(return_value={"snapshot_dir": "downloads/example"})
    monkeypatch.setattr("majsoul_client_data.cli.MetadataHTTPClient", factory)
    monkeypatch.setattr("majsoul_client_data.cli.download_snapshot", operation)
    monkeypatch.chdir(tmp_path)
    assert main(["fetch"]) == 0
    assert operation.call_args.kwargs["client_settings_url"] == (
        "https://appstatic.mahjongsoul.com/v4/jp/clientbundlesettings/jp-release.json"
    )
    assert factory.call_args.kwargs["max_requests"] == 68
    assert operation.call_args.kwargs["metadata_only"] is False


@pytest.mark.usefixtures("working_directory")
def test_metadata_only_cli_uses_four_gets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = MagicMock()
    operation = Mock(return_value={"snapshot_dir": "example"})
    monkeypatch.setattr("majsoul_client_data.cli.MetadataHTTPClient", factory)
    monkeypatch.setattr("majsoul_client_data.cli.download_snapshot", operation)
    assert main(["fetch", "--metadata-only"]) == 0
    factory.assert_called_once_with(timeout=20, max_requests=4)
    assert operation.call_args.kwargs["metadata_only"] is True


def test_extract_cli_uses_local_input_without_session(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    factory = Mock()
    operation = Mock(return_value={"file_count": 2})
    monkeypatch.setattr("majsoul_client_data.cli.MetadataHTTPClient", factory)
    monkeypatch.setattr("majsoul_client_data.cli.extract_snapshot", operation)
    assert main(["extract", "--input-dir", str(tmp_path)]) == 0
    operation.assert_called_once_with(
        tmp_path, output_dir=None, overwrite=False
    )
    factory.assert_not_called()


def test_extract_cli_passes_output_root_and_reports_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    operation = Mock(side_effect=ValueError("Missing TextAsset"))
    monkeypatch.setattr("majsoul_client_data.cli.extract_snapshot", operation)
    assert (
        main(
            [
                "extract",
                "--input-dir",
                str(tmp_path),
                "--output-dir",
                str(tmp_path / "out"),
            ]
        )
        == 1
    )
    operation.assert_called_once_with(
        tmp_path, output_dir=tmp_path / "out", overwrite=False
    )
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Missing TextAsset" in captured.err
    assert "Traceback" not in captured.err


@pytest.mark.parametrize(
    ("command", "operation", "input_dir"),
    [
        ("extract", "extract_snapshot", Path("fetched")),
        ("convert", "convert_extraction", Path("extracted")),
    ],
)
def test_offline_cli_defaults_and_overwrite(
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    operation: str,
    input_dir: Path,
) -> None:
    mock = Mock(return_value={})
    factory = Mock()
    monkeypatch.setattr(f"majsoul_client_data.cli.{operation}", mock)
    monkeypatch.setattr("majsoul_client_data.cli.MetadataHTTPClient", factory)
    assert main([command, "--overwrite"]) == 0
    mock.assert_called_once_with(input_dir, output_dir=None, overwrite=True)
    factory.assert_not_called()


def test_existing_fetch_destination_stops_before_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "fetched").mkdir()
    factory = Mock()
    monkeypatch.setattr("majsoul_client_data.cli.MetadataHTTPClient", factory)
    with pytest.raises(SystemExit):
        main(["fetch"])
    factory.assert_not_called()


@pytest.mark.parametrize("destination", ["fetched", "extracted", "converted"])
def test_run_existing_destination_stops_before_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, destination: str
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / destination).mkdir()
    factory = Mock()
    monkeypatch.setattr("majsoul_client_data.cli.MetadataHTTPClient", factory)
    with pytest.raises(SystemExit) as exc:
        main(["run"])
    assert exc.value.code == 2
    factory.assert_not_called()


def test_run_cli_passes_options_and_aggregates_report(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    factory = MagicMock()
    operation = Mock(return_value={"status": "complete", "stages": {}})
    monkeypatch.setattr("majsoul_client_data.cli.MetadataHTTPClient", factory)
    monkeypatch.setattr("majsoul_client_data.cli.run_pipeline", operation)
    assert (
        main(
            [
                "run",
                "--output-dir",
                "downloads/fetch",
                "--extracted-dir",
                "downloads/extract",
                "--converted-dir",
                "downloads/convert",
                "--overwrite",
                "--timeout",
                "10",
                "--texture-profile",
                "ASTC",
                "--client-settings-url",
                "https://config.test/jp.json",
            ]
        )
        == 0
    )
    factory.assert_called_once_with(timeout=10, max_requests=68)
    operation.assert_called_once_with(
        factory.return_value.__enter__.return_value,
        client_settings_url="https://config.test/jp.json",
        output_dir=tmp_path / "downloads/fetch",
        extracted_dir=tmp_path / "downloads/extract",
        converted_dir=tmp_path / "downloads/convert",
        texture_profile="ASTC",
        overwrite=True,
        merged_proto=None,
    )
    assert json.loads(capsys.readouterr().out)["status"] == "complete"


def test_run_overlap_stops_before_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    factory = Mock()
    monkeypatch.setattr("majsoul_client_data.cli.MetadataHTTPClient", factory)
    with pytest.raises(SystemExit) as exc:
        main(["run", "--output-dir", "extracted/child", "--overwrite"])
    assert exc.value.code == 2
    factory.assert_not_called()


def test_run_failure_has_no_success_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "majsoul_client_data.cli.MetadataHTTPClient", MagicMock()
    )
    monkeypatch.setattr(
        "majsoul_client_data.cli.run_pipeline",
        Mock(side_effect=ValueError("bad input")),
    )
    assert main(["run"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "run stopped: bad input" in captured.err


@pytest.mark.parametrize(
    "arguments", [["--merge-proto"], ["--merge-proto", "export/liqi.proto"]]
)
def test_run_cli_passes_optional_merge_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    operation = Mock(return_value={})
    monkeypatch.setattr(
        "majsoul_client_data.cli.MetadataHTTPClient", MagicMock()
    )
    monkeypatch.setattr("majsoul_client_data.cli.run_pipeline", operation)
    assert main(["run", *arguments]) == 0
    expected = Path(arguments[1] if len(arguments) == 2 else "liqi.proto")
    assert operation.call_args.kwargs["merged_proto"] == expected


@pytest.mark.parametrize(
    "output", ["liqi.proto", "converted/liqi.proto", "fetched"]
)
def test_run_merge_conflicts_stop_before_session(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "liqi.proto").write_bytes(b"user supplied old proto")
    factory = Mock()
    monkeypatch.setattr("majsoul_client_data.cli.MetadataHTTPClient", factory)
    with pytest.raises(SystemExit) as exc:
        main(["run", "--merge-proto", output])
    assert exc.value.code == 2
    factory.assert_not_called()
    assert (tmp_path / "liqi.proto").read_bytes() == b"user supplied old proto"

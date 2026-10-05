from pathlib import Path
from unittest.mock import Mock

import pytest

from majsoul_client_data.pipeline import (
    run_pipeline,
    validate_run_destinations,
)


def test_pipeline_orders_stages_and_passes_outputs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = []

    def fetch(_client: Mock, **_kwargs: object) -> dict[str, object]:
        calls.append("fetch")
        return {"snapshot_dir": str(tmp_path / "fetched"), "manifest": {}}

    def extract(source: Path, **_kwargs: object) -> dict[str, object]:
        assert source == tmp_path / "fetched"
        calls.append("extract")
        return {"extracted_dir": str(tmp_path / "extracted")}

    def convert(source: Path, **_kwargs: object) -> dict[str, object]:
        assert source == tmp_path / "extracted"
        calls.append("convert")
        return {"converted_dir": str(tmp_path / "converted")}

    for name, operation in [
        ("download_snapshot", fetch),
        ("extract_snapshot", extract),
        ("convert_extraction", convert),
    ]:
        monkeypatch.setattr(f"majsoul_client_data.pipeline.{name}", operation)
    monkeypatch.chdir(tmp_path)
    report = run_pipeline(
        Mock(), client_settings_url="https://config.test/jp.json"
    )
    assert calls == ["fetch", "extract", "convert"]
    assert report["status"] == "complete"
    assert set(report["stages"]) == {"fetch", "extract", "convert"}


@pytest.mark.parametrize("stage", ["fetch", "extract", "convert"])
def test_pipeline_stops_on_stage_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stage: str
) -> None:
    monkeypatch.chdir(tmp_path)
    operations = [
        Mock(return_value={"snapshot_dir": "fetched"}),
        Mock(return_value={"extracted_dir": "extracted"}),
        Mock(return_value={}),
    ]
    names = ["fetch", "extract", "convert"]
    merge = Mock()
    monkeypatch.setattr("majsoul_client_data.pipeline.merge_conversion", merge)
    operations[names.index(stage)].side_effect = OSError("disk full")
    for name, operation in zip(
        ["download_snapshot", "extract_snapshot", "convert_extraction"],
        operations,
        strict=True,
    ):
        monkeypatch.setattr(f"majsoul_client_data.pipeline.{name}", operation)
    with pytest.raises(OSError, match="disk full"):
        run_pipeline(
            Mock(),
            client_settings_url="https://config.test/jp.json",
            merged_proto=Path("liqi.proto"),
        )
    for operation in operations[names.index(stage) + 1 :]:
        operation.assert_not_called()
    merge.assert_not_called()


@pytest.mark.parametrize(
    "paths",
    [
        ("same", "same", "other"),
        ("a", "a/child", "c"),
        ("a", "b", "a/child"),
        ("a", "b", "b/child"),
    ],
)
def test_all_output_pairs_must_be_disjoint(
    tmp_path: Path, paths: tuple[str, str, str]
) -> None:
    with pytest.raises(ValueError, match="overlap"):
        validate_run_destinations(
            *(tmp_path / p for p in paths), overwrite=True
        )


def test_pipeline_merges_only_after_conversion_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    calls = []

    def stage(name: str, result: dict[str, str]) -> Mock:
        def operation(*_args: object, **_kwargs: object) -> dict[str, str]:
            calls.append(name)
            return result

        return Mock(side_effect=operation)

    for name, result in [
        ("download_snapshot", {"snapshot_dir": str(tmp_path / "fetched")}),
        ("extract_snapshot", {"extracted_dir": str(tmp_path / "extracted")}),
        ("convert_extraction", {"converted_dir": str(tmp_path / "converted")}),
    ]:
        monkeypatch.setattr(
            f"majsoul_client_data.pipeline.{name}", stage(name, result)
        )
    merged = stage("merge", {"output_file": str(tmp_path / "liqi.proto")})
    monkeypatch.setattr(
        "majsoul_client_data.pipeline.merge_conversion", merged
    )
    report = run_pipeline(Mock(), merged_proto=Path("liqi.proto"))
    assert calls == [
        "download_snapshot",
        "extract_snapshot",
        "convert_extraction",
        "merge",
    ]
    assert report["stages"]["merge-proto"]["output_file"] == str(
        tmp_path / "liqi.proto"
    )
    merged.assert_called_once_with(
        tmp_path / "converted",
        output_file=tmp_path / "liqi.proto",
        overwrite=False,
    )


@pytest.mark.parametrize(
    "output",
    ["fetched/liqi.proto", "extracted/liqi.proto", "converted/liqi.proto"],
)
def test_pipeline_rejects_merge_overlap_before_fetch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, output: str
) -> None:
    monkeypatch.chdir(tmp_path)
    fetch = Mock()
    monkeypatch.setattr(
        "majsoul_client_data.pipeline.download_snapshot", fetch
    )
    with pytest.raises(ValueError, match="overlap"):
        run_pipeline(Mock(), merged_proto=Path(output))
    fetch.assert_not_called()

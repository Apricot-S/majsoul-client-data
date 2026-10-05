from itertools import combinations
from pathlib import Path
from typing import Any

from .convert import convert_extraction
from .discovery import DEFAULT_CLIENT_SETTINGS_URL
from .download import BinaryClient, download_snapshot
from .extract import extract_snapshot
from .merge import merge_conversion, validate_merge_destination
from .storage import protect_input, validate_destination, validate_output_dir


def validate_run_destinations(
    fetched: Path,
    extracted: Path,
    converted: Path,
    *,
    overwrite: bool,
    merged_proto: Path | None = None,
) -> tuple[Path, Path, Path]:
    paths = (
        validate_output_dir(fetched),
        validate_output_dir(extracted),
        validate_output_dir(converted),
    )
    for first, second in combinations(paths, 2):
        protect_input(first, second)
    if merged_proto is not None:
        validate_merge_destination(merged_proto, *paths, overwrite=overwrite)
    for path in paths:
        validate_destination(path, overwrite=overwrite)
    return paths


def run_pipeline(  # noqa: PLR0913
    client: BinaryClient,
    *,
    client_settings_url: str = DEFAULT_CLIENT_SETTINGS_URL,
    output_dir: Path = Path("fetched"),
    extracted_dir: Path = Path("extracted"),
    converted_dir: Path = Path("converted"),
    texture_profile: str = "DXT",
    overwrite: bool = False,
    merged_proto: Path | None = None,
) -> dict[str, Any]:
    fetched, extracted, converted = validate_run_destinations(
        output_dir,
        extracted_dir,
        converted_dir,
        overwrite=overwrite,
        merged_proto=merged_proto,
    )
    fetch_result = download_snapshot(
        client,
        client_settings_url=client_settings_url,
        output_dir=fetched,
        texture_profile=texture_profile,
        metadata_only=False,
        overwrite=overwrite,
    )
    extract_result = extract_snapshot(
        Path(fetch_result["snapshot_dir"]),
        output_dir=extracted,
        overwrite=overwrite,
    )
    convert_result = convert_extraction(
        Path(extract_result["extracted_dir"]),
        output_dir=converted,
        overwrite=overwrite,
    )
    stages = {
        "fetch": fetch_result,
        "extract": extract_result,
        "convert": convert_result,
    }
    if merged_proto is not None:
        stages["merge-proto"] = merge_conversion(
            Path(convert_result["converted_dir"]),
            output_file=merged_proto.resolve(),
            overwrite=overwrite,
        )
    return {"status": "complete", "stages": stages}

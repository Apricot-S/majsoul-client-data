import argparse
import json
import math
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import niquests

from .bundles import MAX_BUNDLES
from .convert import convert_extraction
from .discovery import (
    DEFAULT_CLIENT_SETTINGS_URL,
    TEXTURE_PROFILES,
    discover,
)
from .download import DownloadResult, download_snapshot
from .extract import extract_snapshot
from .http import MetadataHTTPClient
from .merge import merge_conversion
from .parsing import validate_url
from .pipeline import run_pipeline, validate_run_destinations
from .storage import validate_destination, validate_output_dir


def _https_url(value: str) -> str:
    try:
        return validate_url(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _timeout(value: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        msg = "Timeout must be a number"
        raise argparse.ArgumentTypeError(msg) from exc
    if not math.isfinite(number) or number <= 0:
        msg = "Timeout must be positive and finite"
        raise argparse.ArgumentTypeError(msg)
    return number


def _add_common_arguments(
    parser: argparse.ArgumentParser, *, texture_profile: str
) -> None:
    parser.add_argument(
        "--client-settings-url",
        type=_https_url,
        help=(
            "Override the JP settings URL "
            f"(default: {DEFAULT_CLIENT_SETTINGS_URL})."
        ),
    )
    parser.add_argument(
        "--texture-profile", choices=TEXTURE_PROFILES, default=texture_profile
    )
    parser.add_argument("--timeout", type=_timeout, default=20)


def _configure_inspect_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--timeout", type=_timeout, default=20)
    parser.set_defaults(handler=_run_inspect)


def _run_inspect(
    args: argparse.Namespace, _parser: argparse.ArgumentParser
) -> dict[str, Any]:
    with MetadataHTTPClient(timeout=args.timeout) as client:
        return discover(client)


def _configure_fetch_parser(parser: argparse.ArgumentParser) -> None:
    _add_common_arguments(parser, texture_profile="DXT")
    parser.add_argument(
        "--metadata-only",
        action="store_true",
        help="Save only settings, hash and bundle index (four GETs).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("fetched"),
        help="Destination (default: ./fetched).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace the entire existing stage directory after success.",
    )
    parser.set_defaults(handler=_run_fetch)


def _run_fetch(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> DownloadResult:
    try:
        output_dir = validate_output_dir(args.output_dir)
        validate_destination(output_dir, overwrite=args.overwrite)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    with MetadataHTTPClient(
        timeout=args.timeout,
        max_requests=4 if args.metadata_only else 4 + MAX_BUNDLES,
    ) as client:
        return download_snapshot(
            client,
            client_settings_url=args.client_settings_url
            or DEFAULT_CLIENT_SETTINGS_URL,
            output_dir=output_dir,
            texture_profile=args.texture_profile,
            metadata_only=args.metadata_only,
            overwrite=args.overwrite,
        )


def _configure_extract_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("fetched"),
        help="Completed fetch directory (default: ./fetched).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Destination (default: ./extracted).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace the entire existing stage directory after success.",
    )
    parser.set_defaults(handler=_run_extract)


def _run_extract(
    args: argparse.Namespace, _parser: argparse.ArgumentParser
) -> dict[str, Any]:
    return extract_snapshot(
        args.input_dir, output_dir=args.output_dir, overwrite=args.overwrite
    )


def _configure_convert_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("extracted"),
        help="Completed extraction directory (default: ./extracted).",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        help="Destination (default: ./converted).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace the entire existing stage directory after success.",
    )
    parser.set_defaults(handler=_run_convert)


def _run_convert(
    args: argparse.Namespace, _parser: argparse.ArgumentParser
) -> dict[str, Any]:
    return convert_extraction(
        args.input_dir, output_dir=args.output_dir, overwrite=args.overwrite
    )


def _configure_merge_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("converted"),
        help="Completed conversion directory (default: ./converted).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("liqi.proto"),
        help="Destination file (default: ./liqi.proto).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace the existing output file after success.",
    )
    parser.set_defaults(handler=_run_merge)


def _run_merge(
    args: argparse.Namespace, _parser: argparse.ArgumentParser
) -> dict[str, Any]:
    return merge_conversion(
        args.input_dir, output_file=args.output, overwrite=args.overwrite
    )


def _configure_run_parser(parser: argparse.ArgumentParser) -> None:
    _add_common_arguments(parser, texture_profile="DXT")
    for option, default in [
        ("output", "fetched"),
        ("extracted", "extracted"),
        ("converted", "converted"),
    ]:
        parser.add_argument(
            f"--{option}-dir",
            type=Path,
            default=Path(default),
            help=f"Destination (default: ./{default}).",
        )
    parser.add_argument(
        "--merge-proto",
        nargs="?",
        type=Path,
        const=Path("liqi.proto"),
        metavar="OUTPUT",
        help="Also merge communication protos (default output: ./liqi.proto).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing stage outputs after successful completion.",
    )
    parser.set_defaults(handler=_run_all)


def _run_all(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> dict[str, Any]:
    try:
        fetched, extracted, converted = validate_run_destinations(
            args.output_dir,
            args.extracted_dir,
            args.converted_dir,
            overwrite=args.overwrite,
            merged_proto=args.merge_proto,
        )
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    with MetadataHTTPClient(
        timeout=args.timeout, max_requests=4 + MAX_BUNDLES
    ) as client:
        return run_pipeline(
            client,
            client_settings_url=args.client_settings_url
            or DEFAULT_CLIENT_SETTINGS_URL,
            output_dir=fetched,
            extracted_dir=extracted,
            converted_dir=converted,
            texture_profile=args.texture_profile,
            overwrite=args.overwrite,
            merged_proto=args.merge_proto,
        )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inspect JP data or fetch, extract, convert and merge.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    _configure_inspect_parser(
        commands.add_parser("inspect", help="Inspect JP metadata.")
    )
    _configure_fetch_parser(
        commands.add_parser(
            "fetch", help="Save metadata and selected client-data bundles."
        )
    )
    _configure_extract_parser(
        commands.add_parser(
            "extract", help="Extract saved TextAssets and decode Lua offline."
        )
    )
    _configure_convert_parser(
        commands.add_parser(
            "convert", help="Convert saved Lua and RPC data offline."
        )
    )
    _configure_merge_parser(
        commands.add_parser(
            "merge-proto", help="Merge converted communication protos offline."
        )
    )
    _configure_run_parser(
        commands.add_parser(
            "run", help="Fetch, extract and convert JP client data."
        )
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        report = args.handler(args, parser)
    except (niquests.RequestException, ValueError, OSError) as exc:
        print(f"{args.command} stopped: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0

import re
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

_RESERVED = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", re.IGNORECASE
)


def validate_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        msg = "Invalid relative path"
        raise ValueError(msg)

    parts = value.split("/")
    if any(
        not re.fullmatch(r"[A-Za-z0-9_.$@-]+", part)
        or part in {".", ".."}
        or part.endswith(".")
        or _RESERVED.match(part)
        for part in parts
    ):
        msg = f"Unsafe relative path: {value}"
        raise ValueError(msg)
    return value


def validate_output_dir(output_dir: Path) -> Path:
    output = output_dir.resolve()
    for path in (output, *output.parents):
        if path.exists() and not path.is_dir():
            msg = f"Output must be a directory: {path}"
            raise ValueError(msg)
    return output


def validate_destination(destination: Path, *, overwrite: bool) -> None:
    if destination.exists() and not overwrite:
        msg = f"Destination already exists: {destination}; use --overwrite"
        raise ValueError(msg)


def protect_input(destination: Path, source: Path) -> None:
    if destination.is_relative_to(source) or source.is_relative_to(
        destination
    ):
        msg = "Output must not overlap input directory"
        raise ValueError(msg)


@contextmanager
def publish(
    staging: Path, destination: Path, *, overwrite: bool
) -> Generator[None, None, None]:
    validate_destination(destination, overwrite=overwrite)
    with TemporaryDirectory(
        prefix=".partial-backup-", dir=destination.parent
    ) as temporary:
        backup = Path(temporary) / "previous"
        if destination.exists():
            destination.rename(backup)
        try:
            staging.rename(destination)
            try:
                yield
            except BaseException:
                destination.rename(staging)
                raise
        except BaseException:
            if backup.exists():
                backup.rename(destination)
            raise

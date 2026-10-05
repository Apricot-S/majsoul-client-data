from pathlib import Path

import pytest

from majsoul_client_data.storage import publish, validate_destination


def test_existing_destination_requires_overwrite(tmp_path: Path) -> None:
    target = tmp_path / "out"
    target.mkdir()
    with pytest.raises(ValueError, match="already exists"):
        validate_destination(target, overwrite=False)


def test_replacement_removes_stale_files_and_rolls_back_on_failure(
    tmp_path: Path,
) -> None:
    target = tmp_path / "out"
    target.mkdir()
    (target / "old").write_bytes(b"old")
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "new").write_bytes(b"new")
    msg = "manifest failed"
    with (
        pytest.raises(OSError, match="manifest failed"),
        publish(staging, target, overwrite=True),
    ):
        raise OSError(msg)
    assert (target / "old").read_bytes() == b"old"
    assert (staging / "new").read_bytes() == b"new"
    with publish(staging, target, overwrite=True):
        pass
    assert not (target / "old").exists()
    assert (target / "new").read_bytes() == b"new"


def test_publish_rename_failure_restores_previous_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "out"
    target.mkdir()
    (target / "old").write_bytes(b"old")
    staging = tmp_path / "staging"
    staging.mkdir()
    rename = Path.rename

    def fail_staging(path: Path, destination: Path) -> Path:
        if path == staging:
            msg = "publish failed"
            raise OSError(msg)
        return rename(path, destination)

    monkeypatch.setattr(Path, "rename", fail_staging)
    with (
        pytest.raises(OSError, match="publish failed"),
        publish(staging, target, overwrite=True),
    ):
        pass
    assert (target / "old").read_bytes() == b"old"

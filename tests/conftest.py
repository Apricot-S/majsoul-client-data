from pathlib import Path
from typing import NoReturn

import niquests
import pytest


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail(*_args, **_kwargs) -> NoReturn:
        msg = "Tests must not contact Mahjong Soul or any other server"
        raise AssertionError(msg)

    monkeypatch.setattr(niquests.Session, "request", fail)


@pytest.fixture
def working_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

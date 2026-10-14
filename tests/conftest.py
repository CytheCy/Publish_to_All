"""Never access the user's runtime state during tests."""

import sys

import pytest

# Apply during collection too: imported exporter modules may otherwise write .pyc.
sys.dont_write_bytecode = True


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "runtime-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "runtime-state"))

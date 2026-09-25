from pathlib import Path

import pytest

from publish_to_all.config import load_config, runtime_paths
from publish_to_all.errors import ConfigurationError


def test_missing_configuration_allows_offline_use(tmp_path):
    config = load_config(tmp_path / "config.toml")
    assert config.publication_url is None
    with pytest.raises(ConfigurationError, match="publication_url"):
        config.require_substack()


def test_publication_root(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[substack]\npublication_url = "https://stories.example.org/"')
    assert load_config(path).require_substack() == "https://stories.example.org"


@pytest.mark.parametrize("content", [
    "[broken", '[substack]\npublication_url = "http://example.com"',
    '[substack]\npublication_url = "https://user:secret@example.com"',
    '[substack]\npublication_url = "https://example.com/p/post"',
    '[substack]\npublication_url = "https://example.com?token=secret"',
    '[substack]\npublication_url = "https://example.com#fragment"',
    '[substack]\npublication_url = 12', '[substack]\npassword = "secret"',
    'substack = "wrong"', '[subs tack]', '[unknown]',
])
def test_invalid_config(tmp_path, content):
    path = tmp_path / "config.toml"
    path.write_text(content)
    with pytest.raises(ConfigurationError) as error:
        load_config(path)
    assert "secret" not in str(error.value)


def test_runtime_paths_are_external_and_not_created(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "external"))
    paths = runtime_paths(tmp_path / "project")
    assert paths.browser_profile == tmp_path / "external/publish-to-all/browser-profile"
    assert not paths.state.exists()


@pytest.mark.parametrize("location", ["relative", "inside"])
def test_unsafe_state_location(tmp_path, monkeypatch, location):
    monkeypatch.setenv("XDG_STATE_HOME", "relative" if location == "relative" else str(tmp_path))
    with pytest.raises(ConfigurationError):
        runtime_paths(tmp_path)


def test_data_path_override(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    paths = runtime_paths(tmp_path / "project")
    assert paths.database == tmp_path / "data/publish-to-all/publications.sqlite3"
    assert paths.logs != paths.database.parent
    assert not paths.data.exists()


@pytest.mark.parametrize("value", [None, ""])
def test_data_path_defaults(tmp_path, monkeypatch, value):
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")
    if value is None:
        monkeypatch.delenv("XDG_DATA_HOME")
    else:
        monkeypatch.setenv("XDG_DATA_HOME", value)
    assert runtime_paths(tmp_path / "project").database == tmp_path / "home/.local/share/publish-to-all/publications.sqlite3"


@pytest.mark.parametrize("location", ["relative", "inside"])
def test_unsafe_data_location(tmp_path, monkeypatch, location):
    monkeypatch.setenv("XDG_DATA_HOME", "relative" if location == "relative" else str(tmp_path))
    with pytest.raises(ConfigurationError):
        runtime_paths(tmp_path)


def test_database_symlink_into_project_rejected(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    data = tmp_path / "data"
    (data / "publish-to-all").mkdir(parents=True)
    (data / "publish-to-all/publications.sqlite3").symlink_to(project / "database.sqlite3")
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    with pytest.raises(ConfigurationError):
        runtime_paths(project)

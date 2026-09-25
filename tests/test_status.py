import sqlite3

import pytest

from publish_to_all.cli import main
from publish_to_all.config import runtime_paths
from publish_to_all.publishers.base import Publisher
from publish_to_all.state import PublicationRepository
from publish_to_all.story import load_story


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / "project"
    (root / "In").mkdir(parents=True)
    (root / "In/story.md").write_text("---\ntitle: Status story\n---\nBody.")
    monkeypatch.chdir(root)
    return root


def test_unseen_status_creates_only_schema(project, capsys):
    assert main(["status"]) == 0
    output = capsys.readouterr()
    for value in ("Story status", "File: story.md", "Title: Status story", "Hash:",
                  "Substack: Not started", "Nothing was published by this command."):
        assert value in output.out
    assert not output.err
    with sqlite3.connect(runtime_paths(project).database) as connection:
        assert connection.execute("SELECT count(*) FROM stories").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM publications").fetchone()[0] == 0


@pytest.mark.parametrize("state", ["draft", "published", "failed", "in_progress"])
def test_recorded_status(project, capsys, state):
    story = load_story(project / "In")
    repository = PublicationRepository(runtime_paths(project).database)
    record = repository.begin_attempt(story, "substack")
    if state in ("draft", "published"):
        repository.mark_draft_created(record.id, "https://example.com/draft")
    if state == "published":
        repository.mark_published(record.id, "https://example.com/post")
    if state == "failed":
        repository.mark_failed(record.id, "Editor unavailable")
    before = repository.get_publication(story.source_hash, "substack")
    assert main(["status"]) == 0
    output = capsys.readouterr().out
    if state in ("draft", "published"):
        assert "Draft: https://example.com/draft" in output
    if state == "draft":
        assert "Substack: Draft created" in output
    elif state == "published":
        assert "Substack: Published" in output
        assert "Published: https://example.com/post" in output
    elif state == "failed":
        assert "Substack: Failed" in output
        assert "Error: Editor unavailable" in output
    else:
        assert "reconciliation required" in output
    assert repository.get_publication(story.source_hash, "substack") == before
    story.source.write_text("---\ntitle: Status story\n---\nEdited body.")
    assert main(["status"]) == 0
    assert "Substack: Not started" in capsys.readouterr().out


def test_status_database_error_without_traceback(project, capsys):
    path = runtime_paths(project).database
    path.parent.mkdir(parents=True)
    path.write_text("corrupt")
    assert main(["status"]) == 1
    output = capsys.readouterr()
    assert "database" in output.err
    assert "Traceback" not in output.err
    assert not output.out


@pytest.mark.parametrize("command", ["check", "preview"])
def test_readonly_commands_do_not_create_runtime_state(project, command):
    paths = runtime_paths(project)
    assert main([command]) == 0
    assert not paths.data.exists()
    assert not paths.state.exists()


def test_publisher_is_abstract():
    with pytest.raises(TypeError):
        Publisher(None)

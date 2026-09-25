from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
import sqlite3

import pytest

from publish_to_all.state import (
    DuplicatePublicationError, PublicationRepository, PublicationStatus as Status,
    SCHEMA_VERSION, StateError,
)
from publish_to_all.story import load_story


@pytest.fixture
def story(tmp_path):
    source = tmp_path / "story.md"
    source.write_text("---\ntitle: A story\n---\nCanonical body.")
    return load_story(tmp_path)


@pytest.fixture
def repository(tmp_path):
    return PublicationRepository(tmp_path / "data/publications.sqlite3")


def test_initialize_and_reopen(repository):
    with sqlite3.connect(repository.path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 2
        assert connection.execute("SELECT count(*) FROM stories").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM publications").fetchone()[0] == 0
        columns = {row[1] for row in connection.execute("PRAGMA table_info(stories)")}
        assert "markdown" not in columns and "body" not in columns
    PublicationRepository(repository.path)


def test_registration_hash_identity_and_timestamps(repository, story):
    before = datetime.now(timezone.utc)
    first = repository.register_story(story)
    renamed = replace(story, source=story.source.with_name("renamed.md"))
    second = repository.register_story(renamed)
    after = datetime.now(timezone.utc)
    assert first.id == second.id
    assert first.created_at == second.created_at
    assert before <= datetime.fromisoformat(first.created_at) <= datetime.fromisoformat(second.last_seen_at) <= after
    assert repository.get_story(story.source_hash) == second
    assert second.source_filename == "renamed.md"
    changed = repository.register_story(replace(story, source_hash="different"))
    assert changed.id != first.id
    assert repository.get_story("unseen") is None
    assert repository.get_publication(story.source_hash, "substack") is None


def test_full_lifecycle_and_duplicate_protection(repository, story):
    start = datetime.now(timezone.utc)
    attempt = repository.begin_attempt(story, "substack")
    assert attempt.status == Status.DRAFT_CREATING
    assert attempt.created_at == attempt.updated_at == attempt.last_attempt_at
    assert repository.find_duplicate(story.source_hash, "substack") == attempt
    draft = repository.mark_draft_created(attempt.id, "https://example.substack.com/p/draft")
    assert draft.status == Status.DRAFT_CREATED
    with pytest.raises(DuplicatePublicationError, match="https://example.substack.com/p/draft"):
        repository.begin_attempt(story, "substack")
    publishing = repository.mark_publishing(attempt.id)
    assert publishing.status == Status.PUBLISHING
    published = repository.mark_published(attempt.id, "https://example.substack.com/p/post")
    assert published.status == Status.PUBLISHED
    assert published.draft_url == draft.draft_url
    assert published.published_url.endswith("/post")
    assert published.created_at == published.last_attempt_at == attempt.created_at
    assert start <= datetime.fromisoformat(published.created_at) <= datetime.fromisoformat(published.updated_at) <= datetime.now(timezone.utc)
    assert repository.get_publication(story.source_hash, "substack") == published
    assert repository.find_duplicate(story.source_hash, "substack") == published
    with pytest.raises(DuplicatePublicationError):
        repository.begin_attempt(story, "substack")
    assert repository.find_duplicate("edited", "substack") is None
    assert repository.get_publication(story.source_hash, "medium") is None
    edited = repository.begin_attempt(replace(story, source_hash="edited"), "substack")
    assert edited.story_id != attempt.story_id


def test_failed_attempt_retry_keeps_history(repository, story):
    attempt = repository.begin_attempt(story, "substack")
    failed = repository.mark_failed(attempt.id, "Editor unavailable")
    assert failed.status == Status.FAILED
    assert failed.error_message == "Editor unavailable"
    assert repository.get_publication(story.source_hash, "substack") == failed
    assert repository.find_duplicate(story.source_hash, "substack") is None
    retry = repository.begin_attempt(story, "substack")
    assert retry.id != failed.id
    assert retry.story_id == failed.story_id
    with sqlite3.connect(repository.path) as connection:
        assert connection.execute("SELECT status FROM publications ORDER BY id").fetchall() == [("failed",), ("draft_creating",)]


def test_failed_publish_preserves_remote_copy(repository, story):
    attempt = repository.begin_attempt(story, "substack")
    repository.mark_draft_created(attempt.id, "https://example.com/draft")
    repository.mark_publishing(attempt.id)
    failed = repository.mark_failed(attempt.id, "Publishing failed")
    assert failed.draft_url == "https://example.com/draft"
    assert repository.find_duplicate(story.source_hash, "substack") == failed
    with pytest.raises(DuplicatePublicationError):
        repository.begin_attempt(story, "substack")
    recovered = repository.mark_draft_created(attempt.id, failed.draft_url)
    assert recovered.error_message is None


def test_duplicate_checks_all_attempts(repository, story):
    # Reconciliation may discover a remote copy in an earlier failed attempt.
    first = repository.begin_attempt(story, "substack")
    repository.mark_failed(first.id, "Initially believed no draft existed")
    second = repository.begin_attempt(story, "substack")
    repository.mark_failed(second.id, "No draft created")
    repository.mark_draft_created(first.id, "https://example.com/recovered")
    assert repository.find_duplicate(story.source_hash, "substack").id == first.id
    with pytest.raises(DuplicatePublicationError):
        repository.begin_attempt(story, "substack")


def test_concurrent_attempts_are_serialized(repository, story):
    def reserve(_):
        try:
            return repository.begin_attempt(story, "substack")
        except DuplicatePublicationError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, range(2)))
    assert sum(result is not None for result in results) == 1


def test_invalid_transitions_and_missing_records(repository, story):
    with pytest.raises(StateError, match="does not exist"):
        repository.mark_published(100)
    attempt = repository.begin_attempt(story, "substack")
    with pytest.raises(StateError, match="Cannot change"):
        repository.mark_published(attempt.id)
    repository.mark_draft_created(attempt.id)
    repository.mark_published(attempt.id)
    with pytest.raises(StateError):
        repository.mark_failed(attempt.id, "Should not erase success")


def test_newer_schema_rejected_without_changes(tmp_path):
    path = tmp_path / "future.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA user_version = 999")
    with pytest.raises(StateError, match="newer"):
        PublicationRepository(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 999


def test_migration_failure_rolls_back(tmp_path, monkeypatch):
    import publish_to_all.state as state
    path = tmp_path / "migration.sqlite3"
    monkeypatch.setattr(state, "MIGRATIONS", (("CREATE TABLE first (id INTEGER)", "INVALID SQL"),))
    with pytest.raises(StateError):
        PublicationRepository(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
        assert connection.execute("SELECT name FROM sqlite_master WHERE name = 'first'").fetchone() is None


def test_parameterized_values(repository, story):
    malicious = "Robert'); DROP TABLE stories;--"
    altered = replace(story, metadata=replace(story.metadata, title=malicious))
    assert repository.register_story(altered).title == malicious
    record = repository.begin_attempt(altered, "substack")
    assert repository.mark_failed(record.id, malicious).error_message == malicious


@pytest.mark.parametrize("kind", ["corrupt", "directory", "parent_file"])
def test_database_errors(tmp_path, kind):
    path = tmp_path / "state.sqlite3"
    if kind == "corrupt":
        path.write_text("not sqlite")
    elif kind == "directory":
        path.mkdir()
    else:
        path.write_text("file")
        path = path / "nested.sqlite3"
    with pytest.raises(StateError):
        PublicationRepository(path)

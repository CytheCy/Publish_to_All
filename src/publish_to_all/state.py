"""Versioned local publication state. No article bodies or browser secrets."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
import sqlite3

from .errors import PublishToAllError
from .story import Story


class StateError(PublishToAllError):
    """An expected database or publication lifecycle error."""


class DuplicatePublicationError(StateError):
    """This version already has a remote copy or needs reconciliation."""


class PublicationStatus(StrEnum):
    NOT_STARTED = "not_started"
    DRAFT_CREATING = "draft_creating"
    DRAFT_CREATED = "draft_created"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    FAILED = "failed"


@dataclass(frozen=True)
class StoryVersion:
    id: int
    source_filename: str
    title: str
    source_hash: str
    created_at: str
    last_seen_at: str


@dataclass(frozen=True)
class PublicationRecord:
    id: int
    story_id: int
    destination: str
    status: PublicationStatus
    draft_url: str | None
    published_url: str | None
    created_at: str
    updated_at: str
    last_attempt_at: str
    error_message: str | None
    needs_reconciliation: bool = False


# Each entry migrates the preceding version. Execute individually inside one
# transaction (executescript would implicitly commit a pending transaction).
MIGRATIONS = (
    (
        """CREATE TABLE stories (
            id INTEGER PRIMARY KEY,
            source_filename TEXT NOT NULL,
            title TEXT NOT NULL,
            source_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL
        )""",
        """CREATE TABLE publications (
            id INTEGER PRIMARY KEY,
            story_id INTEGER NOT NULL REFERENCES stories(id),
            destination TEXT NOT NULL CHECK(length(destination) > 0),
            status TEXT NOT NULL CHECK(status IN
                ('not_started', 'draft_creating', 'draft_created', 'publishing', 'published', 'failed')),
            draft_url TEXT,
            published_url TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_attempt_at TEXT NOT NULL,
            error_message TEXT
        )""",
        "CREATE INDEX publications_story_destination ON publications(story_id, destination, id)",
    ),
    ("ALTER TABLE publications ADD COLUMN needs_reconciliation INTEGER NOT NULL DEFAULT 0 CHECK(needs_reconciliation IN (0, 1))",),
)
SCHEMA_VERSION = len(MIGRATIONS)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class PublicationRepository:
    """One row per attempt; hash identity spans renames and all destinations.

    Connections are short lived. BEGIN IMMEDIATE serializes the duplicate check
    and attempt insertion across processes before any future remote side effect.
    """

    def __init__(self, path: Path):
        self.path = path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise StateError(f"Cannot create database directory: {path.parent}") from exc
        with self._connection(write=True) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if version > SCHEMA_VERSION:
                raise StateError(f"Database schema {version} is newer than supported schema {SCHEMA_VERSION}.")
            for index in range(version, SCHEMA_VERSION):
                for statement in MIGRATIONS[index]:
                    connection.execute(statement)
                connection.execute(f"PRAGMA user_version = {index + 1}")

    @contextmanager
    def _connection(self, *, write=False):
        connection = None
        try:
            connection = sqlite3.connect(self.path, timeout=5)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            if write:
                connection.execute("BEGIN IMMEDIATE")
            with connection:
                yield connection
        except sqlite3.Error as exc:
            raise StateError(f"Cannot access publication database at {self.path}: {exc}") from exc
        finally:
            if connection is not None:
                connection.close()

    @staticmethod
    def _register(connection, story: Story) -> StoryVersion:
        now = _now()
        connection.execute(
            """INSERT INTO stories (source_filename, title, source_hash, created_at, last_seen_at)
               VALUES (?, ?, ?, ?, ?) ON CONFLICT(source_hash) DO UPDATE SET
               source_filename = excluded.source_filename, title = excluded.title,
               last_seen_at = excluded.last_seen_at""",
            (story.source.name, story.metadata.title, story.source_hash, now, now),
        )
        return StoryVersion(**dict(connection.execute(
            "SELECT * FROM stories WHERE source_hash = ?", (story.source_hash,)
        ).fetchone()))

    def register_story(self, story: Story) -> StoryVersion:
        with self._connection(write=True) as connection:
            return self._register(connection, story)

    def get_story(self, source_hash: str) -> StoryVersion | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM stories WHERE source_hash = ?", (source_hash,)).fetchone()
            return StoryVersion(**dict(row)) if row else None

    @staticmethod
    def _record(row) -> PublicationRecord | None:
        if row is None:
            return None
        values = dict(row)
        values["status"] = PublicationStatus(values["status"])
        return PublicationRecord(**values)

    @classmethod
    def _lookup(cls, connection, source_hash, destination, *, blocking=False):
        sql = """SELECT p.* FROM publications p JOIN stories s ON s.id = p.story_id
                 WHERE s.source_hash = ? AND p.destination = ?"""
        parameters = [source_hash, destination]
        if blocking:
            sql += """ AND (p.status IN (?, ?, ?, ?) OR p.draft_url IS NOT NULL
                       OR p.published_url IS NOT NULL OR p.needs_reconciliation = 1)"""
            parameters += [PublicationStatus.DRAFT_CREATING, PublicationStatus.DRAFT_CREATED,
                           PublicationStatus.PUBLISHING, PublicationStatus.PUBLISHED]
        sql += " ORDER BY p.id DESC LIMIT 1"
        return cls._record(connection.execute(sql, parameters).fetchone())

    def get_publication(self, source_hash: str, destination: str) -> PublicationRecord | None:
        """Return the latest attempt for exactly this hash and destination."""
        with self._connection() as connection:
            return self._lookup(connection, source_hash, destination)

    def find_duplicate(self, source_hash: str, destination: str) -> PublicationRecord | None:
        """Return any known remote copy or unresolved attempt, across all history."""
        with self._connection() as connection:
            return self._lookup(connection, source_hash, destination, blocking=True)

    @staticmethod
    def refuse_duplicate(story: Story, duplicate: PublicationRecord) -> None:
        lines = [f"This exact version already has a Substack record ({duplicate.status.value}).",
                 f"Title: {story.metadata.title}"]
        if duplicate.draft_url:
            lines.append(f"Draft: {duplicate.draft_url}")
        if duplicate.published_url:
            lines.append(f"Published: {duplicate.published_url}")
        lines += ["Reconcile unresolved attempts before retrying.", "No new draft was created."]
        raise DuplicatePublicationError("\n".join(lines))

    def begin_attempt(self, story: Story, destination: str) -> PublicationRecord:
        """Reserve an attempt atomically, refusing existing or uncertain copies."""
        if not destination or destination != destination.strip().lower():
            raise StateError("Destination must be a non-empty lowercase provider name.")
        with self._connection(write=True) as connection:
            duplicate = self._lookup(connection, story.source_hash, destination, blocking=True)
            if duplicate:
                provider = "Substack" if destination == "substack" else destination
                lines = [f"This exact version of the story already has a {provider} record ({duplicate.status.value}).",
                         f"Title: {story.metadata.title}"]
                if duplicate.draft_url:
                    lines.append(f"Draft: {duplicate.draft_url}")
                if duplicate.published_url:
                    lines.append(f"Published: {duplicate.published_url}")
                lines.append("Reconcile unresolved attempts before retrying. Intentional duplicate copies are not supported yet.")
                lines.append("No new draft was created.")
                raise DuplicatePublicationError("\n".join(lines))
            version = self._register(connection, story)
            now = _now()
            cursor = connection.execute(
                """INSERT INTO publications (story_id, destination, status, created_at, updated_at, last_attempt_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (version.id, destination, PublicationStatus.DRAFT_CREATING, now, now, now),
            )
            return self._record(connection.execute("SELECT * FROM publications WHERE id = ?", (cursor.lastrowid,)).fetchone())

    def _transition(self, record_id, status, allowed, *, draft_url=None, published_url=None, error=None,
                    needs_reconciliation=False):
        with self._connection(write=True) as connection:
            record = self._record(connection.execute("SELECT * FROM publications WHERE id = ?", (record_id,)).fetchone())
            if record is None:
                raise StateError(f"Publication attempt {record_id} does not exist.")
            if record.status not in allowed:
                raise StateError(f"Cannot change {record.status.value} to {status.value}.")
            connection.execute(
                """UPDATE publications SET status = ?, updated_at = ?,
                   draft_url = COALESCE(?, draft_url), published_url = COALESCE(?, published_url),
                   error_message = ?, needs_reconciliation = ? WHERE id = ?""",
                (status, _now(), draft_url, published_url, error, needs_reconciliation, record_id),
            )
            return self._record(connection.execute("SELECT * FROM publications WHERE id = ?", (record_id,)).fetchone())

    def mark_draft_created(self, record_id: int, draft_url: str | None = None) -> PublicationRecord:
        return self._transition(record_id, PublicationStatus.DRAFT_CREATED,
                                {PublicationStatus.DRAFT_CREATING, PublicationStatus.FAILED}, draft_url=draft_url)

    def mark_publishing(self, record_id: int) -> PublicationRecord:
        return self._transition(record_id, PublicationStatus.PUBLISHING, {PublicationStatus.DRAFT_CREATED})

    def mark_published(self, record_id: int, published_url: str | None = None) -> PublicationRecord:
        return self._transition(record_id, PublicationStatus.PUBLISHED,
                                {PublicationStatus.DRAFT_CREATED, PublicationStatus.PUBLISHING, PublicationStatus.FAILED},
                                published_url=published_url)

    def mark_failed(self, record_id: int, error_message: str, *, draft_url: str | None = None,
                    needs_reconciliation: bool = False) -> PublicationRecord:
        """Retain known URLs; block retries when a remote outcome is uncertain.

        A reconciled failed attempt without URLs is eligible for a fresh retry.
        Callers must pass a sanitized error, never credentials or page contents.
        """
        return self._transition(record_id, PublicationStatus.FAILED,
                                {PublicationStatus.DRAFT_CREATING, PublicationStatus.PUBLISHING}, error=error_message,
                                draft_url=draft_url, needs_reconciliation=needs_reconciliation)

    def reconcile_failed_draft(
        self, expected: PublicationRecord, draft_url: str, *, verified_manual_url: bool = False,
    ) -> PublicationRecord:
        """Compare-and-set: preserve failure history and keep duplicate creation blocked.

        A manually supplied URL may replace earlier uncertain URL evidence only
        after the caller has conservatively verified that exact editor page.
        """
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                "SELECT * FROM publications WHERE id = ?", (expected.id,)
            ).fetchone())
            if (current != expected or current.status != PublicationStatus.FAILED
                    or not current.needs_reconciliation or current.published_url
                    or (not verified_manual_url and current.draft_url is not None
                        and current.draft_url.rstrip('/') != draft_url)):
                raise StateError('Reconciliation evidence is stale or insufficient. State unchanged.')
            connection.execute(
                """UPDATE publications SET status = ?, draft_url = ?, needs_reconciliation = 0,
                   updated_at = ? WHERE id = ?""",
                (PublicationStatus.DRAFT_CREATED, draft_url, _now(), current.id),
            )
            return self._record(connection.execute(
                "SELECT * FROM publications WHERE id = ?", (current.id,)
            ).fetchone())

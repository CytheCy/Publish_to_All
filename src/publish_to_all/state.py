"""Versioned local publication state. No article bodies or browser secrets."""

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

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


class BodyStatus(StrEnum):
    NOT_STARTED = "not_started"
    INSERTING = "body_inserting"
    INSERTED = "body_inserted"
    FAILED = "body_insertion_failed"


class ImageStatus(StrEnum):
    NOT_STARTED = "image_not_started"
    UPLOADING = "image_uploading"
    UPLOADED = "image_uploaded"
    FAILED = "image_upload_failed"


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
    body_status: BodyStatus = BodyStatus.NOT_STARTED
    body_started_at: str | None = None
    body_inserted_at: str | None = None
    body_error_message: str | None = None
    image_status: ImageStatus = ImageStatus.NOT_STARTED
    image_started_at: str | None = None
    image_uploaded_at: str | None = None
    image_error_message: str | None = None


@dataclass(frozen=True)
class PublicationReassociation:
    publication: PublicationRecord
    from_story: StoryVersion
    to_story: StoryVersion


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
    (
        "ALTER TABLE publications ADD COLUMN body_status TEXT NOT NULL DEFAULT 'not_started' CHECK(body_status IN ('not_started', 'body_inserting', 'body_inserted', 'body_insertion_failed'))",
        "ALTER TABLE publications ADD COLUMN body_started_at TEXT",
        "ALTER TABLE publications ADD COLUMN body_inserted_at TEXT",
        "ALTER TABLE publications ADD COLUMN body_error_message TEXT",
    ),
    (
        """CREATE TABLE publication_reassociations (
            id INTEGER PRIMARY KEY,
            publication_id INTEGER NOT NULL REFERENCES publications(id),
            from_story_id INTEGER NOT NULL REFERENCES stories(id),
            to_story_id INTEGER NOT NULL REFERENCES stories(id),
            destination TEXT NOT NULL,
            draft_url TEXT NOT NULL,
            reassociated_at TEXT NOT NULL,
            CHECK(from_story_id != to_story_id)
        )""",
        "CREATE INDEX publication_reassociations_publication ON publication_reassociations(publication_id, id)",
    ),
    (
        "ALTER TABLE publications ADD COLUMN image_status TEXT NOT NULL DEFAULT 'image_not_started' CHECK(image_status IN ('image_not_started', 'image_uploading', 'image_uploaded', 'image_upload_failed'))",
        "ALTER TABLE publications ADD COLUMN image_started_at TEXT",
        "ALTER TABLE publications ADD COLUMN image_uploaded_at TEXT",
        "ALTER TABLE publications ADD COLUMN image_error_message TEXT",
    ),
)
SCHEMA_VERSION = len(MIGRATIONS)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _is_numeric_draft_url(value: str | None) -> bool:
    if not value or any(character.isspace() for character in value):
        return False
    try:
        url = urlsplit(value)
        valid_authority = bool(
            url.hostname and url.username is None and url.password is None and url.port is None
        )
    except ValueError:
        return False
    parts = url.path.rstrip('/').split('/')
    return bool(
        url.scheme == 'https' and valid_authority and not url.query and not url.fragment
        and len(parts) == 4 and parts[1:3] == ['publish', 'post'] and parts[3].isdigit()
    )


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
        if "body_status" in values:
            values["body_status"] = BodyStatus(values["body_status"])
        if "image_status" in values:
            values["image_status"] = ImageStatus(values["image_status"])
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
        """Compare-and-set: clear the resolved error and keep duplicate creation blocked.

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
                """UPDATE publications SET status = ?, draft_url = ?, error_message = NULL,
                   needs_reconciliation = 0, updated_at = ? WHERE id = ?""",
                (PublicationStatus.DRAFT_CREATED, draft_url, _now(), current.id),
            )
            return self._record(connection.execute(
                "SELECT * FROM publications WHERE id = ?", (current.id,)
            ).fetchone())

    def replace_linked_draft(
        self, source_hash: str, destination: str, expected: PublicationRecord, draft_url: str,
    ) -> PublicationRecord:
        """Replace one verified local draft association with compare-and-set safety."""
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                """SELECT p.* FROM publications p JOIN stories s ON s.id = p.story_id
                   WHERE p.id = ? AND s.source_hash = ? AND p.destination = ?""",
                (expected.id, source_hash, destination),
            ).fetchone())
            if (current != expected or current.status != PublicationStatus.DRAFT_CREATED
                    or current.published_url is not None or current.draft_url is None):
                raise StateError('Draft reassociation evidence is stale or insufficient. State unchanged.')
            if current.draft_url.rstrip('/') == draft_url.rstrip('/'):
                return current
            connection.execute(
                "UPDATE publications SET draft_url = ?, updated_at = ? WHERE id = ?",
                (draft_url, _now(), current.id),
            )
            return self._record(connection.execute(
                "SELECT * FROM publications WHERE id = ?", (current.id,)
            ).fetchone())

    def reassociate_draft_version(
        self, from_hash: str, to_story: Story, destination: str,
    ) -> PublicationReassociation:
        """Atomically transfer one untouched draft to a corrected story version.

        The publication row remains unique and an audit row records its prior
        owner. This operation is deliberately local and performs no browser or
        provider calls.
        """
        if not from_hash:
            raise StateError('The source story hash must be supplied explicitly. State unchanged.')
        if from_hash == to_story.source_hash:
            raise StateError('The source and current story hashes are identical. State unchanged.')
        if not destination or destination != destination.strip().lower():
            raise StateError('Destination must be a non-empty lowercase provider name.')

        with self._connection(write=True) as connection:
            old_row = connection.execute(
                'SELECT * FROM stories WHERE source_hash = ?', (from_hash,)
            ).fetchone()
            if old_row is None:
                raise StateError('The source story hash is not recorded. State unchanged.')
            old_story = StoryVersion(**dict(old_row))
            old_rows = connection.execute(
                '''SELECT p.* FROM publications p
                   WHERE p.story_id = ? AND p.destination = ? ORDER BY p.id''',
                (old_story.id, destination),
            ).fetchall()
            if len(old_rows) != 1:
                raise StateError(
                    f'The source story version must have exactly one {destination} publication record; '
                    f'found {len(old_rows)}. State unchanged.'
                )
            record = self._record(old_rows[0])
            if record.status != PublicationStatus.DRAFT_CREATED:
                raise StateError(
                    f'The source publication must be draft_created, not {record.status.value}. State unchanged.'
                )
            if record.published_url is not None:
                raise StateError('The source publication is already published. State unchanged.')
            if record.needs_reconciliation:
                raise StateError('The source publication requires reconciliation. State unchanged.')
            if record.body_status != BodyStatus.NOT_STARTED:
                raise StateError('The source publication body is not untouched. State unchanged.')
            if record.body_started_at or record.body_inserted_at or record.body_error_message:
                raise StateError('Local state indicates that body insertion may have started. State unchanged.')
            if record.error_message:
                raise StateError('The source publication has an unresolved error. State unchanged.')
            if not _is_numeric_draft_url(record.draft_url):
                raise StateError('The source publication has no valid numeric draft URL. State unchanged.')

            new_row = connection.execute(
                'SELECT * FROM stories WHERE source_hash = ?', (to_story.source_hash,)
            ).fetchone()
            if new_row is not None:
                new_count = connection.execute(
                    'SELECT count(*) FROM publications WHERE story_id = ? AND destination = ?',
                    (new_row['id'], destination),
                ).fetchone()[0]
                if new_count:
                    raise StateError(
                        f'The current story version already has a {destination} publication record. State unchanged.'
                    )

            matching_urls = connection.execute(
                'SELECT id, draft_url FROM publications WHERE draft_url IS NOT NULL'
            ).fetchall()
            canonical_url = record.draft_url.rstrip('/')
            owners = [row['id'] for row in matching_urls if row['draft_url'].rstrip('/') == canonical_url]
            if owners != [record.id]:
                raise StateError('The draft URL does not have exactly one active local association. State unchanged.')

            new_story = self._register(connection, to_story)
            now = _now()
            connection.execute(
                '''INSERT INTO publication_reassociations
                   (publication_id, from_story_id, to_story_id, destination, draft_url, reassociated_at)
                   VALUES (?, ?, ?, ?, ?, ?)''',
                (record.id, old_story.id, new_story.id, destination, record.draft_url, now),
            )
            connection.execute(
                'UPDATE publications SET story_id = ?, updated_at = ? WHERE id = ?',
                (new_story.id, now, record.id),
            )
            moved = self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (record.id,)
            ).fetchone())
            return PublicationReassociation(moved, old_story, new_story)

    def require_body_candidate(self, source_hash: str, destination: str) -> PublicationRecord:
        """Return the exact linked draft only when body insertion is safe to begin."""
        with self._connection() as connection:
            record = self._lookup(connection, source_hash, destination)
        if record is None:
            raise StateError('No publication record exists for this exact story version. Nothing was changed.')
        if record.status != PublicationStatus.DRAFT_CREATED:
            raise StateError(
                f'The publication record must be draft_created, not {record.status.value}. Nothing was changed.'
            )
        if not record.draft_url:
            raise StateError('The publication record has no linked draft URL. Nothing was changed.')
        if record.needs_reconciliation:
            raise StateError('The publication record requires reconciliation. Nothing was changed.')
        if record.error_message or record.body_error_message:
            raise StateError('The publication record has an unresolved failed attempt. Nothing was changed.')
        if record.body_status == BodyStatus.INSERTED:
            raise StateError('The story body is already recorded as inserted. Automatic insertion was refused.')
        if record.body_status in {BodyStatus.INSERTING, BodyStatus.FAILED}:
            raise StateError(
                'A previous body insertion has an uncertain outcome. Inspect the linked draft before retrying.'
            )
        return record

    def require_title_repair_candidate(
        self, source_hash: str, destination: str,
    ) -> PublicationRecord:
        """Return the exact, uniquely linked untouched draft eligible for title repair."""
        with self._connection() as connection:
            record = self._lookup(connection, source_hash, destination)
            if record is None:
                raise StateError(
                    'No publication record exists for this exact story version. Nothing was changed.'
                )
            if record.status != PublicationStatus.DRAFT_CREATED:
                raise StateError(
                    f'The publication record must be draft_created, not {record.status.value}. '
                    'Nothing was changed.'
                )
            if not _is_numeric_draft_url(record.draft_url):
                raise StateError(
                    'The publication record has no valid numeric linked draft URL. Nothing was changed.'
                )
            if record.published_url:
                raise StateError('The publication record is already published. Nothing was changed.')
            if record.needs_reconciliation:
                raise StateError('The publication record requires reconciliation. Nothing was changed.')
            if record.error_message or record.body_error_message:
                raise StateError(
                    'The publication record has an unresolved failed state. Nothing was changed.'
                )
            if record.body_status != BodyStatus.NOT_STARTED:
                raise StateError(
                    'Local state does not identify an untouched empty-body draft. Manual review is required.'
                )
            canonical_url = record.draft_url.rstrip('/')
            owners = connection.execute(
                """SELECT p.id FROM publications p
                   WHERE p.draft_url IS NOT NULL AND rtrim(p.draft_url, '/') = ?""",
                (canonical_url,),
            ).fetchall()
            if [row['id'] for row in owners] != [record.id]:
                raise StateError(
                    'The linked draft URL does not belong uniquely to this exact story version. '
                    'Nothing was changed.'
                )
        return record

    def mark_title_repairing(self, expected: PublicationRecord) -> PublicationRecord:
        """Compare-and-set a crash-safe guard immediately before title mutation."""
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (expected.id,)
            ).fetchone())
            if (current != expected or current.status != PublicationStatus.DRAFT_CREATED
                    or not _is_numeric_draft_url(current.draft_url)
                    or current.published_url or current.needs_reconciliation
                    or current.error_message or current.body_error_message
                    or current.body_status != BodyStatus.NOT_STARTED):
                raise StateError('Title repair preflight became stale. Nothing was changed remotely.')
            now = _now()
            connection.execute(
                """UPDATE publications SET needs_reconciliation = 1,
                   error_message = ?, updated_at = ?, last_attempt_at = ? WHERE id = ?""",
                ('Title repair started; save outcome is not yet confirmed.', now, now, current.id),
            )
            return self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (current.id,)
            ).fetchone())

    def mark_title_repaired(self, expected: PublicationRecord) -> PublicationRecord:
        """Clear only the active title-repair guard after confirmed autosave."""
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (expected.id,)
            ).fetchone())
            if (current != expected or current.status != PublicationStatus.DRAFT_CREATED
                    or not current.draft_url or not current.needs_reconciliation
                    or current.error_message != 'Title repair started; save outcome is not yet confirmed.'):
                raise StateError('Cannot record title repair success from the current state.')
            connection.execute(
                """UPDATE publications SET needs_reconciliation = 0, error_message = NULL,
                   updated_at = ? WHERE id = ?""",
                (_now(), current.id),
            )
            return self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (current.id,)
            ).fetchone())

    def mark_title_repair_uncertain(
        self, expected: PublicationRecord, error_message: str,
    ) -> PublicationRecord:
        """Keep the draft associated and block retries after an uncertain title mutation."""
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (expected.id,)
            ).fetchone())
            if (current != expected or current.status != PublicationStatus.DRAFT_CREATED
                    or not current.needs_reconciliation
                    or current.error_message != 'Title repair started; save outcome is not yet confirmed.'):
                raise StateError('Cannot record an uncertain title repair from the current state.')
            connection.execute(
                'UPDATE publications SET error_message = ?, updated_at = ? WHERE id = ?',
                (error_message, _now(), current.id),
            )
            return self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (current.id,)
            ).fetchone())

    def mark_body_inserting(self, expected: PublicationRecord) -> PublicationRecord:
        """Compare-and-set immediately before the first remote body mutation."""
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (expected.id,)
            ).fetchone())
            if (current != expected or current.status != PublicationStatus.DRAFT_CREATED
                    or not current.draft_url or current.needs_reconciliation
                    or current.error_message or current.body_status != BodyStatus.NOT_STARTED):
                raise StateError('Body insertion preflight became stale. Nothing was changed remotely.')
            now = _now()
            connection.execute(
                """UPDATE publications SET body_status = ?, body_started_at = ?,
                   body_inserted_at = NULL, body_error_message = NULL,
                   updated_at = ?, last_attempt_at = ? WHERE id = ?""",
                (BodyStatus.INSERTING, now, now, now, current.id),
            )
            return self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (current.id,)
            ).fetchone())

    def mark_body_inserted(self, record_id: int) -> PublicationRecord:
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (record_id,)
            ).fetchone())
            if (current is None or current.status != PublicationStatus.DRAFT_CREATED
                    or current.body_status != BodyStatus.INSERTING or not current.draft_url):
                raise StateError('Cannot record body insertion success from the current state.')
            now = _now()
            connection.execute(
                """UPDATE publications SET body_status = ?, body_inserted_at = ?,
                   body_error_message = NULL, needs_reconciliation = 0, updated_at = ?
                   WHERE id = ?""",
                (BodyStatus.INSERTED, now, now, current.id),
            )
            return self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (current.id,)
            ).fetchone())

    def mark_body_insertion_failed(self, record_id: int, error_message: str) -> PublicationRecord:
        """Protect an uncertain remote body from any automatic second insertion."""
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (record_id,)
            ).fetchone())
            if current is None or current.body_status != BodyStatus.INSERTING:
                raise StateError('Cannot record body insertion failure from the current state.')
            connection.execute(
                """UPDATE publications SET body_status = ?, body_error_message = ?,
                   needs_reconciliation = 1, updated_at = ? WHERE id = ?""",
                (BodyStatus.FAILED, error_message, _now(), current.id),
            )
            return self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (current.id,)
            ).fetchone())

    def require_image_candidate(self, source_hash: str, destination: str) -> PublicationRecord:
        """Return the exact linked draft only when one cover upload is safe."""
        with self._connection() as connection:
            record = self._lookup(connection, source_hash, destination)
            if record is None:
                raise StateError(
                    'No publication record exists for this exact story version. Nothing was changed.'
                )
            if record.status != PublicationStatus.DRAFT_CREATED:
                raise StateError(
                    f'The publication record must be draft_created, not {record.status.value}. '
                    'Nothing was changed.'
                )
            if not _is_numeric_draft_url(record.draft_url):
                raise StateError(
                    'The publication record has no valid numeric linked draft URL. Nothing was changed.'
                )
            if record.published_url:
                raise StateError('The publication record is already published. Nothing was changed.')
            if record.needs_reconciliation:
                raise StateError('The publication record requires reconciliation. Nothing was changed.')
            if record.error_message or record.body_error_message or record.image_error_message:
                raise StateError(
                    'The publication record has an unresolved failed state. Nothing was changed.'
                )
            if record.body_status != BodyStatus.INSERTED:
                raise StateError(
                    'The story body must already be recorded as inserted. Nothing was changed.'
                )
            if record.image_status == ImageStatus.UPLOADED:
                raise StateError(
                    'The cover image is already recorded as uploaded. Automatic re-upload was refused.'
                )
            if record.image_status in {ImageStatus.UPLOADING, ImageStatus.FAILED}:
                raise StateError(
                    'A previous image upload has an uncertain outcome. Inspect the linked draft before retrying.'
                )
            canonical_url = record.draft_url.rstrip('/')
            owners = connection.execute(
                """SELECT p.id FROM publications p
                   WHERE p.draft_url IS NOT NULL AND rtrim(p.draft_url, '/') = ?""",
                (canonical_url,),
            ).fetchall()
            if [row['id'] for row in owners] != [record.id]:
                raise StateError(
                    'The linked draft URL does not belong uniquely to this exact story version. '
                    'Nothing was changed.'
                )
        return record

    def mark_image_uploading(self, expected: PublicationRecord) -> PublicationRecord:
        """Compare-and-set immediately before selecting the local image file."""
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (expected.id,)
            ).fetchone())
            if (current != expected or current.status != PublicationStatus.DRAFT_CREATED
                    or not _is_numeric_draft_url(current.draft_url) or current.published_url
                    or current.needs_reconciliation or current.error_message
                    or current.body_error_message or current.image_error_message
                    or current.body_status != BodyStatus.INSERTED
                    or current.image_status != ImageStatus.NOT_STARTED):
                raise StateError('Image upload preflight became stale. Nothing was changed remotely.')
            now = _now()
            connection.execute(
                """UPDATE publications SET image_status = ?, image_started_at = ?,
                   image_uploaded_at = NULL, image_error_message = NULL,
                   updated_at = ?, last_attempt_at = ? WHERE id = ?""",
                (ImageStatus.UPLOADING, now, now, now, current.id),
            )
            return self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (current.id,)
            ).fetchone())

    def mark_image_uploaded(self, expected: PublicationRecord) -> PublicationRecord:
        """Record a cover only after appearance, content, URL, and save checks pass."""
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (expected.id,)
            ).fetchone())
            if (current != expected or current.status != PublicationStatus.DRAFT_CREATED
                    or not current.draft_url or current.needs_reconciliation
                    or current.body_status != BodyStatus.INSERTED
                    or current.image_status != ImageStatus.UPLOADING):
                raise StateError('Cannot record image upload success from the current state.')
            now = _now()
            connection.execute(
                """UPDATE publications SET image_status = ?, image_uploaded_at = ?,
                   image_error_message = NULL, needs_reconciliation = 0, updated_at = ?
                   WHERE id = ?""",
                (ImageStatus.UPLOADED, now, now, current.id),
            )
            return self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (current.id,)
            ).fetchone())

    def mark_image_upload_failed(
        self, expected: PublicationRecord, error_message: str,
    ) -> PublicationRecord:
        """Block automatic retry after any upload may have reached Substack."""
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (expected.id,)
            ).fetchone())
            if current != expected or current.image_status != ImageStatus.UPLOADING:
                raise StateError('Cannot record image upload failure from the current state.')
            connection.execute(
                """UPDATE publications SET image_status = ?, image_error_message = ?,
                   needs_reconciliation = 1, updated_at = ? WHERE id = ?""",
                (ImageStatus.FAILED, error_message, _now(), current.id),
            )
            return self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (current.id,)
            ).fetchone())

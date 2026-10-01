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


class PublicationState(StrEnum):
    """State that is orthogonal to the coarse workflow status."""

    STANDARD = "standard"
    VERIFIED_NOT_PUBLISHED_AFTER_CLICK = "verified_not_published_after_click"


class BodyStatus(StrEnum):
    NOT_STARTED = "not_started"
    INSERTING = "body_inserting"
    INSERTED = "body_inserted"
    FAILED = "body_insertion_failed"


class SubtitleStatus(StrEnum):
    NOT_STARTED = "subtitle_not_started"
    INSERTING = "subtitle_inserting"
    INSERTED = "subtitle_inserted"
    FAILED = "subtitle_insertion_failed"


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
    subtitle_status: SubtitleStatus = SubtitleStatus.NOT_STARTED
    subtitle_started_at: str | None = None
    subtitle_inserted_at: str | None = None
    subtitle_error_message: str | None = None
    image_status: ImageStatus = ImageStatus.NOT_STARTED
    image_started_at: str | None = None
    image_uploaded_at: str | None = None
    image_error_message: str | None = None
    final_click_attempted: bool = False
    final_click_attempted_at: str | None = None
    publication_verification_status: str = 'not_started'
    publication_verification_evidence: str | None = None
    publication_ambiguity_reason: str | None = None
    publication_state: PublicationState = PublicationState.STANDARD
    final_click_attempt_count: int = 0
    retry_authorized: bool = False
    retry_authorized_at: str | None = None
    retry_authorization_count: int = 0


@dataclass(frozen=True)
class PublicationReassociation:
    publication: PublicationRecord
    from_story: StoryVersion
    to_story: StoryVersion


@dataclass(frozen=True)
class DeletedDraftRemoval:
    publication_id: int
    story_id: int
    deleted_draft_url: str
    image_attempts_removed: int
    reassociations_removed: int
    story_removed: bool
    removed_at: str


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
    (
        """CREATE TABLE publication_image_attempts (
            id INTEGER PRIMARY KEY,
            publication_id INTEGER NOT NULL REFERENCES publications(id),
            started_at TEXT,
            recorded_at TEXT NOT NULL,
            outcome TEXT NOT NULL CHECK(outcome IN ('failed_uncertain', 'reconciled_none', 'reconciled_present')),
            error_message TEXT,
            failed_step TEXT,
            diagnostic_screenshot TEXT
        )""",
        "CREATE INDEX publication_image_attempts_publication ON publication_image_attempts(publication_id, id)",
    ),
    (
        "ALTER TABLE publications ADD COLUMN final_click_attempted INTEGER NOT NULL DEFAULT 0 CHECK(final_click_attempted IN (0, 1))",
        "ALTER TABLE publications ADD COLUMN final_click_attempted_at TEXT",
        "ALTER TABLE publications ADD COLUMN publication_verification_status TEXT NOT NULL DEFAULT 'not_started' CHECK(publication_verification_status IN ('not_started', 'pending', 'verified', 'ambiguous'))",
        "ALTER TABLE publications ADD COLUMN publication_verification_evidence TEXT",
        "ALTER TABLE publications ADD COLUMN publication_ambiguity_reason TEXT",
    ),
    (
        "ALTER TABLE publications ADD COLUMN publication_state TEXT NOT NULL DEFAULT 'standard' CHECK(publication_state IN ('standard', 'verified_not_published_after_click'))",
        "ALTER TABLE publications ADD COLUMN final_click_attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(final_click_attempt_count >= 0 AND final_click_attempt_count <= 2)",
        "ALTER TABLE publications ADD COLUMN retry_authorized INTEGER NOT NULL DEFAULT 0 CHECK(retry_authorized IN (0, 1))",
        "ALTER TABLE publications ADD COLUMN retry_authorized_at TEXT",
        """CREATE TABLE publication_audit_events (
            id INTEGER PRIMARY KEY,
            publication_id INTEGER NOT NULL REFERENCES publications(id),
            event_type TEXT NOT NULL CHECK(event_type IN ('final_click_attempt', 'verified_not_published', 'retry_authorized')),
            recorded_at TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            draft_url TEXT,
            evidence TEXT
        )""",
        "CREATE INDEX publication_audit_events_publication ON publication_audit_events(publication_id, id)",
        """INSERT INTO publication_audit_events
           (publication_id, event_type, recorded_at, source_hash, draft_url, evidence)
           SELECT p.id, 'final_click_attempt', COALESCE(p.final_click_attempted_at, p.updated_at),
                  s.source_hash, p.draft_url, 'Migrated from preserved final_click_attempt record'
           FROM publications p JOIN stories s ON s.id = p.story_id
           WHERE p.final_click_attempted = 1""",
        "UPDATE publications SET final_click_attempt_count = 1 WHERE final_click_attempted = 1 AND final_click_attempt_count = 0",
    ),
    (
        "ALTER TABLE publications ADD COLUMN retry_authorization_count INTEGER NOT NULL DEFAULT 0 CHECK(retry_authorization_count >= 0 AND retry_authorization_count <= 1)",
        "UPDATE publications SET retry_authorization_count = 1 WHERE retry_authorized = 1",
        "ALTER TABLE publications ADD COLUMN subtitle_status TEXT NOT NULL DEFAULT 'subtitle_not_started' CHECK(subtitle_status IN ('subtitle_not_started', 'subtitle_inserting', 'subtitle_inserted', 'subtitle_insertion_failed'))",
        "ALTER TABLE publications ADD COLUMN subtitle_started_at TEXT",
        "ALTER TABLE publications ADD COLUMN subtitle_inserted_at TEXT",
        "ALTER TABLE publications ADD COLUMN subtitle_error_message TEXT",
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


def _is_public_url_for_draft(value: str, draft_url: str | None) -> bool:
    try:
        parsed = urlsplit(value)
        draft = urlsplit(draft_url or '')
        parts = parsed.path.rstrip('/').split('/')
        return bool(
            parsed.scheme == 'https' and parsed.hostname == draft.hostname
            and parsed.username is None and parsed.password is None
            and parsed.port in (None, 443) and not parsed.query and not parsed.fragment
            and len(parts) == 3 and parts[1] == 'p' and parts[2]
        )
    except (TypeError, ValueError):
        return False


class PublicationRepository:
    """One row per attempt; hash identity spans renames and all destinations.

    Connections are short lived. BEGIN IMMEDIATE serializes the duplicate check
    and attempt insertion across processes before any future remote side effect.
    """

    def __init__(self, path: Path, *, migrate: bool = True):
        self.path = path
        if not migrate and not path.is_file():
            raise StateError('No publication database exists. Local state unchanged.')
        if not migrate:
            with self._connection() as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                if version > SCHEMA_VERSION:
                    raise StateError(
                        f'This read-only command supports database schema through {SCHEMA_VERSION}; '
                        f'found newer schema {version}. Local state unchanged.'
                    )
            return
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
            # Databases created at schema version 9 before subtitle support
            # need the same columns, while the established public schema
            # version remains 9.
            columns = {
                row[1] for row in connection.execute('PRAGMA table_info(publications)').fetchall()
            }
            if version == SCHEMA_VERSION and 'subtitle_status' not in columns:
                for statement in MIGRATIONS[-1][2:]:
                    connection.execute(statement)

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
        if "subtitle_status" in values:
            values["subtitle_status"] = SubtitleStatus(values["subtitle_status"])
        if "image_status" in values:
            values["image_status"] = ImageStatus(values["image_status"])
        if "final_click_attempted" in values:
            values["final_click_attempted"] = bool(values["final_click_attempted"])
        if "publication_state" in values:
            values["publication_state"] = PublicationState(values["publication_state"])
        if "retry_authorized" in values:
            values["retry_authorized"] = bool(values["retry_authorized"])
        return PublicationRecord(**values)

    @classmethod
    def _lookup(cls, connection, source_hash, destination, *, blocking=False):
        sql = """SELECT p.* FROM publications p JOIN stories s ON s.id = p.story_id
                 WHERE s.source_hash = ? AND p.destination = ?"""
        parameters = [source_hash, destination]
        if blocking:
            sql += """ AND (p.status IN (?, ?, ?, ?) OR p.draft_url IS NOT NULL
                       OR p.published_url IS NOT NULL OR p.needs_reconciliation = 1
                       OR p.publication_state = ?)"""
            parameters += [PublicationStatus.DRAFT_CREATING, PublicationStatus.DRAFT_CREATED,
                           PublicationStatus.PUBLISHING, PublicationStatus.PUBLISHED,
                           PublicationState.VERIFIED_NOT_PUBLISHED_AFTER_CLICK]
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

    def require_deleted_draft_removal_candidate(
        self, source_hash: str, destination: str,
    ) -> PublicationRecord:
        """Return the sole unpublished linked draft for an exact historical hash."""
        if not source_hash:
            raise StateError('The exact historical story hash is required. Local state unchanged.')
        with self._connection() as connection:
            story = connection.execute(
                'SELECT id FROM stories WHERE source_hash = ?', (source_hash,),
            ).fetchone()
            if story is None:
                raise StateError(
                    'No publication record exists for this exact story hash. Local state unchanged.'
                )
            rows = [self._record(row) for row in connection.execute(
                '''SELECT * FROM publications
                   WHERE story_id = ? AND destination = ? ORDER BY id''',
                (story['id'], destination),
            ).fetchall()]
        if any(row.status == PublicationStatus.PUBLISHED or row.published_url for row in rows):
            raise StateError(
                'The exact story has a published Substack record. Deleted-draft cleanup refused; '
                'local state unchanged.'
            )
        linked = [row for row in rows if row.draft_url is not None]
        if not linked:
            raise StateError(
                'The Substack publication record has no valid stored numeric draft URL. '
                'Local state unchanged.'
            )
        if len(linked) != 1:
            raise StateError(
                'The exact story must have exactly one Substack record with a stored draft URL. '
                'Local state unchanged.'
            )
        record = linked[0]
        if record.status != PublicationStatus.DRAFT_CREATED:
            raise StateError(
                'The linked Substack record is not in the draft-created workflow. '
                'Deleted-draft cleanup refused; '
                'local state unchanged.'
            )
        if not _is_numeric_draft_url(record.draft_url):
            raise StateError(
                'The active publication record has no valid stored numeric draft URL. '
                'Local state unchanged.'
            )
        return record

    def remove_verified_deleted_draft(
        self, source_hash: str, destination: str, expected: PublicationRecord,
        verified_draft_url: str,
    ) -> DeletedDraftRemoval:
        """Atomically remove a positively verified deleted draft and dependent state."""
        if not source_hash:
            raise StateError('The exact historical story hash is required. Local state unchanged.')
        with self._connection(write=True) as connection:
            rows = connection.execute(
                '''SELECT p.* FROM publications p JOIN stories s ON s.id = p.story_id
                   WHERE s.source_hash = ? AND p.destination = ?
                   ORDER BY p.id''',
                (source_hash, destination),
            ).fetchall()
            records = [self._record(row) for row in rows]
            if any(row.status == PublicationStatus.PUBLISHED or row.published_url for row in records):
                raise StateError(
                    'Local state indicates publication. Deleted-draft cleanup refused; '
                    'local state unchanged.'
                )
            linked = [row for row in records if row.draft_url is not None]
            if len(linked) != 1 or linked[0] != expected:
                raise StateError('Deleted-draft cleanup evidence is stale. Local state unchanged.')
            current = linked[0]
            if current.status != PublicationStatus.DRAFT_CREATED:
                raise StateError(
                    'The linked Substack record is no longer in the draft-created workflow. '
                    'Local state unchanged.'
                )
            if (not _is_numeric_draft_url(current.draft_url)
                    or current.draft_url.rstrip('/') != verified_draft_url.rstrip('/')):
                raise StateError(
                    'Verified remote URL does not match the active stored draft URL. '
                    'Local state unchanged.'
                )
            removed_at = _now()
            image_attempts = connection.execute(
                'DELETE FROM publication_image_attempts WHERE publication_id = ?',
                (current.id,),
            ).rowcount
            reassociations = connection.execute(
                'DELETE FROM publication_reassociations WHERE publication_id = ?',
                (current.id,),
            ).rowcount
            connection.execute('DELETE FROM publications WHERE id = ?', (current.id,))
            story_removed = connection.execute(
                '''DELETE FROM stories WHERE id = ?
                   AND NOT EXISTS (SELECT 1 FROM publications WHERE story_id = ?)
                   AND NOT EXISTS (SELECT 1 FROM publication_reassociations
                                   WHERE from_story_id = ? OR to_story_id = ?)''',
                (current.story_id, current.story_id, current.story_id, current.story_id),
            ).rowcount == 1
            return DeletedDraftRemoval(
                current.id, current.story_id, current.draft_url, image_attempts,
                reassociations, story_removed, removed_at,
            )

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

    def record_provisional_draft_url(self, record_id: int, draft_url: str) -> PublicationRecord:
        """Persist an observed numeric editor URL without claiming creation success."""
        if not _is_numeric_draft_url(draft_url):
            raise StateError('Cannot record a non-numeric provisional draft URL.')
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (record_id,)
            ).fetchone())
            if current is None or current.status != PublicationStatus.DRAFT_CREATING:
                raise StateError('Cannot attach draft identity to the current publication state.')
            if (current.draft_url is not None
                    and current.draft_url.rstrip('/') != draft_url.rstrip('/')):
                raise StateError('A different provisional draft URL is already recorded.')
            if current.draft_url is None:
                connection.execute(
                    'UPDATE publications SET draft_url = ?, updated_at = ? WHERE id = ?',
                    (draft_url.rstrip('/'), _now(), current.id),
                )
            return self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (current.id,)
            ).fetchone())

    def mark_publishing(self, record_id: int) -> PublicationRecord:
        return self._transition(record_id, PublicationStatus.PUBLISHING, {PublicationStatus.DRAFT_CREATED})

    def mark_published(self, record_id: int, published_url: str | None = None) -> PublicationRecord:
        return self._transition(record_id, PublicationStatus.PUBLISHED,
                                {PublicationStatus.DRAFT_CREATED, PublicationStatus.PUBLISHING, PublicationStatus.FAILED},
                                published_url=published_url)

    def mark_final_click_attempted(self, expected: PublicationRecord) -> PublicationRecord:
        """Durably reserve the sole final click using compare-and-set semantics."""
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (expected.id,),
            ).fetchone())
            if current != expected:
                raise StateError('Final-click preflight state is stale. The final action was not clicked.')
            retry_click = (
                current.status == PublicationStatus.DRAFT_CREATED
                and current.final_click_attempt_count == 1
                and current.final_click_attempted
                and current.retry_authorized
                and current.retry_authorization_count == 1
            )
            first_click = (
                current.status == PublicationStatus.DRAFT_CREATED
                and current.final_click_attempt_count == 0
                and not current.final_click_attempted
            )
            if (not (first_click or retry_click) or current.published_url
                    or current.needs_reconciliation):
                raise StateError(
                    'The publication is not eligible for a first final click. The final action was not clicked.'
                )
            if current.final_click_attempt_count >= 2:
                raise StateError('The maximum guarded final-click attempts has been reached.')
            if current.final_click_attempt_count == 1 and not retry_click:
                raise StateError('An explicit retry authorization is required before another final click.')
            owner = connection.execute(
                '''SELECT s.source_hash, p.destination FROM publications p
                   JOIN stories s ON s.id = p.story_id WHERE p.id = ?''',
                (current.id,),
            ).fetchone()
            duplicate = self._lookup(
                connection, owner['source_hash'], owner['destination'], blocking=True,
            )
            if duplicate != current:
                raise StateError(
                    'Duplicate protection changed before the final click. The final action was not clicked.'
                )
            now = _now()
            connection.execute(
                '''UPDATE publications SET status = ?, final_click_attempted = 1,
                   final_click_attempt_count = final_click_attempt_count + 1,
                   retry_authorized = 0,
                   final_click_attempted_at = ?, publication_verification_status = 'pending',
                   publication_verification_evidence = NULL,
                   publication_ambiguity_reason = NULL, needs_reconciliation = 1,
                   updated_at = ?, last_attempt_at = ?
                   WHERE id = ?''',
                (PublicationStatus.PUBLISHING, now, now, now, current.id),
            )
            connection.execute(
                '''INSERT INTO publication_audit_events
                   (publication_id, event_type, recorded_at, source_hash, draft_url, evidence)
                   SELECT p.id, 'final_click_attempt', ?, s.source_hash, p.draft_url,
                          'Guarded final-action click reserved'
                   FROM publications p JOIN stories s ON s.id = p.story_id WHERE p.id = ?''',
                (now, current.id),
            )
            return self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (current.id,),
            ).fetchone())

    @staticmethod
    def _verification_evidence(evidence: tuple[str, ...]) -> str | None:
        if not evidence:
            return None
        # Evidence is generated from fixed labels and trusted URLs. Keep the field
        # bounded and single-line so arbitrary rendered page text is never persisted.
        return ' | '.join(str(item).replace('\n', ' ')[:500] for item in evidence)[:2000]

    def mark_publication_verified(
        self, expected: PublicationRecord, published_url: str,
        evidence: tuple[str, ...],
    ) -> PublicationRecord:
        """Persist publication only after positive post-click verification."""
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (expected.id,),
            ).fetchone())
            if (current != expected or current.status != PublicationStatus.PUBLISHING
                    or not current.final_click_attempted
                    or current.publication_verification_status != 'pending'):
                raise StateError('Publication verification state is stale. State unchanged.')
            if not _is_public_url_for_draft(published_url, current.draft_url):
                raise StateError(
                    'Verified publication URL is not a canonical public URL for the linked draft.'
                )
            connection.execute(
                '''UPDATE publications SET status = ?, published_url = ?, error_message = NULL,
                   needs_reconciliation = 0, publication_verification_status = 'verified',
                   publication_verification_evidence = ?, publication_ambiguity_reason = NULL,
                   updated_at = ? WHERE id = ?''',
                (PublicationStatus.PUBLISHED, published_url.rstrip('/'),
                 self._verification_evidence(evidence), _now(), current.id),
            )
            return self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (current.id,),
            ).fetchone())

    def mark_publication_uncertain(
        self, expected: PublicationRecord, reason: str, evidence: tuple[str, ...],
    ) -> PublicationRecord:
        """Fail closed after a click attempt whose remote outcome is not proven."""
        safe_reason = ' '.join(str(reason).split())[:1000]
        if not safe_reason:
            safe_reason = 'Publication outcome is ambiguous after the final click attempt.'
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (expected.id,),
            ).fetchone())
            if (current != expected or current.status != PublicationStatus.PUBLISHING
                    or not current.final_click_attempted
                    or current.publication_verification_status != 'pending'):
                raise StateError('Publication uncertainty state is stale. State unchanged.')
            connection.execute(
                '''UPDATE publications SET status = ?, error_message = ?, needs_reconciliation = 1,
                   publication_verification_status = 'ambiguous',
                   publication_verification_evidence = ?, publication_ambiguity_reason = ?,
                   updated_at = ? WHERE id = ?''',
                (PublicationStatus.FAILED, safe_reason, self._verification_evidence(evidence),
                 safe_reason, _now(), current.id),
            )
            return self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (current.id,),
            ).fetchone())

    def reconcile_publication_verified(
        self, expected: PublicationRecord, published_url: str,
        evidence: tuple[str, ...],
    ) -> PublicationRecord:
        """Resolve an ambiguous final click after read-only remote verification."""
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (expected.id,),
            ).fetchone())
            if (current != expected or current.status != PublicationStatus.FAILED
                    or not current.final_click_attempted or not current.needs_reconciliation
                    or current.publication_verification_status != 'ambiguous'):
                raise StateError('Publication reconciliation evidence is stale. State unchanged.')
            if not _is_public_url_for_draft(published_url, current.draft_url):
                raise StateError(
                    'Reconciled publication URL is not a canonical public URL for the linked draft.'
                )
            connection.execute(
                '''UPDATE publications SET status = ?, published_url = ?, error_message = NULL,
                   needs_reconciliation = 0, publication_verification_status = 'verified',
                   publication_verification_evidence = ?, publication_ambiguity_reason = NULL,
                   updated_at = ? WHERE id = ?''',
                (PublicationStatus.PUBLISHED, published_url.rstrip('/'),
                 self._verification_evidence(evidence), _now(), current.id),
            )
            return self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (current.id,),
            ).fetchone())

    def reconcile_publication_not_published(
        self, expected: PublicationRecord, evidence: tuple[str, ...],
    ) -> PublicationRecord:
        """Resolve an ambiguous click after positive proof that the draft remained unpublished."""
        if ('classification=NOT_PUBLISHED_VERIFIED' not in evidence
                or 'published_url=None' not in evidence):
            raise StateError('Verified non-publication evidence is stale or insufficient. State unchanged.')
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (expected.id,),
            ).fetchone())
            if (current != expected or current.status != PublicationStatus.FAILED
                    or not current.final_click_attempted
                    or current.final_click_attempt_count not in (1, 2)
                    or not current.needs_reconciliation
                    or current.publication_verification_status != 'ambiguous'
                    or current.published_url is not None
                    or not _is_numeric_draft_url(current.draft_url)):
                raise StateError('Verified non-publication evidence is stale or insufficient. State unchanged.')
            now = _now()
            safe_evidence = self._verification_evidence(evidence)
            connection.execute(
                '''UPDATE publications SET status = ?, error_message = NULL,
                   needs_reconciliation = 0, publication_state = ?,
                   publication_verification_status = 'not_started',
                   publication_verification_evidence = ?, publication_ambiguity_reason = NULL,
                   updated_at = ? WHERE id = ?''',
                (PublicationStatus.FAILED, PublicationState.VERIFIED_NOT_PUBLISHED_AFTER_CLICK,
                 safe_evidence, now, current.id),
            )
            connection.execute(
                '''INSERT INTO publication_audit_events
                   (publication_id, event_type, recorded_at, source_hash, draft_url, evidence)
                   SELECT p.id, 'verified_not_published', ?, s.source_hash, p.draft_url, ?
                   FROM publications p JOIN stories s ON s.id = p.story_id WHERE p.id = ?''',
                (now, safe_evidence, current.id),
            )
            return self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (current.id,),
            ).fetchone())

    def authorize_retry(
        self, expected: PublicationRecord, *, story_hash: str,
        remote_draft_reverified: bool, no_public_url: bool,
    ) -> PublicationRecord:
        """Explicitly unlock the single remaining guarded attempt."""
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                '''SELECT p.* FROM publications p JOIN stories s ON s.id = p.story_id
                   WHERE p.id = ? AND s.source_hash = ?''', (expected.id, story_hash),
            ).fetchone())
            if (current != expected
                    or current.publication_state != PublicationState.VERIFIED_NOT_PUBLISHED_AFTER_CLICK
                    or current.status != PublicationStatus.FAILED
                    or current.final_click_attempt_count != 1
                    or current.retry_authorized or current.retry_authorization_count != 0
                    or not current.final_click_attempted or not remote_draft_reverified
                    or not no_public_url or current.published_url is not None
                    or current.needs_reconciliation
                    or current.error_message or current.body_error_message or current.image_error_message
                    or current.body_status != BodyStatus.INSERTED
                    or current.image_status != ImageStatus.UPLOADED
                    or not _is_numeric_draft_url(current.draft_url)
                    or len(connection.execute(
                        "SELECT 1 FROM publication_audit_events WHERE publication_id = ? AND event_type = 'final_click_attempt'",
                        (current.id,),
                    ).fetchall()) != 1
                    or story_hash != (connection.execute(
                        'SELECT source_hash FROM stories WHERE id = ?', (current.story_id,)
                    ).fetchone()['source_hash'])):
                raise StateError('Retry authorization requires a reverified exact unpublished draft in the verified-not-published state.')
            now = _now()
            connection.execute(
                '''UPDATE publications SET status = ?, publication_state = ?, retry_authorized = 1,
                   retry_authorized_at = ?, retry_authorization_count = 1,
                   publication_verification_status = 'not_started', needs_reconciliation = 0,
                   updated_at = ? WHERE id = ?''',
                (PublicationStatus.DRAFT_CREATED, PublicationState.VERIFIED_NOT_PUBLISHED_AFTER_CLICK, now, now, current.id),
            )
            connection.execute(
                '''INSERT INTO publication_audit_events
                   (publication_id, event_type, recorded_at, source_hash, draft_url, evidence)
                   VALUES (?, 'retry_authorized', ?, ?, ?, ?)''',
                (current.id, now, story_hash, current.draft_url,
                 'Explicit user authorization; exact draft reverified; no public URL'),
            )
            return self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (current.id,),
            ).fetchone())

    def audit_history(self, publication_id: int) -> tuple[dict, ...]:
        with self._connection() as connection:
            return tuple(dict(row) for row in connection.execute(
                'SELECT * FROM publication_audit_events WHERE publication_id = ? ORDER BY id',
                (publication_id,),
            ).fetchall())

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
            if (record.publication_state == PublicationState.VERIFIED_NOT_PUBLISHED_AFTER_CLICK
                    and not record.retry_authorized):
                raise StateError('The current draft requires explicit retry authorization. Local state unchanged.')
            if record.retry_authorized and (record.final_click_attempt_count != 1
                    or record.retry_authorization_count != 1):
                raise StateError('The retry authorization is invalid or exhausted. Local state unchanged.')
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

    def require_subtitle_candidate(self, source_hash: str, destination: str) -> PublicationRecord:
        """Return the exact linked draft when subtitle insertion is safe to begin."""
        with self._connection() as connection:
            record = self._lookup(connection, source_hash, destination)
        if record is None:
            raise StateError('No publication record exists for this exact story version. Nothing was changed.')
        if record.status != PublicationStatus.DRAFT_CREATED or not record.draft_url:
            raise StateError('The publication record is not a linked draft. Nothing was changed.')
        if record.published_url or record.needs_reconciliation or record.error_message:
            raise StateError('The publication record requires reconciliation. Nothing was changed.')
        if record.subtitle_status == SubtitleStatus.INSERTED:
            raise StateError('The story subtitle is already recorded as inserted. Automatic replacement was refused.')
        if record.subtitle_status in {SubtitleStatus.INSERTING, SubtitleStatus.FAILED}:
            raise StateError('A previous subtitle insertion has an uncertain outcome. Inspect the linked draft before retrying.')
        return record

    def mark_subtitle_inserting(self, expected: PublicationRecord) -> PublicationRecord:
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (expected.id,)
            ).fetchone())
            if (current != expected or current.status != PublicationStatus.DRAFT_CREATED
                    or not current.draft_url or current.needs_reconciliation
                    or current.error_message or current.subtitle_status != SubtitleStatus.NOT_STARTED):
                raise StateError('Subtitle insertion preflight became stale. Nothing was changed remotely.')
            now = _now()
            connection.execute(
                """UPDATE publications SET subtitle_status = ?, subtitle_started_at = ?,
                   subtitle_inserted_at = NULL, subtitle_error_message = NULL,
                   updated_at = ?, last_attempt_at = ? WHERE id = ?""",
                (SubtitleStatus.INSERTING, now, now, now, current.id),
            )
            return self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (current.id,)
            ).fetchone())

    def mark_subtitle_inserted(self, record_id: int) -> PublicationRecord:
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (record_id,)
            ).fetchone())
            if (current is None or current.status != PublicationStatus.DRAFT_CREATED
                    or current.subtitle_status != SubtitleStatus.INSERTING or not current.draft_url):
                raise StateError('Cannot record subtitle insertion success from the current state.')
            now = _now()
            connection.execute(
                """UPDATE publications SET subtitle_status = ?, subtitle_inserted_at = ?,
                   subtitle_error_message = NULL, needs_reconciliation = 0, updated_at = ?
                   WHERE id = ?""",
                (SubtitleStatus.INSERTED, now, now, current.id),
            )
            return self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (current.id,)
            ).fetchone())

    def mark_subtitle_matched(self, expected: PublicationRecord) -> PublicationRecord:
        """Record a remotely verified matching subtitle without a remote write."""
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (expected.id,)
            ).fetchone())
            if (current != expected or current.status != PublicationStatus.DRAFT_CREATED
                    or current.subtitle_status != SubtitleStatus.NOT_STARTED
                    or current.needs_reconciliation):
                raise StateError('Subtitle match evidence is stale. Local state unchanged.')
            now = _now()
            connection.execute(
                """UPDATE publications SET subtitle_status = ?, subtitle_inserted_at = ?,
                   subtitle_error_message = NULL, updated_at = ? WHERE id = ?""",
                (SubtitleStatus.INSERTED, now, now, current.id),
            )
            return self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (current.id,)
            ).fetchone())

    def mark_subtitle_insertion_failed(self, record_id: int, error_message: str) -> PublicationRecord:
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (record_id,)
            ).fetchone())
            if current is None or current.subtitle_status != SubtitleStatus.INSERTING:
                raise StateError('Cannot record subtitle insertion failure from the current state.')
            connection.execute(
                """UPDATE publications SET subtitle_status = ?, subtitle_error_message = ?,
                   needs_reconciliation = 1, updated_at = ? WHERE id = ?""",
                (SubtitleStatus.FAILED, error_message, _now(), current.id),
            )
            return self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (current.id,)
            ).fetchone())

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

    def require_publish_inspection_candidate(
        self, source_hash: str, destination: str,
    ) -> PublicationRecord:
        """Return one fully prepared unpublished draft without changing SQLite."""
        if not source_hash:
            raise StateError('The exact current story hash is required. Local state unchanged.')
        with self._connection() as connection:
            record = self._lookup(connection, source_hash, destination)
            if record is None:
                raise StateError(
                    'No publication record exists for the exact current story hash. '
                    'Local state unchanged.'
                )
            if (record.publication_state == PublicationState.VERIFIED_NOT_PUBLISHED_AFTER_CLICK
                    and not record.retry_authorized):
                raise StateError('The current draft requires explicit retry authorization. Local state unchanged.')
            if record.retry_authorized and (
                    record.final_click_attempt_count != 1
                    or record.retry_authorization_count != 1
            ):
                raise StateError('The retry authorization is invalid or exhausted. Local state unchanged.')
            if record.status != PublicationStatus.DRAFT_CREATED:
                raise StateError(
                    f'Publication state must be draft_created, not {record.status.value}. '
                    'Local state unchanged.'
                )
            if not _is_numeric_draft_url(record.draft_url):
                raise StateError(
                    'The current story requires one linked numeric draft URL. Local state unchanged.'
                )
            if record.published_url:
                raise StateError('The current story is already published. Inspection refused.')
            if record.needs_reconciliation:
                raise StateError('The current draft requires reconciliation. Local state unchanged.')
            if record.error_message or record.body_error_message or record.image_error_message:
                raise StateError(
                    'The current draft has an unresolved error. Local state unchanged.'
                )
            if record.body_status != BodyStatus.INSERTED:
                raise StateError('The story body is not recorded as inserted. Local state unchanged.')
            if record.image_status != ImageStatus.UPLOADED:
                raise StateError(
                    'The Social Preview image is not recorded as uploaded. Local state unchanged.'
                )
            canonical_url = record.draft_url.rstrip('/')
            owners = connection.execute(
                """SELECT p.id FROM publications p
                   WHERE p.draft_url IS NOT NULL AND rtrim(p.draft_url, '/') = ?""",
                (canonical_url,),
            ).fetchall()
            if [row['id'] for row in owners] != [record.id]:
                raise StateError(
                    'The linked draft URL does not belong uniquely to the exact current story. '
                    'Local state unchanged.'
                )
        return record

    def require_image_reconciliation_candidate(
        self, source_hash: str, destination: str,
    ) -> PublicationRecord:
        """Return an exact linked draft whose image state can be verified read-only."""
        if not source_hash:
            raise StateError('The exact current story hash is required. State unchanged.')
        with self._connection() as connection:
            record = self._lookup(connection, source_hash, destination)
            if record is None:
                raise StateError(
                    'No publication record exists for this exact story version. State unchanged.'
                )
            if (record.status != PublicationStatus.DRAFT_CREATED
                    or record.published_url is not None):
                raise StateError('Image reconciliation requires an unpublished linked draft. State unchanged.')
            if not _is_numeric_draft_url(record.draft_url):
                raise StateError('Image reconciliation requires a numeric linked draft URL. State unchanged.')
            clean_not_started = (
                record.image_status == ImageStatus.NOT_STARTED
                and not record.needs_reconciliation and not record.image_error_message
            )
            uncertain_failed = (
                record.image_status == ImageStatus.FAILED
                and record.needs_reconciliation and bool(record.image_error_message)
            )
            if not (clean_not_started or uncertain_failed):
                raise StateError(
                    'Image reconciliation requires a clean not-started image state or an uncertain '
                    'failed image upload. State unchanged.'
                )
            if record.error_message or record.body_error_message:
                raise StateError(
                    'The publication has another unresolved error; image reconciliation was refused. '
                    'State unchanged.'
                )
            if record.body_status != BodyStatus.INSERTED:
                raise StateError('The linked draft body is not recorded as inserted. State unchanged.')
            canonical_url = record.draft_url.rstrip('/')
            owners = connection.execute(
                """SELECT p.id FROM publications p
                   WHERE p.draft_url IS NOT NULL AND rtrim(p.draft_url, '/') = ?""",
                (canonical_url,),
            ).fetchall()
            if [row['id'] for row in owners] != [record.id]:
                raise StateError(
                    'The linked draft URL does not belong uniquely to this exact story version. '
                    'State unchanged.'
                )
        return record

    @staticmethod
    def _preserve_image_failure(connection, record: PublicationRecord) -> None:
        existing = connection.execute(
            """SELECT 1 FROM publication_image_attempts
               WHERE publication_id = ? AND outcome = 'failed_uncertain'
               AND started_at IS ? AND error_message IS ?""",
            (record.id, record.image_started_at, record.image_error_message),
        ).fetchone()
        if existing is None:
            connection.execute(
                """INSERT INTO publication_image_attempts
                   (publication_id, started_at, recorded_at, outcome, error_message)
                   VALUES (?, ?, ?, 'failed_uncertain', ?)""",
                (record.id, record.image_started_at, _now(), record.image_error_message),
            )

    def reconcile_image_upload(
        self, expected: PublicationRecord, remote_social_preview_state: str,
    ) -> PublicationRecord:
        """Resolve an uncertain Social Preview upload from positive remote evidence."""
        if remote_social_preview_state not in {'none', 'present'}:
            raise StateError(
                'Remote Social Preview state is not positive reconciliation evidence. State unchanged.'
            )
        with self._connection(write=True) as connection:
            current = self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (expected.id,)
            ).fetchone())
            clean_not_started = (
                current.image_status == ImageStatus.NOT_STARTED
                and not current.needs_reconciliation and not current.image_error_message
            )
            uncertain_failed = (
                current.image_status == ImageStatus.FAILED
                and current.needs_reconciliation and bool(current.image_error_message)
            )
            if (current != expected or current.status != PublicationStatus.DRAFT_CREATED
                    or current.published_url is not None
                    or not _is_numeric_draft_url(current.draft_url)
                    or current.body_status != BodyStatus.INSERTED
                    or not (clean_not_started or uncertain_failed)
                    or current.error_message or current.body_error_message):
                raise StateError('Image reconciliation evidence is stale or insufficient. State unchanged.')
            if clean_not_started and remote_social_preview_state == 'none':
                return current
            if uncertain_failed:
                self._preserve_image_failure(connection, current)
            now = _now()
            new_status = (
                ImageStatus.NOT_STARTED
                if remote_social_preview_state == 'none' else ImageStatus.UPLOADED
            )
            uploaded_at = None if new_status == ImageStatus.NOT_STARTED else now
            connection.execute(
                """UPDATE publications SET image_status = ?, image_uploaded_at = ?,
                   image_error_message = NULL, needs_reconciliation = 0, updated_at = ?
                   WHERE id = ?""",
                (new_status, uploaded_at, now, current.id),
            )
            connection.execute(
                """INSERT INTO publication_image_attempts
                   (publication_id, started_at, recorded_at, outcome)
                   VALUES (?, ?, ?, ?)""",
                (current.id, current.image_started_at, now,
                 'reconciled_none'
                 if remote_social_preview_state == 'none' else 'reconciled_present'),
            )
            return self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (current.id,)
            ).fetchone())

    def image_attempt_history(self, publication_id: int) -> tuple[sqlite3.Row, ...]:
        """Return image audit evidence in insertion order."""
        with self._connection() as connection:
            return tuple(connection.execute(
                'SELECT * FROM publication_image_attempts WHERE publication_id = ? ORDER BY id',
                (publication_id,),
            ).fetchall())

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
        """Record a Social Preview image after preview, save, content, and URL checks pass."""
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
        self, expected: PublicationRecord, error_message: str, *, failed_step: str | None = None,
        diagnostic_screenshot: str | None = None,
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
            connection.execute(
                """INSERT INTO publication_image_attempts
                   (publication_id, started_at, recorded_at, outcome, error_message,
                    failed_step, diagnostic_screenshot)
                   VALUES (?, ?, ?, 'failed_uncertain', ?, ?, ?)""",
                (current.id, current.image_started_at, _now(), error_message,
                 failed_step, diagnostic_screenshot),
            )
            return self._record(connection.execute(
                'SELECT * FROM publications WHERE id = ?', (current.id,)
            ).fetchone())

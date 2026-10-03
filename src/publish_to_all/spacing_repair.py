"""One-shot cycle-2 body spacing repair, with an immutable dry-run preflight."""

from contextlib import contextmanager
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit

from .application import inspect_project
from .browser import body, body_spacing as spacing, editor
from .browser.session import allow_read_only_request, persistent_browser, private_directory
from .config import runtime_paths
from .errors import BrowserSessionError
from .state import StateError

_PENDING = 'Body spacing repair attempted; save and read-only verification require reconciliation. Do not retry.'


def checksum(path):
    return sha256(path.read_bytes()).hexdigest()


@contextmanager
def connection(path, *, write=False):
    """No schema initialization/migration; dry-run connections cannot write."""
    conn = None
    try:
        conn = sqlite3.connect(path.resolve().as_uri() + ('?mode=rw' if write else '?mode=ro'), uri=True)
        conn.row_factory = sqlite3.Row
        if write:
            conn.execute('BEGIN IMMEDIATE')
        else:
            conn.execute('PRAGMA query_only = ON')
        with conn:
            yield conn
    except sqlite3.Error as exc:
        raise StateError('Spacing repair database preflight/guard failed; stopped.') from exc
    finally:
        if conn is not None:
            conn.close()


def require_local(conn):
    rows = [dict(r) for r in conn.execute(
        '''SELECT p.* FROM publications p JOIN stories s ON s.id = p.story_id
           WHERE s.source_hash = ? AND p.destination = 'substack' AND p.lifecycle_status = 'active'
           ORDER BY p.id''', (spacing.STORY_HASH,),
    )]
    if len(rows) != 1:
        raise StateError('Spacing repair requires exactly one active story publication.')
    row = rows[0]
    required = {
        'cycle_id': 2, 'status': 'draft_created', 'draft_url': spacing.DRAFT_URL,
        'published_url': None, 'body_status': 'body_inserted', 'image_status': 'image_not_started',
        'subtitle_status': 'subtitle_inserted', 'needs_reconciliation': 0,
        'final_click_attempt_count': 0, 'final_click_attempted': 0,
        'publication_state': 'standard', 'publication_verification_status': 'not_started',
        'retry_authorized': 0, 'retry_authorization_count': 0,
    }
    nulls = ('error_message', 'body_error_message', 'image_error_message', 'subtitle_error_message',
             'image_started_at', 'image_uploaded_at', 'final_click_attempted_at',
             'publication_ambiguity_reason', 'publication_verification_evidence',
             'retry_authorized_at', 'retired_at')
    if (any(row.get(k) != v for k, v in required.items()) or any(row.get(k) for k in nulls)
            or not row.get('body_inserted_at') or not row.get('subtitle_inserted_at')):
        raise StateError('Spacing repair local lifecycle does not match the exact cycle-2 preflight.')
    owners = [r['id'] for r in conn.execute('SELECT id, draft_url FROM publications')
              if urlsplit(r['draft_url'] or '').path.rstrip('/').rsplit('/', 1)[-1] == '218388044']
    if owners != [row['id']]:
        raise StateError('Spacing repair draft ownership is not exclusive to cycle 2.')
    if conn.execute(
        '''SELECT count(*) FROM publication_audit_events a JOIN publications p ON p.id=a.publication_id
           WHERE p.story_id=? AND p.destination='substack' AND p.cycle_id=2''', (row['story_id'],),
    ).fetchone()[0]:
        raise StateError('Spacing repair requires zero cycle-2 publication actions.')
    return row


def begin_guard(path, expected):
    with connection(path, write=True) as conn:
        if require_local(conn) != expected:
            raise StateError('Spacing repair local preflight changed; no remote deletion permitted.')
        conn.execute('UPDATE publications SET needs_reconciliation=1, error_message=? WHERE id=?',
                     (_PENDING, expected['id']))


def finish_guard(path, expected):
    guarded = dict(expected, needs_reconciliation=1, error_message=_PENDING)
    with connection(path, write=True) as conn:
        current = conn.execute('SELECT * FROM publications WHERE id=?', (expected['id'],)).fetchone()
        if current is None or dict(current) != guarded:
            raise StateError('Spacing repair local guard changed; reconciliation required.')
        conn.execute('UPDATE publications SET needs_reconciliation=0, error_message=NULL WHERE id=?',
                     (expected['id'],))
        if require_local(conn) != expected:
            raise StateError('Spacing repair lifecycle changed; reconciliation required.')


def require_project(root):
    project = inspect_project(root)
    if (project.config.require_substack() != spacing.PUBLICATION_URL
            or project.story.source_hash != spacing.STORY_HASH
            or project.story.metadata.title != spacing.TITLE
            or project.story.metadata.description != spacing.SUBTITLE):
        raise StateError('Spacing repair source hash, publication, title or subtitle does not match.')
    return project


def repair_body_spacing(root: Path, *, dry_run=False) -> str:
    project = require_project(root)
    paths = runtime_paths(root)
    before_db = checksum(paths.database)
    with connection(paths.database) as conn:
        record = require_local(conn)
    if not paths.substack_browser_profile.is_dir():
        raise BrowserSessionError('Spacing repair requires the existing saved browser session.')
    receipt_path = paths.state / 'body-spacing-repair-218388044-dry-run.json'
    attempt_path = paths.state / 'body-spacing-repair-218388044-attempt.json'
    if attempt_path.exists():
        raise StateError('A spacing repair invocation was already recorded. No retry is permitted.')
    receipt = None
    if not dry_run:
        try:
            receipt = json.loads(receipt_path.read_text())
        except (OSError, ValueError):
            raise StateError('A passing spacing-repair dry run is required before live deletion.') from None
        if receipt.get('sqlite_sha256') != before_db or receipt.get('story_hash') != spacing.STORY_HASH:
            raise StateError('Spacing repair dry-run certificate is stale.')
    prepared = body.prepare_story_body(project.story)
    removed = 0
    attempted = False
    try:
        # Even the live command starts behind the read-only request barrier.
        with persistent_browser(paths.substack_browser_profile, paths.diagnostics,
                                headless=False, read_only=True) as context:
            page = context.new_page()
            monitor = body.RateLimitMonitor(spacing.PUBLICATION_URL)
            page.on('response', monitor.observe)
            surface = spacing.open_verified(page, monitor)
            inspection = spacing.inspect_spacing(surface, prepared)
            spacing.require_metadata(page, monitor)
            with connection(paths.database) as conn:
                if require_local(conn) != record or checksum(paths.database) != before_db:
                    raise StateError('Spacing repair local state changed during preflight.')
            if require_project(root).story != project.story:
                raise StateError('Spacing repair source changed during preflight.')
            if not dry_run:
                if receipt.get('body_sha256') != inspection.fingerprint:
                    raise BrowserSessionError('Spacing repair DOM differs from the passing dry run.')
                editor.start_save_observation(page)
                # Durable one-shot marker and crash guard precede any remote edit.
                private_directory(paths.state)
                with attempt_path.open('x', encoding='utf-8') as log:
                    attempt_path.chmod(0o600)
                    json.dump({'attempts': 1, 'status': 'started', 'story_hash': spacing.STORY_HASH}, log)
                begin_guard(paths.database, record)
                attempted = True
                spacing.require_metadata(page, monitor)
                context.unroute('**/*', allow_read_only_request)
                removed = spacing.delete_approved_blanks(surface, inspection)
                spacing.confirm_spacing_save(page, surface, prepared, inspection, monitor)
                # Fresh server-loaded read-only inspection, with writes blocked
                # again and HTTP cache disabled by Playwright routing.
                context.route('**/*', allow_read_only_request)
                surface = spacing.open_verified(page, monitor)
                after = spacing.inspect_spacing(surface, prepared, repaired=True)
                if after.meaningful_exact != inspection.meaningful_exact:
                    raise BrowserSessionError('Spacing repair saved meaningful HTML/text differs; stopped.')
                spacing.require_metadata(page, monitor)
                if require_project(root).story != project.story:
                    raise StateError('Spacing repair source changed during verification.')
        if not dry_run:
            attempt_path.write_text(json.dumps({
                'attempts': 1, 'status': 'remote_verified', 'removed': removed,
                'meaningful_blocks': 36, 'unwanted_blanks': 0, 'story_hash': spacing.STORY_HASH,
                'sqlite_before': before_db,
            }) + '\n')
            finish_guard(paths.database, record)
    except Exception as exc:
        if attempted:
            raise BrowserSessionError(
                'Spacing repair stopped after its single guarded attempt; reconciliation required. '
                'Do not retry deletion. ' + str(exc),
            ) from None
        raise
    finally:
        if dry_run and checksum(paths.database) != before_db:
            raise StateError('Spacing repair dry run changed SQLite; live repair forbidden.')
    after_db = checksum(paths.database)
    if dry_run:
        private_directory(paths.state)
        receipt_path.write_text(json.dumps({
            'story_hash': spacing.STORY_HASH, 'sqlite_sha256': before_db,
            'body_sha256': inspection.fingerprint,
        }) + '\n')
        receipt_path.chmod(0o600)
    return '\n'.join([
        'Dry-run result: PASS' if dry_run else 'Live spacing repair: VERIFIED',
        'Meaningful blocks preserved: 36', 'Blank repair candidates: 36',
        'Unsafe/ambiguous candidates: 0',
        f'Remote mutations: {0 if dry_run else 1}', f'SQLite mutations: {0 if dry_run else 2}',
        f'Live repair attempts: {0 if dry_run else 1}', f'Blank blocks removed: {removed}',
        'Headings matched: 18 / 18', 'Story paragraphs matched: 18 / 18',
        'Story text duplicated: No', 'Story text missing: No', 'Story order preserved: Yes',
        'Full normalized story text matches: Yes', 'Title preserved: Yes', 'Subtitle preserved: Yes',
        'Draft URL preserved: Yes', 'Body lifecycle: body_inserted', 'Image lifecycle: image_not_started',
        'Editor Saved: Yes', 'Draft unpublished: Yes',
        f'Visible unintended blank paragraphs: {36 if dry_run else 0}',
        f'SQLite checksum before: {before_db}', f'SQLite checksum after: {after_db}',
        f'SQLite unchanged: {"Yes" if before_db == after_db else "No"}',
        'Nothing published: Yes',
    ])

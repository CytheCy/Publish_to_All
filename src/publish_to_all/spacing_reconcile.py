"""Recover the single uncertain cycle-2 spacing repair without remote writes."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import Error as PlaywrightError

from .browser import body, body_spacing as spacing
from .browser.body_spacing_reconcile import Classification, FreshRead, classify, read_fresh
from .browser.session import allow_read_only_request, persistent_browser
from .config import runtime_paths
from .errors import BrowserSessionError
from .spacing_repair import _PENDING, checksum, connection, require_project
from .state import StateError

_DEFECT = ('Spacing repair did not persist; the original 36 unwanted blank paragraphs remain. '
           'Exactly one repair attempt is recorded. Any future repair requires explicit authorization.')


def database_snapshot(conn):
    """Include all cycles, attempts, audit records and schema in the local guard."""
    schema = tuple(tuple(r) for r in conn.execute('SELECT * FROM sqlite_master ORDER BY name'))
    tables = {}
    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        name = row['name']
        quoted = '"' + name.replace('"', '""') + '"'
        tables[name] = [dict(r) for r in conn.execute(f'SELECT * FROM {quoted} ORDER BY rowid')]
    return {'schema': schema, 'tables': tables}


def require_local(conn, paths):
    rows = [dict(r) for r in conn.execute(
        """SELECT p.* FROM publications p JOIN stories s ON s.id=p.story_id
           WHERE s.source_hash=? AND p.destination='substack' ORDER BY p.id""",
        (spacing.STORY_HASH,),
    )]
    active = [r for r in rows if r['lifecycle_status'] == 'active']
    if len(active) != 1 or max((r['cycle_id'] for r in rows), default=0) != 2:
        raise StateError('Spacing reconciliation requires exactly one active cycle, cycle 2.')
    row = active[0]
    required = {
        'cycle_id': 2, 'status': 'draft_created', 'draft_url': spacing.DRAFT_URL,
        'published_url': None, 'subtitle_status': 'subtitle_inserted',
        'body_status': 'body_inserted', 'image_status': 'image_not_started',
        'needs_reconciliation': 1, 'error_message': _PENDING,
        'final_click_attempt_count': 0, 'final_click_attempted': 0,
        'publication_state': 'standard', 'publication_verification_status': 'not_started',
        'retry_authorized': 0, 'retry_authorization_count': 0,
    }
    nulls = ('body_error_message', 'subtitle_error_message', 'image_error_message',
             'image_started_at', 'image_uploaded_at', 'final_click_attempted_at',
             'publication_ambiguity_reason', 'publication_verification_evidence',
             'retry_authorized_at', 'retired_at')
    if (any(row.get(k) != v for k, v in required.items()) or any(row.get(k) for k in nulls)
            or not row.get('body_inserted_at') or not row.get('subtitle_inserted_at')
            or len([r for r in rows if r['cycle_id'] == 2]) != 1):
        raise StateError('Spacing reconciliation lifecycle differs from the exact uncertain repair state.')
    owners = [r['id'] for r in conn.execute('SELECT id, draft_url FROM publications ORDER BY id')
              if urlsplit(r['draft_url'] or '').path.rstrip('/').rsplit('/', 1)[-1] == '218388044']
    if owners != [row['id']]:
        raise StateError('Cycle 2 does not exclusively own draft 218388044.')
    for table in ('publication_audit_events', 'publication_image_attempts'):
        if conn.execute(f'SELECT count(*) FROM {table} WHERE publication_id=?', (row['id'],)).fetchone()[0]:
            raise StateError('Cycle 2 must have zero publication actions and image attempts.')
    attempt_path = paths.state / 'body-spacing-repair-218388044-attempt.json'
    try:
        attempt_bytes = attempt_path.read_bytes()
        attempt = json.loads(attempt_bytes)
        stamp = attempt_path.stat().st_mtime_ns
        if attempt != {'attempts': 1, 'status': 'started', 'story_hash': spacing.STORY_HASH}:
            raise ValueError
        for key in ('body_started_at', 'body_inserted_at', 'last_attempt_at', 'updated_at'):
            if datetime.fromisoformat(row[key]).timestamp() * 1_000_000_000 > stamp:
                raise ValueError
        if set(paths.state.glob('*spacing*attempt*')) != {attempt_path}:
            raise ValueError
    except (OSError, ValueError, TypeError):
        raise StateError('Exactly one prior spacing attempt and no later body/repair attempt are required.') from None
    return row, (attempt_bytes, stamp)


@contextmanager
def fresh_browser(paths):
    # The saved profile supplies cookies only. Neither repair tabs nor origin
    # storage (which could contain an unsaved recovery body) enter this context.
    with persistent_browser(paths.substack_browser_profile, paths.diagnostics,
                            headless=False, read_only=True) as saved:
        with saved.browser.new_context(
            storage_state={'cookies': saved.cookies(), 'origins': []},
            service_workers='block', accept_downloads=False,
        ) as context:
            context.set_default_timeout(5000)
            context.set_default_navigation_timeout(30000)
            # Routing disables the HTTP cache. Install every barrier before
            # creating any page; never remove these routes during inspection.
            context.route('**/*', allow_read_only_request)
            context.route_web_socket('**/*', lambda socket: socket.close())
            yield context


def reconcile_local(path, paths, record, attempt, snapshot, before_sha, classification):
    if classification not in (Classification.PERSISTED, Classification.NOT_PERSISTED):
        raise StateError('Unknown remote spacing evidence cannot mutate SQLite.')
    with connection(path, write=True) as conn:
        if (checksum(path) != before_sha or require_local(conn, paths) != (record, attempt)
                or database_snapshot(conn) != snapshot):
            raise StateError('Local state changed; spacing reconciliation refused.')
        error = None if classification == Classification.PERSISTED else _DEFECT
        expected = deepcopy(snapshot)
        for row in expected['tables']['publications']:
            if row['id'] == record['id']:
                row.update(needs_reconciliation=0, error_message=error)
        conn.execute('UPDATE publications SET needs_reconciliation=0, error_message=? WHERE id=?',
                     (error, record['id']))
        if conn.total_changes != 1 or database_snapshot(conn) != expected:
            raise StateError('Unexpected local reconciliation changes; transaction rolled back.')


def _answer(value):
    return 'Unknown' if value is None else 'Yes' if value else 'No'


def format_result(classification, reads, row, before, after_reads, after):
    summaries = [r.summary() for r in reads]
    lines = [f'Remote reconciliation classification: {classification.value}']
    for i, info in enumerate(summaries, 1):
        lines.extend([
            '', f'Fresh read {i}:',
            f'Prepared meaningful blocks: {info["prepared_meaningful_blocks"]}',
            f'Meaningful blocks: {info["meaningful_blocks"]}',
            f'Unwanted blank paragraphs: {info["unwanted_blank_paragraphs"]}',
            f'Exact safe blank candidates: {info["safe_blank_candidates"]}',
            f'Headings matched: {info["headings_matched"]} / 18',
            f'Story paragraphs matched: {info["story_paragraphs_matched"]} / 18',
            f'Missing meaningful blocks: {info["missing_blocks"]}',
            f'Duplicate meaningful blocks: {info["duplicate_blocks"]}',
            f'Reason: {info["reason"]}',
        ])
    def all_evidence(key):
        values = [s[key] for s in summaries]
        return False if False in values else None if None in values else all(values)
    def any_count(key):
        values = [s[key] for s in summaries]
        return True if any(v for v in values if v is not None) else None if None in values else False
    action = {
        Classification.PERSISTED: 'Cleared spacing-repair uncertainty and reconciliation requirement.',
        Classification.NOT_PERSISTED: 'Cleared uncertainty; recorded the original spacing defect and explicit authorization requirement.',
        Classification.UNKNOWN: 'None; reconciliation remains required.',
    }[classification]
    next_step = {
        Classification.PERSISTED: 'Stop; await authorization for the next workflow stage.',
        Classification.NOT_PERSISTED: 'Stop; any future spacing repair requires explicit authorization.',
        Classification.UNKNOWN: 'Stop; resolve uncertainty with read-only evidence before any edit.',
    }[classification]
    lines.extend([
        '', f'Story text duplicated: {_answer(any_count("duplicate_blocks"))}',
        f'Story text missing: {_answer(any_count("missing_blocks"))}',
        f'Story order preserved: {_answer(all_evidence("story_order_preserved"))}',
        f'Title preserved: {_answer(all_evidence("title_preserved"))}',
        f'Subtitle preserved: {_answer(all_evidence("subtitle_preserved"))}',
        f'Local reconciliation action: {action}',
        f'Body state: {row["body_status"]}', f'Subtitle state: {row["subtitle_status"]}',
        f'Image state: {row["image_status"]}',
        f'Reconciliation required: {bool(row["needs_reconciliation"])}',
        'Spacing repair attempts: 1', 'Body insertion attempts added: 0',
        'Remote changes made: No', 'Remote writes allowed: 0',
        f'SQLite checksum before: {before}',
        f'SQLite checksum after remote inspection: {after_reads}',
        f'SQLite checksum after reconciliation: {after}',
        'Nothing published: Yes', f'Safe next step: {next_step}',
    ])
    return '\n'.join(lines)


def reconcile_body_spacing(root: Path) -> str:
    project = require_project(root)
    paths = runtime_paths(root)
    before = checksum(paths.database)
    with connection(paths.database) as conn:
        record, attempt = require_local(conn, paths)
        snapshot = database_snapshot(conn)
    receipt_path = paths.state / 'body-spacing-reconciliation-218388044.json'
    if receipt_path.exists():
        raise StateError('A spacing reconciliation receipt already exists; stopped.')
    if not paths.substack_browser_profile.is_dir():
        raise BrowserSessionError('Spacing reconciliation requires the existing saved browser session.')
    prepared = body.prepare_story_body(project.story)
    reads = []
    browser_failed = False
    try:
        with fresh_browser(paths) as context:
            monitor = body.RateLimitMonitor(spacing.PUBLICATION_URL)
            context.on('response', monitor.observe)
            for _ in range(2):
                # Each page starts without the earlier page's DOM/session state.
                page = context.new_page()
                try:
                    reads.append(read_fresh(page, prepared, monitor))
                finally:
                    page.close()
                if reads[-1].rate_limited or monitor.encountered:
                    reads[-1].rate_limited = True
                    break
            if monitor.encountered and reads:
                reads[-1].rate_limited = True
    except (BrowserSessionError, PlaywrightError):
        browser_failed = True
    while len(reads) < 2:
        reads.append(FreshRead(reason='Fresh read unavailable; inspection stopped without retry.'))
    classification = Classification.UNKNOWN if browser_failed else classify(reads)
    after_reads = checksum(paths.database)
    with connection(paths.database) as conn:
        if (after_reads != before or require_local(conn, paths) != (record, attempt)
                or database_snapshot(conn) != snapshot):
            raise StateError('SQLite or repair-attempt evidence changed during inspection; stopped.')
    if require_project(root).story != project.story:
        raise StateError('The story changed during inspection; reconciliation refused.')
    if classification != Classification.UNKNOWN:
        reconcile_local(paths.database, paths, record, attempt, snapshot, before, classification)
    after = checksum(paths.database)
    with connection(paths.database) as conn:
        updated = dict(conn.execute('SELECT * FROM publications WHERE id=?', (record['id'],)).fetchone())
    report = format_result(classification, reads, updated, before, after_reads, after)
    if classification != Classification.UNKNOWN:
        # Preserve the original one-shot attempt marker, including its mtime.
        # This separate receipt is reconciliation evidence, never another attempt.
        with receipt_path.open('x', encoding='utf-8') as handle:
            receipt_path.chmod(0o600)
            json.dump({
                'classification': classification.value, 'cycle_id': 2,
                'draft_url': spacing.DRAFT_URL, 'story_hash': spacing.STORY_HASH,
                'reads': [r.summary() for r in reads], 'spacing_repair_attempts': 1,
                'remote_writes': 0, 'sqlite_before': before,
                'sqlite_after_remote_inspection': after_reads, 'sqlite_after_reconciliation': after,
            }, handle, indent=2)
            handle.write('\n')
    return report

"""Reconcile one ambiguous final click locally after independent public evidence."""

from dataclasses import asdict
from hashlib import sha256
import json
from pathlib import Path
import re
from urllib.parse import urlsplit
from uuid import uuid4

from playwright.sync_api import Error as PlaywrightError

from .application import inspect_project
from .browser import body, editor, subtitle
from .browser.publication_reconcile import (
    Expected, PUBLISHED, UNKNOWN, PublicReader, classify, discover, public_url, verify,
)
from .browser.session import persistent_browser, private_directory
from .config import runtime_paths
from .errors import BrowserSessionError, SubstackRateLimitError
from .state import PublicationRepository, PublicationStatus, StateError


def checksum(path):
    return sha256(path.read_bytes()).hexdigest()


def require_attempt(repository, story_hash, origin):
    record = repository.select_active_cycle(story_hash, 'substack', required=True)
    if (record.status != PublicationStatus.FAILED or record.lifecycle_status != 'active'
            or not record.final_click_attempted or record.final_click_attempt_count != 1
            or not record.final_click_attempted_at or not record.needs_reconciliation
            or record.publication_verification_status != 'ambiguous' or record.published_url
            or record.retry_authorized or record.retry_authorization_count):
        raise StateError('Reconciliation requires one ambiguous final click with retries prohibited.')
    if not re.fullmatch(re.escape(origin) + r'/publish/post/[0-9]+', record.draft_url or ''):
        raise StateError('The attempted draft must belong to the trusted publication origin.')
    events = repository.audit_history(record.id)
    clicks = [e for e in events if e['event_type'] == 'final_click_attempt']
    if (len(clicks) != 1 or clicks[0]['recorded_at'] != record.final_click_attempted_at
            or clicks[0]['draft_url'] != record.draft_url or clicks[0]['source_hash'] != story_hash):
        raise StateError('The one-click audit identity is inconsistent; local state unchanged.')
    with repository._connection() as connection:
        owners = connection.execute(
            'SELECT id FROM publications WHERE draft_url = ?', (record.draft_url,),
        ).fetchall()
    if [row['id'] for row in owners] != [record.id]:
        raise StateError('The attempted draft is not exclusively owned by the active cycle.')
    return record


def inspect_supporting(paths, expected, draft_url):
    """Read dashboard/editor only. Every non-GET request and every socket is blocked."""
    result = {'draft_url': draft_url, 'opens': False, 'reason': 'Saved session unavailable.'}
    if not paths.substack_browser_profile.is_dir():
        return result
    limited = False

    def observe(response):
        nonlocal limited
        if response.status == 429:
            limited = True

    def guard(route):
        if limited or route.request.method not in {'GET', 'HEAD', 'OPTIONS'}:
            route.abort()
        else:
            route.continue_()

    def check(page):
        if limited or page.get_by_text(re.compile(r'^Too many requests\b', re.I)).count():
            raise SubstackRateLimitError('Rate limiting; stop without local reconciliation.')

    try:
        with persistent_browser(paths.substack_browser_profile, paths.diagnostics,
                                headless=False, read_only=True) as saved:
            saved.route('**/*', guard)
            saved.on('response', observe)
            # Cookies only: discard recovery/editor storage and previous page DOM.
            with saved.browser.new_context(
                storage_state={'cookies': saved.cookies(), 'origins': []},
                service_workers='block', accept_downloads=False,
            ) as context:
                context.route('**/*', guard)
                context.route_web_socket('**/*', lambda socket: None)
                context.on('response', observe)
                context.set_default_timeout(5000)
                context.set_default_navigation_timeout(30000)
                page = context.new_page()
                response = page.goto(draft_url, wait_until='domcontentloaded')
                check(page)
                try:
                    page.locator('.ProseMirror, [data-lexical-editor="true"]').first.wait_for(
                        state='visible', timeout=10000,
                    )
                except PlaywrightError:
                    pass
                check(page)
                result = {
                    'draft_url': draft_url, 'loaded_url': page.url,
                    'http_status': response.status if response else None,
                    'opens': page.url == draft_url and bool(response and response.status == 200),
                    'reason': 'Editor is supporting evidence only; editability cannot prove non-publication.',
                }
                title = editor.unique_visible(editor.title_fields(page))
                _, value = subtitle.inspect_subtitle(page)
                surfaces = body.body_surfaces(page)
                text = surfaces[0].inner_text() if len(surfaces) == 1 else None
                result.update(
                    title_exact=bool(title and editor.title_value(title) == expected.title),
                    subtitle_exact=value == expected.subtitle,
                    body_exact=text is not None and body.normalize_visible_text(text)
                    == body.normalize_visible_text(expected.body),
                    body_characters=len(body.normalize_visible_text(text)) if text else 0,
                    editable=bool(title and title.is_editable()),
                    published_sent_status=[x.inner_text() for x in page.get_by_text(
                        re.compile(r'^(Published|Sent)(?:\s.*)?$', re.I),
                    ).all() if x.is_visible()],
                    public_links=sorted({url for href in page.locator('a[href]').evaluate_all(
                        '(nodes) => nodes.map(n => n.href)',
                    ) if (url := public_url(href, expected.origin))}),
                )
                check(page)
                dashboard = context.new_page()
                response = dashboard.goto(expected.origin + '/publish/posts/published',
                                          wait_until='domcontentloaded')
                check(dashboard)
                try:
                    dashboard.get_by_text(expected.title, exact=True).first.wait_for(timeout=7000)
                except PlaywrightError:
                    pass
                check(dashboard)
                links = dashboard.locator('a[href]').evaluate_all(
                    '(nodes) => nodes.map(n => ({url:n.href, text:n.innerText}))',
                )
                result['dashboard'] = {
                    'http_status': response.status if response else None, 'url': dashboard.url,
                    'matching_links': [link for link in links if str(expected.draft_id) in link['url']
                                       or link['text'].strip() == expected.title],
                }
                check(dashboard)
    except SubstackRateLimitError:
        raise
    except (BrowserSessionError, PlaywrightError):
        if limited:
            raise SubstackRateLimitError('Rate limiting; local state unchanged.') from None
        result['reason'] = 'Supporting authenticated evidence unavailable; no non-publication inference.'
    return result


def collect_remote(paths, expected, draft_url):
    report = {'candidates': {}, 'excluded_retired_candidates': [], 'reads': [],
              'editor': {}, 'rate_limited': False, 'incomplete': False, 'reason': ''}
    reader = PublicReader(expected.origin)
    reads = []
    try:
        candidates, excluded = discover(reader.get(expected.origin + '/archive'), expected)
        report.update(candidates=candidates, excluded_retired_candidates=excluded)
        for url in candidates:
            first = verify(reader.get(url), expected)
            reads.append(first)
            # A mismatch is rejected. A missing/inaccessible same-title candidate
            # prevents a uniqueness claim even if another candidate verifies.
            if first.status != 200 or first.post_id is None:
                report['incomplete'] = True
            if first.accepted:
                reads.append(verify(reader.get(url), expected))
        report['editor'] = inspect_supporting(paths, expected, draft_url)
    except SubstackRateLimitError as exc:
        report.update(rate_limited=True, incomplete=True, reason=str(exc))
    except BrowserSessionError as exc:
        report.update(incomplete=True, reason=str(exc))
    report['reads'] = [asdict(read) for read in reads]
    report['classification'], report['verified_url'] = classify(
        reads, incomplete=report['incomplete'], rate_limited=report['rate_limited'],
    )
    return report


def reconcile_publication(root: Path):
    """No publication executor is used; unknown observations never open SQLite for writing."""
    paths = runtime_paths(root)
    before = checksum(paths.database)
    project = inspect_project(root)
    origin = project.config.require_substack()
    repository = PublicationRepository(paths.database, migrate=False, read_only=True)
    record = require_attempt(repository, project.story.source_hash, origin)
    history = repository.publication_history(project.story.source_hash, 'substack')
    retired = [r for r in history if r.lifecycle_status == 'retired_test_publication']
    expected = Expected(
        origin, int(urlsplit(record.draft_url).path.rsplit('/', 1)[1]),
        project.story.metadata.title, project.story.metadata.description,
        body.prepare_story_body(project.story).text, record.final_click_attempted_at,
        tuple(int(r.draft_url.rsplit('/', 1)[1]) for r in retired if r.draft_url),
        tuple(r.published_url for r in retired if r.published_url),
    )
    if checksum(paths.database) != before:
        raise StateError('SQLite changed during local inspection; stopped before remote reads.')
    report = collect_remote(paths, expected, record.draft_url)
    after_reads = checksum(paths.database)
    if after_reads != before:
        raise StateError('SQLite changed during remote reads; local reconciliation refused.')
    # Revalidate inputs and the active cycle before opening any writable connection.
    if (inspect_project(root) != project
            or require_attempt(repository, project.story.source_hash, origin) != record):
        raise StateError('Local inputs or cycle changed; reconciliation evidence is stale.')
    current = record
    if report['classification'] == PUBLISHED:
        evidence = (
            f'classification={PUBLISHED}', f'cycle_id={record.cycle_id}',
            f'draft_url={record.draft_url}', f'source_hash={project.story.source_hash}',
            'anonymous_fresh_public_reads=2',
            *(json.dumps({key: read[key] for key in (
                'url', 'read_id', 'read_at', 'status', 'post_id', 'published_at',
                'body_sha256', 'body_characters',
            )}, sort_keys=True) for read in report['reads'] if read['accepted']),
        )
        writable = PublicationRepository(paths.database, migrate=False)
        current = writable.reconcile_publication_verified(
            record, report['verified_url'], evidence, expected_database_sha256=before,
        )
    report.update(
        runtime_sqlite=str(paths.database), initial_state=asdict(record), final_state=asdict(current),
        sqlite_before=before, sqlite_after_remote_reads=after_reads,
        sqlite_after_reconciliation=checksum(paths.database),
        local_action='Recorded verified public URL; cleared reconciliation.'
        if report['classification'] == PUBLISHED else 'None; ambiguity and retry prohibition preserved.',
        remote_changes_made=False, additional_publication_clicks=0,
        publication_action_count=len([e for e in repository.audit_history(record.id)
                                      if e['event_type'] == 'final_click_attempt']),
        safe_to_retry_publication='NO',
    )
    # Receipt is outside SQLite and contains identities/hashes, never story bodies or cookies.
    private_directory(paths.diagnostics)
    receipt = paths.diagnostics / f'publication-reconciliation-{record.cycle_id}-{uuid4().hex}.json'
    report['receipt'] = str(receipt)
    with receipt.open('x') as handle:
        receipt.chmod(0o600)
        json.dump(report, handle, indent=2)
        handle.write('\n')
    return report

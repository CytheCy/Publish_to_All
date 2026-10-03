"""Reconciliation uses local HTML and isolated SQLite, never the user's draft."""

from contextlib import contextmanager
from copy import deepcopy
from html import escape
import json
from pathlib import Path
import sqlite3
from unittest.mock import MagicMock

import pytest

from publish_to_all import cli, spacing_reconcile as operation, spacing_repair
from publish_to_all.browser import body, body_spacing as spacing, editor, reconcile
from publish_to_all.browser.body_spacing_reconcile import Classification
from publish_to_all.browser.session import allow_read_only_request
from publish_to_all.state import StateError

from test_substack_body_spacing_repair import article, chromium, project, BLANK  # noqa: F401


@pytest.fixture
def pending(project):
    root, paths = project
    with spacing_repair.connection(paths.database) as conn:
        record = spacing_repair.require_local(conn)
    spacing_repair.begin_guard(paths.database, record)
    marker = paths.state / 'body-spacing-repair-218388044-attempt.json'
    marker.write_text(json.dumps({'attempts': 1, 'status': 'started', 'story_hash': spacing.STORY_HASH}))
    return root, paths


def snapshot(path):
    with spacing_repair.connection(path) as conn:
        return operation.database_snapshot(conn)


@pytest.fixture
def browser_reads(chromium, article, monkeypatch):
    """Two actual document loads, with local response bodies and no external IO."""
    _, _, blocks = article
    evidence = {'loads': 0, 'pages': [], 'events': [], 'writes': [], 'launches': 0}

    def forbidden(*args, **kwargs):
        raise AssertionError('Reconciliation must not call an editor mutation or save observer')

    monkeypatch.setattr(spacing, 'delete_approved_blanks', forbidden)
    monkeypatch.setattr(spacing_repair, 'repair_body_spacing', forbidden)
    monkeypatch.setattr(body, 'insert_prepared_body', forbidden)
    monkeypatch.setattr(editor, 'start_save_observation', forbidden)
    # The test pages are static. Keep production's full draft inspection but
    # avoid spending three seconds waiting for fixture HTML to settle.
    monkeypatch.setattr(reconcile, '_wait_for_stable_editor', lambda *a, **k: True)
    real_read = operation.read_fresh

    def read(page, prepared, monitor):
        result = real_read(page, prepared, monitor)
        if not result.rate_limited:
            evidence['events'].append(page.evaluate('window.editorEvents'))
            assert page.evaluate('document.activeElement.tagName') == 'BODY'
        return result

    monkeypatch.setattr(operation, 'read_fresh', read)

    def configure(counts=(0, 0), *, change=None, title=spacing.TITLE, subtitle=spacing.SUBTITLE,
                  status=200, blank=BLANK, before_yield=None, after_reads=None):
        @contextmanager
        def launch(paths):
            evidence['launches'] += 1
            context = chromium.new_context(service_workers='block')

            def respond(route):
                if route.request.method not in ('GET', 'HEAD', 'OPTIONS'):
                    evidence['writes'].append(route.request.method)
                    route.abort()
                    return
                assert route.request.url == spacing.DRAFT_URL
                index = evidence['loads']
                evidence['loads'] += 1
                count = counts[index]
                content = list(blocks)
                if change:
                    content = change(content, index)
                article_html = ''.join(b + (blank if i < count else '') for i, b in enumerate(content))
                html = (f'<input placeholder="Title" value="{escape(title, quote=True)}">'
                        f'<textarea placeholder="Subtitle">{escape(subtitle)}</textarea>'
                        '<span>Saved</span><button>Continue</button>'
                        '<div class="ProseMirror" contenteditable="true" style="white-space:break-spaces">'
                        + article_html + '</div><script>window.editorEvents=[];'
                        'const e=document.querySelector(".ProseMirror");'
                        'for(const n of ["focus","input","beforeinput"])'
                        'e.addEventListener(n,()=>window.editorEvents.push(n));'
                        'new MutationObserver(()=>window.editorEvents.push("mutation"))'
                        '.observe(e,{subtree:true,childList:true,characterData:true,attributes:true});</script>')
                route.fulfill(status=status[index] if isinstance(status, tuple) else status,
                              content_type='text/html', body=html)

            context.route('**/*', respond)
            context.on('page', lambda page: evidence['pages'].append(page))
            try:
                if before_yield:
                    before_yield()
                yield context
                if after_reads:
                    after_reads()
            finally:
                context.close()

        monkeypatch.setattr(operation, 'fresh_browser', launch)
        return evidence
    return configure


@pytest.mark.parametrize('count,classification', [
    (0, Classification.PERSISTED), (36, Classification.NOT_PERSISTED),
])
def test_conclusive_reads_update_only_recovery_fields(pending, browser_reads, count, classification):
    root, paths = pending
    evidence = browser_reads((count, count))
    before = snapshot(paths.database)
    before_sha = spacing_repair.checksum(paths.database)
    marker = paths.state / 'body-spacing-repair-218388044-attempt.json'
    marker_before = marker.read_bytes(), marker.stat().st_mtime_ns
    report = operation.reconcile_body_spacing(root)
    assert f'Remote reconciliation classification: {classification}' in report
    assert f'SQLite checksum after remote inspection: {before_sha}' in report
    assert report.count('Meaningful blocks: 36') == 2
    assert report.count('Headings matched: 18 / 18') == 2
    assert report.count('Story paragraphs matched: 18 / 18') == 2
    assert f'Unwanted blank paragraphs: {count}' in report
    assert 'Reconciliation required: False' in report
    assert 'Remote writes allowed: 0' in report and 'Nothing published: Yes' in report
    expected = deepcopy(before)
    cycle2 = expected['tables']['publications'][1]
    cycle2.update(needs_reconciliation=0,
                  error_message=None if count == 0 else operation._DEFECT)
    assert snapshot(paths.database) == expected
    assert marker_before == (marker.read_bytes(), marker.stat().st_mtime_ns)
    assert cycle2['body_status'] == 'body_inserted'
    assert cycle2['subtitle_status'] == 'subtitle_inserted'
    assert cycle2['image_status'] == 'image_not_started'
    assert cycle2['final_click_attempt_count'] == 0
    assert cycle2['published_url'] is None
    assert evidence['loads'] == 2 and evidence['pages'][0] != evidence['pages'][1]
    assert all(p.is_closed() for p in evidence['pages'])
    assert evidence['events'] == [[], []] and evidence['writes'] == []
    receipt = json.loads((paths.state / 'body-spacing-reconciliation-218388044.json').read_text())
    assert receipt['classification'] == classification
    assert receipt['spacing_repair_attempts'] == 1
    assert receipt['sqlite_before'] == receipt['sqlite_after_remote_inspection'] == before_sha
    assert receipt['sqlite_after_reconciliation'] == spacing_repair.checksum(paths.database)
    assert receipt['sqlite_after_reconciliation'] != before_sha
    # No repeated reconciliation or retry after a conclusive outcome.
    with pytest.raises(StateError):
        operation.reconcile_body_spacing(root)
    assert evidence['launches'] == 1


@pytest.mark.parametrize('counts', [(0, 36), (36, 0), (1, 1), (35, 35), (0, 35)])
def test_disagreement_or_partial_counts_remain_unknown(pending, browser_reads, counts):
    root, paths = pending
    evidence = browser_reads(counts)
    before = paths.database.read_bytes()
    report = operation.reconcile_body_spacing(root)
    assert Classification.UNKNOWN in report
    assert 'Reconciliation required: True' in report
    assert paths.database.read_bytes() == before
    assert evidence['loads'] == 2
    assert not (paths.state / 'body-spacing-reconciliation-218388044.json').exists()


@pytest.mark.parametrize('change', [
    lambda b, i: b[:-1],  # missing meaningful block
    lambda b, i: b + [b[-1]],  # duplicate meaningful block
    lambda b, i: b[2:4] + b[:2] + b[4:],  # reordered meaningful blocks
    lambda b, i: [b[0].replace('h1', 'h2')] + b[1:],  # changed heading structure
    lambda b, i: [b[0]] + [b[1].replace('Story', 'Altered')] + b[2:],
    lambda b, i: b if i == 0 else [x.replace('strong', 'em') for x in b],  # same text, changed HTML
])
def test_content_or_structure_difference_unknown_without_local_mutation(pending, browser_reads, change):
    root, paths = pending
    browser_reads(change=change)
    before = paths.database.read_bytes()
    report = operation.reconcile_body_spacing(root)
    assert Classification.UNKNOWN in report
    assert paths.database.read_bytes() == before


@pytest.mark.parametrize('field', ['title', 'subtitle'])
def test_metadata_mismatch_unknown(pending, browser_reads, field):
    root, paths = pending
    browser_reads(**{field: 'Changed metadata'})
    before = paths.database.read_bytes()
    report = operation.reconcile_body_spacing(root)
    assert Classification.UNKNOWN in report
    assert f'{field.capitalize()} preserved: No' in report
    assert paths.database.read_bytes() == before


@pytest.mark.parametrize('statuses,loads', [((429, 200), 1), ((200, 429), 2)])
def test_rate_limit_no_further_navigation_or_local_mutation(pending, browser_reads, statuses, loads):
    root, paths = pending
    evidence = browser_reads(status=statuses)
    before = paths.database.read_bytes()
    report = operation.reconcile_body_spacing(root)
    assert Classification.UNKNOWN in report
    assert evidence['loads'] == loads
    assert paths.database.read_bytes() == before
    assert not (paths.state / 'body-spacing-reconciliation-218388044.json').exists()


@pytest.mark.parametrize('blank', [
    '<p><br></p>',
    '<p data-extra="unknown"><br><br class="ProseMirror-trailingBreak"></p>',
    '<p><span></span><br><br class="ProseMirror-trailingBreak"></p>',
])
def test_36_blanks_with_inexact_signature_remain_unknown(pending, browser_reads, blank):
    root, paths = pending
    browser_reads((36, 36), blank=blank)
    before = paths.database.read_bytes()
    report = operation.reconcile_body_spacing(root)
    assert Classification.UNKNOWN in report
    assert report.count('Unwanted blank paragraphs: 36') == 2
    assert paths.database.read_bytes() == before


@pytest.mark.parametrize('column,value', [
    ('cycle_id', 3), ('needs_reconciliation', 0), ('body_status', 'not_started'),
    ('subtitle_status', 'subtitle_not_started'), ('image_status', 'image_uploaded'),
    ('final_click_attempt_count', 1), ('published_url', 'https://cyporter.substack.com/p/example'),
    ('draft_url', spacing.PUBLICATION_URL + '/publish/post/3'), ('error_message', 'Other uncertainty'),
    ('body_started_at', '2099-01-01T00:00:00+00:00'),
])
def test_preflight_mismatch_stops_before_browser(pending, browser_reads, column, value):
    root, paths = pending
    evidence = browser_reads()
    with sqlite3.connect(paths.database) as conn:
        conn.execute(f'UPDATE publications SET {column}=? WHERE cycle_id=2', (value,))
    before = paths.database.read_bytes()
    with pytest.raises(StateError):
        operation.reconcile_body_spacing(root)
    assert evidence['launches'] == 0
    assert paths.database.read_bytes() == before


@pytest.mark.parametrize('change', ['attempt_count', 'extra_attempt', 'owner', 'hash', 'audit'])
def test_ownership_and_attempt_preflight(pending, browser_reads, monkeypatch, change):
    root, paths = pending
    evidence = browser_reads()
    marker = paths.state / 'body-spacing-repair-218388044-attempt.json'
    if change == 'attempt_count':
        content = json.loads(marker.read_text())
        content['attempts'] = 2
        marker.write_text(json.dumps(content))
    elif change == 'extra_attempt':
        (paths.state / 'body-spacing-repair-218388044-attempt-2.json').write_bytes(marker.read_bytes())
    elif change == 'hash':
        monkeypatch.setattr(spacing, 'STORY_HASH', 'wrong')
    else:
        with sqlite3.connect(paths.database) as conn:
            if change == 'owner':
                conn.execute('UPDATE publications SET draft_url=? WHERE cycle_id=1', (spacing.DRAFT_URL,))
            else:
                conn.execute("""INSERT INTO publication_audit_events
                    (publication_id,event_type,recorded_at,source_hash) VALUES
                    (2,'final_click_attempt','2026-10-01',?)""", (spacing.STORY_HASH,))
    before = paths.database.read_bytes()
    with pytest.raises(StateError):
        operation.reconcile_body_spacing(root)
    assert evidence['launches'] == 0 and paths.database.read_bytes() == before


def test_concurrent_local_change_refuses_reconciliation(pending, browser_reads):
    root, paths = pending
    def change():
        with sqlite3.connect(paths.database) as conn:
            conn.execute("UPDATE publications SET error_message='Concurrent change' WHERE cycle_id=2")
    browser_reads(after_reads=change)
    with pytest.raises(StateError, match='changed during inspection'):
        operation.reconcile_body_spacing(root)
    assert snapshot(paths.database)['tables']['publications'][1]['needs_reconciliation'] == 1


def test_unknown_classification_cannot_update_sqlite(pending):
    _, paths = pending
    with spacing_repair.connection(paths.database) as conn:
        record, attempt = operation.require_local(conn, paths)
        original = operation.database_snapshot(conn)
    before = spacing_repair.checksum(paths.database)
    with pytest.raises(StateError, match='Unknown'):
        operation.reconcile_local(paths.database, paths, record, attempt, original, before,
                                  Classification.UNKNOWN)
    assert spacing_repair.checksum(paths.database) == before


def test_fresh_context_uses_only_cookies_and_installs_write_barriers(pending, monkeypatch):
    _, paths = pending
    saved = MagicMock()
    saved.cookies.return_value = [{'name': 'fixture-auth-cookie'}]
    context = saved.browser.new_context.return_value.__enter__.return_value
    @contextmanager
    def launch(*args, **kwargs):
        assert kwargs == {'headless': False, 'read_only': True}
        yield saved
    monkeypatch.setattr(operation, 'persistent_browser', launch)
    with operation.fresh_browser(paths) as actual:
        assert actual is context
        saved.browser.new_context.assert_called_once_with(
            storage_state={'cookies': saved.cookies(), 'origins': []},
            service_workers='block', accept_downloads=False,
        )
        context.route.assert_called_once_with('**/*', allow_read_only_request)
        socket = MagicMock()
        context.route_web_socket.call_args.args[1](socket)
        socket.close.assert_called_once_with()
        context.unroute.assert_not_called()
        saved.new_page.assert_not_called()
        saved.storage_state.assert_not_called()


@pytest.mark.parametrize('method', ['POST', 'PUT', 'PATCH', 'DELETE'])
def test_read_only_barrier_blocks_every_remote_write(method):
    route = MagicMock()
    route.request.method = method
    allow_read_only_request(route)
    route.abort.assert_called_once_with()
    route.continue_.assert_not_called()


def test_dedicated_cli(monkeypatch, capsys):
    run = MagicMock(return_value=Classification.PERSISTED.value)
    monkeypatch.setattr(operation, 'reconcile_body_spacing', run)
    assert cli.main(['substack-body-spacing-reconcile']) == 0
    run.assert_called_once_with(Path('.'))
    assert Classification.PERSISTED in capsys.readouterr().out

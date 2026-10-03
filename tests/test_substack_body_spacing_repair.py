"""Local Chromium/SQLite fixtures only; never access the user's draft or state."""

from contextlib import contextmanager
from html import escape
from pathlib import Path
import sqlite3
from unittest.mock import MagicMock

import pytest
from playwright.sync_api import sync_playwright, Error as PlaywrightError

from publish_to_all import cli, spacing_repair as operation
from publish_to_all.browser import body, body_spacing as spacing, editor, reconcile
from publish_to_all.config import runtime_paths
from publish_to_all.errors import BrowserSessionError
from publish_to_all.state import PublicationRepository, StateError
from publish_to_all.story import load_story

BLANK = '<p><br><br class="ProseMirror-trailingBreak"></p>'


@pytest.fixture(scope='module')
def chromium():
    with sync_playwright() as driver:
        browser = driver.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def article(tmp_path):
    root = tmp_path / 'article'
    root.mkdir()
    markdown = '\n\n'.join(f'# Chapter {i}\n\nStory paragraph {i} with **exact** text and enough substance.'
                           for i in range(1, 19))
    (root / 'story.md').write_text(f'---\ntitle: {spacing.TITLE}\nDescription: {spacing.SUBTITLE}\n---\n{markdown}')
    story = load_story(root)
    prepared = body.prepare_story_body(story)
    blocks = prepared.html.strip().split('\n')
    assert len(blocks) == 36
    return story, prepared, blocks


@pytest.fixture
def remote(chromium, article):
    page = chromium.new_page()
    page.route('**/*', lambda route: route.fulfill(status=200, content_type='text/html', body='<html></html>'))
    page.goto(spacing.DRAFT_URL)
    _, prepared, blocks = article
    metadata = (f'<input placeholder="Title" value="{escape(spacing.TITLE, quote=True)}">'
                f'<textarea placeholder="Subtitle">{escape(spacing.SUBTITLE)}</textarea>'
                '<span id="save">Saved</span><button>Continue</button>')
    page.set_content(metadata + '<div class="ProseMirror" contenteditable="true" '
                     'style="white-space:break-spaces">' + ''.join(b + BLANK for b in blocks) + '</div>')
    yield page, page.locator('.ProseMirror'), prepared
    page.close()


def test_exact_36_defect_accepted_without_dom_changes(remote):
    page, surface, prepared = remote
    before = surface.inner_html()
    focus = page.evaluate('document.activeElement.tagName')
    inspection = spacing.inspect_spacing(surface, prepared)
    assert len(inspection.candidates) == 36
    assert len(inspection.meaningful_exact) == 36
    assert inspection.report.headings_matched == 18
    assert surface.inner_html() == before
    assert page.evaluate('document.activeElement.tagName') == focus


@pytest.mark.parametrize('blank', [
    '<p><strong style="font-size:0">meaningful</strong><br><br class="ProseMirror-trailingBreak"></p>',
    '<p><span data-mention="person"></span><br><br class="ProseMirror-trailingBreak"></p>',
    '<p><span data-attachment="file"></span><br><br class="ProseMirror-trailingBreak"></p>',
    '<p><iframe></iframe><br><br class="ProseMirror-trailingBreak"></p>',
    '<p><a href="https://example.com"></a><br><br class="ProseMirror-trailingBreak"></p>',
    '<p><img alt=""><br><br class="ProseMirror-trailingBreak"></p>',
    '<p><em></em><br><br class="ProseMirror-trailingBreak"></p>',
    '<p><br></p>',
    '<p hidden><br><br class="ProseMirror-trailingBreak"></p>',
    '<div><p><br><br class="ProseMirror-trailingBreak"></p></div>',
])
def test_unsafe_or_ambiguous_empty_looking_block_rejected(remote, blank):
    _, surface, prepared = remote
    surface.evaluate('(e,html)=>e.children[1].outerHTML=html', blank)
    before = surface.inner_html()
    with pytest.raises(BrowserSessionError):
        spacing.inspect_spacing(surface, prepared)
    assert surface.inner_html() == before


@pytest.mark.parametrize('mutation', [
    'e.children[1].remove()',  # 35 blanks
    'e.insertAdjacentHTML("beforeend", e.children[1].outerHTML)',  # 37 blanks
    'e.children[2].remove()',  # missing paragraph
    'e.insertAdjacentHTML("beforeend", e.children[2].outerHTML)',  # duplicate paragraph
    'e.insertBefore(e.children[4], e.children[0])',  # reordered heading
    'e.insertBefore(e.children[6], e.children[2])',  # reordered story paragraph
    'e.append(e.children[1])',  # count correct, defect pattern changed
])
def test_inexact_defects_rejected(remote, mutation):
    _, surface, prepared = remote
    surface.evaluate('e=>{' + mutation + '}')
    with pytest.raises(BrowserSessionError):
        spacing.inspect_spacing(surface, prepared)


def test_live_deletion_preserves_exact_meaningful_nodes_text_and_metadata(remote):
    page, surface, prepared = remote
    before = spacing.inspect_spacing(surface, prepared)
    surface.evaluate('e=>window.retained=[...e.children].filter(n=>n.textContent.trim())')
    fields = (page.get_by_placeholder('Title', exact=True).input_value(), page.get_by_placeholder('Subtitle').input_value())
    assert spacing.delete_approved_blanks(surface, before) == 36
    after = spacing.inspect_spacing(surface, prepared, repaired=True)
    assert before.meaningful_exact == after.meaningful_exact
    assert surface.evaluate('e=>window.retained.every((n,i)=>n===e.children[i])')
    assert fields == (page.get_by_placeholder('Title', exact=True).input_value(), page.get_by_placeholder('Subtitle').input_value())
    assert page.url == spacing.DRAFT_URL
    assert len(after.report.remote.meaningful) == 36
    assert len(after.report.remote.blanks) == 0


def test_atomic_deletion_guard_rejects_stale_snapshot_without_partial_deletion(remote):
    _, surface, prepared = remote
    before = spacing.inspect_spacing(surface, prepared)
    surface.evaluate('e=>e.children[70].append("changed")')
    changed = surface.inner_html()
    with pytest.raises(PlaywrightError, match='stale'):
        spacing.delete_approved_blanks(surface, before)
    assert surface.inner_html() == changed
    assert surface.locator('br.ProseMirror-trailingBreak').count() == 36


def test_stale_saved_rejected_and_fresh_transition_required(remote):
    page, surface, prepared = remote
    before = spacing.inspect_spacing(surface, prepared)
    monitor = MagicMock()
    editor.start_save_observation(page)
    spacing.delete_approved_blanks(surface, before)
    with pytest.raises(BrowserSessionError, match='fresh save'):
        spacing.confirm_spacing_save(page, surface, prepared, before, monitor, timeout=0)
    page.locator('#save').evaluate('e=>e.textContent="Saving…"')
    page.locator('#save').evaluate('e=>e.textContent="Saved"')
    after = spacing.confirm_spacing_save(page, surface, prepared, before, monitor, timeout=0)
    assert after.meaningful_exact == before.meaningful_exact


@pytest.mark.parametrize('selector', ['input', 'textarea'])
def test_metadata_mismatch_stops(remote, selector):
    page, _, _ = remote
    page.locator(selector).fill('Changed')
    with pytest.raises(BrowserSessionError, match='title or subtitle'):
        spacing.require_metadata(page, MagicMock())


@pytest.fixture
def project(tmp_path, article, monkeypatch):
    root = tmp_path / 'project'
    (root / 'In').mkdir(parents=True)
    (root / 'In/story.md').write_bytes(article[0].source.read_bytes())
    (root / 'config.toml').write_text(f'[substack]\npublication_url="{spacing.PUBLICATION_URL}"\n')
    paths = runtime_paths(root)
    paths.substack_browser_profile.mkdir(parents=True)
    story = load_story(root / 'In')
    monkeypatch.setattr(spacing, 'STORY_HASH', story.source_hash)
    repo = PublicationRepository(paths.database)
    first = repo.mark_draft_created(repo.begin_attempt(story, 'substack').id,
                                   spacing.PUBLICATION_URL + '/publish/post/1')
    with sqlite3.connect(repo.path) as conn:
        conn.execute("UPDATE publications SET lifecycle_status='retired_test_publication' WHERE id=?", (first.id,))
    repo.start_new_cycle(story, 'substack')
    second = repo.mark_draft_created(repo.begin_attempt(story, 'substack').id, spacing.DRAFT_URL)
    second = repo.mark_subtitle_inserted(repo.mark_subtitle_inserting(second).id)
    repo.mark_body_inserted(repo.mark_body_inserting(second).id)
    return root, paths


def rows(path):
    with sqlite3.connect(path) as conn:
        return conn.execute('SELECT * FROM publications ORDER BY id').fetchall()


@pytest.fixture
def command_browser(monkeypatch, remote):
    page, surface, _ = remote
    context = MagicMock()
    context.new_page.return_value = page

    @contextmanager
    def launch(*args, **kwargs):
        assert kwargs == {'headless': False, 'read_only': True}
        yield context

    launcher = MagicMock(side_effect=launch)
    monkeypatch.setattr(operation, 'persistent_browser', launcher)
    monkeypatch.setattr(body, 'RateLimitMonitor', MagicMock())
    monkeypatch.setattr(spacing, 'open_verified', lambda *_: surface)
    return page, surface, context, launcher


def test_dry_run_zero_remote_and_sqlite_writes(project, command_browser, monkeypatch):
    root, paths = project
    page, surface, context, _ = command_browser
    db = paths.database.read_bytes()
    before = surface.inner_html()
    delete = MagicMock(side_effect=AssertionError('remote write'))
    begin = MagicMock(side_effect=AssertionError('SQLite write'))
    observer = MagicMock(side_effect=AssertionError('page write'))
    monkeypatch.setattr(spacing, 'delete_approved_blanks', delete)
    monkeypatch.setattr(operation, 'begin_guard', begin)
    monkeypatch.setattr(editor, 'start_save_observation', observer)
    report = operation.repair_body_spacing(root, dry_run=True)
    assert 'Remote mutations: 0' in report and 'SQLite mutations: 0' in report
    assert 'Meaningful blocks preserved: 36' in report and 'Blank repair candidates: 36' in report
    assert 'Unsafe/ambiguous candidates: 0' in report
    assert 'SQLite unchanged: Yes' in report
    assert paths.database.read_bytes() == db and surface.inner_html() == before
    context.unroute.assert_not_called()
    delete.assert_not_called(); begin.assert_not_called(); observer.assert_not_called()
    with operation.connection(paths.database) as conn:
        with pytest.raises(sqlite3.OperationalError, match='readonly'):
            conn.execute('UPDATE publications SET needs_reconciliation=1')


def test_live_one_shot_preserves_all_lifecycle_fields_and_cycle1(project, command_browser, monkeypatch):
    root, paths = project
    page, surface, context, _ = command_browser
    before_rows = rows(paths.database)
    operation.repair_body_spacing(root, dry_run=True)
    original_delete = spacing.delete_approved_blanks

    def delete(*args):
        with operation.connection(paths.database) as conn:
            assert conn.execute('SELECT needs_reconciliation FROM publications WHERE cycle_id=2').fetchone()[0] == 1
        result = original_delete(*args)
        page.locator('#save').evaluate('e=>e.textContent="Saving…"')
        page.locator('#save').evaluate('e=>e.textContent="Saved"')
        return result

    spy = MagicMock(side_effect=delete)
    monkeypatch.setattr(spacing, 'delete_approved_blanks', spy)
    report = operation.repair_body_spacing(root)
    assert 'Live spacing repair: VERIFIED' in report and 'Blank blocks removed: 36' in report
    assert 'Visible unintended blank paragraphs: 0' in report
    assert rows(paths.database) == before_rows
    assert spy.call_count == 1
    context.unroute.assert_called_once()
    context.route.assert_called_once()
    with pytest.raises(StateError, match='No retry'):
        operation.repair_body_spacing(root)
    assert spy.call_count == 1


def test_uncertain_save_no_retry_and_reconciliation_required(project, command_browser, monkeypatch):
    root, paths = project
    operation.repair_body_spacing(root, dry_run=True)
    delete = MagicMock(wraps=spacing.delete_approved_blanks)
    monkeypatch.setattr(spacing, 'delete_approved_blanks', delete)
    monkeypatch.setattr(spacing, 'confirm_spacing_save', MagicMock(side_effect=BrowserSessionError('uncertain save')))
    with pytest.raises(BrowserSessionError, match='reconciliation required'):
        operation.repair_body_spacing(root)
    with operation.connection(paths.database) as conn:
        row = dict(conn.execute('SELECT * FROM publications WHERE cycle_id=2').fetchone())
    assert row['needs_reconciliation'] == 1 and row['body_status'] == 'body_inserted'
    assert row['image_status'] == 'image_not_started'
    with pytest.raises(StateError):
        operation.repair_body_spacing(root)
    assert delete.call_count == 1


@pytest.mark.parametrize('column,value', [
    ('cycle_id', 3), ('needs_reconciliation', 1), ('body_status', 'not_started'),
    ('image_status', 'image_uploaded'), ('final_click_attempt_count', 1),
    ('published_url', 'https://cyporter.substack.com/p/example'),
    ('subtitle_status', 'subtitle_not_started'), ('draft_url', spacing.PUBLICATION_URL + '/publish/post/3'),
])
def test_local_preflight_mismatch_stops_before_browser(project, command_browser, column, value):
    root, paths = project
    with sqlite3.connect(paths.database) as conn:
        conn.execute(f'UPDATE publications SET {column}=? WHERE cycle_id=2', (value,))
    before = paths.database.read_bytes()
    with pytest.raises(StateError):
        operation.repair_body_spacing(root, dry_run=True)
    command_browser[3].assert_not_called()
    assert paths.database.read_bytes() == before


def test_exclusive_ownership_required(project, command_browser):
    root, paths = project
    with sqlite3.connect(paths.database) as conn:
        conn.execute('UPDATE publications SET draft_url=? WHERE cycle_id=1', (spacing.DRAFT_URL,))
    with pytest.raises(StateError, match='exclusive'):
        operation.repair_body_spacing(root, dry_run=True)
    command_browser[3].assert_not_called()


def test_failed_dry_run_and_no_receipt_forbid_live(project, command_browser):
    root, paths = project
    surface = command_browser[1]
    surface.evaluate('e=>e.children[1].remove()')
    before = paths.database.read_bytes()
    with pytest.raises(BrowserSessionError):
        operation.repair_body_spacing(root, dry_run=True)
    with pytest.raises(StateError, match='passing'):
        operation.repair_body_spacing(root)
    assert paths.database.read_bytes() == before


def test_remote_changed_since_dry_run_stops_before_guard(project, command_browser):
    root, paths = project
    operation.repair_body_spacing(root, dry_run=True)
    before = paths.database.read_bytes()
    command_browser[1].evaluate('e=>e.firstElementChild.setAttribute("data-changed","yes")')
    with pytest.raises(BrowserSessionError, match='differs'):
        operation.repair_body_spacing(root)
    assert paths.database.read_bytes() == before


def test_cli_dedicated_command(monkeypatch, capsys):
    run = MagicMock(return_value='PASS')
    monkeypatch.setattr(operation, 'repair_body_spacing', run)
    assert cli.main(['substack-body-spacing-repair', '--dry-run']) == 0
    run.assert_called_once_with(Path('.'), dry_run=True)
    assert 'PASS' in capsys.readouterr().out


@pytest.mark.parametrize('whitespace', ['pre-wrap', 'break-spaces'])
def test_future_insertion_alternating_fixture_no_separator_blanks(chromium, tmp_path, whitespace):
    (tmp_path / 'story.md').write_bytes((Path(__file__).parent / 'fixtures/alternating_body.md').read_bytes())
    prepared = body.prepare_story_body(load_story(tmp_path))
    page = chromium.new_page()
    page.route('**/*', lambda route: route.abort())
    page.set_content(f'<div contenteditable="true" style="white-space:{whitespace}"><p><br></p></div>')
    surface = page.locator('div')
    body.insert_prepared_body(surface, prepared)
    assert surface.evaluate('e=>[...e.children].map(n=>n.tagName)') == ['H1', 'P', 'H1', 'P']
    assert surface.locator('strong').inner_text() == 'bold'
    assert surface.locator('em').inner_text() == 'italic'
    assert body.normalize_visible_text(surface.inner_text()) == body.normalize_visible_text(prepared.text)
    page.close()


def test_original_serialization_newlines_reproduce_blanks_locally(chromium):
    page = chromium.new_page()
    page.set_content('<div contenteditable="true" style="white-space:break-spaces"><p><br></p></div>')
    surface = page.locator('div')
    surface.evaluate('''e=>{e.focus(); const r=document.createRange(); r.selectNodeContents(e); r.collapse(true);
      const s=getSelection(); s.removeAllRanges(); s.addRange(r);
      document.execCommand('insertHTML',false,'<h1>One</h1>\\n<p>Two</p>\\n');}''')
    assert surface.evaluate('e=>[...e.children].map(n=>[n.tagName,n.textContent.trim()])') == [
        ['H1', 'One'], ['P', ''], ['P', 'Two'], ['P', ''],
    ]
    page.close()


def test_future_insertion_preserves_intentional_source_breaks_and_code(chromium, tmp_path):
    (tmp_path / 'story.md').write_text('---\ntitle: Intentional spacing\n---\n'
                                    'First\\\n\\\nThird **bold** *italic* words.\n\n'
                                    '```\nline one\n\nline three\n```\n')
    prepared = body.prepare_story_body(load_story(tmp_path))
    page = chromium.new_page()
    page.set_content('<div contenteditable="true" style="white-space:break-spaces"><p><br></p></div>')
    surface = page.locator('div')
    body.insert_prepared_body(surface, prepared)
    assert surface.locator('p').count() == 1
    assert surface.locator('p br').count() == 2
    assert surface.locator('pre code').text_content() == 'line one\n\nline three\n'
    assert surface.locator('p').text_content() == 'First\n\nThird bold italic words.'
    page.close()


def test_future_insertion_retains_explicit_blank_block(chromium):
    page = chromium.new_page()
    page.set_content('<div contenteditable="true" style="white-space:break-spaces"><p><br></p></div>')
    surface = page.locator('div')
    body.insert_prepared_body(surface, body.PreparedBody('<h1>First</h1>\n<p><br></p>\n<p>Third</p>\n', 'First Third'))
    assert surface.evaluate('e=>[...e.children].map(n=>n.tagName)') == ['H1', 'P', 'P']
    assert surface.locator('p').first.inner_html() == '<br>'
    page.close()


@pytest.mark.parametrize('difference', ['unpublished', 'saved', 'substantial', 'rate_limit', 'editable'])
def test_remote_preflight_requires_all_evidence(remote, monkeypatch, difference):
    page, _, _ = remote
    inspection = reconcile.DraftInspection(
        spacing.DRAFT_URL, spacing.TITLE, 'substantial', False, ('Saved',), True,
        spacing.DRAFT_URL, 'Editor', ('Saved', 'Continue'),
    )
    from dataclasses import replace
    if difference == 'unpublished':
        inspection = replace(inspection, controls=('Saved', 'Continue', 'Published'))
    elif difference == 'substantial':
        inspection = replace(inspection, body_classification='empty')
    elif difference == 'editable':
        inspection = replace(inspection, definitely_draft_editor=False)
    elif difference == 'saved':
        page.locator('#save').evaluate('e=>e.textContent="Saving…"')
    evidence = reconcile.SuppliedDraftEvidence(True, 'verified',
                                             rate_limited=difference == 'rate_limit', inspection=inspection)
    monkeypatch.setattr(reconcile, 'verify_supplied_draft', lambda *a, **k: evidence)
    with pytest.raises(BrowserSessionError):
        spacing.open_verified(page, MagicMock())


def test_positive_remote_preflight(remote, monkeypatch):
    page, surface, _ = remote
    inspection = reconcile.DraftInspection(
        spacing.DRAFT_URL, spacing.TITLE, 'substantial', False, ('Saved',), True,
        spacing.DRAFT_URL, 'Editor', ('Saved', 'Continue'),
    )
    evidence = reconcile.SuppliedDraftEvidence(True, 'verified', inspection=inspection)
    monkeypatch.setattr(reconcile, 'verify_supplied_draft', lambda *a, **k: evidence)
    assert spacing.open_verified(page, MagicMock()).inner_html() == surface.inner_html()


def test_wrong_story_hash_stops_before_browser(project, command_browser, monkeypatch):
    root, _ = project
    monkeypatch.setattr(spacing, 'STORY_HASH', 'wrong')
    with pytest.raises(StateError, match='source hash'):
        operation.repair_body_spacing(root, dry_run=True)
    command_browser[3].assert_not_called()


def test_saved_reload_must_preserve_exact_meaningful_html(project, command_browser, monkeypatch):
    root, paths = project
    page, surface, _, _ = command_browser
    operation.repair_body_spacing(root, dry_run=True)
    original_delete = spacing.delete_approved_blanks

    def delete(*args):
        count = original_delete(*args)
        page.locator('#save').evaluate('e=>e.textContent="Saving…"')
        page.locator('#save').evaluate('e=>e.textContent="Saved"')
        return count

    def open_second_time(*args):
        if surface.locator('br.ProseMirror-trailingBreak').count() == 0:
            # Same normalized story, different exact persisted HTML must fail.
            surface.locator('strong').first.evaluate('e=>e.outerHTML="<em>exact</em>"')
        return surface

    monkeypatch.setattr(spacing, 'open_verified', open_second_time)
    spy = MagicMock(side_effect=delete)
    monkeypatch.setattr(spacing, 'delete_approved_blanks', spy)
    with pytest.raises(BrowserSessionError, match='saved meaningful HTML/text differs'):
        operation.repair_body_spacing(root)
    assert spy.call_count == 1
    with operation.connection(paths.database) as conn:
        assert conn.execute('SELECT needs_reconciliation FROM publications WHERE cycle_id=2').fetchone()[0] == 1

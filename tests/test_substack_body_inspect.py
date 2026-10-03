"""Exercise real DOM extraction on local fixtures; never contact Substack."""

from contextlib import contextmanager
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from playwright.sync_api import sync_playwright

from publish_to_all import application, cli
from publish_to_all.browser import body, body_inspect, reconcile, subtitle
from publish_to_all.browser.body_inspect import FormattingClassification as Classification
from publish_to_all.config import runtime_paths
from publish_to_all.errors import BrowserSessionError
from publish_to_all.story import load_story


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as driver:
        browser = driver.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def inspect(browser, tmp_path):
    page = browser.new_page()
    page.route('**/*', lambda route: route.abort())

    def compare(markdown, html):
        (tmp_path / 'story.md').write_text('---\ntitle: Private title\n---\n' + markdown)
        prepared = body.prepare_story_body(load_story(tmp_path))
        page.set_content('<div id="body" contenteditable="true">' + html + '</div>')
        surface = page.locator('#body')
        before = surface.inner_html()
        focus = page.evaluate('document.activeElement.tagName')
        result = body_inspect.inspect_body_formatting(surface, prepared)
        assert surface.inner_html() == before
        assert page.evaluate('document.activeElement.tagName') == focus
        return result

    yield compare
    page.close()


def test_production_renderer_metadata_and_top_level_alignment(inspect):
    result = inspect('# Chapter\n\nA **bold** paragraph.',
                     '<h1>Chapter</h1><p>A <strong>bold</strong> paragraph.</p>')
    assert result.classification == Classification.VERIFIED
    assert result.headings_matched == result.headings_expected == 1
    paragraph = result.prepared.meaningful[1]
    assert paragraph.index == 2 and paragraph.kind == 'paragraph'
    assert paragraph.normalized_text == 'A bold paragraph.'
    assert paragraph.text_hash == sha256(b'A bold paragraph.').hexdigest()
    assert paragraph.text_length == len('A bold paragraph.')
    assert 'Private title' not in repr(result)
    assert 'A bold paragraph.' not in body_inspect.format_body_formatting(result)


def test_nested_paragraph_like_nodes_are_not_double_counted(inspect):
    result = inspect('Article text.', '<section><div role="paragraph"><p>Article text.</p></div></section>')
    assert result.classification == Classification.VERIFIED
    assert len(result.remote.meaningful) == 1
    assert result.remote.meaningful[0].path == '1.0.0'
    assert result.remote.meaningful[0].top_level_index == 1


def test_raw_counts_differ_but_hidden_and_editor_helpers_are_harmless(inspect):
    result = inspect('Article text.', '''<div><p>Article text.</p></div>
        <p hidden>Article text.</p><p style="display:none">Article text.</p>
        <div class="ProseMirror-widget"><p>Placeholder</p></div>''')
    assert result.prepared.raw_paragraph_count == 1
    assert result.remote.raw_paragraph_count == 4
    assert result.remote.hidden_paragraphs == 2
    assert result.remote.helper_paragraphs == 1
    assert result.classification == Classification.VERIFIED
    assert not result.repeated_text


def test_empty_editor_widget_paragraph_is_excluded(inspect):
    result = inspect('Article.', '<p>Article.</p><p class="ProseMirror-widget"><br></p>')
    assert result.remote.helper_paragraphs == 1
    assert not result.remote.blanks
    assert result.classification == Classification.VERIFIED


def test_real_blank_paragraph_with_caret_helper_is_not_discarded(inspect):
    result = inspect('# 01\n\nArticle.', '''<h1>01</h1>
        <p><br><br class="ProseMirror-trailingBreak"></p><p>Article.</p>
        <p><br><br class="ProseMirror-trailingBreak"></p>''')
    assert result.complete_text_matches
    assert result.classification == Classification.DEFECT
    assert result.unexpected_blank_indexes == (2, 4)
    assert len(result.remote.blanks) == 2
    assert result.remote.trailing_break_helpers == 2
    assert result.remote.helper_paragraphs == 0
    assert result.remote.consecutive_blank_pairs == 0
    assert all(b.height > 0 and b.br_count == 1 for b in result.remote.blanks)
    assert [(a.prepared_indexes, a.remote_indexes) for a in result.alignment] == [((1,), (1,)), ((2,), (3,))]


def test_consecutive_blank_paragraphs_and_placeholder_still_take_space(inspect):
    result = inspect('Article.', '''<p>Article.</p><p><br></p>
        <p data-placeholder="Write something" class="is-empty"><br class="ProseMirror-trailingBreak"></p>
        <p aria-hidden="true"><br></p>''')
    assert len(result.remote.blanks) == 3
    assert result.remote.consecutive_blank_pairs == 2
    assert result.classification == Classification.DEFECT


def test_actual_duplicate_body_paragraph(inspect):
    result = inspect('Original paragraph.', '<p>Original paragraph.</p><p>Original paragraph.</p>')
    assert not result.complete_text_matches
    assert result.classification == Classification.DEFECT
    assert result.repeated_text[0].classification == 'actual duplicate article content'
    assert result.repeated_text[0].remote_indexes == (1, 2)


def test_duplicate_text_inside_one_block_is_not_deduplicated(inspect):
    result = inspect('Original.', '<p><span>Original.</span> <span>Original.</span></p>')
    assert not result.complete_text_matches
    assert result.classification == Classification.DEFECT


@pytest.mark.parametrize('html,status', [
    ('<p>First.</p><p>Third.</p>', 'delete'),
    ('<p>Second.</p><p>First.</p><p>Third.</p>', 'insert'),
])
def test_missing_and_reordered_paragraphs(inspect, html, status):
    result = inspect('First.\n\nSecond.\n\nThird.', html)
    assert result.classification == Classification.DEFECT
    assert not result.complete_text_matches
    assert any(a.status == status for a in result.alignment)


@pytest.mark.parametrize('html', [
    '<h2>Chapter</h2><p>Article.</p>',
    '<p>Chapter</p><p>Article.</p>',
    '<p>Article.</p><h1>Chapter</h1>',
])
def test_heading_level_type_and_order(inspect, html):
    result = inspect('# Chapter\n\nArticle.', html)
    assert result.classification == Classification.DEFECT


def test_visually_split_paragraph_preserves_text_and_heading_boundaries(inspect):
    result = inspect('# Chapter\n\nFirst sentence. Second sentence.\n\n## End',
                     '<h1>Chapter</h1><p>First sentence.</p><p>Second sentence.</p><h2>End</h2>')
    assert result.classification == Classification.SPLIT
    assert result.complete_text_matches
    assert result.headings_matched == 2
    assert result.alignment[1].status == 'paragraph_split'
    assert result.alignment[1].prepared_indexes == (2,)
    assert result.alignment[1].remote_indexes == (2, 3)


def test_complete_text_match_does_not_mask_changed_block_boundaries(inspect):
    result = inspect('Alpha beta.\n\nGamma delta.', '<p>Alpha</p><p>beta. Gamma delta.</p>')
    assert result.complete_text_matches
    assert result.classification == Classification.DEFECT


@pytest.mark.parametrize('html', ['<p>First line. Second line.</p>', '<p>First line.<br>Second line.</p>'])
def test_soft_newline_may_collapse_or_become_br_without_paragraph_split(inspect, html):
    result = inspect('First line.\nSecond line.', html)
    assert result.classification == Classification.VERIFIED
    assert len(result.remote.meaningful) == 1


@pytest.mark.parametrize('markdown,html', [
    ('First.  \nSecond.', '<p>First. Second.</p>'),
    ('First. Second.', '<p>First.<br>Second.</p>'),
    ('First.\nSecond.', '<p>First.<br><br>Second.</p>'),
])
def test_missing_or_extra_hard_breaks_are_detected(inspect, markdown, html):
    result = inspect(markdown, html)
    assert result.complete_text_matches
    assert result.classification == Classification.DEFECT
    assert result.break_mismatch_indexes == (1,)


def test_expected_repeated_short_text_and_headings(inspect):
    result = inspect('# Repeat\n\nYes.\n\n# Repeat\n\nYes.',
                     '<h1>Repeat</h1><p>Yes.</p><h1>Repeat</h1><p>Yes.</p>')
    assert result.classification == Classification.VERIFIED
    assert {r.classification for r in result.repeated_text} == {
        'expected heading repetition', 'expected repeated short text',
    }


def test_list_and_quote_wrappers_are_preserved_without_duplicate_text(inspect):
    result = inspect('> Quote.\n\n- One\n- Two',
                     '<blockquote><div><p>Quote.</p></div></blockquote><ul><li><p>One</p></li><li>Two</li></ul>')
    assert result.classification == Classification.VERIFIED
    assert len(result.remote.meaningful) == 3
    assert result.remote.meaningful[1].containers == ('ul', 'li')


def test_changed_list_boundaries_do_not_verify_from_text_alone(inspect):
    result = inspect('- One\n- Two', '<p>One</p><p>Two</p>')
    assert result.complete_text_matches
    assert result.classification == Classification.DEFECT


def test_unsupported_media_is_unknown(inspect):
    result = inspect('Article.', '<p>Article.</p><img src="data:,">')
    assert result.classification == Classification.UNKNOWN


def test_mixed_wrapper_text_is_not_silently_dropped(inspect):
    result = inspect('Article.', '<div>Unmodeled text<p>Article.</p></div>')
    assert result.classification == Classification.UNKNOWN


def test_hidden_inline_text_is_not_an_article_duplicate(inspect):
    result = inspect('Article.', '<p>Article.<span hidden>Article.</span></p>')
    assert result.classification == Classification.VERIFIED


def test_application_and_cli_use_write_blocking_browser_without_sqlite_connection(tmp_path, monkeypatch):
    tmp_path = tmp_path / 'project'
    tmp_path.mkdir()
    (tmp_path / 'In').mkdir()
    (tmp_path / 'In/story.md').write_text('---\ntitle: Story\ndescription: Subtitle\n---\nArticle.')
    (tmp_path / 'config.toml').write_text('[substack]\npublication_url="https://example.substack.com"')
    paths = runtime_paths(tmp_path)
    paths.substack_browser_profile.mkdir(parents=True)
    paths.data.mkdir(parents=True)
    paths.database.write_bytes(b'Not even an openable database: checksum only.')
    original = paths.database.read_bytes()
    page, context = MagicMock(), MagicMock()
    context.new_page.return_value = page
    draft = 'https://example.substack.com/publish/post/123'
    inspection = SimpleNamespace(visible_title='Story')

    @contextmanager
    def launch(*args, **kwargs):
        assert kwargs['read_only'] is True
        yield context

    monkeypatch.setattr(application, 'persistent_browser', launch)
    monkeypatch.setattr(reconcile, 'verify_supplied_draft', lambda *a, **k: SimpleNamespace(verified=True, inspection=inspection))
    monkeypatch.setattr(reconcile, 'verified_not_published_evidence', lambda *a: ('verified',))
    monkeypatch.setattr(subtitle, 'inspect_subtitle', lambda *a: (None, 'Subtitle'))
    monkeypatch.setattr(body, 'RateLimitMonitor', MagicMock())
    monkeypatch.setattr(body, 'locate_body_surface', lambda *a: MagicMock())
    report = object()
    monkeypatch.setattr(body_inspect, 'inspect_body_formatting', lambda *a: report)
    monkeypatch.setattr(body_inspect, 'format_body_formatting', lambda *a: 'Content report')
    monkeypatch.setattr(application.sqlite3, 'connect', lambda *a, **k: pytest.fail('SQLite opened'))
    text = application.inspect_substack_body_formatting(tmp_path, draft)
    assert 'SQLite unchanged: Yes' in text
    assert paths.database.read_bytes() == original
    page.click.assert_not_called()
    page.fill.assert_not_called()
    page.evaluate.assert_not_called()
    cli_inspect = MagicMock(return_value=text)
    monkeypatch.setattr(cli, 'inspect_substack_body_formatting', cli_inspect)
    assert cli.main(['substack-inspect-draft', '--draft-url', draft, '--body-formatting']) == 0
    cli_inspect.assert_called_once()

    def changed_database(*args):
        paths.database.write_bytes(b'Unexpected external change')
        return report

    monkeypatch.setattr(body_inspect, 'inspect_body_formatting', changed_database)
    with pytest.raises(BrowserSessionError, match='SQLite changed'):
        application.inspect_substack_body_formatting(tmp_path, draft)

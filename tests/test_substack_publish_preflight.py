"""Exact publish preflight against isolated local DOM; no Substack requests."""

from html import escape

import pytest
from playwright.sync_api import sync_playwright

from publish_to_all.browser.publish_preflight import verify_prepared_content
from publish_to_all.errors import BrowserSessionError
from publish_to_all.story import load_story


@pytest.fixture(scope='module')
def browser():
    with sync_playwright() as driver:
        browser = driver.chromium.launch(headless=True)
        yield browser
        browser.close()


@pytest.fixture
def draft(browser, tmp_path):
    source = tmp_path / 'story.md'
    source.write_text('---\ntitle: Story\nDescription: Exact subtitle\n---\n'
                      '# One\n\nFirst paragraph.\n\n# Two\n\nSecond paragraph.')
    story = load_story(tmp_path)
    page = browser.new_page()
    page.route('**/*', lambda route: route.abort())
    page.set_content(
        '<textarea placeholder="Title">Story</textarea>'
        f'<textarea data-testid="subtitle">{escape(story.metadata.description)}</textarea>'
        f'<div class="ProseMirror" contenteditable="true">{story.html}</div>'
    )
    yield page, story
    page.close()


def test_exact_preflight_reads_only_and_can_revalidate_behind_modal(draft):
    page, story = draft
    before = page.content()
    check = verify_prepared_content(page, story)
    assert page.content() == before
    assert 'Meaningful body blocks: 4 / 4' in check.evidence
    assert 'Headings matched: 2 / 2' in check.evidence
    assert 'Story paragraphs matched: 2 / 2' in check.evidence
    assert 'Unintended blank paragraphs: 0' in check.evidence
    page.evaluate("document.body.insertAdjacentHTML('beforeend', '<div role=dialog>Publish</div>')")
    check.require_unchanged()


@pytest.mark.parametrize('mutation', [
    "document.querySelector('[data-testid=subtitle]').value = 'Different'",
    "document.querySelector('textarea').value = 'Different'",
    "document.querySelector('.ProseMirror p').remove()",
    "document.querySelector('.ProseMirror').append(document.querySelector('.ProseMirror p').cloneNode(true))",
    "document.querySelector('.ProseMirror').insertAdjacentHTML('beforeend', '<p><br></p>')",
    "document.querySelector('.ProseMirror').prepend(document.querySelector('.ProseMirror h1:last-of-type'))",
])
def test_preflight_rejects_content_mismatch_and_never_repairs_it(draft, mutation):
    page, story = draft
    page.evaluate(mutation)
    before = page.content()
    with pytest.raises(BrowserSessionError):
        verify_prepared_content(page, story)
    assert page.content() == before


@pytest.mark.parametrize('mutation', [
    "document.querySelector('.ProseMirror p').textContent = 'Changed after preflight'",
    "document.body.insertAdjacentHTML('beforeend', '<span>Published</span>')",
])
def test_preclick_detects_new_content_or_publication(draft, mutation):
    page, story = draft
    check = verify_prepared_content(page, story)
    page.evaluate(mutation)
    with pytest.raises(BrowserSessionError):
        check.require_unchanged()

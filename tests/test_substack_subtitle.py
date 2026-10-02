from hashlib import sha256
from unittest.mock import MagicMock

import pytest

from publish_to_all.browser import body, editor, subtitle
from publish_to_all.errors import BrowserSessionError, PublishToAllError
from publish_to_all.publishers.substack import SubstackSubtitlePublisher
from publish_to_all.state import PublicationRepository
from publish_to_all.story import load_story


def test_subtitle_selector_uses_semantic_editor_evidence():
    page = MagicMock()
    role = MagicMock()
    placeholder = MagicMock()
    test_id = MagicMock()
    page.get_by_role.return_value = role
    page.get_by_placeholder.return_value = placeholder
    page.locator.return_value = test_id
    role.or_.return_value = placeholder
    placeholder.or_.return_value = test_id

    assert subtitle.subtitle_fields(page) is test_id
    page.get_by_role.assert_called_once_with('textbox', name=subtitle.SUBTITLE_NAME)
    page.get_by_placeholder.assert_called_once_with(subtitle.SUBTITLE_NAME)
    page.locator.assert_called_once_with('[data-testid="subtitle"]')


def test_subtitle_field_must_be_unique_and_editable(monkeypatch):
    field = MagicMock()
    field.is_visible.return_value = True
    field.is_editable.return_value = False
    monkeypatch.setattr(subtitle, 'subtitle_fields', lambda _: MagicMock(all=lambda: [field]))
    with pytest.raises(BrowserSessionError, match='not editable'):
        subtitle.locate_subtitle_field(MagicMock())


def test_subtitle_fill_rejects_existing_mismatch():
    field = MagicMock()
    field.get_attribute.return_value = None
    field.input_value.return_value = 'Existing subtitle'
    with pytest.raises(BrowserSessionError, match='different content'):
        subtitle.insert_exact_subtitle(field, 'Description')
    field.fill.assert_not_called()


def test_subtitle_fill_is_exactly_once_and_verifies_visible_value():
    field = MagicMock()
    field.get_attribute.return_value = None
    field.input_value.side_effect = ['', 'Description']
    subtitle.insert_exact_subtitle(field, 'Description')
    field.fill.assert_called_once_with('Description')
    field.blur.assert_called_once_with()


@pytest.mark.parametrize('remote_subtitle', ['Description', '', 'Different subtitle'])
def test_recorded_subtitle_is_read_only_before_body(tmp_path, monkeypatch, remote_subtitle):
    (tmp_path / 'story.md').write_text(
        '---\ntitle: Story\ndescription: Description\n---\nStory body.'
    )
    story = load_story(tmp_path)
    database = tmp_path / 'publications.sqlite3'
    repository = PublicationRepository(database)
    attempt = repository.begin_attempt(story, 'substack')
    record = repository.mark_draft_created(
        attempt.id, 'https://example.substack.com/publish/post/123',
    )
    record = repository.mark_subtitle_matched(record)
    before = sha256(database.read_bytes()).hexdigest()
    page = MagicMock(url=record.draft_url)
    title_field = MagicMock()
    title_field.is_visible.return_value = True
    fields = MagicMock()
    fields.all.return_value = [title_field]
    surface = MagicMock()
    surface.inner_text.return_value = ''
    field = MagicMock()
    monkeypatch.setattr(editor, 'title_fields', lambda _: fields)
    monkeypatch.setattr(editor, 'title_value', lambda _: 'Story')
    monkeypatch.setattr(body, 'locate_body_surface', lambda _: surface)
    monkeypatch.setattr(subtitle, 'locate_subtitle_field', lambda _: field)
    monkeypatch.setattr(subtitle, 'subtitle_value', lambda _: remote_subtitle)
    monkeypatch.setattr(editor, 'start_save_observation', MagicMock())
    publisher = SubstackSubtitlePublisher(
        repository, page, 'https://example.substack.com', MagicMock(),
    )

    if remote_subtitle == 'Description':
        assert publisher.add_subtitle(story, record) == record
    else:
        with pytest.raises(PublishToAllError):
            publisher.add_subtitle(story, record)

    field.fill.assert_not_called()
    field.blur.assert_not_called()
    assert repository.get_publication(story.source_hash, 'substack') == record
    assert sha256(database.read_bytes()).hexdigest() == before

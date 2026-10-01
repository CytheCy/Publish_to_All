from unittest.mock import MagicMock

import pytest

from publish_to_all.browser import subtitle
from publish_to_all.errors import BrowserSessionError


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

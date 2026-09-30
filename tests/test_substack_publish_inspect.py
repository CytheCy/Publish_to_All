from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from PIL import Image
import pytest

from publish_to_all import application
from publish_to_all.browser import image_observe, publish_inspect, reconcile
from publish_to_all.cli import main
from publish_to_all.config import runtime_paths
from publish_to_all.errors import BrowserSessionError
from publish_to_all.state import (
    BodyStatus, ImageStatus, PublicationRepository, StateError,
)
from publish_to_all.story import load_story


URL = 'https://example.substack.com'
DRAFT = URL + '/publish/post/123'


class Node:
    def __init__(self, *, evidence=None, visible=True, enabled=True):
        self.evidence = evidence or {}
        self.visible = visible
        self.enabled = enabled
        self.click = MagicMock()

    def is_visible(self):
        return self.visible

    def is_enabled(self):
        return self.enabled

    def evaluate(self, _script):
        return self.evidence


class Locator:
    def __init__(self, nodes=()):
        self.nodes = list(nodes)

    def all(self):
        return self.nodes


class ContinuePage:
    def __init__(self, nodes):
        self.nodes = nodes

    def get_by_role(self, role, name=None, exact=False):
        assert (role, name, exact) == ('button', 'Continue', True)
        return Locator(self.nodes)


def continue_evidence(**overrides):
    result = {
        'tag': 'button', 'role': 'button', 'type': 'button', 'testId': 'publish-button',
        'inDialog': False, 'inEditor': True, 'inForm': False,
        'formAction': '', 'href': '', 'text': 'Continue',
    }
    result.update(overrides)
    return result


def test_correct_editor_continue_control_selected():
    node = Node(evidence=continue_evidence())
    selected = publish_inspect.select_continue_control(ContinuePage([node]))
    assert selected.locator is node
    assert selected.label == 'Continue'
    assert selected.test_id == 'publish-button'
    assert selected.context == 'div.editor.newsletter-post-editor'
    node.click.assert_not_called()


@pytest.mark.parametrize('nodes', [[], [Node(), Node()]])
def test_ambiguous_or_missing_continue_safely_stops(nodes):
    with pytest.raises(BrowserSessionError, match='missing or ambiguous'):
        publish_inspect.select_continue_control(ContinuePage(nodes))
    for node in nodes:
        node.click.assert_not_called()


@pytest.mark.parametrize('change', [
    {'type': 'submit'}, {'inDialog': True}, {'inEditor': False},
    {'inForm': True}, {'formAction': '/publish'}, {'href': '/publish'},
    {'testId': 'continue-button'},
])
def test_continue_must_be_navigation_not_final_form_action(change):
    node = Node(evidence=continue_evidence(**change))
    with pytest.raises(BrowserSessionError, match='navigation semantics'):
        publish_inspect.select_continue_control(ContinuePage([node]))
    node.click.assert_not_called()


def test_same_continue_text_in_wrong_context_is_rejected():
    node = Node(evidence=continue_evidence(inEditor=False))
    with pytest.raises(BrowserSessionError, match='navigation semantics'):
        publish_inspect.select_continue_control(ContinuePage([node]))
    node.click.assert_not_called()


def test_submit_style_continue_is_rejected_even_in_editor():
    node = Node(evidence=continue_evidence(type='submit', inForm=True))
    with pytest.raises(BrowserSessionError, match='navigation semantics'):
        publish_inspect.select_continue_control(ContinuePage([node]))
    node.click.assert_not_called()


def test_read_only_diagnostic_records_exact_live_signature_without_clicking():
    raw = continue_evidence(
        accessibleName='Continue', ariaLabel='', title='', text='Continue', href='',
        visible=True, enabled=True, appearsSubmit=False, form='',
        ancestors=['div class="editor typography newsletter-post-editor use-theme-bg"'],
        nearby=['Preview'],
    )
    node = Node(evidence=raw)
    result = publish_inspect.inspect_continue_candidates(ContinuePage([node]))
    assert result.visible_enabled_count == 1
    candidate = result.candidates[0]
    assert (candidate.tag, candidate.role, candidate.accessible_name) == (
        'button', 'button', 'Continue',
    )
    assert candidate.test_id == 'publish-button'
    assert candidate.element_type == 'button'
    assert candidate.in_editor and not candidate.appears_submit
    assert candidate.form_attributes == '[none]'
    assert candidate.nearby_labels == ('Preview',)
    node.click.assert_not_called()


def test_configuration_values_and_final_actions_are_classified(monkeypatch):
    monkeypatch.setattr(publish_inspect, '_raw_controls', lambda _scope: [
        {'label': 'Publish post', 'role': 'heading', 'type': 'h2', 'value': ''},
        {'label': 'Audience', 'role': 'group-label', 'type': 'legend', 'value': ''},
        {'label': 'Everyone', 'role': 'radio', 'type': 'radio', 'value': 'Selected'},
        {'label': 'Send via email', 'role': 'checkbox', 'type': 'checkbox',
         'value': 'Not selected', 'required': False},
        {'label': 'Publish now', 'role': 'button', 'type': 'button', 'value': ''},
    ])
    controls = publish_inspect._publication_controls(MagicMock())
    everyone = next(item for item in controls if item.label == 'Everyone')
    email = next(item for item in controls if item.label == 'Send via email')
    final = next(item for item in controls if item.label == 'Publish now')
    assert everyone.value == 'Selected' and everyone.control_type == 'radio'
    assert email.value == 'Not selected' and email.optional
    assert final.final_action and not final.optional


class FinalPage:
    def __init__(self, dialog, back=None):
        self.url = DRAFT
        self.dialog = dialog
        self.back = back
        self.wait_for_timeout = MagicMock()

    def locator(self, selector):
        if selector == '[role="dialog"]':
            return Locator([self.dialog])
        if selector == 'body':
            return self.dialog
        raise AssertionError(selector)

    def title(self):
        return 'Editing post | Substack'


class FinalScope(Node):
    def __init__(self, back=None):
        super().__init__()
        self.back = back

    def get_by_role(self, role, name=None, exact=False):
        if self.back is not None and (role, name, exact) == ('button', 'Back', True):
            return Locator([self.back])
        return Locator([])


def screen_controls(include_back=False):
    items = (
        publish_inspect.PublicationControl('Publish post', 'heading', 'h2', 'None', False, True, False),
        publish_inspect.PublicationControl('Everyone', 'radio', 'radio', 'Selected', False, True, False),
        publish_inspect.PublicationControl('Publish now', 'button', 'button', 'None', False, False, True),
    )
    if include_back:
        items += (publish_inspect.PublicationControl(
            'Back', 'button', 'button', 'None', False, True, False,
        ),)
    return items


def test_final_screen_detected_and_final_action_never_clicked(monkeypatch):
    continue_node = Node()
    final_action = Node()
    scope = FinalScope()
    page = FinalPage(scope)
    selected = publish_inspect.ContinueControl(
        continue_node, 'button', 'Continue', 'continue-button', 'button', 'editor header/toolbar',
    )
    monkeypatch.setattr(publish_inspect, '_publication_controls', lambda _scope: screen_controls())
    result = publish_inspect.inspect_final_publication_screen(
        page, URL, DRAFT, MagicMock(), publish_inspect.MutationGuard([]),
        continue_control=selected,
    )
    continue_node.click.assert_called_once()
    final_action.click.assert_not_called()
    assert result.final_actions == ('Publish now',)
    assert result.safe_exit is None


def test_safe_back_returns_to_editor_without_final_action(monkeypatch):
    continue_node, back = Node(), Node()
    scope = FinalScope(back)
    page = FinalPage(scope, back)
    selected = publish_inspect.ContinueControl(
        continue_node, 'button', 'Continue', '', 'button', 'editor header/toolbar',
    )
    monkeypatch.setattr(
        publish_inspect, '_publication_controls', lambda _scope: screen_controls(include_back=True),
    )
    result = publish_inspect.inspect_final_publication_screen(
        page, URL, DRAFT, MagicMock(), publish_inspect.MutationGuard([]),
        continue_control=selected,
    )
    back.click.assert_called_once()
    assert result.safe_exit == 'Back'
    assert 'returned to the draft editor' in result.exit_behavior


def test_mutation_guard_blocks_publish_transports():
    guard = publish_inspect.MutationGuard([])
    post = MagicMock(request=SimpleNamespace(method='POST'))
    get = MagicMock(request=SimpleNamespace(method='GET'))
    guard.handle(post)
    guard.handle(get)
    post.abort.assert_called_once()
    post.continue_.assert_not_called()
    get.continue_.assert_called_once()
    assert guard.blocked_methods == ['POST']


def make_project(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    root.mkdir()
    monkeypatch.chdir(root)
    (root / 'In').mkdir()
    (root / 'In/story.md').write_text(
        '---\ntitle: Story\n---\n' + ('A substantial body sentence. ' * 10)
    )
    Image.new('RGB', (1200, 630)).save(root / 'In/Social.png')
    (root / 'config.toml').write_text(f'[substack]\npublication_url = "{URL}"')
    paths = runtime_paths(root)
    paths.substack_browser_profile.mkdir(parents=True)
    story = load_story(root / 'In')
    repository = PublicationRepository(paths.database)
    attempt = repository.begin_attempt(story, 'substack')
    draft = repository.mark_draft_created(attempt.id, DRAFT)
    inserting = repository.mark_body_inserting(draft)
    body_done = repository.mark_body_inserted(inserting.id)
    uploading = repository.mark_image_uploading(body_done)
    record = repository.mark_image_uploaded(uploading)
    return root, story, repository, record, paths


def fake_live_path(monkeypatch, story, record, paths):
    page = MagicMock(url=DRAFT)
    context = MagicMock()
    context.new_page.return_value = page

    @contextmanager
    def browser(*_args, **_kwargs):
        yield context

    monkeypatch.setattr(application, 'persistent_browser', browser)
    monkeypatch.setattr(
        reconcile, 'verify_supplied_draft',
        lambda *_args, **_kwargs: reconcile.SuppliedDraftEvidence(
            True, 'verified', inspection=reconcile.DraftInspection(
                DRAFT, story.metadata.title, 'substantial', False, ('Saved',), True,
                DRAFT, 'Editing post', ('Draft', 'Saved', 'Continue', 'Settings'),
            ),
        ),
    )
    monkeypatch.setattr(
        image_observe, 'inspect_social_preview_image',
        lambda *_args, **_kwargs: SimpleNamespace(state=SimpleNamespace(value='present')),
    )
    monkeypatch.setattr(publish_inspect, 'close_preflight_dialogs', lambda *_args: None)
    selected = publish_inspect.ContinueControl(
        MagicMock(), 'button', 'Continue', 'continue-button', 'button', 'editor header/toolbar',
    )
    monkeypatch.setattr(publish_inspect, 'select_continue_control', lambda *_args: selected)
    monkeypatch.setattr(
        publish_inspect, 'install_mutation_guard',
        lambda *_args: publish_inspect.MutationGuard([]),
    )
    final = publish_inspect.FinalScreenInspection(
        DRAFT, 'Publish post', screen_controls(), ('Publish now',), None,
        'Browser closed on final publication screen; no exit control was used.', (),
    )
    inspect = MagicMock(return_value=final)
    monkeypatch.setattr(publish_inspect, 'inspect_final_publication_screen', inspect)
    return inspect


def test_command_is_read_only_in_sqlite_and_reports_defaults(tmp_path, monkeypatch, capsys):
    root, story, _repository, record, paths = make_project(tmp_path, monkeypatch)
    inspect = fake_live_path(monkeypatch, story, record, paths)
    before = paths.database.read_bytes()
    assert main(['substack-publish-inspect']) == 0
    assert paths.database.read_bytes() == before
    output = capsys.readouterr()
    assert not output.err
    assert 'Current value/default: Selected' in output.out
    assert 'Nothing published: Yes' in output.out
    inspect.assert_called_once()


def test_continue_diagnostic_command_never_clicks_and_preserves_sqlite(
    tmp_path, monkeypatch, capsys,
):
    _root, story, _repository, record, paths = make_project(tmp_path, monkeypatch)
    final = fake_live_path(monkeypatch, story, record, paths)
    diagnostic = publish_inspect.ContinueDiagnostic((
        publish_inspect.ContinueCandidateEvidence(
            'button', 'button', 'Continue', 'Continue', '[none]', '[none]',
            'publish-button', 'button', '[none]', True, True, False, True, False,
            '[none]', 'div class="editor newsletter-post-editor"', ('Preview',),
        ),
    ))
    inspect_candidates = MagicMock(return_value=diagnostic)
    monkeypatch.setattr(publish_inspect, 'inspect_continue_candidates', inspect_candidates)
    before = paths.database.read_bytes()
    assert main(['substack-publish-inspect', '--continue-diagnostic-only']) == 0
    assert paths.database.read_bytes() == before
    output = capsys.readouterr()
    assert 'data-testid: publish-button' in output.out
    assert 'Continue clicked: No' in output.out
    assert 'Nothing published: Yes' in output.out
    inspect_candidates.assert_called_once()
    final.assert_not_called()


@pytest.mark.parametrize('broken', ['body', 'image', 'reconciliation'])
def test_preflight_requires_fully_prepared_draft(tmp_path, monkeypatch, broken):
    _root, story, repository, record, _paths = make_project(tmp_path, monkeypatch)
    with repository._connection(write=True) as connection:
        if broken == 'body':
            connection.execute(
                'UPDATE publications SET body_status = ? WHERE id = ?',
                (BodyStatus.NOT_STARTED, record.id),
            )
        elif broken == 'image':
            connection.execute(
                'UPDATE publications SET image_status = ? WHERE id = ?',
                (ImageStatus.NOT_STARTED, record.id),
            )
        else:
            connection.execute(
                'UPDATE publications SET needs_reconciliation = 1 WHERE id = ?', (record.id,),
            )
    with pytest.raises(StateError):
        repository.require_publish_inspection_candidate(story.source_hash, 'substack')


def test_rate_limit_stops_before_social_preview_or_continue(tmp_path, monkeypatch, capsys):
    _root, story, _repository, record, paths = make_project(tmp_path, monkeypatch)
    page = MagicMock(url=DRAFT)
    context = MagicMock()
    context.new_page.return_value = page

    @contextmanager
    def browser(*_args, **_kwargs):
        yield context

    monkeypatch.setattr(application, 'persistent_browser', browser)
    monkeypatch.setattr(
        reconcile, 'verify_supplied_draft',
        lambda *_args, **_kwargs: reconcile.SuppliedDraftEvidence(
            False, 'HTTP 429', rate_limited=True,
        ),
    )
    social = MagicMock()
    final = MagicMock()
    monkeypatch.setattr(image_observe, 'inspect_social_preview_image', social)
    monkeypatch.setattr(publish_inspect, 'inspect_final_publication_screen', final)
    before = paths.database.read_bytes()
    assert main(['substack-publish-inspect']) == 1
    assert 'rate limiting' in capsys.readouterr().err.lower()
    assert paths.database.read_bytes() == before
    social.assert_not_called()
    final.assert_not_called()

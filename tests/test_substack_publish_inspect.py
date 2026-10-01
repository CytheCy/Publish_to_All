from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from PIL import Image
import pytest

from publish_to_all import application
from publish_to_all.browser import image_observe, publish_inspect, publish_navigate, reconcile
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
    monkeypatch.setattr(publish_inspect, '_raw_publication_controls', lambda _scope: [
        {'label': 'Everyone', 'accessibleName': 'Everyone', 'role': 'radio',
         'type': 'radio', 'value': 'Selected', 'selected': True, 'group': 'Audience'},
        {'label': 'Send via email', 'role': 'checkbox', 'type': 'checkbox',
         'accessibleName': 'Send via email', 'value': 'Not selected', 'selected': False,
         'required': False, 'group': 'Delivery', 'attributes': ['name=email']},
        {'label': 'Publish now', 'accessibleName': 'Publish now', 'role': 'button',
         'type': 'button', 'value': '', 'attributes': ['data-testid=publish']},
    ])
    controls = publish_inspect._publication_controls(MagicMock())
    everyone = next(item for item in controls if item.label == 'Everyone')
    email = next(item for item in controls if item.label == 'Send via email')
    final = next(item for item in controls if item.label == 'Publish now')
    assert everyone.value == 'Selected' and everyone.control_type == 'radio'
    assert everyone.selected is True and everyone.group == 'Audience'
    assert email.value == 'Not selected' and email.optional
    assert not email.selected and email.semantic_attributes == ('name=email',)
    assert final.final_action and not final.optional


def test_select_and_text_date_values_are_read_without_interaction(monkeypatch):
    monkeypatch.setattr(publish_inspect, '_raw_publication_controls', lambda _scope: [
        {'label': 'Section', 'accessibleName': 'Section', 'role': 'combobox',
         'type': 'select', 'value': 'Main publication', 'group': 'Destination'},
        {'label': 'Publish date', 'accessibleName': 'Publish date', 'role': 'textbox',
         'type': 'date', 'value': '2026-10-01', 'group': 'Scheduling'},
        {'label': 'Preview text', 'accessibleName': 'Preview text', 'role': 'textbox',
         'type': 'text', 'value': 'A short preview', 'group': 'Other options'},
    ])
    scope = MagicMock()
    controls = publish_inspect._publication_controls(scope)
    assert [(item.label, item.value, item.group) for item in controls] == [
        ('Section', 'Main publication', 'Destination'),
        ('Publish date', '2026-10-01', 'Scheduling'),
        ('Preview text', 'A short preview', 'Other options'),
    ]
    scope.get_by_role.assert_not_called()


def test_custom_combobox_open_state_is_not_treated_as_its_value():
    script = MagicMock(return_value=[{
        'label': 'Select or create tags', 'accessibleName': 'Select or create tags',
        'role': 'combobox', 'type': 'button', 'value': '', 'selected': None,
        'group': 'Tags', 'attributes': ['role=combobox', 'aria-expanded=false'],
        'disabled': True,
    }])
    scope = MagicMock(evaluate=script)
    controls = publish_inspect._publication_controls(scope)
    assert controls[0].value == 'None'
    assert not controls[0].enabled


def test_same_label_controls_in_different_semantic_groups_are_preserved(monkeypatch):
    monkeypatch.setattr(publish_inspect, '_raw_publication_controls', lambda _scope: [
        {'label': 'Everyone', 'accessibleName': 'Everyone', 'role': 'radio',
         'type': 'radio', 'value': 'Selected', 'selected': True,
         'attributes': ['name=audience']},
        {'label': 'Everyone', 'accessibleName': 'Everyone', 'role': 'radio',
         'type': 'radio', 'value': 'Selected', 'selected': True,
         'attributes': ['name=commentLevel']},
    ])
    controls = publish_inspect._publication_controls(MagicMock())
    assert [(item.label, item.group) for item in controls] == [
        ('Everyone', 'Audience'), ('Everyone', 'Comments'),
    ]


def test_live_scheduling_off_dom_fixture_is_preserved():
    scope = MagicMock()
    scope.evaluate.return_value = {
        'target': {
            'element': 'button', 'role': 'checkbox',
            'name': 'Schedule time to email and publish', 'checked': False,
            'enabled': True,
            'attributes': [
                'data-testid=scheduled-at', 'type=button', 'role=checkbox',
                'aria-checked=false',
            ],
            'ancestors': [
                'label', 'div', 'div', 'div', 'div', 'div', 'div', 'div',
                'div[data-testid=publish-modal, role=dialog, aria-labelledby=publish-title]',
            ],
        },
        'matchCount': 1, 'visibleMatchCount': 1, 'hiddenMatchCount': 0,
        'native': [
            'input[name=scheduled-at, type=checkbox]; name="scheduled-at"; '
            'visible=false; enabled=true; checked=false',
        ],
        'delivery': (
            'button[type=button, role=checkbox, aria-checked=true]; '
            'name="Send via email and the Substack app"; '
            'visible=true; enabled=true; checked=true'
        ),
        'relationship': (
            'same div ancestor; scheduling distance=8; delivery distance=8; '
            'scheduling follows delivery=true'
        ),
        'deliveryMatchCount': 1,
    }
    evidence = publish_inspect._scheduling_evidence(scope)
    assert evidence.state == 'OFF'
    assert evidence.semantic_element == 'button'
    assert evidence.role == 'checkbox'
    assert evidence.accessible_name == 'Schedule time to email and publish'
    assert evidence.checked_semantics == 'false' and evidence.enabled
    assert evidence.exact_name_match_count == 1
    assert evidence.visible_exact_name_match_count == 1
    assert evidence.hidden_exact_name_match_count == 0
    assert evidence.stable_attributes == (
        'data-testid=scheduled-at', 'type=button', 'role=checkbox',
        'aria-checked=false',
    )
    assert 'visible=false' in evidence.related_controls[1]


def test_scheduling_fixture_fails_closed_on_hidden_exact_name_duplicate():
    scope = MagicMock()
    scope.evaluate.return_value = {
        'target': {
            'element': 'button', 'role': 'checkbox',
            'name': 'Schedule time to email and publish', 'checked': False,
            'enabled': True,
            'attributes': [
                'data-testid=scheduled-at', 'type=button', 'role=checkbox',
                'aria-checked=false',
            ],
            'ancestors': ['label'],
        },
        'matchCount': 2, 'visibleMatchCount': 1, 'hiddenMatchCount': 1,
        'native': [], 'delivery': '[not uniquely identified]',
        'relationship': '[no unique common relationship]', 'deliveryMatchCount': 0,
    }
    assert publish_inspect._scheduling_evidence(scope).state == 'UNKNOWN'


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
        publish_inspect.PublicationControl(
            'Everyone', 'radio', 'radio', 'Selected', False, True, False,
            accessible_name='Everyone', selected=True, group='Audience',
            mutates_configuration=True,
        ),
        publish_inspect.PublicationControl(
            'Publish now', 'button', 'button', 'None', False, False, True,
            accessible_name='Publish now', mutates_configuration=True,
        ),
    )
    if include_back:
        items += (publish_inspect.PublicationControl(
            'Back', 'button', 'button', 'None', False, True, False,
            accessible_name='Back', safe_navigation=True,
        ),)
    return items


def open_screen(include_back=False, evidence=None):
    controls = screen_controls(include_back)
    return publish_inspect.FinalScreenInspection(
        DRAFT, 'Publish post', controls,
        tuple(item.label for item in controls if item.final_action), None,
        'Browser remains on the final publication screen.', (), 'dialog',
        ('scope=dialog',), final_action_evidence=evidence,
    )


def test_final_screen_detected_and_final_action_never_clicked(monkeypatch):
    continue_node = Node()
    final_action = Node()
    scope = FinalScope()
    page = FinalPage(scope)
    selected = publish_inspect.ContinueControl(
        continue_node, 'button', 'Continue', 'continue-button', 'button', 'editor header/toolbar',
    )
    monkeypatch.setattr(publish_inspect, '_publication_controls', lambda _scope: screen_controls())
    monkeypatch.setattr(
        publish_navigate, 'inspect_open_final_publication_screen',
        lambda *_args: open_screen(),
    )
    result = publish_navigate.open_and_inspect_final_publication_screen(
        page, URL, DRAFT, MagicMock(), publish_inspect.MutationGuard([]),
        continue_control=selected,
    )
    continue_node.click.assert_called_once()
    final_action.click.assert_not_called()
    assert result.final_actions == ('Publish now',)
    assert result.safe_exit is None
    assert result.screen_kind == 'dialog'
    assert result.screen_attributes == ('scope=dialog',)


def test_final_screen_requires_a_visible_final_action(monkeypatch):
    continue_node = Node()
    page = FinalPage(FinalScope())
    selected = publish_inspect.ContinueControl(
        continue_node, 'button', 'Continue', 'publish-button', 'button',
        'div.editor.newsletter-post-editor',
    )
    monkeypatch.setattr(publish_navigate, 'monotonic', MagicMock(side_effect=[0, 11]))
    monkeypatch.setattr(publish_inspect, '_publication_controls', lambda _scope: ())
    with pytest.raises(BrowserSessionError, match='could not be positively identified'):
        publish_navigate.open_and_inspect_final_publication_screen(
            page, URL, DRAFT, MagicMock(), publish_inspect.MutationGuard([]),
            continue_control=selected,
        )
    continue_node.click.assert_called_once()


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
    monkeypatch.setattr(
        publish_navigate, 'inspect_open_final_publication_screen',
        lambda *_args: open_screen(include_back=True),
    )
    result = publish_navigate.open_and_inspect_final_publication_screen(
        page, URL, DRAFT, MagicMock(), publish_inspect.MutationGuard([]),
        continue_control=selected,
    )
    back.click.assert_called_once()
    assert result.safe_exit == 'Back'
    assert 'returned to the draft editor' in result.exit_behavior


def test_final_action_diagnostic_closes_browser_without_clicking_any_final_screen_control(
    monkeypatch,
):
    continue_node, back = Node(), Node()
    page = FinalPage(FinalScope(back), back)
    selected = publish_inspect.ContinueControl(
        continue_node, 'button', 'Continue', 'publish-button', 'button',
        'div.editor.newsletter-post-editor',
    )
    monkeypatch.setattr(
        publish_inspect, '_publication_controls', lambda _scope: screen_controls(include_back=True),
    )
    evidence = publish_inspect.FinalActionEvidence(
        'Send to everyone now', (), 0, 0, 0, 0, 0, 1, False, True,
    )
    collector = MagicMock(return_value=evidence)
    monkeypatch.setattr(publish_inspect, 'inspect_final_action_evidence', collector)
    result = publish_navigate.open_and_inspect_final_publication_screen(
        page, URL, DRAFT, MagicMock(), publish_inspect.MutationGuard([]),
        continue_control=selected, leave_open=True,
    )
    continue_node.click.assert_called_once()
    back.click.assert_not_called()
    assert result.safe_exit is None
    assert result.final_action_evidence is evidence
    assert result.exit_behavior == (
        'Browser closed on final publication screen; no exit control was used.'
    )


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
    with pytest.raises(BrowserSessionError, match='write-like network activity'):
        guard.require_clear()


@pytest.mark.parametrize('method', ['POST', 'PUT', 'PATCH', 'DELETE'])
def test_write_like_request_records_redacted_diagnostic_metadata(method):
    request = SimpleNamespace(
        method=method,
        url='https://example.substack.com/api/graphql?operationName=LoadConfig&csrf=secret',
        resource_type='fetch',
        redirected_from=None,
        initiator=None,
    )
    observer = publish_inspect.WriteLikeNetworkObserver([])
    observer.set_stage('PUBLISH_SCREEN_LOAD')
    observer.observe(request)

    item = observer.first_diagnostic
    assert item.http_method == method
    assert item.host == 'example.substack.com'
    assert item.path == '/api/graphql'
    assert item.query_parameter_names == ('csrf', 'operationName')
    assert item.resource_type == 'fetch'
    assert item.initiator_type == '[unavailable]'
    assert item.timing_stage == 'PUBLISH_SCREEN_LOAD'
    assert item.operation_name == 'LoadConfig'
    rendered = item.format()
    assert 'secret' not in rendered
    assert 'csrf=secret' not in rendered
    assert 'cookie' not in rendered.lower()
    assert 'authorization' not in rendered.lower()
    assert 'body' not in rendered.lower()


def request(method, url, resource_type):
    return SimpleNamespace(method=method, url=url, resource_type=resource_type,
                           redirected_from=None, initiator=None)


TELEMETRY = [
    ('firehose', request('POST', 'https://cyporter.substack.com/api/v1/firehose/batch', 'ping')),
    ('sentry', request('POST', 'https://o350427.ingest.sentry.io/api/4504244653457408/envelope/', 'fetch')),
    ('cloudflare', request('POST', 'https://cloudflareinsights.com/cdn-cgi/rum', 'xhr')),
    ('remarketing', request('POST', 'https://www.google.com/rmkt/collect/316245675/', 'fetch')),
    ('ccm', request('POST', 'https://www.google.com/ccm/collect', 'fetch')),
]


REFERRAL = request(
    'PUT', 'https://cyporter.substack.com/api/v1/user/writer_referrals/code', 'fetch',
)


@pytest.mark.parametrize('name, telemetry', TELEMETRY)
def test_exact_known_telemetry_is_aborted_and_recorded_as_suppressed(name, telemetry):
    route = SimpleNamespace(request=telemetry, continue_=MagicMock(), abort=MagicMock())
    observer = publish_inspect.WriteLikeNetworkObserver([], URL)
    guard = publish_inspect.MutationGuard([], observer.diagnostics, observer=observer)
    guard.set_stage('DRAFT_EDITOR_LOAD')
    guard.handle(route)
    route.abort.assert_called_once_with()
    route.continue_.assert_not_called()
    assert observer.methods == []
    assert len(observer.suppressed_telemetry) == 1
    assert observer.suppressed_telemetry[0].host in telemetry.url
    assert 'Suppressed telemetry:' in observer.suppressed_telemetry[0].format_suppressed_telemetry()


@pytest.mark.parametrize('telemetry', [item[1] for item in TELEMETRY])
@pytest.mark.parametrize('change', [
    {'method': 'GET'}, {'url_suffix': 'x'}, {'host': 'other.example.test'},
    {'resource_type': 'beacon'},
])
def test_known_telemetry_requires_exact_method_host_path_and_resource(telemetry, change):
    from urllib.parse import urlsplit
    parsed = urlsplit(telemetry.url)
    url = telemetry.url
    if 'url_suffix' in change:
        url += change['url_suffix']
    if 'host' in change:
        url = f'https://{change["host"]}{parsed.path}'
    altered = request(change.get('method', telemetry.method), url,
                      change.get('resource_type', telemetry.resource_type))
    observer = publish_inspect.WriteLikeNetworkObserver([], URL)
    observer.set_stage('DRAFT_EDITOR_LOAD')
    observer.observe(altered)
    assert not observer.suppressed_telemetry
    if altered.method == 'POST':
        assert observer.methods == ['POST']


@pytest.mark.parametrize('path', [
    '/api/v1/posts/123', '/api/v1/publish/123', '/api/v1/send/123',
    '/api/v1/schedule/123', '/api/graphql',
])
def test_publication_and_graphql_write_endpoints_remain_blocked(path):
    observer = publish_inspect.WriteLikeNetworkObserver([], URL)
    observer.observe(request('POST', f'{URL}{path}', 'fetch'))
    with pytest.raises(BrowserSessionError):
        observer.require_clear()


def test_suppressed_request_is_never_transmitted():
    telemetry = TELEMETRY[-1][1]
    route = SimpleNamespace(request=telemetry, continue_=MagicMock(), abort=MagicMock())
    observer = publish_inspect.WriteLikeNetworkObserver([], URL)
    guard = publish_inspect.MutationGuard([], observer.diagnostics, observer=observer)
    guard.set_stage('DRAFT_EDITOR_LOAD')
    guard.handle(route)
    route.continue_.assert_not_called()
    route.abort.assert_called_once_with()


def test_exact_referral_put_is_aborted_and_classified_as_editor_mutation():
    route = SimpleNamespace(request=REFERRAL, continue_=MagicMock(), abort=MagicMock())
    observer = publish_inspect.WriteLikeNetworkObserver([], URL)
    observer.set_stage('DRAFT_EDITOR_LOAD')
    guard = publish_inspect.MutationGuard([], observer.diagnostics, observer=observer)
    guard.handle(route)

    route.abort.assert_called_once_with()
    route.continue_.assert_not_called()
    assert observer.methods == []
    assert len(observer.suppressed_editor_mutations) == 1
    assert not observer.suppressed_telemetry
    report = observer.suppressed_editor_mutations[0].format_suppressed_editor_mutation()
    assert 'Suppressed editor mutation:' in report
    assert 'known referral-code initialization mutation' in report


@pytest.mark.parametrize('altered', [
    request('POST', 'https://cyporter.substack.com/api/v1/user/writer_referrals/code', 'fetch'),
    request('PUT', 'https://other.substack.com/api/v1/user/writer_referrals/code', 'fetch'),
    request('PUT', 'https://cyporter.substack.com/api/v1/user/writer_referrals/other', 'fetch'),
    request('PUT', 'https://cyporter.substack.com/api/v1/user/other/code', 'fetch'),
    request('PUT', 'https://cyporter.substack.com/api/v1/user/writer_referrals/code', 'xhr'),
])
def test_referral_suppression_requires_every_exact_signature_field(altered):
    observer = publish_inspect.WriteLikeNetworkObserver([], URL)
    observer.set_stage('DRAFT_EDITOR_LOAD')
    observer.observe(altered)

    assert not observer.suppressed_editor_mutations
    assert altered.method in observer.methods
    with pytest.raises(BrowserSessionError, match='write-like network activity'):
        observer.require_clear()


def test_referral_suppression_requires_draft_editor_load_stage():
    observer = publish_inspect.WriteLikeNetworkObserver([], URL)
    observer.set_stage('PUBLISH_SCREEN_LOAD')
    observer.observe(REFERRAL)

    assert not observer.suppressed_editor_mutations
    assert observer.methods == ['PUT']


@pytest.mark.parametrize('path', [
    '/api/v1/posts/123', '/api/v1/drafts/123', '/api/v1/publish/123',
    '/api/v1/send/123', '/api/v1/schedule/123',
])
def test_substack_mutation_puts_remain_blocked(path):
    observer = publish_inspect.WriteLikeNetworkObserver([], URL)
    observer.set_stage('DRAFT_EDITOR_LOAD')
    observer.observe(request('PUT', f'https://cyporter.substack.com{path}', 'fetch'))

    assert not observer.suppressed_editor_mutations
    assert observer.methods == ['PUT']


def test_real_publishing_guard_does_not_inherit_dry_run_referral_suppression():
    route = SimpleNamespace(request=REFERRAL, continue_=MagicMock(), abort=MagicMock())
    guard = publish_inspect.MutationGuard([])
    guard.set_stage('DRAFT_EDITOR_LOAD')
    guard.handle(route)

    route.abort.assert_called_once_with()
    route.continue_.assert_not_called()
    assert guard.blocked_methods == ['PUT']


def test_write_like_response_status_and_redirect_are_safe():
    request = SimpleNamespace(
        method='POST', url='https://example.substack.com/api/save?token=secret',
        resource_type='xhr', redirected_from=None, initiator=None,
    )
    response = SimpleNamespace(
        request=request, status=307,
        url='https://example.substack.com/login?session=secret',
    )
    observer = publish_inspect.WriteLikeNetworkObserver([])
    observer.observe(request)
    observer.observe_response(response)
    item = observer.first_diagnostic
    assert item.response_status == 307
    assert item.redirect_target == 'example.substack.com/login'
    assert 'secret' not in item.format()


def test_write_like_request_does_not_retain_body_or_sensitive_headers():
    request = SimpleNamespace(
        method='POST', url='https://example.substack.com/api/save',
        resource_type='fetch', redirected_from=None, initiator=None,
        post_data='password=secret',
        headers={'authorization': 'Bearer secret', 'cookie': 'sid=secret'},
    )
    observer = publish_inspect.WriteLikeNetworkObserver([])
    observer.observe(request)
    item = observer.first_diagnostic
    assert not hasattr(item, 'post_data')
    assert not hasattr(item, 'headers')
    assert 'secret' not in repr(item)


def test_unknown_write_like_request_fails_closed_with_stage_and_no_click():
    request = SimpleNamespace(
        method='POST', url='https://example.substack.com/api/publish',
        resource_type='fetch', redirected_from=None, initiator=None,
    )
    observer = publish_inspect.WriteLikeNetworkObserver([])
    observer.set_stage('FINAL_ACTION_DISCOVERY')
    observer.observe(request)
    with pytest.raises(BrowserSessionError, match='FINAL_ACTION_DISCOVERY'):
        observer.require_clear()


def test_read_only_get_navigation_is_allowed():
    observer = publish_inspect.WriteLikeNetworkObserver([])
    observer.observe(SimpleNamespace(
        method='GET', url='https://example.substack.com/publish/post/123',
        resource_type='document', redirected_from=None, initiator=None,
    ))
    observer.require_clear()
    assert observer.methods == []


def test_unexpected_write_like_activity_stops_final_screen_before_exit(monkeypatch):
    continue_node, back = Node(), Node()
    page = FinalPage(FinalScope(back), back)
    selected = publish_inspect.ContinueControl(
        continue_node, 'button', 'Continue', 'publish-button', 'button',
        'div.editor.newsletter-post-editor',
    )
    monkeypatch.setattr(
        publish_inspect, '_publication_controls', lambda _scope: screen_controls(include_back=True),
    )
    guard = publish_inspect.MutationGuard(['POST'])
    with pytest.raises(BrowserSessionError, match='write-like network activity'):
        publish_navigate.open_and_inspect_final_publication_screen(
            page, URL, DRAFT, MagicMock(), guard, continue_control=selected,
        )
    continue_node.click.assert_called_once()
    back.click.assert_not_called()


@pytest.mark.parametrize('label', [
    'Publish', 'Send to everyone now', 'Publish now', 'Schedule', 'Confirm', 'Done',
])
def test_mutating_control_names_never_qualify_as_safe_navigation(monkeypatch, label):
    monkeypatch.setattr(publish_inspect, '_raw_publication_controls', lambda _scope: [{
        'label': label, 'accessibleName': label, 'role': 'button', 'type': 'button',
    }])
    control = publish_inspect._publication_controls(MagicMock())[0]
    assert control.mutates_configuration
    assert not control.safe_navigation


def test_safe_exit_requires_non_form_button_semantics(monkeypatch):
    monkeypatch.setattr(publish_inspect, '_raw_publication_controls', lambda _scope: [
        {'label': 'Back', 'accessibleName': 'Back', 'role': 'button', 'type': 'button'},
        {'label': 'Cancel', 'accessibleName': 'Cancel', 'role': 'button', 'type': 'submit',
         'inForm': True},
    ])
    back, cancel = publish_inspect._publication_controls(MagicMock())
    assert back.safe_navigation and not back.mutates_configuration
    assert not cancel.safe_navigation and cancel.mutates_configuration


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
    monkeypatch.setattr(publish_navigate, 'close_preflight_dialogs', lambda *_args: None)
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
    monkeypatch.setattr(publish_navigate, 'open_and_inspect_final_publication_screen', inspect)
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
    assert 'Audience:' in output.out
    assert 'Final action controls:' in output.out
    assert 'Publish now' in output.out
    assert 'Unexpected mutating network activity: No' in output.out
    assert 'SQLite unchanged: Yes' in output.out
    assert 'No settings changed.' in output.out
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


def test_final_action_diagnostic_preserves_sqlite_and_leaves_final_screen_untouched(
    tmp_path, monkeypatch, capsys,
):
    _root, story, _repository, record, paths = make_project(tmp_path, monkeypatch)
    inspect = fake_live_path(monkeypatch, story, record, paths)
    evidence = publish_inspect.FinalActionEvidence(
        'Send to everyone now', (), 0, 0, 0, 0, 0, 1, False, True,
    )
    inspect.return_value = replace(inspect.return_value, final_action_evidence=evidence)
    before = paths.database.read_bytes()
    assert main(['substack-final-action-diagnostic']) == 0
    assert paths.database.read_bytes() == before
    output = capsys.readouterr()
    assert not output.err
    assert 'Final action evidence' in output.out
    assert 'SQLite SHA-256 before:' in output.out
    assert 'SQLite unchanged: yes' in output.out
    assert 'Browser closed on final publication screen; no exit control was used.' in output.out
    assert 'Final action clicked: No' in output.out
    inspect.assert_called_once()
    assert inspect.call_args.kwargs['leave_open'] is True


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
    monkeypatch.setattr(publish_navigate, 'open_and_inspect_final_publication_screen', final)
    before = paths.database.read_bytes()
    assert main(['substack-publish-inspect']) == 1
    assert 'rate limiting' in capsys.readouterr().err.lower()
    assert paths.database.read_bytes() == before
    social.assert_not_called()
    final.assert_not_called()

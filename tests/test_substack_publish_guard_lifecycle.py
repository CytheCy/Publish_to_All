"""Exercise production real-mode wiring with synthetic browser traffic only."""

from contextlib import contextmanager
from dataclasses import replace
import sqlite3
from types import SimpleNamespace
from unittest.mock import MagicMock

from playwright.sync_api import Error as PlaywrightError
import pytest

from publish_to_all import application
from publish_to_all.browser import (
    image_observe, publish_execute, publish_inspect, publish_navigate,
    publish_preflight, reconcile,
)
from publish_to_all.browser.publish_execute import (
    ExecutionStatus, FinalActionTarget, PublicationVerification,
    PublishPreconditions, VerificationStatus,
)
from publish_to_all.browser.substack import AuthenticationState
from publish_to_all.errors import BrowserSessionError, SubstackRateLimitError
from publish_to_all.state import PublicationRepository, StateError

from test_substack_publish_dry_run_immutability import (
    DRAFT_URL, PUBLICATION_URL, _prepared_project,
)
from test_substack_publish_execute import action_evidence, make_executor, valid_screen


ORDER = (
    'PRE_CLICK_VALIDATED', 'CLICK_INTENT_DURABLY_RECORDED',
    'FINAL_ACTION_ARMED', 'PLAYWRIGHT_CLICK_CALLED',
)
STAGES = (
    'AUTHENTICATION', 'DRAFT_EDITOR_LOAD', 'CONTINUE_NAVIGATION',
    'PUBLISH_SCREEN_LOAD', 'FINAL_ACTION_DISCOVERY', 'PRE_CLICK_REVALIDATION',
    'PIN_CANDIDATE', 'CONTENT_REVALIDATION',
)


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    root = tmp_path / 'project'
    story, repository, record, page = _prepared_project(root, monkeypatch)
    context = MagicMock()
    context.new_page.return_value = page
    run = SimpleNamespace(
        root=root, repository=repository, record=record, page=page, context=context,
        story=story, guard=None, executor=None, handle=MagicMock(),
        continue_control=SimpleNamespace(locator=MagicMock()),
        screen=valid_screen(), injection=None, rate_limit_at=None,
        telemetry=False, post_request=False, outcome='published', visited=[],
        shutdown_strict=None, browser_calls=0, writes=0,
    )

    def response(status, request=None):
        for call in context.on.call_args_list:
            if call.args[0] == 'response':
                call.args[1](SimpleNamespace(status=status, url=PUBLICATION_URL + '/api/unknown',
                                            request=request))

    def request():
        url = ('https://ad.doubleclick.net/ccm/s/collect' if run.telemetry
               else PUBLICATION_URL + '/api/unknown')
        item = SimpleNamespace(method='POST', url=url, resource_type='fetch',
                               redirected_from=None, initiator=None)
        route = SimpleNamespace(request=item, abort=MagicMock(), continue_=MagicMock())
        context.route.call_args.args[1](route)
        if run.guard.strict_blocking:
            route.abort.assert_called_once_with()
            route.continue_.assert_not_called()
        else:
            route.continue_.assert_called_once_with()
            route.abort.assert_not_called()
            response(200, item)

    def visit(stage):
        run.visited.append(stage)
        assert run.guard.strict_blocking
        context.unroute.assert_not_called()
        context.unroute_all.assert_not_called()
        context.remove_listener.assert_not_called()
        page.unroute.assert_not_called()
        if run.injection == stage:
            request()
        if run.rate_limit_at == stage:
            response(429)

    @contextmanager
    def browser(*_args, **kwargs):
        run.browser_calls += 1
        assert kwargs['guarded_publication'] is True
        try:
            yield context
        finally:
            if run.guard is not None:
                run.shutdown_strict = run.guard.strict_blocking
                if run.injection == 'SHUTDOWN':
                    request()
            context.close()

    install = publish_inspect.install_mutation_guard

    def install_guard(target, **kwargs):
        assert target is context
        run.guard = install(target, **kwargs)
        original_stage = run.guard.set_stage

        def set_stage(stage):
            original_stage(stage)
            if stage == 'FINAL_ACTION_DISCOVERY':
                visit(stage)

        run.guard.set_stage = set_stage
        return run.guard

    def authentication(*_args):
        assert run.guard is not None
        assert run.guard.observer.observe_response in [c.args[1] for c in context.on.call_args_list]
        assert run.guard.observer.stage == 'AUTHENTICATION'
        visit('AUTHENTICATION')
        return AuthenticationState.AUTHENTICATED

    def draft(*_args, **_kwargs):
        visit('DRAFT_EDITOR_LOAD')
        remote = reconcile.DraftInspection(
            DRAFT_URL, 'Story', 'substantial', False, ('Saved',), True,
            DRAFT_URL, 'Editing post | Substack', ('Saved', 'Continue'),
        )
        return reconcile.SuppliedDraftEvidence(True, 'verified', inspection=remote)

    def screen(*_args):
        visit(run.guard.stage)
        return run.screen

    def collect(_page):
        visit('PIN_CANDIDATE')
        return FinalActionTarget(run.screen.final_action_evidence,
                                 run.screen.final_action_evidence.candidates[0], run.handle)

    def stored():
        return repository.get_publication(story.source_hash, 'substack')

    def native_click():
        assert run.executor.boundary_events == list(ORDER)
        assert not run.guard.strict_blocking
        assert stored().final_click_attempt_count == 1
        assert stored().needs_reconciliation
        with sqlite3.connect(repository.path.as_uri() + '?mode=ro', uri=True) as connection:
            assert connection.execute(
                "SELECT count(*) FROM publication_audit_events WHERE event_type = 'final_click_attempt'"
            ).fetchone()[0] == 1
        context.unroute.assert_not_called()
        context.unroute_all.assert_not_called()
        context.remove_listener.assert_not_called()
        context.close.assert_not_called()
        page.unroute.assert_not_called()
        if run.post_request:
            request()
        if run.rate_limit_at == 'CLICK':
            response(429)
        if run.outcome == 'click_raises':
            raise PlaywrightError('timeout after dispatch')
        page.url = PUBLICATION_URL + '/p/story'

    def verify(*_args):
        assert not run.guard.strict_blocking
        assert run.guard.stage == 'POST_CLICK'
        context.close.assert_not_called()
        if run.rate_limit_at == 'VERIFICATION':
            response(429)
        if run.post_request:
            request()
        if run.outcome == 'verification_raises':
            raise PlaywrightError('verification failed')
        if run.outcome == 'ambiguous':
            return PublicationVerification(VerificationStatus.AMBIGUOUS, None, (), 'Ambiguous proof')
        return PublicationVerification(VerificationStatus.PUBLISHED,
                                       PUBLICATION_URL + '/p/story', ('explicit_status=Published',), None)

    executor_class = publish_execute.GuardedFinalPublishExecutor

    def executor(*args, **kwargs):
        kwargs['verifier'] = verify
        run.executor = executor_class(*args, **kwargs)
        return run.executor

    reserve = PublicationRepository.mark_final_click_attempted

    def persist(self, expected):
        assert run.executor.boundary_events == ['PRE_CLICK_VALIDATED']
        assert run.guard.strict_blocking
        run.writes += 1
        return reserve(self, expected)

    monkeypatch.setattr(application, 'persistent_browser', browser)
    monkeypatch.setattr(application, 'verify_page', authentication)
    monkeypatch.setattr(reconcile, 'verify_supplied_draft', draft)
    monkeypatch.setattr(publish_inspect, 'install_mutation_guard', install_guard)
    monkeypatch.setattr(publish_inspect, 'inspect_open_final_publication_screen', screen)
    monkeypatch.setattr(publish_navigate, 'inspect_open_final_publication_screen', screen)
    monkeypatch.setattr(publish_inspect, 'select_continue_control', lambda _page: run.continue_control)
    monkeypatch.setattr(publish_navigate, 'close_preflight_dialogs', lambda *_args: None)
    monkeypatch.setattr(image_observe, 'inspect_social_preview_image', lambda *_args:
                        image_observe.SocialPreviewInspection(image_observe.SocialPreviewState.PRESENT))
    content = SimpleNamespace(evidence=('Exact content: Yes',),
                              require_unchanged=lambda: visit('CONTENT_REVALIDATION'))
    monkeypatch.setattr(publish_preflight, 'verify_prepared_content', lambda *_args: content)
    monkeypatch.setattr(publish_execute, 'collect_final_action_target', collect)
    monkeypatch.setattr(publish_execute, 'GuardedFinalPublishExecutor', executor)
    monkeypatch.setattr(PublicationRepository, 'mark_final_click_attempted', persist)
    run.continue_control.locator.click.side_effect = lambda: visit('CONTINUE_NAVIGATION')
    run.handle.click.side_effect = native_click
    run.stored = stored
    return run


@pytest.mark.parametrize('stage', STAGES)
@pytest.mark.parametrize('dry_run', [False, True])
def test_unknown_mutation_blocks_shared_production_path_before_intent(workflow, stage, dry_run):
    run = workflow
    run.injection = stage
    before = run.repository.path.read_bytes()
    with pytest.raises(BrowserSessionError, match='UNKNOWN'):
        application.publish_substack(run.root, dry_run=dry_run)
    assert stage in run.visited
    assert run.writes == 0
    assert run.stored().final_click_attempt_count == 0
    assert run.repository.path.read_bytes() == before
    run.handle.click.assert_not_called()
    assert run.shutdown_strict
    assert run.guard.observer.diagnostics[0].safe_report()['classification'] == 'UNKNOWN'


@pytest.mark.parametrize('stage', STAGES)
def test_real_path_suppresses_known_telemetry_without_stopping(workflow, stage):
    run = workflow
    run.injection, run.telemetry = stage, True
    result = application.publish_substack(run.root)
    assert result.status is ExecutionStatus.PUBLISHED
    assert result.boundary_events == ORDER
    assert run.writes == run.stored().final_click_attempt_count == 1
    run.handle.click.assert_called_once_with()
    assert result.suppressed_telemetry
    assert all(item.safe_report()['classification'] == 'EXPECTED NON-PUBLISHING'
               for item in result.suppressed_telemetry)
    assert not run.guard.observer.methods


@pytest.mark.parametrize('stage', STAGES)
def test_real_path_rate_limit_stops_before_click_intent(workflow, stage):
    run = workflow
    run.rate_limit_at = stage
    with pytest.raises(SubstackRateLimitError):
        application.publish_substack(run.root)
    assert run.writes == run.stored().final_click_attempt_count == 0
    run.handle.click.assert_not_called()
    assert run.shutdown_strict


def test_real_path_zero_unknown_reaches_exact_durable_boundary_order(workflow):
    run = workflow
    result = application.publish_substack(run.root)
    assert result.status is ExecutionStatus.PUBLISHED
    assert result.boundary_events == ORDER
    assert run.writes == run.stored().final_click_attempt_count == 1
    run.continue_control.locator.click.assert_called_once_with()
    run.handle.click.assert_called_once_with()
    run.context.route.assert_called_once()
    run.context.unroute.assert_not_called()
    assert not run.guard.observer.methods
    assert not run.stored().needs_reconciliation


@pytest.mark.parametrize('failure', ['candidate_ambiguity', 'audience', 'delivery', 'schedule',
                                    'comments', 'tags', 'paid'])
def test_real_final_settings_and_candidate_guards_stop_before_intent(workflow, failure):
    run = workflow
    if failure == 'candidate_ambiguity':
        run.screen = replace(run.screen, final_action_evidence=action_evidence(ambiguity=True))
    else:
        index, changes = {
            'audience': (0, {'selected': False}),
            'delivery': (4, {'selected': False}),
            'schedule': (5, {'selected': True}),
            'comments': (2, {'selected': False}),
            'tags': (6, {'value': 'new-tag'}),
            'paid': (1, {'selected': True}),
        }[failure]
        controls = list(run.screen.controls)
        controls[index] = replace(controls[index], **changes)
        run.screen = replace(run.screen, controls=tuple(controls))
    result = application.publish_substack(run.root)
    assert result.status is ExecutionStatus.BLOCKED
    assert run.writes == run.stored().final_click_attempt_count == 0
    assert not result.boundary_events
    assert run.shutdown_strict
    run.handle.click.assert_not_called()


@pytest.mark.parametrize('failure', [StateError('disk full'), sqlite3.OperationalError('disk full')])
def test_intent_write_failure_keeps_guard_closed_and_never_arms_or_clicks(workflow, monkeypatch, failure):
    run = workflow
    persist = MagicMock(side_effect=failure)
    monkeypatch.setattr(PublicationRepository, 'mark_final_click_attempted', persist)
    result = application.publish_substack(run.root)
    assert result.status is ExecutionStatus.BLOCKED
    assert result.boundary_events == ('PRE_CLICK_VALIDATED',)
    persist.assert_called_once()
    assert run.stored().final_click_attempt_count == 0
    assert run.shutdown_strict
    run.handle.click.assert_not_called()
    assert run.executor.execute(PublishPreconditions(True, True, True), run.screen).status is ExecutionStatus.BLOCKED
    persist.assert_called_once()


@pytest.mark.parametrize('outcome', ['published', 'ambiguous', 'click_raises', 'verification_raises'])
def test_post_boundary_observation_verification_and_no_retry(workflow, outcome):
    run = workflow
    run.outcome, run.post_request = outcome, True
    result = application.publish_substack(run.root)
    assert result.status is (ExecutionStatus.PUBLISHED if outcome == 'published' else ExecutionStatus.UNCERTAIN)
    assert run.writes == run.stored().final_click_attempt_count == 1
    assert bool(run.stored().needs_reconciliation) is (outcome != 'published')
    assert result.boundary_events == ORDER
    run.handle.click.assert_called_once_with()
    assert not run.shutdown_strict
    assert result.post_boundary_network
    assert all(item.safe_report()['classification'] == 'UNKNOWN' for item in result.post_boundary_network)
    assert all(item.response_status == 200 for item in result.post_boundary_network)
    assert 'Classification: UNKNOWN' in run.stored().publication_verification_evidence
    assert run.guard.observer.methods == []
    with pytest.raises(StateError):
        application.publish_substack(run.root)
    assert run.browser_calls == 1
    run.handle.click.assert_called_once_with()


@pytest.mark.parametrize('stage', ['CLICK', 'VERIFICATION'])
def test_post_boundary_rate_limit_is_uncertain_even_with_positive_verifier(workflow, stage):
    run = workflow
    run.rate_limit_at = stage
    result = application.publish_substack(run.root)
    assert result.status is ExecutionStatus.UNCERTAIN
    assert 'Rate limiting appeared after the irreversible boundary' in result.failures[0]
    assert run.writes == run.stored().final_click_attempt_count == 1
    assert run.stored().needs_reconciliation
    run.handle.click.assert_called_once_with()


def test_dry_run_never_arms_and_keeps_strict_guard_through_shutdown(workflow):
    run = workflow
    before = run.repository.path.read_bytes()
    result = application.publish_substack(run.root, dry_run=True)
    assert result.status is ExecutionStatus.DRY_RUN_VERIFIED
    assert result.boundary_events == ('PRE_CLICK_VALIDATED',)
    assert result.pre_click_revalidation_passed
    assert result.candidate_count == 1
    assert run.writes == run.stored().final_click_attempt_count == 0
    assert run.shutdown_strict
    assert run.repository.path.read_bytes() == before
    run.continue_control.locator.click.assert_called_once_with()
    run.handle.click.assert_not_called()
    run.context.route.assert_called_once()
    run.context.unroute.assert_not_called()
    with pytest.raises(BrowserSessionError):
        run.executor._arm_final_action()


def test_dry_run_unknown_during_shutdown_cannot_report_success(workflow):
    workflow.injection = 'SHUTDOWN'
    with pytest.raises(BrowserSessionError, match='UNKNOWN'):
        application.publish_substack(workflow.root, dry_run=True)
    assert workflow.writes == workflow.stored().final_click_attempt_count == 0
    workflow.handle.click.assert_not_called()


def test_boundary_and_native_click_cannot_be_called_before_durable_intent(monkeypatch, tmp_path):
    repository, record, page, handle, screen, executor = make_executor(monkeypatch, tmp_path)
    executor._pre_click_validated = True
    with pytest.raises(BrowserSessionError, match='durable click intent'):
        executor._arm_final_action()
    with pytest.raises(BrowserSessionError, match='durable pending click intent'):
        executor.mutation_guard.arm_final_action(record)
    target = FinalActionTarget(screen.final_action_evidence,
                               screen.final_action_evidence.candidates[0], handle)
    with pytest.raises(BrowserSessionError, match='armed final action'):
        executor._click_final_action(target)
    assert not executor.boundary_events
    assert executor.mutation_guard.strict_blocking
    handle.click.assert_not_called()

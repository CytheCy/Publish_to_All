import sqlite3

import pytest

from publish_to_all.application import (
    inspect_status, retire_substack_test_publication, start_new_substack_cycle,
)
from publish_to_all.browser.public_post import (
    ACCEPTED_TEST_TITLES, PublicPostVerification, TEST_PUBLIC_URL,
    verify_public_post_html,
)
from publish_to_all.cli import main
from publish_to_all.config import runtime_paths
from publish_to_all.errors import BrowserSessionError
from publish_to_all.state import (
    BodyStatus, ImageStatus, PublicationRepository, PublicationState,
    DuplicatePublicationError, PublicationStatus, StateError, SubtitleStatus,
)
from publish_to_all.story import load_story


def public_html(title: str, extra: str = "") -> str:
    return (
        '<html><head><meta property="og:type" content="article">'
        '</head><body><article aria-label="Post" role="article">'
        f'<h1 class="post-title">{title}</h1>{extra}</article></body></html>'
    )


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path.parent / f'{tmp_path.name}-runtime-data'))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path.parent / f'{tmp_path.name}-runtime-state'))
    (tmp_path / 'In').mkdir()
    (tmp_path / 'In/story.md').write_text('---\ntitle: Saturation of Artificial Intelligence\n---\nBody.')
    return tmp_path


def seeded_cycle(project):
    story = load_story(project / 'In')
    repository = PublicationRepository(runtime_paths(project).database)
    attempt = repository.begin_attempt(story, 'substack')
    draft = repository.mark_draft_created(
        attempt.id, 'https://cyporter.substack.com/publish/post/217947847',
    )
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            '''UPDATE publications SET body_status = ?, body_inserted_at = 'body-history',
               image_status = ?, image_uploaded_at = 'image-history',
               subtitle_status = ?, subtitle_inserted_at = 'subtitle-history',
               needs_reconciliation = 0, subtitle_error_message = 'old subtitle history'
               WHERE id = ?''',
            (BodyStatus.INSERTED, ImageStatus.UPLOADED, SubtitleStatus.INSERTED, draft.id),
        )
    current = repository.get_publication(story.source_hash, 'substack')
    clicked = repository.mark_final_click_attempted(current)
    uncertain = repository.mark_publication_uncertain(clicked, 'old ambiguity', ('old=evidence',))
    reconciled = repository.reconcile_publication_not_published(
        uncertain, ('classification=NOT_PUBLISHED_VERIFIED', 'published_url=None'),
    )
    authorized = repository.authorize_retry(
        reconciled, story_hash=story.source_hash,
        remote_draft_reverified=True, no_public_url=True,
    )
    with sqlite3.connect(repository.path) as connection:
        connection.execute(
            'UPDATE publications SET needs_reconciliation = 1 WHERE id = ?',
            (authorized.id,),
        )
    return story, repository, repository.get_publication(story.source_hash, 'substack')


@pytest.mark.parametrize('title', ACCEPTED_TEST_TITLES)
def test_exact_public_url_verification_accepts_both_titles(title):
    result = verify_public_post_html(
        TEST_PUBLIC_URL, status=200, final_url=TEST_PUBLIC_URL,
        content_type='text/html; charset=utf-8', html=public_html(title),
    )
    assert result.title == title
    assert result.public_post
    assert not result.editor_ui
    assert not result.publish_modal


def test_public_verification_rejects_unrelated_title():
    with pytest.raises(BrowserSessionError, match='accepted test titles'):
        verify_public_post_html(
            TEST_PUBLIC_URL, status=200, final_url=TEST_PUBLIC_URL,
            content_type='text/html', html=public_html('An unrelated title'),
        )


@pytest.mark.parametrize('url', [
    'https://other.substack.com/p/saturation-of-artificial-intelligence',
    'http://cyporter.substack.com/p/saturation-of-artificial-intelligence',
])
def test_public_verification_rejects_wrong_host_or_scheme(url):
    with pytest.raises(BrowserSessionError, match='exactly'):
        verify_public_post_html(
            url, status=200, final_url=url,
            content_type='text/html', html=public_html(ACCEPTED_TEST_TITLES[0]),
        )


def test_public_verification_rejects_wrong_path():
    url = 'https://cyporter.substack.com/p/another-post'
    with pytest.raises(BrowserSessionError, match='exactly'):
        verify_public_post_html(
            url, status=200, final_url=url,
            content_type='text/html', html=public_html(ACCEPTED_TEST_TITLES[0]),
        )


def test_public_verification_rejects_editor_or_publish_modal_markup():
    with pytest.raises(BrowserSessionError, match='editor UI'):
        verify_public_post_html(
            TEST_PUBLIC_URL, status=200, final_url=TEST_PUBLIC_URL,
            content_type='text/html',
            html=public_html(ACCEPTED_TEST_TITLES[0], '<div contenteditable="true"></div>'),
        )
    with pytest.raises(BrowserSessionError, match='publish modal'):
        verify_public_post_html(
            TEST_PUBLIC_URL, status=200, final_url=TEST_PUBLIC_URL,
            content_type='text/html',
            html=public_html(ACCEPTED_TEST_TITLES[0], '<div role="dialog" aria-label="Publish"></div>'),
        )


def test_retirement_preserves_history_and_stores_public_url(project, monkeypatch):
    story, repository, before = seeded_cycle(project)
    prior_audit = repository.audit_history(before.id)
    verification = PublicPostVerification(
        TEST_PUBLIC_URL, 200, ACCEPTED_TEST_TITLES[0], True, False, False,
    )
    monkeypatch.setattr('publish_to_all.application.verify_test_public_post', lambda url: verification)
    monkeypatch.setattr(
        'publish_to_all.application.persistent_browser',
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('retirement opened a browser')),
    )

    result = retire_substack_test_publication(project, TEST_PUBLIC_URL)
    retired = result.retired

    assert retired.status == PublicationStatus.RETIRED_TEST_PUBLICATION
    assert retired.published_url == TEST_PUBLIC_URL
    assert retired.draft_url == before.draft_url
    for field in (
        'final_click_attempted', 'final_click_attempted_at', 'final_click_attempt_count',
        'retry_authorized', 'retry_authorized_at', 'retry_authorization_count',
        'body_status', 'body_started_at', 'body_inserted_at', 'body_error_message',
        'subtitle_status', 'subtitle_started_at', 'subtitle_inserted_at', 'subtitle_error_message',
        'image_status', 'image_started_at', 'image_uploaded_at', 'image_error_message',
        'publication_state', 'publication_verification_evidence', 'publication_ambiguity_reason',
        'needs_reconciliation',
    ):
        assert getattr(retired, field) == getattr(before, field), field
    assert repository.audit_history(retired.id)[:-1] == prior_audit
    assert repository.audit_history(retired.id)[-1]['event_type'] == 'retired_test_publication'


def test_retired_state_blocks_publish_retry_reconciliation_and_content_changes(project, monkeypatch):
    story, repository, before = seeded_cycle(project)
    retired = repository.retire_test_publication(
        story.source_hash, 'substack', TEST_PUBLIC_URL, 'verified public test artifact',
    )
    assert retired.status == PublicationStatus.RETIRED_TEST_PUBLICATION
    for operation in (
        lambda: repository.require_publish_inspection_candidate(story.source_hash, 'substack'),
        lambda: repository.authorize_retry(
            retired, story_hash=story.source_hash,
            remote_draft_reverified=True, no_public_url=True,
        ),
        lambda: repository.reconcile_failed_draft(retired, before.draft_url),
        lambda: repository.require_body_candidate(story.source_hash, 'substack'),
        lambda: repository.require_subtitle_candidate(story.source_hash, 'substack'),
        lambda: repository.require_image_candidate(story.source_hash, 'substack'),
        lambda: repository.reconcile_publication_not_published(
            retired, ('classification=NOT_PUBLISHED_VERIFIED', 'published_url=None'),
        ),
    ):
        with pytest.raises(StateError):
            operation()
    monkeypatch.setattr('publish_to_all.application.verify_test_public_post', lambda url: None)
    assert retired.published_url == TEST_PUBLIC_URL


def test_start_new_cycle_requires_explicit_retirement(project):
    with pytest.raises(BrowserSessionError, match='retired_test_publication'):
        start_new_substack_cycle(project)
    story = load_story(project / 'In')
    repository = PublicationRepository(runtime_paths(project).database)
    assert repository.publication_history(story.source_hash, 'substack') == ()


def test_start_new_cycle_is_local_and_gets_fresh_state(project, monkeypatch):
    story, repository, retired = seeded_cycle(project)
    repository.retire_test_publication(
        story.source_hash, 'substack', TEST_PUBLIC_URL, 'verified public test artifact',
    )
    monkeypatch.setattr(
        'publish_to_all.application.persistent_browser',
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('new cycle opened a browser')),
    )

    result = start_new_substack_cycle(project)
    active = result.active
    assert active.id != retired.id
    assert active.status == PublicationStatus.NOT_STARTED
    assert active.lifecycle_status == 'active'
    assert active.draft_url is None and active.published_url is None
    assert active.body_status == BodyStatus.NOT_STARTED
    assert active.subtitle_status == SubtitleStatus.NOT_STARTED
    assert active.image_status == ImageStatus.NOT_STARTED
    assert active.final_click_attempt_count == 0
    assert active.retry_authorization_count == 0
    assert not active.needs_reconciliation
    assert repository.get_publication(story.source_hash, 'substack') == active
    assert repository.publication_history(story.source_hash, 'substack')[0].status == PublicationStatus.RETIRED_TEST_PUBLICATION


def test_cli_status_and_history_distinguish_retired_cycle(project, monkeypatch, capsys):
    story, repository, _ = seeded_cycle(project)
    repository.retire_test_publication(
        story.source_hash, 'substack', TEST_PUBLIC_URL, 'verified public test artifact',
    )
    assert main(['status']) == 0
    status = capsys.readouterr().out
    assert 'State: Retired test publication' in status
    assert f'Public URL: {TEST_PUBLIC_URL}' in status
    assert 'Historical final click attempts: 1' in status
    assert main(['substack-history']) == 0
    history = capsys.readouterr().out
    assert 'Cycle 1' in history
    assert 'Audit events: final_click_attempt, verified_not_published, retry_authorized, retired_test_publication' in history


def active_cycle_after_retirement(project):
    story, repository, retired = seeded_cycle(project)
    repository.retire_test_publication(
        story.source_hash, 'substack', TEST_PUBLIC_URL, 'verified public test artifact',
    )
    active = start_new_substack_cycle(project).active
    return story, repository, retired, active


def test_active_selector_ignores_retired_cycle_and_selects_cycle_two(project):
    story, repository, retired, active = active_cycle_after_retirement(project)

    selected = repository.select_active_cycle(story.source_hash, 'substack', required=True)

    assert retired.id == 1
    assert active.id == 2
    assert selected == active
    assert repository.get_publication(story.source_hash, 'substack') == active
    assert repository.find_duplicate(story.source_hash, 'substack') is None


def test_retired_cycle_does_not_block_draft_reservation(project):
    story, repository, retired, active = active_cycle_after_retirement(project)

    reserved = repository.begin_attempt(story, 'substack')

    assert reserved.id == active.id
    assert reserved.id != retired.id
    assert reserved.status == PublicationStatus.DRAFT_CREATING


def test_active_cycle_with_draft_url_blocks_duplicate(project):
    story, repository, _, active = active_cycle_after_retirement(project)
    reserved = repository.begin_attempt(story, 'substack')
    linked = repository.mark_draft_created(reserved.id, 'https://cyporter.substack.com/publish/post/999')

    assert repository.find_duplicate(story.source_hash, 'substack') == linked
    with pytest.raises(DuplicatePublicationError, match='already has a Substack record'):
        repository.begin_attempt(story, 'substack')


def test_active_cycle_reconciliation_required_blocks_duplicate(project):
    story, repository, _, active = active_cycle_after_retirement(project)
    reserved = repository.begin_attempt(story, 'substack')
    failed = repository.mark_failed(reserved.id, 'uncertain creation', needs_reconciliation=True)

    assert failed.id == active.id
    assert repository.find_duplicate(story.source_hash, 'substack') == failed
    with pytest.raises(DuplicatePublicationError, match='already has a Substack record'):
        repository.begin_attempt(story, 'substack')


def test_two_active_cycles_fail_safely(project):
    story = load_story(project / 'In')
    repository = PublicationRepository(runtime_paths(project).database)
    first = repository.begin_attempt(story, 'substack')
    repository.mark_failed(first.id, 'confirmed local failure')
    second = repository.begin_attempt(story, 'substack')
    with sqlite3.connect(repository.path) as connection:
        connection.execute('UPDATE publications SET cycle_id = ? WHERE id = ?', (second.id, second.id))

    with pytest.raises(StateError, match='More than one active publication cycle'):
        repository.select_active_cycle(story.source_hash, 'substack', required=True)
    with pytest.raises(StateError, match='More than one active publication cycle'):
        repository.find_duplicate(story.source_hash, 'substack')


def test_only_retired_cycle_has_explicit_no_active_error(project):
    story, repository, _ = seeded_cycle(project)
    repository.retire_test_publication(
        story.source_hash, 'substack', TEST_PUBLIC_URL, 'verified public test artifact',
    )

    with pytest.raises(StateError, match='No active publication cycle'):
        repository.select_active_cycle(story.source_hash, 'substack', required=True)
    with pytest.raises(StateError, match='No active publication cycle'):
        repository.find_duplicate(story.source_hash, 'substack')


def test_status_and_draft_preflight_select_same_active_cycle(project):
    story, repository, retired, active = active_cycle_after_retirement(project)

    status = inspect_status(project)
    preflight = repository.select_active_cycle(story.source_hash, 'substack', required=True)

    assert status.substack == active
    assert preflight == active
    assert status.substack.id != retired.id
    assert repository.find_duplicate(story.source_hash, 'substack') is None


def test_same_story_hash_across_cycles_preserves_queryable_retired_history(project):
    story, repository, retired, active = active_cycle_after_retirement(project)

    history = repository.publication_history(story.source_hash, 'substack')

    assert [record.id for record in history] == [retired.id, active.id]
    assert all(record.story_id == retired.story_id for record in history)
    assert history[0].status == PublicationStatus.RETIRED_TEST_PUBLICATION
    assert history[0].published_url == TEST_PUBLIC_URL
    assert history[1].status == PublicationStatus.NOT_STARTED

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from publish_to_all import application
from publish_to_all.browser import image_observe, publish_inspect, publish_navigate, reconcile
from publish_to_all.browser.image_observe import SocialPreviewInspection, SocialPreviewState
from publish_to_all.browser.reconcile import DraftInspection, SuppliedDraftEvidence
from publish_to_all.browser.substack import AuthenticationState
from publish_to_all.config import runtime_paths
from publish_to_all.errors import BrowserSessionError, SubstackRateLimitError
from publish_to_all.state import BodyStatus, ImageStatus, PublicationRepository
from publish_to_all.story import load_story


PUBLICATION_URL = 'https://example.substack.com'
DRAFT_URL = PUBLICATION_URL + '/publish/post/123'


class _MutationGuard:
    def set_stage(self, _stage):
        pass

    def require_clear(self):
        pass


class _NetworkObserver:
    def __init__(self, *_args):
        self.methods = []
        self.suppressed_telemetry = []
        self.suppressed_editor_mutations = []

    def set_stage(self, _stage):
        pass

    def observe_response(self, _response):
        pass

    def require_clear(self):
        pass


def _prepared_project(root, monkeypatch):
    (root / 'In').mkdir(parents=True)
    (root / 'config.toml').write_text(
        '[substack]\npublication_url = "' + PUBLICATION_URL + '"\n'
    )
    (root / 'In' / 'story.md').write_text(
        '---\ntitle: Story\n---\n' + ('A substantial article paragraph. ' * 100)
    )
    story = load_story(root / 'In')
    database = runtime_paths(root).database
    repository = PublicationRepository(database)
    attempt = repository.begin_attempt(story, 'substack')
    draft = repository.mark_draft_created(attempt.id, DRAFT_URL)
    inserting = repository.mark_body_inserting(draft)
    body = repository.mark_body_inserted(inserting.id)
    uploading = repository.mark_image_uploading(body)
    record = repository.mark_image_uploaded(uploading)
    assert record.body_status is BodyStatus.INSERTED
    assert record.image_status is ImageStatus.UPLOADED
    runtime_paths(root).substack_browser_profile.mkdir(parents=True)

    page = MagicMock()
    page.url = DRAFT_URL
    context = SimpleNamespace(new_page=lambda: page)

    @contextmanager
    def browser(*_args, **_kwargs):
        yield context

    monkeypatch.setattr(application, 'persistent_browser', browser)
    return story, repository, record, page


def _database_snapshot(repository):
    return repository.path.read_bytes()


@pytest.mark.parametrize(
    ('authentication', 'error'),
    [
        (AuthenticationState.RATE_LIMITED, SubstackRateLimitError),
        (AuthenticationState.NOT_AUTHENTICATED, BrowserSessionError),
    ],
)
def test_dry_run_authentication_and_rate_limit_failures_leave_sqlite_unchanged(
        tmp_path, monkeypatch, authentication, error):
    root = tmp_path / 'project'
    _story, repository, record, _page = _prepared_project(root, monkeypatch)
    before = _database_snapshot(repository)
    monkeypatch.setattr(application, 'verify_page', lambda *_args: authentication)

    with pytest.raises(error):
        application.publish_substack(root, dry_run=True)

    assert repository.path.read_bytes() == before
    assert repository.get_publication(
        load_story(root / 'In').source_hash, 'substack',
    ) == record


def test_dry_run_navigation_failure_after_authenticated_preflight_leaves_sqlite_unchanged(
        tmp_path, monkeypatch):
    root = tmp_path / 'project'
    _story, repository, record, page = _prepared_project(root, monkeypatch)
    before = _database_snapshot(repository)
    monkeypatch.setattr(
        application, 'verify_page', lambda *_args: AuthenticationState.AUTHENTICATED,
    )
    remote = DraftInspection(
        DRAFT_URL, 'Story', 'substantial', False, ('Saved',), True,
        DRAFT_URL, 'Editing post | Substack', ('Saved', 'Continue'),
    )
    monkeypatch.setattr(
        reconcile, 'verify_supplied_draft',
        lambda *_args, **_kwargs: SuppliedDraftEvidence(True, 'verified', inspection=remote),
    )
    monkeypatch.setattr(
        image_observe, 'inspect_social_preview_image',
        lambda *_args: SocialPreviewInspection(SocialPreviewState.PRESENT),
    )
    monkeypatch.setattr(publish_inspect, 'WriteLikeNetworkObserver', _NetworkObserver)
    monkeypatch.setattr(publish_inspect, 'install_mutation_guard', lambda *_args, **_kwargs: _MutationGuard())
    monkeypatch.setattr(publish_inspect, 'select_continue_control', lambda _page: object())
    monkeypatch.setattr(publish_navigate, 'close_preflight_dialogs', lambda *_args: None)

    def navigation_failure(*_args, **_kwargs):
        raise BrowserSessionError('ambiguous Continue navigation')

    monkeypatch.setattr(
        publish_navigate, 'open_and_inspect_final_publication_screen', navigation_failure,
    )

    with pytest.raises(BrowserSessionError, match='ambiguous Continue navigation'):
        application.publish_substack(root, dry_run=True)

    assert page.url == DRAFT_URL
    assert repository.path.read_bytes() == before
    assert repository.get_publication(
        load_story(root / 'In').source_hash, 'substack',
    ) == record

"""Read-only application operations reusable by any interface."""

from dataclasses import dataclass
from pathlib import Path

from .config import Config, load_config, runtime_paths
from .story import Story, load_story
from .state import PublicationRecord, PublicationRepository
from .browser.session import persistent_browser
from .browser.substack import AuthenticationState, verify_page
from .errors import BrowserSessionError
from .publishers.substack import SubstackPublisher


@dataclass(frozen=True)
class Inspection:
    config: Config
    story: Story


def inspect_project(root: Path) -> Inspection:
    config = load_config(root / "config.toml")
    return Inspection(config, load_story(root / "In"))


@dataclass(frozen=True)
class StoryStatus:
    story: Story
    substack: PublicationRecord | None


def inspect_status(root: Path) -> StoryStatus:
    """Read exact-version state, initializing only the schema if necessary."""
    story = load_story(root / "In")
    repository = PublicationRepository(runtime_paths(root).database)
    return StoryStatus(story, repository.get_publication(story.source_hash, "substack"))


def prepare_substack_draft(root: Path) -> StoryStatus:
    inspection = inspect_project(root)
    publication_url = inspection.config.require_substack()
    story = inspection.story
    paths = runtime_paths(root)
    repository = PublicationRepository(paths.database)
    duplicate = repository.find_duplicate(story.source_hash, 'substack')
    if duplicate:
        repository.refuse_duplicate(story, duplicate)
    if not paths.substack_browser_profile.is_dir():
        raise BrowserSessionError('No saved Substack session. Run publish-to-all substack-login first. No draft was created.')
    with persistent_browser(paths.substack_browser_profile, paths.diagnostics, headless=False) as context:
        page = context.new_page()
        if verify_page(page, publication_url) != AuthenticationState.AUTHENTICATED:
            raise BrowserSessionError(
                'Could not confirm an authenticated Substack session. Run publish-to-all substack-login. '
                'No draft was created. Nothing was published.'
            )
        # Repeat duplicate detection atomically after authentication and before mutation.
        attempt = repository.begin_attempt(story, 'substack')
        publisher = SubstackPublisher(repository, page, publication_url, paths.diagnostics)
        record = publisher.prepare_draft(story, attempt)
    return StoryStatus(story, record)


def reconcile_substack(root: Path, *, link: bool = False) -> str:
    """Read-only remotely; optionally link locally with corroborating attempt URL."""
    from playwright.sync_api import Error as PlaywrightError
    from .browser.reconcile import inspect_drafts, DraftEvidence
    from .state import PublicationStatus

    inspection = inspect_project(root)
    publication_url = inspection.config.require_substack()
    paths = runtime_paths(root)
    repository = PublicationRepository(paths.database)
    attempt = repository.find_duplicate(inspection.story.source_hash, 'substack')
    if not attempt or attempt.status != PublicationStatus.FAILED or not attempt.needs_reconciliation:
        return 'No unresolved failed attempt for this exact story version. Local state unchanged.'
    if not paths.substack_browser_profile.is_dir():
        raise BrowserSessionError('No saved Substack session. Local state unchanged.')
    with persistent_browser(paths.substack_browser_profile, paths.diagnostics, headless=False) as context:
        page = context.new_page()
        if verify_page(page, publication_url) != AuthenticationState.AUTHENTICATED:
            return 'Unknown: cannot confirm authentication. Local state unchanged; retry remains blocked.'
        try:
            evidence = inspect_drafts(page, publication_url, inspection.story.metadata.title)
        except (BrowserSessionError, PlaywrightError):
            evidence = DraftEvidence((), 'Cannot confidently inspect the rendered draft listing.')
    report = [evidence.reason, *evidence.matches]
    # Title alone cannot associate a pre-existing draft with this failed attempt.
    if len(evidence.matches) == 1 and attempt.draft_url and evidence.matches[0] == attempt.draft_url.rstrip('/'):
        if link:
            repository.reconcile_failed_draft(attempt, evidence.matches[0])
            report.append('Linked existing draft locally. No remote changes. Duplicate creation remains blocked.')
        else:
            report.append('Verified against the URL recorded by the failed attempt. Local state unchanged. '
                          'To link locally, run: publish-to-all substack-reconcile --link')
    else:
        report.append('Unknown: insufficient evidence to associate a unique draft with the failed attempt. '
                      'Local state unchanged; retry remains blocked. An untitled draft cannot be matched by story title.')
    return '\n'.join(report)

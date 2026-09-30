"""Read-only application operations reusable by any interface."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

from .config import Config, load_config, runtime_paths
from .story import Story, load_story
from .state import (
    DeletedDraftRemoval, PublicationReassociation, PublicationRecord,
    PublicationRepository, StoryVersion,
)
from .browser.session import persistent_browser
from .browser.substack import AuthenticationState, collect_diagnostics, verify_page
from .errors import BrowserSessionError, SubstackRateLimitError
from .publishers.substack import (
    SubstackBodyPublisher, SubstackImagePublisher, SubstackPublisher,
    SubstackTitleRepairPublisher,
)

if TYPE_CHECKING:
    from .browser.publish_execute import PublishExecutionResult
    from .browser.publish_validate import FinalPublishValidation


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


@dataclass(frozen=True)
class ImageReconciliationResult:
    story: Story
    substack: PublicationRecord
    remote_social_preview_state: str


@dataclass(frozen=True)
class ImageObservationResult:
    story: Story
    substack: PublicationRecord
    draft_url: str
    target_feature: str
    dom: object
    network_responses: int
    successful_network_responses: int
    failed_network_responses: int
    final_url_unchanged: bool
    title_preserved: bool
    body_preserved: bool
    local_state_unchanged: bool


@dataclass(frozen=True)
class SocialPreviewReadOnlyResult:
    draft_url: str
    image_state: str
    local_save_count: int
    local_save_enabled: bool


@dataclass(frozen=True)
class DeletedDraftCleanupResult:
    story: StoryVersion
    removal: DeletedDraftRemoval
    remote_reason: str


@dataclass(frozen=True)
class PublishInspectionResult:
    story: Story
    substack: PublicationRecord
    continue_role: str
    continue_label: str
    continue_test_id: str
    continue_type: str
    continue_context: str
    final_screen: object
    local_state_changed: bool
    database_sha256_before: str = ''
    database_sha256_after: str = ''


@dataclass(frozen=True)
class PublishContinueDiagnosticResult:
    story: Story
    substack: PublicationRecord
    diagnostic: object
    local_state_changed: bool


@dataclass(frozen=True)
class FinalPublishDryRunResult:
    inspection: PublishInspectionResult
    validation: 'FinalPublishValidation'


def inspect_status(root: Path) -> StoryStatus:
    """Read exact-version state, initializing only the schema if necessary."""
    story = load_story(root / "In")
    repository = PublicationRepository(runtime_paths(root).database)
    return StoryStatus(story, repository.get_publication(story.source_hash, "substack"))


def inspect_substack_publish(
    root: Path, *, continue_diagnostic_only: bool = False,
    final_action_diagnostic: bool = False,
) -> PublishInspectionResult | PublishContinueDiagnosticResult:
    """Inspect the prepared draft's final publication UI without permitting mutation."""
    from playwright.sync_api import Error as PlaywrightError
    from .browser.body import RateLimitMonitor
    from .browser.image_observe import inspect_social_preview_image
    from .browser.publish_inspect import (
        inspect_continue_candidates, install_mutation_guard, select_continue_control,
    )
    from .browser.publish_navigate import (
        close_preflight_dialogs, open_and_inspect_final_publication_screen,
    )
    from .browser.reconcile import validate_supplied_draft_url, verify_supplied_draft

    inspection = inspect_project(root)
    publication_url = inspection.config.require_substack()
    story = inspection.story
    paths = runtime_paths(root)
    if not paths.database.is_file():
        raise BrowserSessionError(
            'No publication database exists for the exact current story. Local state unchanged.'
        )
    database_before = sha256(paths.database.read_bytes()).hexdigest()
    repository = PublicationRepository(paths.database, migrate=False)
    record = repository.require_publish_inspection_candidate(story.source_hash, 'substack')
    draft_url = validate_supplied_draft_url(record.draft_url, publication_url)
    if draft_url != record.draft_url.rstrip('/'):
        raise BrowserSessionError('The linked draft URL is not canonical. Local state unchanged.')
    if not paths.substack_browser_profile.is_dir():
        raise BrowserSessionError(
            'No saved Substack session. Run publish-to-all substack-login first. '
            'Local state unchanged.'
        )

    with persistent_browser(
        paths.substack_browser_profile, paths.diagnostics, headless=False,
    ) as context:
        page = context.new_page()
        monitor = RateLimitMonitor(publication_url)
        page.on('response', monitor.observe)
        try:
            evidence = verify_supplied_draft(
                page, publication_url, draft_url, paths.diagnostics,
                rate_limit_monitor=monitor,
            )
        except SubstackRateLimitError:
            raise
        except (BrowserSessionError, PlaywrightError):
            monitor.require_clear(page)
            raise BrowserSessionError(
                'Could not safely verify the linked authenticated Substack editor. '
                'Continue was not clicked. Local state unchanged.'
            ) from None
        if evidence.rate_limited or monitor.encountered:
            raise SubstackRateLimitError(
                'Substack rate limiting occurred during publication inspection. '
                'Inspection stopped immediately without retry or reload. Local state unchanged.'
            )
        if not evidence.verified or evidence.inspection is None:
            raise BrowserSessionError(
                'Could not positively verify the linked authenticated draft editor. '
                'Continue was not clicked. Local state unchanged.'
            )
        remote = evidence.inspection
        if remote.draft_url.rstrip('/') != draft_url or remote.final_url.rstrip('/') != draft_url:
            raise BrowserSessionError(
                'The opened editor URL does not exactly match the linked numeric draft URL. '
                'Continue was not clicked.'
            )
        if remote.visible_title != story.metadata.title:
            raise BrowserSessionError('The live draft title does not exactly match the current story.')
        if remote.body_classification != 'substantial':
            raise BrowserSessionError('The live draft body is not substantial. Continue was not clicked.')
        if 'Saved' not in remote.editor_state or 'Saving' in remote.editor_state:
            raise BrowserSessionError('The live editor is not positively saved. Continue was not clicked.')

        try:
            social_preview = inspect_social_preview_image(page, monitor)
            monitor.require_clear(page)
        except SubstackRateLimitError:
            raise
        except (BrowserSessionError, PlaywrightError):
            monitor.require_clear(page)
            raise BrowserSessionError(
                'The Social Preview image could not be verified. Continue was not clicked.'
            ) from None
        if social_preview.state.value != 'present':
            raise BrowserSessionError(
                'The Social Preview image is not positively present. Continue was not clicked.'
            )
        close_preflight_dialogs(page, monitor)
        monitor.require_clear(page)
        if page.url.rstrip('/') != draft_url:
            raise BrowserSessionError(
                'The exact draft editor was not preserved after preflight. Continue was not clicked.'
            )

        if continue_diagnostic_only:
            continue_diagnostic = inspect_continue_candidates(page)
            monitor.require_clear(page)
            continue_control = None
            final_screen = None
        else:
            continue_diagnostic = None
            continue_control = select_continue_control(page)
            guard = install_mutation_guard(page)
            try:
                final_screen = open_and_inspect_final_publication_screen(
                    page, publication_url, draft_url, monitor, guard,
                    continue_control=continue_control,
                    leave_open=final_action_diagnostic,
                )
            except SubstackRateLimitError:
                raise
            except (BrowserSessionError, PlaywrightError):
                monitor.require_clear(page)
                raise

    database_after = sha256(paths.database.read_bytes()).hexdigest()
    local_state_changed = database_before != database_after
    if local_state_changed:
        raise BrowserSessionError(
            'The read-only inspection detected an unexpected SQLite change. Nothing was published.'
        )
    if continue_diagnostic_only:
        return PublishContinueDiagnosticResult(
            story, record, continue_diagnostic, local_state_changed,
        )
    return PublishInspectionResult(
        story, record, continue_control.role, continue_control.label,
        continue_control.test_id, continue_control.element_type,
        continue_control.context, final_screen, local_state_changed,
        database_before, database_after,
    )


def diagnose_substack_final_action(root: Path) -> PublishInspectionResult:
    """Collect raw final-action DOM evidence and close the browser without leaving the screen."""
    result = inspect_substack_publish(root, final_action_diagnostic=True)
    if not isinstance(result, PublishInspectionResult):
        raise BrowserSessionError('Final-action evidence was not available.')
    return result


def validate_substack_publish_configuration(root: Path) -> FinalPublishDryRunResult:
    """Run the existing safe inspection path, then validate its immutable snapshot."""
    from .browser.publish_validate import validate_final_publish_configuration

    inspection = inspect_substack_publish(root)
    validation = validate_final_publish_configuration(inspection.final_screen)
    return FinalPublishDryRunResult(inspection, validation)


def publish_substack(root: Path) -> 'PublishExecutionResult':
    """Publish the exact prepared draft through the guarded one-click executor."""
    from playwright.sync_api import Error as PlaywrightError
    from .browser.body import RateLimitMonitor
    from .browser.image_observe import inspect_social_preview_image
    from .browser.publish_execute import GuardedFinalPublishExecutor, PublishPreconditions
    from .browser.publish_inspect import (
        inspect_open_final_publication_screen, install_mutation_guard, select_continue_control,
    )
    from .browser.publish_navigate import (
        close_preflight_dialogs, open_and_inspect_final_publication_screen,
    )
    from .browser.reconcile import validate_supplied_draft_url, verify_supplied_draft

    inspection = inspect_project(root)
    publication_url = inspection.config.require_substack()
    story = inspection.story
    paths = runtime_paths(root)
    if not paths.database.is_file():
        raise BrowserSessionError('No publication database exists for the exact current story.')
    repository = PublicationRepository(paths.database)
    record = repository.require_publish_inspection_candidate(story.source_hash, 'substack')
    draft_url = validate_supplied_draft_url(record.draft_url, publication_url)
    if draft_url != record.draft_url.rstrip('/'):
        raise BrowserSessionError('The linked draft URL is not canonical. Nothing was published.')
    if not paths.substack_browser_profile.is_dir():
        raise BrowserSessionError(
            'No saved Substack session. Run publish-to-all substack-login first. '
            'Nothing was published.'
        )

    with persistent_browser(
        paths.substack_browser_profile, paths.diagnostics, headless=False,
    ) as context:
        page = context.new_page()
        authentication = verify_page(page, publication_url, 10, True)
        if authentication == AuthenticationState.RATE_LIMITED:
            raise SubstackRateLimitError(
                'Substack rate limiting occurred during publish preflight. Nothing was published.'
            )
        if authentication != AuthenticationState.AUTHENTICATED:
            raise BrowserSessionError(
                'Authenticated Substack preflight did not succeed. Nothing was published.'
            )

        # Re-read the exact association after authentication and before browser
        # mutation. This is the duplicate/identity compare point for the attempt.
        current = repository.require_publish_inspection_candidate(
            story.source_hash, 'substack',
        )
        duplicate = repository.find_duplicate(story.source_hash, 'substack')
        if current != record or duplicate != record:
            raise BrowserSessionError(
                'The exact story/draft association changed during preflight. Nothing was published.'
            )
        monitor = RateLimitMonitor(publication_url)
        page.on('response', monitor.observe)
        try:
            evidence = verify_supplied_draft(
                page, publication_url, draft_url, paths.diagnostics,
                rate_limit_monitor=monitor,
            )
        except SubstackRateLimitError:
            raise
        except (BrowserSessionError, PlaywrightError):
            monitor.require_clear(page)
            raise BrowserSessionError(
                'Could not safely verify the linked authenticated Substack editor. '
                'Nothing was published.'
            ) from None
        if evidence.rate_limited or monitor.encountered:
            raise SubstackRateLimitError(
                'Substack rate limiting occurred during publish preflight. Nothing was published.'
            )
        if not evidence.verified or evidence.inspection is None:
            raise BrowserSessionError(
                'Could not positively verify the linked authenticated draft editor. '
                'Nothing was published.'
            )
        remote = evidence.inspection
        if remote.draft_url.rstrip('/') != draft_url or remote.final_url.rstrip('/') != draft_url:
            raise BrowserSessionError(
                'The opened editor URL does not exactly match the linked numeric draft URL. '
                'Nothing was published.'
            )
        if remote.visible_title != story.metadata.title:
            raise BrowserSessionError(
                'The live draft title does not exactly match the current story. Nothing was published.'
            )
        if remote.body_classification != 'substantial':
            raise BrowserSessionError('The live draft body is not substantial. Nothing was published.')
        if 'Saved' not in remote.editor_state or 'Saving' in remote.editor_state:
            raise BrowserSessionError('The live editor is not positively saved. Nothing was published.')
        social_preview = inspect_social_preview_image(page, monitor)
        monitor.require_clear(page)
        if social_preview.state.value != 'present':
            raise BrowserSessionError(
                'The Social Preview image is not positively present. Nothing was published.'
            )
        close_preflight_dialogs(page, monitor)
        monitor.require_clear(page)
        if page.url.rstrip('/') != draft_url:
            raise BrowserSessionError(
                'The exact draft editor was not preserved after preflight. Nothing was published.'
            )

        continue_control = select_continue_control(page)
        guard = install_mutation_guard(page)
        initial_screen = open_and_inspect_final_publication_screen(
            page, publication_url, draft_url, monitor, guard,
            continue_control=continue_control, leave_open=True,
        )
        guard.require_clear()
        # Continue has now been proven read-only. Remove the inspection transport
        # guard so the single explicitly authorized final click can reach Substack.
        page.unroute('**/*')
        executor = GuardedFinalPublishExecutor(
            repository, page, publication_url, record, draft_url,
            lambda: inspect_open_final_publication_screen(page, publication_url),
        )
        return executor.execute(
            PublishPreconditions(
                authenticated=True, duplicate_protection_passed=True,
                draft_identity_matches=True,
            ),
            initial_screen,
        )


def reassociate_substack_version(root: Path, from_hash: str) -> PublicationReassociation:
    """Move an untouched draft association to the current story hash locally."""
    story = load_story(root / 'In')
    repository = PublicationRepository(runtime_paths(root).database)
    return repository.reassociate_draft_version(from_hash, story, 'substack')


def forget_deleted_substack_draft(
    root: Path, story_hash: str,
) -> DeletedDraftCleanupResult:
    """Verify one historical remote draft is gone, then remove its local record."""
    from playwright.sync_api import Error as PlaywrightError
    from .browser.body import RateLimitMonitor
    from .browser.deleted_draft import DeletedDraftState, inspect_deleted_draft
    from .browser.reconcile import validate_supplied_draft_url

    publication_url = load_config(root / 'config.toml').require_substack()
    paths = runtime_paths(root)

    # Verification is read-only. Do not migrate or otherwise modify SQLite until
    # remote deletion has been positively established.
    repository = PublicationRepository(paths.database, migrate=False)
    story = repository.get_story(story_hash)
    if story is None:
        raise BrowserSessionError(
            'The supplied historical story hash does not exist. Local state unchanged.'
        )
    record = repository.require_deleted_draft_removal_candidate(story_hash, 'substack')
    draft_url = validate_supplied_draft_url(record.draft_url, publication_url)
    if draft_url != record.draft_url.rstrip('/'):
        raise BrowserSessionError('The stored draft URL is not canonical. Local state unchanged.')
    if not paths.substack_browser_profile.is_dir():
        raise BrowserSessionError(
            'No saved Substack session. Authentication cannot be verified. Local state unchanged.'
        )

    with persistent_browser(
        paths.substack_browser_profile, paths.diagnostics, headless=False,
    ) as context:
        page = context.new_page()
        monitor = RateLimitMonitor(publication_url)
        page.on('response', monitor.observe)
        try:
            evidence = inspect_deleted_draft(
                page, publication_url, draft_url, monitor,
            )
        except PlaywrightError:
            if monitor.encountered:
                raise SubstackRateLimitError(
                    'Substack rate limiting occurred while checking the stored draft. '
                    'No retry or reload was attempted. Local state unchanged.'
                ) from None
            raise BrowserSessionError(
                'Remote draft state is unknown because the stored URL could not be inspected. '
                'Local state unchanged.'
            ) from None

    if evidence.state == DeletedDraftState.RATE_LIMITED:
        raise SubstackRateLimitError(
            'Substack rate limiting occurred while checking the stored draft. '
            'No retry or reload was attempted. Local state unchanged.'
        )
    if evidence.state == DeletedDraftState.AUTHENTICATION_FAILED:
        raise BrowserSessionError(
            'Authentication could not be verified for the stored Substack draft. '
            'Local state unchanged.'
        )
    if evidence.state == DeletedDraftState.EXISTS:
        raise BrowserSessionError(
            'The stored Substack draft still exists remotely. Deleted-draft cleanup refused; '
            'local state unchanged.'
        )
    if evidence.state == DeletedDraftState.PUBLISHED:
        raise BrowserSessionError(
            'The stored Substack draft redirects to a published post. '
            'Deleted-draft cleanup refused; local state unchanged.'
        )
    if evidence.state != DeletedDraftState.DELETED:
        raise BrowserSessionError(
            'Remote draft state is unknown. Deletion was not positively verified; '
            'local state unchanged.'
        )

    repository = PublicationRepository(paths.database)
    removal = repository.remove_verified_deleted_draft(
        story_hash, 'substack', record, draft_url,
    )
    return DeletedDraftCleanupResult(story, removal, evidence.reason)


def prepare_substack_draft(root: Path) -> StoryStatus:
    inspection = inspect_project(root)
    publication_url = inspection.config.require_substack()
    story = inspection.story
    paths = runtime_paths(root)
    # Preserve first-run local state when a browser preflight cannot proceed.
    repository = PublicationRepository(paths.database) if paths.database.exists() else None
    if repository is not None:
        duplicate = repository.find_duplicate(story.source_hash, 'substack')
        if duplicate:
            repository.refuse_duplicate(story, duplicate)
    if not paths.substack_browser_profile.is_dir():
        raise BrowserSessionError('No saved Substack session. Run publish-to-all substack-login first. No draft was created.')
    with persistent_browser(paths.substack_browser_profile, paths.diagnostics, headless=False) as context:
        page = context.new_page()
        authentication = verify_page(page, publication_url, 10, True)
        if authentication == AuthenticationState.RATE_LIMITED:
            diagnostic = collect_diagnostics(
                page, publication_url, paths.diagnostics, rate_limited=True,
            )
            raise SubstackRateLimitError(
                'Substack is temporarily rate limited.\n\n'
                'Substack is refusing requests from this browser session right now.\n\n'
                f'Final URL: {diagnostic.final_url}\n'
                f'Page title: {diagnostic.title}\n'
                'Rate limiting detected: Yes\n\n'
                'Retry manually later. Run publish-to-all substack-session first to check whether it has cleared.\n\n'
                'No local publication state was changed.\n'
                'No draft was created.\n'
                'Nothing was published.'
            )
        if authentication != AuthenticationState.AUTHENTICATED:
            raise BrowserSessionError(
                'Could not confirm an authenticated Substack session. Run publish-to-all substack-login. '
                'No draft was created. Nothing was published.'
            )
        # Repeat duplicate detection atomically after authentication and before mutation.
        repository = repository or PublicationRepository(paths.database)
        attempt = repository.begin_attempt(story, 'substack')
        publisher = SubstackPublisher(repository, page, publication_url, paths.diagnostics)
        record = publisher.prepare_draft(story, attempt)
    return StoryStatus(story, record)


def add_substack_body(root: Path) -> StoryStatus:
    """Insert the parsed body into the exact already-linked Substack draft."""
    from playwright.sync_api import Error as PlaywrightError
    from .browser.body import RateLimitMonitor
    from .browser.reconcile import validate_supplied_draft_url, verify_supplied_draft

    inspection = inspect_project(root)
    publication_url = inspection.config.require_substack()
    story = inspection.story
    paths = runtime_paths(root)
    repository = PublicationRepository(paths.database)
    record = repository.require_body_candidate(story.source_hash, 'substack')
    draft_url = validate_supplied_draft_url(record.draft_url, publication_url)
    if draft_url != record.draft_url.rstrip('/'):
        raise BrowserSessionError('The linked draft URL is not canonical. Nothing was changed.')
    if not paths.substack_browser_profile.is_dir():
        raise BrowserSessionError(
            'No saved Substack session. Run publish-to-all substack-login first. '
            'The linked draft was not changed.'
        )

    with persistent_browser(paths.substack_browser_profile, paths.diagnostics, headless=False) as context:
        page = context.new_page()
        monitor = RateLimitMonitor(publication_url)
        page.on('response', monitor.observe)
        try:
            evidence = verify_supplied_draft(
                page, publication_url, draft_url, paths.diagnostics,
            )
        except (BrowserSessionError, PlaywrightError):
            raise BrowserSessionError(
                'Could not safely verify the linked authenticated Substack editor. '
                'The draft was not changed. Nothing was published.'
            ) from None
        if evidence.rate_limited or monitor.encountered:
            raise SubstackRateLimitError(
                'Substack rate limit encountered before body insertion.\n\n'
                'Local publication state is unchanged.\n\nNothing was published.'
            )
        if not evidence.verified or evidence.inspection is None:
            raise BrowserSessionError(
                'Could not safely verify the linked authenticated Substack editor. '
                'The draft was not changed. Nothing was published.'
            )
        if evidence.inspection.draft_url.rstrip('/') != record.draft_url.rstrip('/'):
            raise BrowserSessionError(
                'The opened editor does not match the linked draft URL. '
                'The draft was not changed. Nothing was published.'
            )
        publisher = SubstackBodyPublisher(
            repository, page, publication_url, monitor,
        )
        updated = publisher.add_body(story, record)
    return StoryStatus(story, updated)


def add_substack_image(root: Path) -> StoryStatus:
    """Upload the current story image only through the article Social Preview editor."""
    from playwright.sync_api import Error as PlaywrightError
    from .browser.body import RateLimitMonitor
    from .browser.image import validate_upload_image
    from .browser.reconcile import validate_supplied_draft_url, verify_supplied_draft

    inspection = inspect_project(root)
    publication_url = inspection.config.require_substack()
    story = inspection.story
    if story.substack_image is None:
        raise BrowserSessionError(
            'No matching story image exists for the current story. Nothing was changed.'
        )
    validate_upload_image(story.substack_image)

    paths = runtime_paths(root)
    repository = PublicationRepository(paths.database)
    record = repository.require_image_candidate(story.source_hash, 'substack')
    draft_url = validate_supplied_draft_url(record.draft_url, publication_url)
    if draft_url != record.draft_url.rstrip('/'):
        raise BrowserSessionError('The linked draft URL is not canonical. Nothing was changed.')
    if not paths.substack_browser_profile.is_dir():
        raise BrowserSessionError(
            'No saved Substack session. Run publish-to-all substack-login first. '
            'The linked draft was not changed.'
        )

    with persistent_browser(paths.substack_browser_profile, paths.diagnostics, headless=False) as context:
        page = context.new_page()
        monitor = RateLimitMonitor(publication_url)
        page.on('response', monitor.observe)
        try:
            evidence = verify_supplied_draft(
                page, publication_url, draft_url, paths.diagnostics,
                rate_limit_monitor=monitor,
            )
        except (BrowserSessionError, PlaywrightError):
            monitor.require_clear(page)
            raise BrowserSessionError(
                'Could not safely verify the linked authenticated Substack editor. '
                'The draft was not changed. Nothing was published.'
            ) from None
        if evidence.rate_limited or monitor.encountered:
            raise SubstackRateLimitError(
                'Substack rate limit encountered before Social Preview image upload.\n\n'
                'Local publication state is unchanged.\n\nNothing was published.'
            )
        if not evidence.verified or evidence.inspection is None:
            raise BrowserSessionError(
                'Could not safely verify the linked authenticated Substack editor. '
                'The draft was not changed. Nothing was published.'
            )
        remote = evidence.inspection
        if remote.draft_url.rstrip('/') != draft_url:
            raise BrowserSessionError(
                'The opened editor does not match the linked draft URL. Nothing was changed.'
            )
        if remote.visible_title != story.metadata.title or remote.body_classification != 'substantial':
            raise BrowserSessionError(
                'The linked draft title or populated body does not match the image preflight. '
                'Nothing was changed.'
            )
        publisher = SubstackImagePublisher(
            repository, page, publication_url, monitor, paths.diagnostics,
        )
        updated = publisher.upload_image(story, record)
    return StoryStatus(story, updated)


def reconcile_substack_image(
    root: Path, exact_story_hash: str,
) -> ImageReconciliationResult:
    """Read one linked draft and reconcile its local Social Preview image state."""
    from playwright.sync_api import Error as PlaywrightError
    from .browser.body import RateLimitMonitor
    from .browser.image_observe import inspect_social_preview_image
    from .browser.reconcile import validate_supplied_draft_url, verify_supplied_draft

    inspection = inspect_project(root)
    publication_url = inspection.config.require_substack()
    story = inspection.story
    if not exact_story_hash or exact_story_hash != story.source_hash:
        raise BrowserSessionError(
            'The supplied story hash does not exactly match the current story. '
            'Local state is unchanged. Nothing was published.'
        )
    paths = runtime_paths(root)
    repository = PublicationRepository(paths.database)
    record = repository.require_image_reconciliation_candidate(exact_story_hash, 'substack')
    draft_url = validate_supplied_draft_url(record.draft_url, publication_url)
    if draft_url != record.draft_url.rstrip('/'):
        raise BrowserSessionError('The linked draft URL is not canonical. Local state is unchanged.')
    if not paths.substack_browser_profile.is_dir():
        raise BrowserSessionError(
            'No saved Substack session. Run publish-to-all substack-login first. '
            'Local state is unchanged.'
        )

    with persistent_browser(paths.substack_browser_profile, paths.diagnostics, headless=False) as context:
        page = context.new_page()
        monitor = RateLimitMonitor(publication_url)
        page.on('response', monitor.observe)
        try:
            evidence = verify_supplied_draft(
                page, publication_url, draft_url, paths.diagnostics,
                rate_limit_monitor=monitor,
            )
        except (BrowserSessionError, PlaywrightError):
            try:
                monitor.require_clear(page)
            except SubstackRateLimitError:
                raise SubstackRateLimitError(
                    'Substack rate limit encountered during image reconciliation.\n\n'
                    'Local state is unchanged. Nothing was published.'
                ) from None
            raise BrowserSessionError(
                'Could not safely inspect the linked authenticated Substack editor. '
                'Local state is unchanged. Nothing was published.'
            ) from None
        if evidence.rate_limited or monitor.encountered:
            raise SubstackRateLimitError(
                'Substack rate limit encountered during image reconciliation.\n\n'
                'Local state is unchanged. Nothing was published.'
            )
        if not evidence.verified or evidence.inspection is None:
            raise BrowserSessionError(
                'Could not positively verify the linked draft before Social Preview inspection. '
                'Local state is unchanged and image retry remains blocked. Nothing was published.'
            )
        remote = evidence.inspection
        if remote.draft_url.rstrip('/') != draft_url:
            raise BrowserSessionError(
                'The opened editor does not match the linked draft URL. Local state is unchanged.'
            )
        if remote.visible_title != story.metadata.title or remote.body_classification != 'substantial':
            raise BrowserSessionError(
                'The linked draft title or populated body does not match the current story. '
                'Local state is unchanged.'
            )
        try:
            social_preview = inspect_social_preview_image(page, monitor)
        except (BrowserSessionError, PlaywrightError):
            monitor.require_clear(page)
            raise BrowserSessionError(
                'Remote Social Preview image state is unknown. Local state is unchanged '
                'and image retry remains blocked.'
            ) from None
        social_state = social_preview.state.value
        if social_state == 'unknown':
            raise BrowserSessionError(
                'Remote Social Preview image state is unknown. Local state is unchanged '
                'and image retry remains blocked.'
            )
        if social_state not in {'none', 'present'}:
            raise BrowserSessionError(
                'Remote Social Preview image state is not positive reconciliation evidence. '
                'Local state is unchanged.'
            )
        monitor.require_clear(page)
        updated = repository.reconcile_image_upload(record, social_state)
    return ImageReconciliationResult(story, updated, social_state)


def observe_substack_image(root: Path) -> ImageObservationResult:
    """Observe one user-driven social/post-preview upload without injecting a file."""
    from playwright.sync_api import Error as PlaywrightError
    from .browser import body as body_editor
    from .browser import editor
    from .browser import image_observe
    from .browser.body import RateLimitMonitor
    from .browser.reconcile import validate_supplied_draft_url, verify_supplied_draft

    inspection = inspect_project(root)
    publication_url = inspection.config.require_substack()
    story = inspection.story
    paths = runtime_paths(root)
    repository = PublicationRepository(paths.database)
    record = repository.require_image_candidate(story.source_hash, 'substack')
    draft_url = validate_supplied_draft_url(record.draft_url, publication_url)
    if draft_url != record.draft_url.rstrip('/'):
        raise BrowserSessionError('The linked draft URL is not canonical. Local state is unchanged.')
    if not paths.substack_browser_profile.is_dir():
        raise BrowserSessionError(
            'No saved Substack session. Run publish-to-all substack-login first. '
            'Local state is unchanged.'
        )

    with persistent_browser(paths.substack_browser_profile, paths.diagnostics, headless=False) as context:
        page = context.new_page()
        monitor = RateLimitMonitor(publication_url)
        network = image_observe.SafeNetworkObservation(publication_url)
        page.on('response', monitor.observe)
        page.on('response', network.observe)
        try:
            evidence = verify_supplied_draft(
                page, publication_url, draft_url, paths.diagnostics,
                rate_limit_monitor=monitor,
            )
        except (BrowserSessionError, PlaywrightError):
            monitor.require_clear(page)
            raise BrowserSessionError(
                'Could not safely verify the linked authenticated Substack editor. '
                'Local state is unchanged. Nothing was published.'
            ) from None
        if evidence.rate_limited or monitor.encountered:
            raise SubstackRateLimitError(
                'Substack rate limit encountered before manual image observation.\n\n'
                'Local state is unchanged. Nothing was published.'
            )
        if not evidence.verified or evidence.inspection is None:
            raise BrowserSessionError(
                'Could not safely verify the linked authenticated Substack editor. '
                'Local state is unchanged. Nothing was published.'
            )
        remote = evidence.inspection
        if remote.draft_url.rstrip('/') != draft_url:
            raise BrowserSessionError('The opened editor does not match the linked draft URL.')
        if remote.visible_title != story.metadata.title or remote.body_classification != 'substantial':
            raise BrowserSessionError(
                'The linked draft title or populated body does not match the observation preflight.'
            )

        title_field = editor.unique_visible(editor.title_fields(page))
        body_surface = body_editor.locate_body_surface(page)
        if title_field is None or not title_field.is_editable():
            raise BrowserSessionError('The draft title could not be inspected safely.')
        initial_title = editor.title_value(title_field).strip()
        initial_body = ' '.join(body_surface.inner_text().split())
        target = image_observe.open_social_preview_image(page, monitor)
        selected_files = target.file_input.evaluate(
            'node => node.files ? node.files.length : 0'
        )
        if selected_files:
            raise BrowserSessionError(
                'The Social preview file input was not empty before observation. '
                'No file was selected by this command.'
            )
        image_observe.install_dom_observer(page, target)
        network.active = True
        print(
            '\nManual observation is ready in the visible browser.\n'
            'Target: Post settings → Social preview → Edit social preview → Image.\n'
            'Choose the image yourself with Select image and complete the Social preview Save step.\n'
            'Do not click Continue, Publish, Send, or Schedule.\n'
            'This command will not select a file or click any publication control.\n',
            flush=True,
        )
        image_observe.wait_for_manual_upload(page, monitor)
        page.wait_for_timeout(500)
        monitor.require_clear(page)
        dom = image_observe.read_dom_observation(page)
        final_url_unchanged = page.url.rstrip('/') == draft_url
        try:
            final_title_field = editor.unique_visible(editor.title_fields(page))
            final_body_surface = body_editor.locate_body_surface(page)
            title_preserved = bool(
                final_title_field is not None
                and editor.title_value(final_title_field).strip() == initial_title
            )
            body_preserved = ' '.join(final_body_surface.inner_text().split()) == initial_body
        except (BrowserSessionError, PlaywrightError):
            title_preserved = body_preserved = False

    current = repository.get_publication(story.source_hash, 'substack')
    local_unchanged = current == record
    if not local_unchanged:
        raise BrowserSessionError(
            'Local publication state changed unexpectedly during observation. Nothing was published.'
        )
    return ImageObservationResult(
        story, current, draft_url, image_observe.ImageFeature.SOCIAL_POST_PREVIEW.value,
        dom, network.responses, network.successful_responses, network.failed_responses,
        final_url_unchanged, title_preserved, body_preserved, local_unchanged,
    )


def repair_substack_title(root: Path) -> StoryStatus:
    """Repair the empty title on the exact already-linked Substack draft."""
    from playwright.sync_api import Error as PlaywrightError
    from .browser.body import RateLimitMonitor
    from .browser.reconcile import validate_supplied_draft_url, verify_supplied_draft

    inspection = inspect_project(root)
    publication_url = inspection.config.require_substack()
    story = inspection.story
    paths = runtime_paths(root)
    repository = PublicationRepository(paths.database)
    record = repository.require_title_repair_candidate(story.source_hash, 'substack')
    draft_url = validate_supplied_draft_url(record.draft_url, publication_url)
    if draft_url != record.draft_url.rstrip('/'):
        raise BrowserSessionError('The linked draft URL is not canonical. Nothing was changed.')
    if not paths.substack_browser_profile.is_dir():
        raise BrowserSessionError(
            'No saved Substack session. Run publish-to-all substack-login first. '
            'The linked draft was not changed.'
        )

    with persistent_browser(paths.substack_browser_profile, paths.diagnostics, headless=False) as context:
        page = context.new_page()
        monitor = RateLimitMonitor(publication_url)
        page.on('response', monitor.observe)
        try:
            evidence = verify_supplied_draft(
                page, publication_url, draft_url, paths.diagnostics,
                rate_limit_monitor=monitor,
            )
        except (BrowserSessionError, PlaywrightError):
            try:
                monitor.require_clear(page)
            except SubstackRateLimitError:
                raise SubstackRateLimitError(
                    'Substack rate limit encountered while repairing the draft title.\n\n'
                    'The title may or may not have been saved.\n\n'
                    'No retry was attempted.\nNothing was published.'
                ) from None
            raise BrowserSessionError(
                'Could not safely verify the linked authenticated Substack editor. '
                'The draft was not changed. Nothing was published.'
            ) from None
        if evidence.rate_limited or monitor.encountered:
            raise SubstackRateLimitError(
                'Substack rate limit encountered while repairing the draft title.\n\n'
                'The title may or may not have been saved.\n\n'
                'No retry was attempted.\nNothing was published.'
            )
        if not evidence.verified or evidence.inspection is None:
            raise BrowserSessionError(
                'Could not safely verify the linked authenticated Substack editor. '
                'The draft was not changed. Nothing was published.'
            )
        if evidence.inspection.draft_url.rstrip('/') != record.draft_url.rstrip('/'):
            raise BrowserSessionError(
                'The opened editor does not match the linked draft URL. '
                'The draft was not changed. Nothing was published.'
            )
        publisher = SubstackTitleRepairPublisher(
            repository, page, publication_url, monitor,
        )
        updated = publisher.repair_title(story, record)
    return StoryStatus(story, updated)


def inspect_substack_draft(root: Path, draft_url: str) -> str:
    """Inspect one explicitly supplied editor URL without reading or writing SQLite."""
    from playwright.sync_api import Error as PlaywrightError
    from .browser.body import RateLimitMonitor
    from .browser.image_observe import SocialPreviewState, inspect_social_preview_image
    from .browser.reconcile import (
        SuppliedDraftEvidence, supplied_draft_diagnostics, validate_supplied_draft_url,
        verify_supplied_draft,
    )

    inspection = inspect_project(root)
    publication_url = inspection.config.require_substack()
    supplied_url = validate_supplied_draft_url(draft_url, publication_url)
    paths = runtime_paths(root)
    if not paths.substack_browser_profile.is_dir():
        raise BrowserSessionError('No saved Substack session. Local SQLite state unchanged.')
    with persistent_browser(paths.substack_browser_profile, paths.diagnostics, headless=False) as context:
        page = context.new_page()
        monitor = RateLimitMonitor(publication_url)
        page.on('response', monitor.observe)
        social_preview_state = SocialPreviewState.UNKNOWN.value
        try:
            evidence = verify_supplied_draft(
                page, publication_url, supplied_url, paths.diagnostics,
                rate_limit_monitor=monitor,
            )
        except SubstackRateLimitError:
            return _format_supplied_draft_failure(
                SuppliedDraftEvidence(False, 'Substack returned a rate-limit response.', True),
                rate_limited=True,
            )
        except (BrowserSessionError, PlaywrightError):
            evidence = SuppliedDraftEvidence(
                False, 'Editor evidence could not be read safely.',
                diagnostics=supplied_draft_diagnostics(
                    page, publication_url, paths.diagnostics,
                ),
            )
        if evidence.verified and evidence.inspection is not None:
            try:
                social_preview_state = inspect_social_preview_image(page, monitor).state.value
            except SubstackRateLimitError:
                return _format_supplied_draft_failure(
                    SuppliedDraftEvidence(False, 'Substack returned a rate-limit response.', True),
                    rate_limited=True,
                )
            except (BrowserSessionError, PlaywrightError):
                social_preview_state = SocialPreviewState.UNKNOWN.value
    if evidence.rate_limited:
        return _format_supplied_draft_failure(evidence, rate_limited=True)
    if not evidence.verified or evidence.inspection is None:
        if evidence.verified:
            evidence = SuppliedDraftEvidence(
                False, 'The editable title and body could not both be read consistently.',
            )
        return _format_supplied_draft_failure(evidence)
    result = evidence.inspection
    lines = [
        'Substack draft inspection', '',
        'Verified numeric draft URL: ' + result.draft_url,
        'Visible title: ' + (result.visible_title or '[empty]'),
        'Editable body: ' + result.body_classification,
        'Cover/image appears present: ' + ('Yes' if result.image_present else 'No confirmed cover'),
        'Cover image state: ' + result.cover_state.capitalize(),
        'Social preview image: ' + social_preview_state,
        'Visible save/editor state: ' + (', '.join(result.editor_state) if result.editor_state else 'None visible'),
        'Definitely a draft editor: ' + ('Yes' if result.definitely_draft_editor else 'No'),
        'Final URL: ' + result.final_url,
        'Page title: ' + result.page_title,
        'Visible editor controls: ' + (', '.join(result.controls) if result.controls else 'None recognized'),
    ]
    if result.cover_state == 'unknown':
        diagnostic = result.cover_diagnostics
        lines += [
            '', 'Cover diagnostics:',
            'Relevant image labels: ' + (
                ', '.join(diagnostic.image_labels) if diagnostic.image_labels else 'None recognized'
            ),
            'Cover-like controls: ' + (
                ', '.join(diagnostic.cover_like_controls)
                if diagnostic.cover_like_controls else 'None recognized'
            ),
            'Cover preview present: ' + ('Yes' if diagnostic.cover_preview_present else 'No'),
            'Cover add/upload control present: ' + (
                'Yes' if diagnostic.cover_add_control_present else 'No'
            ),
            f'Inline body-image controls excluded: {diagnostic.inline_image_controls_excluded}',
            f'Social-preview controls excluded: {diagnostic.social_preview_controls_excluded}',
        ]
    return '\n'.join([
        *lines, '', 'Local SQLite state unchanged.',
        'No remote changes were made.', 'Nothing was published.',
    ])


def inspect_substack_social_preview(
    root: Path, draft_url: str,
) -> SocialPreviewReadOnlyResult:
    """Open one supplied draft and inspect Social Preview without changing it."""
    from playwright.sync_api import Error as PlaywrightError
    from .browser.body import RateLimitMonitor
    from .browser.image_observe import inspect_social_preview_image
    from .browser.reconcile import validate_supplied_draft_url, verify_supplied_draft

    inspection = inspect_project(root)
    publication_url = inspection.config.require_substack()
    supplied_url = validate_supplied_draft_url(draft_url, publication_url)
    paths = runtime_paths(root)
    if not paths.substack_browser_profile.is_dir():
        raise BrowserSessionError(
            'No saved Substack session. No remote or local changes were made.'
        )
    with persistent_browser(
        paths.substack_browser_profile, paths.diagnostics, headless=False,
    ) as context:
        page = context.new_page()
        monitor = RateLimitMonitor(publication_url)
        page.on('response', monitor.observe)
        try:
            evidence = verify_supplied_draft(
                page, publication_url, supplied_url, paths.diagnostics,
                rate_limit_monitor=monitor,
            )
        except SubstackRateLimitError:
            raise
        except (BrowserSessionError, PlaywrightError):
            monitor.require_clear(page)
            raise BrowserSessionError(
                'Could not safely verify the supplied authenticated Substack editor. '
                'No remote or local changes were made.'
            ) from None
        if evidence.rate_limited or monitor.encountered:
            raise SubstackRateLimitError(
                'Substack rate limit encountered during read-only Social Preview inspection. '
                'Inspection stopped immediately. No remote or local changes were made.'
            )
        if not evidence.verified or evidence.inspection is None:
            raise BrowserSessionError(
                'Could not positively verify the supplied draft editor. '
                'No remote or local changes were made.'
            )
        if evidence.inspection.draft_url.rstrip('/') != supplied_url:
            raise BrowserSessionError(
                'The opened editor does not match the supplied draft URL. '
                'No remote or local changes were made.'
            )
        result = inspect_social_preview_image(page, monitor)
        monitor.require_clear(page)
        return SocialPreviewReadOnlyResult(
            supplied_url, result.state.value,
            result.diagnostics.local_save_count,
            result.diagnostics.local_save_enabled,
        )


def reconcile_substack(
    root: Path, *, link: bool = False, draft_url: str | None = None,
    replace_linked_draft: bool = False,
) -> str:
    """Read-only remotely; optionally link locally with corroborating attempt URL."""
    from playwright.sync_api import Error as PlaywrightError
    from .browser.body import RateLimitMonitor
    from .browser.reconcile import (
        inspect_drafts, DraftEvidence, SuppliedDraftEvidence, supplied_draft_diagnostics,
        validate_supplied_draft_url, verify_supplied_draft,
    )
    from .state import PublicationStatus

    inspection = inspect_project(root)
    publication_url = inspection.config.require_substack()
    if replace_linked_draft and draft_url is None:
        raise BrowserSessionError('--replace-linked-draft requires --draft-url. Local state unchanged.')
    supplied_url = validate_supplied_draft_url(draft_url, publication_url) if draft_url is not None else None
    paths = runtime_paths(root)
    repository = PublicationRepository(paths.database)
    attempt = repository.find_duplicate(inspection.story.source_hash, 'substack')
    unresolved = bool(
        attempt and attempt.status == PublicationStatus.FAILED and attempt.needs_reconciliation
    )
    linked = bool(
        attempt and attempt.status == PublicationStatus.DRAFT_CREATED
        and attempt.draft_url and not attempt.published_url
    )
    recorded_url = (
        validate_supplied_draft_url(attempt.draft_url, publication_url)
        if unresolved and attempt.draft_url else None
    )
    if supplied_url and recorded_url and supplied_url != recorded_url:
        return '\n'.join([
            'Unable to verify supplied draft URL safely.', '',
            'Reason: The failed attempt already records a different numeric draft URL.', '',
            'Recorded draft: ' + recorded_url, '',
            'Local state unchanged.', 'Nothing was published.',
        ])
    if not unresolved and not (supplied_url and linked):
        if supplied_url:
            return 'Unable to verify supplied draft URL safely.\n\nLocal state unchanged.\nNothing was published.'
        return 'No unresolved failed attempt for this exact story version. Local state unchanged.'
    if not paths.substack_browser_profile.is_dir():
        if supplied_url:
            return 'Unable to verify supplied draft URL safely.\n\nLocal state unchanged.\nNothing was published.'
        raise BrowserSessionError('No saved Substack session. Local state unchanged.')
    with persistent_browser(paths.substack_browser_profile, paths.diagnostics, headless=False) as context:
        page = context.new_page()
        exact_url = supplied_url or recorded_url
        if exact_url:
            monitor = RateLimitMonitor(publication_url)
            page.on('response', monitor.observe)
            try:
                evidence = verify_supplied_draft(
                    page, publication_url, exact_url, paths.diagnostics,
                    rate_limit_monitor=monitor,
                )
            except (BrowserSessionError, PlaywrightError):
                if monitor.encountered:
                    evidence = SuppliedDraftEvidence(
                        False, 'Substack returned a rate-limit response.', rate_limited=True,
                    )
                else:
                    evidence = SuppliedDraftEvidence(
                        False, 'Editor evidence could not be read safely.', diagnostics=supplied_draft_diagnostics(
                            page, publication_url, paths.diagnostics,
                        ),
                    )
            if evidence.rate_limited:
                return _format_supplied_draft_failure(evidence, rate_limited=True)
            if not evidence.verified:
                return _format_supplied_draft_failure(evidence)
            if linked:
                if not replace_linked_draft:
                    return '\n'.join([
                        'Verified supplied Substack draft editor.', '',
                        'Draft:', exact_url, '',
                        'Local association unchanged.',
                        'To replace it explicitly, add --replace-linked-draft.', '',
                        'No remote changes were made.', 'Nothing was published.',
                    ])
                record = repository.replace_linked_draft(
                    inspection.story.source_hash, 'substack', attempt, exact_url,
                )
            else:
                if supplied_url or link:
                    record = repository.reconcile_failed_draft(
                        attempt, exact_url, verified_manual_url=supplied_url is not None,
                    )
                else:
                    return '\n'.join([
                        'Verified the exact Substack draft recorded by the failed attempt.', '',
                        'Draft:', exact_url, '', 'Visible title:',
                        (evidence.inspection.visible_title
                         if evidence.inspection and evidence.inspection.visible_title else '[empty]'), '',
                        'Local state unchanged.',
                        'To reconcile locally, run: publish-to-all substack-reconcile --link', '',
                        'No remote changes were made.', 'Nothing was published.',
                    ])
            return '\n'.join([
                'Substack reconciliation complete', '', 'Story:', inspection.story.metadata.title, '',
                'Draft:', record.draft_url or exact_url, '', 'Local state:', 'Draft created', '',
                'Duplicate protection remains active.', '',
                'No remote draft was modified or deleted.', 'Nothing was published.',
            ])
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


def _format_supplied_draft_failure(evidence, *, rate_limited: bool = False) -> str:
    report = ['RATE_LIMITED' if rate_limited else 'Unable to verify supplied draft URL safely.']
    if evidence.reason:
        report.extend(['', 'Reason: ' + evidence.reason])
    diagnostic = evidence.diagnostics
    if diagnostic is not None:
        report.extend([
            '', 'Final URL: ' + diagnostic.final_url,
            'Page title: ' + diagnostic.title,
            'Editable title field: ' + ('Yes' if diagnostic.title_field_present else 'No'),
            'Editable post editor: ' + ('Yes' if diagnostic.editor_surface_present else 'No'),
            'Visible editor controls: ' + (', '.join(diagnostic.controls) if diagnostic.controls else 'None recognized'),
            'Publish/Send control present: ' + ('Yes' if diagnostic.publish_or_send_present else 'No'),
            'Diagnostic screenshot: ' + (str(diagnostic.screenshot) if diagnostic.screenshot else 'Unavailable'),
        ])
    report.extend(['', 'Local state unchanged.', 'Nothing was published.'])
    return '\n'.join(report)

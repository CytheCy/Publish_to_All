"""Read-only application operations reusable by any interface."""

from dataclasses import dataclass
from pathlib import Path

from .config import Config, load_config, runtime_paths
from .story import Story, load_story
from .state import PublicationReassociation, PublicationRecord, PublicationRepository
from .browser.session import persistent_browser
from .browser.substack import AuthenticationState, collect_diagnostics, verify_page
from .errors import BrowserSessionError, SubstackRateLimitError
from .publishers.substack import (
    SubstackBodyPublisher, SubstackImagePublisher, SubstackPublisher,
    SubstackTitleRepairPublisher,
)


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


def inspect_status(root: Path) -> StoryStatus:
    """Read exact-version state, initializing only the schema if necessary."""
    story = load_story(root / "In")
    repository = PublicationRepository(runtime_paths(root).database)
    return StoryStatus(story, repository.get_publication(story.source_hash, "substack"))


def reassociate_substack_version(root: Path, from_hash: str) -> PublicationReassociation:
    """Move an untouched draft association to the current story hash locally."""
    story = load_story(root / 'In')
    repository = PublicationRepository(runtime_paths(root).database)
    return repository.reassociate_draft_version(from_hash, story, 'substack')


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
        if supplied_url:
            try:
                evidence = verify_supplied_draft(
                    page, publication_url, supplied_url, paths.diagnostics,
                )
            except (BrowserSessionError, PlaywrightError):
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
                        'Draft:', supplied_url, '',
                        'Local association unchanged.',
                        'To replace it explicitly, add --replace-linked-draft.', '',
                        'No remote changes were made.', 'Nothing was published.',
                    ])
                record = repository.replace_linked_draft(
                    inspection.story.source_hash, 'substack', attempt, supplied_url,
                )
            else:
                record = repository.reconcile_failed_draft(
                    attempt, supplied_url, verified_manual_url=True,
                )
            return '\n'.join([
                'Substack reconciliation complete', '', 'Story:', inspection.story.metadata.title, '',
                'Draft:', record.draft_url or supplied_url, '', 'Local state:', 'Draft created', '',
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

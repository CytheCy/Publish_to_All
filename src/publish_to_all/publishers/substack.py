"""Title-only Substack adapter consuming an already authenticated browser page."""

import hashlib

from playwright.sync_api import Error as PlaywrightError

from ..browser import editor
from ..browser import body as body_editor
from ..browser import title as title_editor
from ..browser import image as image_editor
from ..browser.session import safe_screenshot
from ..browser.substack import collect_diagnostics
from ..errors import PublishToAllError, SubstackRateLimitError
from ..state import StateError
from .base import Publisher


class SubstackPublisher(Publisher):
    name = 'substack'

    def __init__(self, repository, page, publication_url, diagnostics):
        super().__init__(repository)
        self.page = page
        self.publication_url = publication_url
        self.diagnostics = diagnostics

    def prepare_draft(self, story, attempt):
        step = 'Navigate to dashboard'
        uncertain = False
        url = None
        try:
            editor.navigate_dashboard(self.page, self.publication_url)
            step = 'Open new-post editor'
            # A click may create a remote draft even if Playwright subsequently fails.
            uncertain = True
            editor.open_new_post(self.page, self.publication_url)
            step = 'Locate title field'
            field = editor.locate_title_field(self.page, self.publication_url)
            url = editor.extract_draft_url(self.page, self.publication_url)
            step = 'Enter title'
            previously_saved = editor.save_visible(self.page)
            editor.enter_title(self.page, field, story.metadata.title, self.publication_url)
            step = 'Confirm draft save'
            url = editor.confirm_draft(self.page, field, story.metadata.title,
                                       self.publication_url, previously_saved=previously_saved)
            step = 'Record draft in SQLite'
            return self.repository.mark_draft_created(attempt.id, url)
        except (Exception, KeyboardInterrupt):
            diagnostic_text = ''
            if step == 'Open new-post editor':
                diagnostic = collect_diagnostics(self.page, self.publication_url, self.diagnostics)
                screenshot = diagnostic.screenshot
                diagnostic_text = (f'Final URL: {diagnostic.final_url}\n'
                                   f'Page title: {diagnostic.title}\n'
                                   f'Visible controls: {", ".join(diagnostic.controls) or "None recognized"}\n')
            else:
                screenshot = safe_screenshot(self.page, self.diagnostics)
            # Do not propagate raw browser errors or rendered page content.
            try:
                url = url or editor.extract_draft_url(self.page, self.publication_url)
            except Exception:
                pass
            message = f'Substack draft creation failed at step: {step}.'
            if uncertain:
                message += ' A remote draft may exist; reconciliation required before retry.'
            if screenshot:
                message += f' Diagnostic screenshot: {screenshot}'
            message += '\n' + diagnostic_text
            state = 'Failed'
            try:
                self.repository.mark_failed(attempt.id, message, draft_url=url,
                                            needs_reconciliation=uncertain)
            except StateError:
                state = 'Could not record failure; inspect SQLite state before retrying'
            raise PublishToAllError(
                f'Substack draft creation failed.\n\nStep:\n{step}\n\n'
                + diagnostic_text + f'\nDiagnostic screenshot:\n{screenshot or "Unavailable (capture failed or browser closed)"}\n\n'
                f'Publication state:\n{state}\n\n'
                + ('A remote draft may exist. Reconcile this attempt before retrying.\n\n' if uncertain else '')
                + 'Nothing was published.'
            ) from None

    def publish(self, story, draft):
        raise PublishToAllError('Publishing is not implemented. Nothing was published.')


class SubstackBodyPublisher(Publisher):
    """Add only the body to one verified, already linked draft editor."""

    name = 'substack'

    def __init__(self, repository, page, publication_url, monitor):
        super().__init__(repository)
        self.page = page
        self.publication_url = publication_url
        self.monitor = monitor

    def prepare_draft(self, story, attempt):
        raise PublishToAllError('This workflow only edits an already-linked draft. Nothing was published.')

    def publish(self, story, draft):
        raise PublishToAllError('Publishing is not implemented. Nothing was published.')

    def add_body(self, story, record):
        prepared = body_editor.prepare_story_body(story)
        self.monitor.require_clear(self.page)
        try:
            editor.require_publication(self.page, self.publication_url)
            field = editor.unique_visible(editor.title_fields(self.page))
            title_editable = field is not None and field.is_editable()
            visible_title = editor.title_value(field).strip() if title_editable else None
        except PlaywrightError:
            raise PublishToAllError(
                'Substack draft body was not changed.\n\nReason:\n'
                'The verified editor could no longer be inspected safely.\n\n'
                'Manual review is required before automatic insertion.\n\nNothing was published.'
            ) from None
        if not title_editable:
            raise PublishToAllError(
                'Substack draft body was not changed.\n\nReason:\n'
                'The verified draft title field is no longer safely editable.\n\n'
                'Manual review is required before automatic insertion.\n\nNothing was published.'
            )
        self.monitor.require_clear(self.page)
        if visible_title != story.metadata.title:
            raise PublishToAllError(
                'Substack draft body was not changed.\n\nReason:\n'
                'The visible draft title does not exactly match the current story title.\n\n'
                'Manual review is required before automatic insertion.\n\nNothing was published.'
            )
        surface = body_editor.locate_body_surface(self.page)
        self.monitor.require_clear(self.page)
        inspection = body_editor.inspect_body(surface, prepared)
        self.monitor.require_clear(self.page)
        if inspection.already_contains_story:
            raise PublishToAllError(
                'Substack draft body was not changed.\n\nReason:\n'
                'The existing draft already appears to contain this story body.\n\n'
                'Automatic duplicate insertion was refused.\n\nNothing was published.'
            )
        if inspection.classification != body_editor.BodyClassification.EMPTY:
            detail = (
                'The existing draft already contains substantial body content.'
                if inspection.classification == body_editor.BodyClassification.SUBSTANTIAL
                else 'The existing draft body is not safely empty.'
            )
            raise PublishToAllError(
                f'Substack draft body was not changed.\n\nReason:\n{detail}\n\n'
                'Manual review is required before automatic insertion.\n\nNothing was published.'
            )

        self.monitor.require_clear(self.page)
        try:
            previously_saved = editor.save_visible(self.page)
        except PlaywrightError:
            raise PublishToAllError(
                'Substack draft body was not changed.\n\nReason:\n'
                'The editor save state could not be inspected safely.\n\n'
                'Manual review is required before automatic insertion.\n\nNothing was published.'
            ) from None
        self.monitor.require_clear(self.page)
        active = self.repository.mark_body_inserting(record)
        try:
            self.monitor.require_clear(self.page)
            body_editor.insert_prepared_body(surface, prepared)
            self.monitor.require_clear(self.page)
            body_editor.confirm_body_save(
                self.page, surface, prepared, self.publication_url, self.monitor,
                previously_saved=previously_saved,
            )
            return self.repository.mark_body_inserted(active.id)
        except (Exception, KeyboardInterrupt) as exc:
            rate_limited = isinstance(exc, SubstackRateLimitError)
            message = (
                'Substack rate limit encountered after body insertion began; remote body is uncertain.'
                if rate_limited else
                'Substack body insertion or save confirmation failed; remote body is uncertain.'
            )
            try:
                self.repository.mark_body_insertion_failed(active.id, message)
            except StateError:
                pass
            if rate_limited:
                raise PublishToAllError(
                    'Substack rate limit encountered during body insertion or save confirmation.\n\n'
                    'The remote draft may contain some or all of the story body.\n\n'
                    'Local state has been protected against automatic retry.\n\nNothing was published.'
                ) from None
            raise PublishToAllError(
                'Substack draft body insertion did not complete safely.\n\n'
                'The remote draft may contain some or all of the story body.\n\n'
                'Local state has been protected against automatic retry. Inspect the linked draft '
                'before any recovery.\n\nNothing was published.'
            ) from None


class SubstackTitleRepairPublisher(Publisher):
    """Repair only an empty title on one verified, already linked draft."""

    name = 'substack'

    def __init__(self, repository, page, publication_url, monitor):
        super().__init__(repository)
        self.page = page
        self.publication_url = publication_url
        self.monitor = monitor

    def prepare_draft(self, story, attempt):
        raise PublishToAllError('This workflow only repairs an already-linked draft title.')

    def publish(self, story, draft):
        raise PublishToAllError('Publishing is not implemented. Nothing was published.')

    def _require_clear_before_edit(self):
        try:
            self.monitor.require_clear(self.page)
        except SubstackRateLimitError:
            raise PublishToAllError(
                'Substack rate limit encountered while repairing the draft title.\n\n'
                'The title may or may not have been saved.\n\n'
                'No retry was attempted.\nNothing was published.'
            ) from None

    def repair_title(self, story, record):
        self._require_clear_before_edit()
        try:
            editor.require_publication(self.page, self.publication_url)
            field, inspection = title_editor.inspect_empty_title_and_body(self.page)
        except PlaywrightError:
            field, inspection = None, title_editor.TitleInspection(
                None, body_editor.BodyClassification.UNKNOWN,
            )
        self._require_clear_before_edit()
        if inspection.title is None or field is None:
            raise PublishToAllError(
                'Substack draft title was not changed.\n\n'
                'The title or body state could not be inspected with certainty.\n\n'
                'Manual review required.\n\nNothing was published.'
            )
        if inspection.title.strip():
            raise PublishToAllError(
                'Substack draft title was not changed.\n\n'
                f'Existing title:\n{inspection.title}\n\n'
                'Manual review required.\n\nNothing was published.'
            )
        if inspection.body_classification != body_editor.BodyClassification.EMPTY:
            reason = (
                'The draft body contains substantial content.'
                if inspection.body_classification == body_editor.BodyClassification.SUBSTANTIAL
                else 'The draft body is not certainly empty.'
            )
            raise PublishToAllError(
                f'Substack draft title was not changed.\n\n{reason}\n\n'
                'Manual review required.\n\nNothing was published.'
            )

        self._require_clear_before_edit()
        try:
            previously_saved = editor.save_visible(self.page)
        except PlaywrightError:
            raise PublishToAllError(
                'Substack draft title was not changed.\n\n'
                'The editor save state could not be inspected safely.\n\n'
                'Manual review required.\n\nNothing was published.'
            ) from None
        self._require_clear_before_edit()
        active = self.repository.mark_title_repairing(record)
        try:
            self.monitor.require_clear(self.page)
            title_editor.insert_exact_title(field, story.metadata.title)
            self.monitor.require_clear(self.page)
            title_editor.confirm_title_save(
                self.page, field, story.metadata.title, self.publication_url, self.monitor,
                previously_saved=previously_saved,
            )
            return self.repository.mark_title_repaired(active)
        except (Exception, KeyboardInterrupt) as exc:
            rate_limited = isinstance(exc, SubstackRateLimitError)
            message = (
                'Substack rate limit encountered after title repair began; remote title is uncertain.'
                if rate_limited else
                'Substack title insertion or save confirmation failed; remote title is uncertain.'
            )
            try:
                self.repository.mark_title_repair_uncertain(active, message)
            except StateError:
                pass
            if rate_limited:
                raise PublishToAllError(
                    'Substack rate limit encountered while repairing the draft title.\n\n'
                    'The title may or may not have been saved.\n\n'
                    'No retry was attempted.\nNothing was published.'
                ) from None
            raise PublishToAllError(
                'Substack draft title repair did not complete safely.\n\n'
                'The title may or may not have been saved.\n\n'
                'Manual inspection is required before any recovery.\n\nNothing was published.'
            ) from None


class SubstackImagePublisher(Publisher):
    """Upload one cover to a verified, populated, already linked draft."""

    name = 'substack'

    def __init__(self, repository, page, publication_url, monitor):
        super().__init__(repository)
        self.page = page
        self.publication_url = publication_url
        self.monitor = monitor

    def prepare_draft(self, story, attempt):
        raise PublishToAllError('This workflow only edits an already-linked draft. Nothing was published.')

    def publish(self, story, draft):
        raise PublishToAllError('Publishing is not implemented. Nothing was published.')

    def _refuse_before_upload(self, reason):
        raise PublishToAllError(
            'Substack draft image was not changed.\n\nReason:\n' + reason + '\n\n'
            'Manual review is required before automatic upload.\n\nNothing was published.'
        )

    def upload_image(self, story, record):
        if story.image is None:
            raise PublishToAllError(
                'No matching cover image exists for the current story. Nothing was changed.'
            )
        try:
            source_digest = hashlib.sha256(story.image.read_bytes()).digest()
        except OSError:
            raise PublishToAllError(
                'The matching cover image is not readable. Nothing was changed.'
            ) from None

        self.monitor.require_clear(self.page)
        try:
            editor.require_publication(self.page, self.publication_url)
            if editor.extract_draft_url(self.page, self.publication_url) != record.draft_url.rstrip('/'):
                self._refuse_before_upload('The editor no longer matches the linked draft URL.')
            title_field = editor.unique_visible(editor.title_fields(self.page))
            if title_field is None or not title_field.is_editable():
                self._refuse_before_upload('The draft title could not be inspected safely.')
            visible_title = editor.title_value(title_field).strip()
            body_surface = body_editor.locate_body_surface(self.page)
            body_text = ' '.join(body_surface.inner_text().split())
        except PlaywrightError:
            self._refuse_before_upload('The verified editor could no longer be inspected safely.')
        self.monitor.require_clear(self.page)
        if visible_title != story.metadata.title:
            self._refuse_before_upload(
                'The visible draft title does not exactly match the current story title.'
            )
        if body_editor.classify_body_text(body_text) != body_editor.BodyClassification.SUBSTANTIAL:
            self._refuse_before_upload('The draft body is not substantially populated.')

        cover = image_editor.inspect_cover(self.page)
        self.monitor.require_clear(self.page)
        if cover.state == image_editor.CoverState.PRESENT:
            self._refuse_before_upload('The draft already appears to contain a cover image.')
        if cover.state != image_editor.CoverState.NONE or cover.control is None:
            self._refuse_before_upload('The draft cover-image state is uncertain.')
        try:
            previously_saved = editor.save_visible(self.page)
        except PlaywrightError:
            self._refuse_before_upload('The editor save state could not be inspected safely.')
        self.monitor.require_clear(self.page)

        active = self.repository.mark_image_uploading(record)
        try:
            self.monitor.require_clear(self.page)
            image_editor.upload_cover(self.page, cover.control, story.image)
            self.monitor.require_clear(self.page)
            image_editor.confirm_cover_and_save(
                self.page, title_field, story.metadata.title, body_surface, body_text,
                self.publication_url, record.draft_url.rstrip('/'), self.monitor,
                previously_saved=previously_saved,
            )
            if hashlib.sha256(story.image.read_bytes()).digest() != source_digest:
                raise PublishToAllError('The local source image changed during upload.')
            return self.repository.mark_image_uploaded(active)
        except (Exception, KeyboardInterrupt) as exc:
            rate_limited = isinstance(exc, SubstackRateLimitError)
            message = (
                'Substack rate limit encountered after image upload began; remote image is uncertain.'
                if rate_limited else
                'Substack image upload or save confirmation failed; remote image is uncertain.'
            )
            try:
                self.repository.mark_image_upload_failed(active, message)
            except StateError:
                pass
            if rate_limited:
                raise PublishToAllError(
                    'Substack rate limit encountered during image upload or save confirmation.\n\n'
                    'The remote draft may or may not contain the image.\n\n'
                    'Local state has been protected against automatic retry.\n\nNothing was published.'
                ) from None
            raise PublishToAllError(
                'Substack draft cover image upload did not complete safely.\n\n'
                'The remote draft may or may not contain the image.\n\n'
                'Local state has been protected against automatic retry. Inspect the linked draft '
                'before any recovery.\n\nNothing was published.'
            ) from None

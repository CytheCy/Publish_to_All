"""Title-only Substack adapter consuming an already authenticated browser page."""

from ..browser import editor
from ..browser.session import safe_screenshot
from ..browser.substack import collect_diagnostics
from ..errors import PublishToAllError
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

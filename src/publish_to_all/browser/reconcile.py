"""Inspect rendered draft pages without mutating remote content."""

from dataclasses import dataclass
from pathlib import Path
import re
from types import SimpleNamespace
from urllib.parse import urljoin, urlsplit

from playwright.sync_api import Error as PlaywrightError

from . import editor
from . import image as cover_image
from .session import safe_screenshot
from .substack import AUTH_PATHS, authentication_evidence, rate_limit_evidence, trusted_page
from ..errors import BrowserSessionError


@dataclass(frozen=True)
class DraftEvidence:
    matches: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class SuppliedDraftEvidence:
    verified: bool
    reason: str
    rate_limited: bool = False
    diagnostics: 'SuppliedDraftDiagnostics | None' = None
    inspection: 'DraftInspection | None' = None


@dataclass(frozen=True)
class DraftInspection:
    draft_url: str
    visible_title: str
    body_classification: str
    image_present: bool
    editor_state: tuple[str, ...]
    definitely_draft_editor: bool
    final_url: str
    page_title: str
    controls: tuple[str, ...]
    cover_state: str = 'unknown'
    cover_diagnostics: cover_image.CoverDiagnostics = cover_image.CoverDiagnostics()


@dataclass(frozen=True)
class SuppliedDraftDiagnostics:
    final_url: str
    title: str
    title_field_present: bool
    editor_surface_present: bool
    controls: tuple[str, ...]
    publish_or_send_present: bool
    screenshot: Path | None


@dataclass(frozen=True)
class _EditorSnapshot:
    evidence: SuppliedDraftEvidence
    title_field_present: bool = False
    editor_surface_present: bool = False
    controls: tuple[str, ...] = ()
    visible_title: str | None = None
    body_classification: str | None = None
    image_present: bool = False
    cover_state: str = 'unknown'
    cover_diagnostics: cover_image.CoverDiagnostics = cover_image.CoverDiagnostics()


def validate_supplied_draft_url(value: str, publication_url: str) -> str:
    """Accept only a numeric editor URL on the configured publication."""
    try:
        parsed = urlsplit(value)
        publication = urlsplit(publication_url)
        valid = (
            parsed.scheme == 'https'
            and parsed.hostname == publication.hostname
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and bool(re.fullmatch(r'/publish/post/[1-9][0-9]*/?', parsed.path))
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise BrowserSessionError(
            'Supplied URL is not a recognized draft editor URL for the configured Substack publication. '
            'Local state unchanged. Nothing was published.'
        )
    return f'https://{parsed.hostname}{parsed.path.rstrip("/")}'


def _visible_text(page, pattern) -> bool:
    return any(item.is_visible() for item in page.get_by_text(pattern).all())


CONTROL_SIGNALS = (
    ('Draft', re.compile(r'^Draft$', re.I)),
    ('Saved', re.compile(r'^(Saved|Draft saved|All changes saved|Saved to drafts)$', re.I)),
    ('Saving', re.compile(r'^Saving(?: changes)?(?:\.{3}|…)?$', re.I)),
    ('Preview', re.compile(r'^Preview$', re.I)),
    ('Continue', re.compile(r'^Continue$', re.I)),
    ('Publish', re.compile(r'^Publish(?: post)?$', re.I)),
    ('Send', re.compile(r'^Send(?: now)?$', re.I)),
    ('Schedule', re.compile(r'^Schedule$', re.I)),
    ('Settings', re.compile(r'^(Settings|Post settings)$', re.I)),
    ('Style', re.compile(r'^Style$', re.I)),
    ('Audience', re.compile(r'^Audience$', re.I)),
    ('More', re.compile(r'^More(?: options)?$', re.I)),
)


def _visible_control_labels(page) -> tuple[str, ...]:
    labels = []
    for label, pattern in CONTROL_SIGNALS:
        locator = (page.get_by_role('button', name=pattern)
                   .or_(page.get_by_role('link', name=pattern))
                   .or_(page.get_by_role('menuitem', name=pattern)))
        role_visible = any(item.is_visible() for item in locator.all())
        text_visible = label in {'Draft', 'Saved', 'Saving'} and _visible_text(page, pattern)
        if role_visible or text_visible:
            labels.append(label)
    return tuple(labels)


def _visible_editor_surface(page) -> bool:
    surfaces = page.locator(
        '.ProseMirror, [data-lexical-editor="true"], '
        '[contenteditable="true"][role="textbox"]'
    )
    return any(item.is_visible() and item.is_editable() for item in surfaces.all())


def classify_body(text: str) -> str:
    """Classify normalized editable body text using deliberately broad bands."""
    length = len(' '.join(text.split()))
    if length == 0:
        return 'empty'
    if length <= 100:
        return 'minimal'
    if length <= 1000:
        return 'partial'
    return 'substantial'


def _editor_contents(page, fields) -> tuple[str | None, str | None, bool]:
    titles = {
        editor.title_value(field).strip()
        for field in fields if field.is_editable()
    }
    visible_title = next(iter(titles)) if len(titles) == 1 else None
    surfaces = [
        item for item in page.locator(
            '.ProseMirror, [data-lexical-editor="true"], '
            '[contenteditable="true"][role="textbox"]'
        ).all()
        if item.is_visible() and item.is_editable()
    ]
    bodies = [' '.join(surface.inner_text().split()) for surface in surfaces]
    body = max(bodies, key=len) if bodies else None
    images = page.locator(
        '.ProseMirror img, [data-lexical-editor="true"] img, '
        '[contenteditable="true"][role="textbox"] img, '
        '[data-testid*="cover" i] img, [class*="cover" i] img'
    )
    image_present = any(item.is_visible() for item in images.all())
    return visible_title, classify_body(body) if body is not None else None, image_present


def _supplied_draft_snapshot(page, publication_url: str, draft_url: str) -> _EditorSnapshot:
    if rate_limit_evidence(page, publication_url):
        return _EditorSnapshot(SuppliedDraftEvidence(
            False, 'Substack returned a rate-limit page.', rate_limited=True,
        ))
    if editor.extract_draft_url(page, publication_url) != draft_url:
        return _EditorSnapshot(SuppliedDraftEvidence(
            False, 'The page did not remain at the supplied editor URL.',
        ))
    authentication = authentication_evidence(page, publication_url)
    if authentication.negative:
        return _EditorSnapshot(SuppliedDraftEvidence(
            False, 'The editor redirected to authentication controls.',
        ))
    try:
        fields = [field for field in editor.title_fields(page).all() if field.is_visible()]
        title_field = any(field.is_editable() for field in fields)
        editor_surface = _visible_editor_surface(page)
        controls = _visible_control_labels(page)
        published = _visible_text(page, re.compile(r'^(Published|Sent)$', re.I))
        visible_title, body_classification, _body_or_cover_image = _editor_contents(page, fields)
        cover = cover_image.inspect_cover(page)
        image_present = cover.state == cover_image.CoverState.PRESENT
    except (BrowserSessionError, PlaywrightError) as exc:
        # Playwright locator errors are verification uncertainty, never grounds
        # for changing local state. Do not include page data in the error.
        return _EditorSnapshot(SuppliedDraftEvidence(
            False, f'Editor evidence could not be read ({type(exc).__name__}).',
        ))
    if published:
        return _EditorSnapshot(
            SuppliedDraftEvidence(False, 'The editor shows published-post status.'),
            title_field, editor_surface, controls, visible_title, body_classification,
            image_present, cover.state.value, cover.diagnostics,
        )
    categories = {
        'status' if label in {'Draft', 'Saved', 'Saving'} else
        'workflow' if label in {'Continue', 'Publish', 'Send', 'Schedule'} else
        'preview' if label == 'Preview' else
        'configuration' if label in {'Settings', 'Style', 'Audience'} else
        'other'
        for label in controls
    } - {'other'}
    if not (title_field or editor_surface) or len(categories) < 2:
        return _EditorSnapshot(
            SuppliedDraftEvidence(False, 'The page lacks enough corroborating draft-editor evidence.'),
            title_field, editor_surface, controls, visible_title, body_classification,
            image_present, cover.state.value, cover.diagnostics,
        )
    return _EditorSnapshot(
        SuppliedDraftEvidence(True, 'Editable post content and multiple creator controls are visible.'),
        title_field, editor_surface, controls, visible_title, body_classification,
        image_present, cover.state.value, cover.diagnostics,
    )


def _safe_page_identity(page, publication_url: str) -> tuple[str, str]:
    final_url = '[unrecognized URL redacted]'
    title = '[nonstandard page title redacted]'
    try:
        if trusted_page(page.url, publication_url):
            parsed = urlsplit(page.url)
            path = parsed.path.rstrip('/')
            safe_path = (
                bool(re.fullmatch(r'/publish/post/[1-9][0-9]*', path))
                or path in {'', '/publish', '/publish/home', '/publish/posts'}
                or path.lstrip('/') in AUTH_PATHS
            )
            final_url = f'https://{parsed.hostname}' + (path if safe_path else '/[path redacted]')
        raw_title = page.title().strip()
        if isinstance(raw_title, str) and re.fullmatch(
            r'(?:Editing (?:newsletter|post|article)|Substack|Home|Dashboard|Sign in|Log in|Login|'
            r'Publish|Posts|Too many requests)(?:\s*[|–—-]\s*Substack)?', raw_title, re.I,
        ):
            title = raw_title
    except (PlaywrightError, AttributeError, TypeError, ValueError):
        pass
    return final_url, title


def supplied_draft_diagnostics(
    page, publication_url: str, directory: Path | None, snapshot: _EditorSnapshot | None = None,
) -> SuppliedDraftDiagnostics:
    final_url, title = _safe_page_identity(page, publication_url)
    if snapshot is None:
        try:
            fields = [field for field in editor.title_fields(page).all() if field.is_visible()]
            title_field = any(field.is_editable() for field in fields)
            editor_surface = _visible_editor_surface(page)
            controls = _visible_control_labels(page)
        except (BrowserSessionError, PlaywrightError):
            title_field, editor_surface, controls = False, False, ()
    else:
        title_field = snapshot.title_field_present
        editor_surface = snapshot.editor_surface_present
        controls = snapshot.controls
    screenshot = safe_screenshot(page, directory) if directory is not None else None
    return SuppliedDraftDiagnostics(
        final_url, title, title_field, editor_surface, controls,
        any(label in {'Publish', 'Send'} for label in controls), screenshot,
    )


def _wait_for_stable_editor(page, publication_url: str, rate_limit_monitor=None) -> bool:
    """Settle in short intervals so a trusted 429 stops inspection promptly."""
    if rate_limit_monitor is None:
        page.wait_for_timeout(1500)
        return not rate_limit_evidence(page, publication_url)
    for _ in range(15):
        if ((rate_limit_monitor is not None and rate_limit_monitor.encountered)
                or rate_limit_evidence(page, publication_url)):
            return False
        page.wait_for_timeout(100)
    return True


def verify_supplied_draft(
    page, publication_url: str, draft_url: str, diagnostics_directory: Path | None = None,
    *, rate_limit_monitor=None,
) -> SuppliedDraftEvidence:
    """Open and inspect an existing editor without clicking or editing anything."""
    response = page.goto(draft_url, wait_until='domcontentloaded')
    response_url = getattr(response, 'url', page.url) if response is not None else page.url
    if (response is not None and getattr(response, 'status', None) == 429
            and trusted_page(response_url, publication_url)):
        return SuppliedDraftEvidence(False, 'Substack returned HTTP 429.', rate_limited=True)
    if response is not None and getattr(response, 'ok', True) is False:
        evidence = SuppliedDraftEvidence(False, 'The supplied editor URL was inaccessible.')
        return SuppliedDraftEvidence(
            False, evidence.reason, diagnostics=supplied_draft_diagnostics(
                page, publication_url, diagnostics_directory,
            ),
        )
    if rate_limit_evidence(page, publication_url):
        return SuppliedDraftEvidence(
            False, 'Substack returned a rate-limit page.', rate_limited=True,
        )
    if not _wait_for_stable_editor(page, publication_url, rate_limit_monitor):
        return SuppliedDraftEvidence(
            False, 'Substack returned a rate-limit response or page.', rate_limited=True,
        )
    first = _supplied_draft_snapshot(page, publication_url, draft_url)
    if first.evidence.rate_limited:
        return SuppliedDraftEvidence(False, first.evidence.reason, True)
    if not _wait_for_stable_editor(page, publication_url, rate_limit_monitor):
        return SuppliedDraftEvidence(
            False, 'Substack returned a rate-limit response or page.', rate_limited=True,
        )
    second = _supplied_draft_snapshot(page, publication_url, draft_url)
    if second.evidence.rate_limited:
        return SuppliedDraftEvidence(False, second.evidence.reason, True)
    if first != second:
        evidence = SuppliedDraftEvidence(False, 'Editor evidence changed while being inspected.')
    else:
        evidence = second.evidence
    if evidence.verified:
        final_url, page_title = _safe_page_identity(page, publication_url)
        inspection = None
        if second.visible_title is not None and second.body_classification is not None:
            inspection = DraftInspection(
                draft_url, second.visible_title, second.body_classification,
                second.image_present,
                tuple(label for label in second.controls if label in {'Draft', 'Saved', 'Saving'}),
                True, final_url, page_title, second.controls, second.cover_state,
                second.cover_diagnostics,
            )
        return SuppliedDraftEvidence(True, evidence.reason, inspection=inspection)
    return SuppliedDraftEvidence(
        False, evidence.reason, diagnostics=supplied_draft_diagnostics(
            page, publication_url, diagnostics_directory, second,
        ),
    )


def scan_draft_listing(page, publication_url, title):
    """Require exact title links and explicit Draft status in a semantic row.

    A listing may be paginated or incomplete. Zero matches never establishes
    remote absence. No editor is opened, including a known numeric editor URL.
    """
    editor.require_publication(page, publication_url)
    matches = set()
    rows = page.get_by_role('row').or_(page.get_by_role('listitem')).or_(page.get_by_role('article'))
    for row in rows.all():
        if not row.is_visible():
            continue
        status = row.get_by_text(re.compile(r'^Draft$', re.I))
        if not any(item.is_visible() for item in status.all()):
            continue
        for link in row.get_by_role('link', name=title, exact=True).all():
            if not link.is_visible():
                continue
            href = link.get_attribute('href')
            if not href:
                continue
            url = editor.extract_draft_url(SimpleNamespace(url=urljoin(page.url, href)), publication_url)
            if url:
                matches.add(url.rstrip('/'))
    urls = tuple(sorted(matches))
    reason = ('No convincing matching draft visible; an untitled draft or another listing page may exist.'
              if not urls else 'Multiple matching drafts; cannot identify the failed attempt.'
              if len(urls) > 1 else 'One exact-title link with explicit Draft status is visible.')
    return DraftEvidence(urls, reason)


def inspect_drafts(page, publication_url, title):
    editor.navigate_dashboard(page, publication_url)
    posts = editor.wait_visible(page, editor.controls(page, re.compile(r'^Posts$', re.I)))
    href = posts.get_attribute('href')
    if not href or urljoin(page.url, href).rstrip('/') != publication_url + '/publish/posts':
        return DraftEvidence((), 'Cannot identify a safe rendered Posts listing link.')
    page.goto(urljoin(page.url, href), wait_until='domcontentloaded')
    # Read after rendering settles, and reject changing evidence.
    page.wait_for_timeout(1500)
    first = scan_draft_listing(page, publication_url, title)
    page.wait_for_timeout(1500)
    second = scan_draft_listing(page, publication_url, title)
    if first != second:
        return DraftEvidence((), 'Draft listing changed while being inspected.')
    return second

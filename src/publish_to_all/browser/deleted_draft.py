"""Conservative, single-navigation verification of a remotely deleted draft."""

from dataclasses import dataclass
from enum import StrEnum
import re
from urllib.parse import urljoin, urlsplit

from .reconcile import _supplied_draft_snapshot
from .substack import AuthenticationState, authentication_evidence, rate_limit_evidence, trusted_page


class DeletedDraftState(StrEnum):
    DELETED = 'deleted'
    EXISTS = 'exists'
    PUBLISHED = 'published'
    UNKNOWN = 'unknown'
    AUTHENTICATION_FAILED = 'authentication_failed'
    RATE_LIMITED = 'rate_limited'


@dataclass(frozen=True)
class DeletedDraftEvidence:
    state: DeletedDraftState
    reason: str
    final_url: str
    http_status: int | None = None


_DELETED_TEXT = re.compile(
    r"^(?:post|draft|page) not found$|^this (?:post|draft) (?:has been deleted|does(?:n't| not) exist)$",
    re.IGNORECASE,
)


def _exact_deleted_text_visible(page) -> bool:
    return any(item.is_visible() for item in page.get_by_text(_DELETED_TEXT).all())


def _deleted_editor_shell_visible(page, publication_url: str, draft_url: str) -> bool:
    """Recognize Substack's exact creator-shell response for a missing draft."""
    try:
        if (not trusted_page(page.url, publication_url)
                or page.url.rstrip('/') != draft_url.rstrip('/')
                or page.title().strip() != 'Editing newsletter - Substack'):
            return False

        def exact_text(value: str) -> bool:
            return any(item.is_visible() for item in page.get_by_text(value, exact=True).all())

        def button(name: str, *, enabled: bool) -> bool:
            return any(
                item.is_visible() and item.is_enabled() is enabled
                for item in page.get_by_role('button', name=name, exact=True).all()
            )

        return (
            exact_text('Post not found.')
            and exact_text('Please try selecting another draft.')
            and button('View drafts', enabled=True)
            and button('Preview', enabled=False)
            and button('Continue', enabled=False)
        )
    except (AttributeError, TypeError):
        return False


def _listing_omits_draft(page, draft_url: str) -> bool:
    target = draft_url.rstrip('/')
    for link in page.get_by_role('link').all():
        if not link.is_visible():
            continue
        href = link.get_attribute('href')
        if href and urljoin(page.url, href).split('?', 1)[0].rstrip('/') == target:
            return False
    return True


def inspect_deleted_draft(page, publication_url: str, draft_url: str, monitor) -> DeletedDraftEvidence:
    """Open the stored URL once and classify only unambiguous rendered evidence."""
    response = page.goto(draft_url, wait_until='domcontentloaded')
    response_url = getattr(response, 'url', page.url) if response is not None else page.url
    status = getattr(response, 'status', None) if response is not None else None
    if status == 429 and trusted_page(response_url, publication_url):
        return DeletedDraftEvidence(
            DeletedDraftState.RATE_LIMITED, 'Substack returned HTTP 429.', page.url, status,
        )

    # Allow client rendering to settle without navigation or reload, checking for
    # a rate-limit response/page between every short interval.
    for _ in range(50):
        if monitor.encountered or rate_limit_evidence(page, publication_url):
            return DeletedDraftEvidence(
                DeletedDraftState.RATE_LIMITED,
                'Substack returned a rate-limit response or page.', page.url, status,
            )
        page.wait_for_timeout(100)
    if monitor.encountered or rate_limit_evidence(page, publication_url):
        return DeletedDraftEvidence(
            DeletedDraftState.RATE_LIMITED,
            'Substack returned a rate-limit response or page.', page.url, status,
        )

    # A stable editable surface is decisive evidence that the remote draft still exists.
    snapshot = _supplied_draft_snapshot(page, publication_url, draft_url)
    if (monitor.encountered or snapshot.evidence.rate_limited
            or rate_limit_evidence(page, publication_url)):
        return DeletedDraftEvidence(
            DeletedDraftState.RATE_LIMITED,
            'Substack returned a rate-limit response or page.', page.url, status,
        )
    if snapshot.evidence.verified:
        return DeletedDraftEvidence(
            DeletedDraftState.EXISTS,
            'The stored URL still opens an editable Substack draft.', page.url, status,
        )

    if _deleted_editor_shell_visible(page, publication_url, draft_url):
        return DeletedDraftEvidence(
            DeletedDraftState.DELETED,
            'The trusted Substack creator shell reports “Post not found” and offers another draft.',
            page.url, status,
        )

    final = urlsplit(page.url)
    if trusted_page(page.url, publication_url) and final.path.startswith('/p/'):
        return DeletedDraftEvidence(
            DeletedDraftState.PUBLISHED,
            'The stored draft URL redirected to a public Substack post.', page.url, status,
        )

    authentication = authentication_evidence(page, publication_url).state
    if authentication == AuthenticationState.RATE_LIMITED:
        return DeletedDraftEvidence(
            DeletedDraftState.RATE_LIMITED, 'Substack returned a rate-limit page.', page.url, status,
        )
    if authentication == AuthenticationState.NOT_AUTHENTICATED:
        return DeletedDraftEvidence(
            DeletedDraftState.AUTHENTICATION_FAILED,
            'The stored draft URL redirected to authentication.', page.url, status,
        )
    if authentication != AuthenticationState.AUTHENTICATED:
        return DeletedDraftEvidence(
            DeletedDraftState.UNKNOWN,
            'Authentication could not be positively verified on the response page.', page.url, status,
        )

    if status in {404, 410} and trusted_page(response_url, publication_url):
        return DeletedDraftEvidence(
            DeletedDraftState.DELETED,
            f'The authenticated request returned HTTP {status} for the stored draft.',
            page.url, status,
        )

    parsed = urlsplit(page.url)
    publication_host = urlsplit(publication_url).hostname
    listing = (
        parsed.scheme == 'https' and parsed.hostname == publication_host
        and parsed.path.rstrip('/') == '/publish/posts'
    )
    if listing and _listing_omits_draft(page, draft_url):
        return DeletedDraftEvidence(
            DeletedDraftState.DELETED,
            'The stored draft redirected to the authenticated Posts listing, which omits its numeric URL.',
            page.url, status,
        )
    if trusted_page(page.url, publication_url) and _exact_deleted_text_visible(page):
        return DeletedDraftEvidence(
            DeletedDraftState.DELETED,
            'The authenticated page explicitly reports that the draft or post was not found or deleted.',
            page.url, status,
        )

    # Remaining HTTP failures and generic error pages are deliberately ambiguous.
    return DeletedDraftEvidence(
        DeletedDraftState.UNKNOWN,
        'The stored URL did not provide unambiguous authenticated deletion evidence.',
        page.url, status,
    )

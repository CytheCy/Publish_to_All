"""Read rendered draft listings; never open editors or mutate remote content."""

from dataclasses import dataclass
import re
from types import SimpleNamespace
from urllib.parse import urljoin

from . import editor


@dataclass(frozen=True)
class DraftEvidence:
    matches: tuple[str, ...]
    reason: str


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

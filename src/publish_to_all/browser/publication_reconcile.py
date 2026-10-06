"""Anonymous GET evidence for an already attempted publication; no action controls."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from hashlib import sha256
from html.parser import HTMLParser
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from ..errors import BrowserSessionError, SubstackRateLimitError
from .body import normalize_visible_text


PUBLISHED = 'VERIFIED PUBLISHED'
UNKNOWN = 'PUBLICATION STATE STILL UNKNOWN'


def public_url(value, origin):
    """Accept only canonical public posts on the configured HTTPS origin."""
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        if (parsed.scheme == 'https' and parsed.netloc == urlsplit(origin).netloc
                and not parsed.query and not parsed.fragment
                and re.fullmatch(r'/p/[A-Za-z0-9_-]+', parsed.path)):
            return value
    except ValueError:
        pass
    return None


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


@dataclass(frozen=True)
class Response:
    url: str
    status: int
    html: str = field(repr=False)
    content_type: str = 'text/html'
    read_id: str = field(default_factory=lambda: uuid4().hex)
    read_at: str = ''


class PublicReader:
    """One fresh anonymous GET per call, no cookies, redirects, scripts or retries."""

    def __init__(self, origin):
        self.origin = origin
        self.rate_limited = False

    def get(self, url):
        if self.rate_limited:
            raise SubstackRateLimitError('Rate limiting; no further requests allowed.')
        if url != self.origin + '/archive' and not public_url(url, self.origin):
            raise BrowserSessionError('Untrusted reconciliation read URL.')
        request = Request(url, method='GET', headers={
            'Accept': 'text/html', 'Cache-Control': 'no-cache', 'Pragma': 'no-cache',
            'User-Agent': 'Mozilla/5.0 (publish-to-all read-only reconciliation)',
        })
        try:
            try:
                response = build_opener(_NoRedirect).open(request, timeout=30)
            except HTTPError as exc:
                response = exc
            with response:
                result = Response(
                    response.geturl(), response.code,
                    response.read().decode('utf-8', errors='replace'),
                    response.headers.get('Content-Type', ''),
                    read_at=datetime.now().astimezone().isoformat(),
                )
        except (URLError, OSError):
            raise BrowserSessionError('Public GET unavailable; no retry performed.') from None
        if result.status == 429 or re.search(r'\btoo many requests\b', result.html, re.I):
            self.rate_limited = True
            raise SubstackRateLimitError('Rate limiting; stop all remote reconciliation.')
        return result


def preloads(html):
    """Decode server data as JSON, never execute a remote script."""
    matches = list(re.finditer(r'window\._preloads\s*=\s*JSON\.parse\(', html))
    if len(matches) != 1:
        raise BrowserSessionError('One unambiguous server metadata object is required.')
    try:
        encoded, _ = json.JSONDecoder().raw_decode(html[matches[0].end():])
        data = json.loads(encoded)
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (TypeError, ValueError):
        raise BrowserSessionError('Server metadata was not valid JSON.') from None


class PublicHTML(HTMLParser):
    """Read visible server-rendered article regions separately from embedded JSON."""

    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
            'meta', 'param', 'source', 'track', 'wbr'}
    BLOCK = {'p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'br', 'li', 'blockquote', 'pre'}

    def __init__(self, html):
        super().__init__(convert_charrefs=True)
        self.stack = []
        self.text = {'title': [], 'subtitle': [], 'body': []}
        self.counts = dict.fromkeys(self.text, 0)
        self.canonical = []
        self.meta = {}
        self.editor = False
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        parent_slot, parent_hidden = self.stack[-1][1:] if self.stack else (None, False)
        style = re.sub(r'\s+', '', attrs.get('style') or '').lower()
        hidden = (parent_hidden or tag in {'script', 'style', 'template', 'noscript'}
                  or 'hidden' in attrs or attrs.get('aria-hidden') == 'true'
                  or 'display:none' in style or 'visibility:hidden' in style)
        slot = parent_slot
        classes = (attrs.get('class') or '').split()
        if not hidden:
            selected = ('body' if {'body', 'markup'} <= set(classes)
                        else 'title' if 'post-title' in classes
                        else 'subtitle' if 'subtitle' in classes else None)
            if selected:
                slot = selected
                self.counts[slot] += 1
            if slot and tag in self.BLOCK:
                self.text[slot].append(' ')
            if attrs.get('contenteditable') in {'', 'true', 'plaintext-only'}:
                self.editor = True
        if tag == 'link' and attrs.get('rel') == 'canonical':
            self.canonical.append(attrs.get('href'))
        if tag == 'meta' and attrs.get('property'):
            self.meta.setdefault(attrs['property'], []).append(attrs.get('content'))
        if tag not in self.VOID:
            self.stack.append((tag, slot, hidden))

    def handle_endtag(self, tag):
        if tag in self.VOID:
            return
        if self.stack:
            _, slot, hidden = self.stack[-1]
            if slot and not hidden and tag in self.BLOCK:
                self.text[slot].append(' ')
            if self.stack[-1][0] == tag:
                self.stack.pop()

    def handle_data(self, text):
        if self.stack:
            _, slot, hidden = self.stack[-1]
            if slot and not hidden:
                self.text[slot].append(text)

    def value(self, slot):
        return normalize_visible_text(''.join(self.text[slot]))


@dataclass(frozen=True)
class Expected:
    origin: str
    draft_id: int
    title: str
    subtitle: str | None
    body: str = field(repr=False)
    clicked_at: str
    retired_ids: tuple[int, ...] = ()
    retired_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class PublicRead:
    url: str
    read_id: str
    read_at: str
    status: int
    accepted: bool
    reason: str
    post_id: int | None = None
    title: str | None = None
    subtitle: str | None = None
    published_at: str | None = None
    body_sha256: str | None = None
    body_characters: int = 0
    cover_image: str | None = None
    og_image: str | None = None


def discover(response, expected):
    if response.status != 200 or response.url != expected.origin + '/archive':
        raise BrowserSessionError('Public archive unavailable; no absence inference allowed.')
    data = preloads(response.html)
    posts = data.get('newPostsForArchive')
    if isinstance(posts, dict):
        posts = posts.get('pub')
    if not isinstance(posts, list):
        raise BrowserSessionError('Public archive post identities unavailable.')
    candidates, excluded = {}, []
    for post in posts:
        if not isinstance(post, dict):
            raise BrowserSessionError('Public archive metadata is ambiguous.')
        url = post.get('canonical_url')
        identity = {key: post.get(key) for key in ('id', 'title', 'subtitle', 'post_date', 'canonical_url')}
        if post.get('id') in expected.retired_ids or url in expected.retired_urls:
            excluded.append(identity)
        elif post.get('title') == expected.title or post.get('id') == expected.draft_id:
            if not public_url(url, expected.origin):
                raise BrowserSessionError('A matching archive candidate lacks a trusted public URL.')
            if url in candidates and candidates[url] != identity:
                raise BrowserSessionError('Conflicting archive candidate identity.')
            candidates[url] = identity
    return candidates, excluded


def verify(response, expected):
    result = dict(url=response.url, read_id=response.read_id, read_at=response.read_at,
                  status=response.status, accepted=False, reason='')
    failures = []
    if (response.status != 200 or not public_url(response.url, expected.origin)
            or response.content_type.split(';')[0].strip().lower() != 'text/html'):
        return PublicRead(**{**result, 'reason': 'Trusted public HTML with HTTP 200 is required.'})
    try:
        post = preloads(response.html).get('post')
        if not isinstance(post, dict):
            raise BrowserSessionError('Public post metadata is absent.')
        parsed = PublicHTML(response.html)
        body = parsed.value('body')
        result.update(post_id=post.get('id'), title=post.get('title'), subtitle=post.get('subtitle'),
                      published_at=post.get('post_date'), body_sha256=sha256(body.encode()).hexdigest(),
                      body_characters=len(body), cover_image=post.get('cover_image'),
                      og_image=next(iter(parsed.meta.get('og:image', [])), None))
        if (post.get('id') in expected.retired_ids or response.url in expected.retired_urls):
            failures.append('Retired cycle candidate.')
        if type(post.get('id')) is not int or post['id'] != expected.draft_id:
            failures.append('Public post ID does not match the exact attempted draft.')
        if (post.get('canonical_url') != response.url or parsed.canonical != [response.url]
                or parsed.meta.get('og:url') != [response.url]):
            failures.append('Public URL metadata conflicts.')
        if (post.get('title') != expected.title or parsed.counts['title'] != 1
                or parsed.value('title') != expected.title
                or parsed.meta.get('og:title') != [expected.title]):
            failures.append('Exact title mismatch.')
        if expected.subtitle is not None and (
                post.get('subtitle') != expected.subtitle or parsed.counts['subtitle'] != 1
                or parsed.value('subtitle') != normalize_visible_text(expected.subtitle)):
            failures.append('Subtitle mismatch.')
        if (parsed.counts['body'] != 1 or len(body) < 500
                or body != normalize_visible_text(expected.body)):
            failures.append('Substantial complete normalized story body does not match.')
        if (post.get('is_published') is not True or post.get('is_editor_preview') is not False
                or parsed.editor or post.get('audience') != 'everyone'):
            failures.append('Anonymous public publication is not established.')
        try:
            published = datetime.fromisoformat(post['post_date'].replace('Z', '+00:00'))
            clicked = datetime.fromisoformat(expected.clicked_at)
            if (not published.tzinfo or not clicked.tzinfo
                    or not timedelta(0) <= published - clicked <= timedelta(minutes=5)):
                failures.append('Publication timing is inconsistent with the recorded final click.')
        except (KeyError, TypeError, ValueError, AttributeError):
            failures.append('Publication timing unavailable.')
    except (BrowserSessionError, ValueError, TypeError) as exc:
        failures.append(str(exc))
    result.update(accepted=not failures, reason=' '.join(failures) or
                  'Exact draft ID, timing, title, subtitle and complete public body verified.')
    return PublicRead(**result)


def classify(reads, *, incomplete=False, rate_limited=False):
    """Only two distinct fresh reads of one unique candidate certify publication."""
    if incomplete or rate_limited:
        return UNKNOWN, None
    accepted = {}
    for read in reads:
        if read.accepted:
            accepted.setdefault(read.url, []).append(read)
    if len(accepted) != 1:
        return UNKNOWN, None
    url, pair = next(iter(accepted.items()))
    if len(pair) != 2 or pair[0].read_id == pair[1].read_id:
        return UNKNOWN, None
    fields = ('url', 'post_id', 'title', 'subtitle', 'published_at', 'body_sha256', 'body_characters')
    if any(getattr(pair[0], f) != getattr(pair[1], f) for f in fields):
        return UNKNOWN, None
    if any(r.url == url and not r.accepted for r in reads):
        return UNKNOWN, None
    return PUBLISHED, url

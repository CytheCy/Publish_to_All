"""Read-only verification of the one known public test publication."""

from dataclasses import dataclass
from html.parser import HTMLParser
import re
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

from ..errors import BrowserSessionError


TEST_PUBLIC_URL = (
    "https://cyporter.substack.com/p/saturation-of-artificial-intelligence"
)
ACCEPTED_TEST_TITLES = (
    "Saturation of Artificial Intelligence",
    "TEST—Saturation of Artificial Intelligence",
)


@dataclass(frozen=True)
class PublicPostVerification:
    url: str
    status: int
    title: str
    public_post: bool
    editor_ui: bool
    publish_modal: bool


class _PublicPostParser(HTMLParser):
    """Collect only visible structural evidence from server-rendered HTML."""

    _VOID = {
        "area", "base", "br", "col", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden: list[bool] = []
        self._ignored_depth = 0
        self._heading_depth = 0
        self._heading_text: list[str] = []
        self.headings: list[str] = []
        self.public_post = False
        self.editor_ui = False
        self.publish_modal = False
        self.canonical_url: str | None = None
        self.og_type: str | None = None

    @staticmethod
    def _attrs(attributes) -> dict[str, str]:
        return {key.lower(): (value or "") for key, value in attributes}

    @staticmethod
    def _has_token(attrs: dict[str, str], token: str) -> bool:
        values = " ".join(
            attrs.get(key, "") for key in ("id", "class", "role", "aria-label", "data-testid")
        )
        return token in values.lower()

    def _is_hidden(self, attrs: dict[str, str]) -> bool:
        style = re.sub(r"\s+", "", attrs.get("style", "").lower())
        return bool(
            self._hidden and self._hidden[-1]
            or "hidden" in attrs
            or attrs.get("aria-hidden", "").lower() == "true"
            or "display:none" in style
            or "visibility:hidden" in style
        )

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        values = self._attrs(attrs)
        if tag == "script" or tag == "style" or tag == "template" or tag == "noscript":
            self._ignored_depth += 1
        visible = not self._is_hidden(values) and self._ignored_depth == 0

        if visible:
            aria_label = values.get("aria-label", "").lower()
            role = values.get("role", "").lower()
            if aria_label == "post" or (role == "article" and "post" in values.get("class", "").lower()):
                self.public_post = True
            contenteditable = values.get("contenteditable", "").lower()
            if contenteditable in {"", "true", "plaintext-only"} and "contenteditable" in values:
                self.editor_ui = True
            editor_values = " ".join(
                values.get(key, "")
                for key in ("id", "class", "role", "aria-label", "data-testid")
            ).lower()
            if re.search(r"(?:post[-_ ]?)?(?:editor|composer)", editor_values):
                self.editor_ui = True
            if (
                ("publish" in editor_values and ("modal" in editor_values or role == "dialog"))
                or (role == "dialog" and re.search(r"\b(?:publish|send|schedule)\b", aria_label))
            ):
                self.publish_modal = True

            if tag in {"h1", "h2", "h3"}:
                self._heading_depth += 1
                self._heading_text.append("")

        if tag not in self._VOID:
            self._hidden.append(not visible)

        if tag == "link" and values.get("rel", "").lower() == "canonical":
            self.canonical_url = values.get("href")
        if tag == "meta" and values.get("property", "").lower() == "og:type":
            self.og_type = values.get("content")

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in self._VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in {"h1", "h2", "h3"} and self._heading_depth:
            self.headings.append(" ".join(self._heading_text.pop().split()))
            self._heading_depth -= 1
        if tag not in self._VOID and self._hidden:
            self._hidden.pop()
        if tag == "script" or tag == "style" or tag == "template" or tag == "noscript":
            self._ignored_depth = max(0, self._ignored_depth - 1)

    def handle_data(self, data: str) -> None:
        if self._heading_depth and self._ignored_depth == 0 and self._hidden and not self._hidden[-1]:
            self._heading_text[-1] += data


def validate_test_public_url(value: str) -> str:
    """Require the exact known URL before making a read-only request."""
    try:
        parsed = urlsplit(value)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname == "cyporter.substack.com"
            and parsed.port is None
            and parsed.username is None
            and parsed.password is None
            and parsed.path == "/p/saturation-of-artificial-intelligence"
            and not parsed.query
            and not parsed.fragment
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise BrowserSessionError(
            "The public URL must exactly be " + TEST_PUBLIC_URL + ". Local state unchanged."
        )
    return TEST_PUBLIC_URL


def verify_public_post_html(
    url: str, *, status: int, final_url: str, content_type: str, html: str,
) -> PublicPostVerification:
    """Validate a captured public response without opening an authenticated editor."""
    validate_test_public_url(url)
    if status != 200:
        raise BrowserSessionError(f"Public verification required HTTP 200, got {status}. Local state unchanged.")
    if final_url != TEST_PUBLIC_URL:
        raise BrowserSessionError("The public request did not remain at the exact URL. Local state unchanged.")
    if not content_type.lower().split(";", 1)[0].strip() == "text/html":
        raise BrowserSessionError("The public URL did not return an HTML post page. Local state unchanged.")

    parser = _PublicPostParser()
    try:
        parser.feed(html)
        parser.close()
    except (TypeError, ValueError) as exc:
        raise BrowserSessionError("The public post HTML could not be verified. Local state unchanged.") from exc

    title = next((heading for heading in parser.headings if heading in ACCEPTED_TEST_TITLES), "")
    if not parser.public_post or parser.og_type and parser.og_type.lower() != "article":
        raise BrowserSessionError("The exact URL is not a public post page. Local state unchanged.")
    if parser.editor_ui:
        raise BrowserSessionError("The exact URL exposed editor UI. Local state unchanged.")
    if parser.publish_modal:
        raise BrowserSessionError("The exact URL exposed a publish modal. Local state unchanged.")
    if title not in ACCEPTED_TEST_TITLES:
        raise BrowserSessionError(
            "The public post heading is not one of the two accepted test titles. Local state unchanged."
        )
    return PublicPostVerification(TEST_PUBLIC_URL, status, title, True, False, False)


def verify_test_public_post(url: str = TEST_PUBLIC_URL) -> PublicPostVerification:
    """Fetch the exact public page with one read-only GET and verify its identity."""
    exact_url = validate_test_public_url(url)
    request = Request(
        exact_url,
        method="GET",
        headers={"Accept": "text/html", "User-Agent": "publish-to-all read-only verifier"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read()
            status = getattr(response, "status", None) or response.getcode()
            final_url = response.geturl()
            content_type = response.headers.get("Content-Type", "")
    except HTTPError as exc:
        raise BrowserSessionError(
            f"Public verification failed with HTTP {exc.code}. Local state unchanged."
        ) from exc
    except (URLError, OSError) as exc:
        raise BrowserSessionError(
            "The exact public URL could not be read. Local state unchanged."
        ) from exc
    try:
        html = body.decode("utf-8", errors="replace")
    except AttributeError as exc:
        raise BrowserSessionError("The public post response was not readable HTML. Local state unchanged.") from exc
    return verify_public_post_html(
        exact_url, status=status, final_url=final_url,
        content_type=content_type, html=html,
    )

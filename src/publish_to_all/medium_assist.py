"""Local, user-driven preparation for pasting a story into Medium."""

from dataclasses import dataclass
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import urlsplit
import webbrowser

from markdown_it import MarkdownIt

from .config import runtime_paths
from .errors import StoryError
from .state import PublicationRepository, StateError
from .story import Story, content_hash, load_story


MEDIUM_WRITE_URL = "https://medium.com/new-story"
SOURCE_CHANGED = "SOURCE_CHANGED_SINCE_PREPARATION"


@dataclass(frozen=True)
class PreparedMediumStory:
    source: Path
    source_hash: str
    title: str
    subtitle: str | None
    body_plain: str
    body_html: str
    word_count: int
    character_count: int
    heading_count: int
    paragraph_count: int
    preview_html: str
    topics: tuple[str, ...]


def load_medium_story(root: Path, source_argument: str | None = None) -> Story:
    input_dir = root / "In"
    if source_argument is None:
        return load_story(input_dir)
    source = (root / source_argument).resolve()
    if source.parent != input_dir.resolve():
        raise StoryError("The explicit Medium story must be directly in In/.")
    return load_story(input_dir, source)


def _inline_plain(token) -> str:
    return "".join(
        child.content if child.type in {"text", "code_inline", "html_inline"}
        else "\n" if child.type in {"softbreak", "hardbreak"}
        else child.content if child.type == "image"
        else ""
        for child in token.children or ()
    ).strip()


def story_blocks(story: Story) -> tuple[tuple[str, str], ...]:
    """Use the generic parser's block tokens; never copy Markdown markers."""
    blocks = []
    tokens = story.tokens
    for index, token in enumerate(tokens):
        if token.type in {"heading_open", "paragraph_open"}:
            inline = tokens[index + 1]
            if inline.type == "inline":
                blocks.append(("heading" if token.type == "heading_open" else "paragraph",
                               _inline_plain(inline)))
        elif token.type in {"fence", "code_block"}:
            blocks.append(("code", token.content.strip()))
        elif token.type == "hr":
            blocks.append(("rule", ""))
    return tuple(blocks)


def prepare_medium_story(story: Story) -> PreparedMediumStory:
    topics_path = story.source.with_suffix(".medium-topics.txt")
    try:
        topics = tuple(line.strip() for line in topics_path.read_text(encoding="utf-8").splitlines())
    except FileNotFoundError:
        topics = ()
    except (OSError, UnicodeError) as exc:
        raise StoryError(f"Cannot read Medium topics from {topics_path}.") from exc
    if len(topics) > 5 or any(not topic for topic in topics) or len(set(topics)) != len(topics):
        raise StoryError("Medium topics must be one unique, non-empty topic per line (up to five).")
    blocks = story_blocks(story)
    plain = "\n\n".join(text if kind != "rule" else "———"
                         for kind, text in blocks if text or kind == "rule")
    preview = []
    for kind, text in blocks[:8]:
        if kind == "rule":
            preview.append("<hr>")
        else:
            tag = "h2" if kind == "heading" else "p"
            shortened = text[:240] + ("…" if len(text) > 240 else "")
            preview.append(f"<{tag}>{escape(shortened)}</{tag}>")
    return PreparedMediumStory(
        story.source, story.source_hash, story.metadata.title,
        story.metadata.description or story.metadata.subtitle,
        plain, MarkdownIt("commonmark", {"html": False, "breaks": True}).render(story.markdown).strip(),
        story.word_count, len(plain),
        sum(kind == "heading" for kind, _ in blocks),
        sum(kind == "paragraph" for kind, _ in blocks),
        "\n".join(preview),
        topics,
    )


def source_unchanged(prepared: PreparedMediumStory) -> bool:
    try:
        return content_hash(prepared.source.read_bytes()) == prepared.source_hash
    except OSError:
        return False


def _page(prepared: PreparedMediumStory, token: str) -> str:
    title = escape(prepared.title)
    subtitle = escape(prepared.subtitle) if prepared.subtitle else None
    subtitle_display = f'<p class="subtitle">{subtitle}</p>' if subtitle else ""
    subtitle_button = '<button type="button" data-copy="subtitle">Copy Subtitle</button>' if subtitle else ""
    topic_buttons = "\n".join(
        f'<button type="button" data-copy="topic/{index}">Copy {escape(topic)}</button>'
        for index, topic in enumerate(prepared.topics)
    )
    topics_section = (f'<section aria-label="Medium topics"><h2>Topics</h2>'
                      f'<p>Copy and paste each topic separately in Medium.</p>'
                      f'<div class="controls">{topic_buttons}</div></section>') if topic_buttons else ""
    return f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="assist-token" content="{token}">
<title>Medium Assist — {title}</title>
<style>
body {{ background:#f7f6f2; color:#252525; font:17px/1.55 system-ui,sans-serif; margin:0; }}
main {{ max-width:850px; margin:2rem auto; padding:0 1.25rem 3rem; }}
h1 {{ font-size:2.2rem; margin:.3rem 0; }}
h2 {{ font-size:1.65rem; margin-top:1.6rem; }}
.subtitle {{ color:#555; font-size:1.15rem; }}
.stats {{ color:#555; font-size:.95rem; }}
.hash {{ overflow-wrap:anywhere; font-family:monospace; font-size:.85rem; }}
.controls {{ display:flex; flex-wrap:wrap; gap:.75rem; margin:1.5rem 0; }}
button,.open {{ border:0; border-radius:.45rem; background:#176b49; color:white;
  font:600 1rem system-ui,sans-serif; padding:.95rem 1.15rem; cursor:pointer; text-decoration:none; }}
button:hover,.open:hover {{ background:#105337; }}
.open {{ background:#303030; }}
.preview {{ background:white; border:1px solid #ddd; border-radius:.5rem; padding:1.25rem 1.5rem; }}
.preview p {{ margin:1rem 0; }}
#copy-status {{ min-height:1.5rem; font-weight:600; }}
</style>
<script src="/medium-assist.js" defer></script>
</head>
<body><main>
<p>Medium Assist</p><h1>{title}</h1>{subtitle_display}
<p class="stats">{prepared.word_count:,} words · {prepared.character_count:,} characters ·
{prepared.heading_count} headings · {prepared.paragraph_count} paragraphs</p>
<p class="hash">Prepared story hash: {prepared.source_hash}</p>
<div class="controls">
<button type="button" data-copy="title">Copy Title</button>
{subtitle_button}
<button type="button" data-copy="body">Copy Body as Rich Text</button>
<a class="open" href="{MEDIUM_WRITE_URL}" target="_blank" rel="noopener noreferrer">Open Medium</a>
</div>
{topics_section}
<p id="copy-status" role="status" aria-live="polite"></p>
<section class="preview" aria-label="Story preview"><h2>Preview</h2>{prepared.preview_html}</section>
<p>Paste into Medium, review the post, and click Publish yourself.</p>
</main></body></html>'''


def make_server(prepared: PreparedMediumStory) -> ThreadingHTTPServer:
    token = secrets.token_urlsafe(24)
    script = (Path(__file__).with_name("medium_assist.js")).read_bytes()
    page = _page(prepared, token).encode("utf-8")
    topic_paths = {f"/copy/topic/{index}": topic for index, topic in enumerate(prepared.topics)}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def _local_host(self) -> bool:
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def _respond(self, status: int, data: bytes, content_type: str):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            if content_type.startswith("text/html"):
                self.send_header("Content-Security-Policy",
                                 "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if not self._local_host():
                self._respond(403, b"Forbidden", "text/plain; charset=utf-8")
                return
            if self.path == "/":
                self._respond(200, page, "text/html; charset=utf-8")
            elif self.path == "/medium-assist.js":
                self._respond(200, script, "text/javascript; charset=utf-8")
            else:
                self._respond(404, b"Not found", "text/plain; charset=utf-8")

        def do_POST(self):
            if not self._local_host():
                self._respond(403, b"Forbidden", "text/plain; charset=utf-8")
                return
            if self.path not in {"/copy/title", "/copy/subtitle", "/copy/body"} | topic_paths.keys():
                self._respond(404, b"Not found", "text/plain; charset=utf-8")
                return
            expected_origin = f"http://127.0.0.1:{self.server.server_port}"
            if (self.headers.get("Origin") not in (None, expected_origin)
                    or self.headers.get("X-Assist-Token") != token):
                self._respond(403, b'{"error":"Forbidden"}', "application/json; charset=utf-8")
                return
            if not source_unchanged(prepared):
                data = {"code": SOURCE_CHANGED,
                        "error": "Prepared source changed. Rerun medium-assist before copying."}
                self._respond(409, json.dumps(data).encode(), "application/json; charset=utf-8")
                return
            if self.path in topic_paths:
                payload = {"plain": topic_paths[self.path]}
                self._respond(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                              "application/json; charset=utf-8")
                return
            kind = self.path.rsplit("/", 1)[1]
            if kind == "subtitle" and prepared.subtitle is None:
                self._respond(404, b'{"error":"No subtitle"}', "application/json; charset=utf-8")
                return
            payload = ({"plain": prepared.body_plain, "html": prepared.body_html}
                       if kind == "body" else
                       {"plain": prepared.title if kind == "title" else prepared.subtitle})
            self._respond(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                          "application/json; charset=utf-8")

    return ThreadingHTTPServer(("127.0.0.1", 0), Handler)


def serve_medium_assist(prepared: PreparedMediumStory) -> None:
    server = make_server(prepared)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Medium Assist: {url}\nKeep this terminal open while copying. Press Ctrl+C when done.", flush=True)
    try:
        webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
    finally:
        server.server_close()


def validate_medium_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        valid = (parsed.scheme == "https" and (host == "medium.com" or host.endswith(".medium.com"))
                 and parsed.username is None and parsed.password is None and parsed.port in (None, 443)
                 and not parsed.query and not parsed.fragment
                 and parsed.path.strip("/") not in {"", "new-story", "me"}
                 and not parsed.path.rstrip("/").endswith("/edit")
                 and not any(char.isspace() for char in value))
    except ValueError:
        valid = False
    if not valid:
        raise StateError("Supply a published HTTPS medium.com story URL without a query or fragment.")
    return value.rstrip("/")


def prepare_medium(root: Path, source_argument: str | None = None) -> tuple[PreparedMediumStory, bool]:
    story = load_medium_story(root, source_argument)
    prepared = prepare_medium_story(story)
    repository = PublicationRepository(runtime_paths(root).database)
    record = repository.prepare_manual_publication(story, "medium")
    return prepared, record.status.value == "published"


def complete_medium(root: Path, published_url: str, source_argument: str | None = None):
    url = validate_medium_url(published_url)
    story = load_medium_story(root, source_argument)
    repository = PublicationRepository(runtime_paths(root).database)
    return repository.complete_manual_publication(story, "medium", url)

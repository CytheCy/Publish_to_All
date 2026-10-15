"""Deterministic local tests for manual Medium preparation and completion."""

from contextlib import contextmanager
from html.parser import HTMLParser
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Thread
import tomllib

import pytest

from publish_to_all.cli import main
from publish_to_all.config import runtime_paths
from publish_to_all.errors import StoryError
from publish_to_all.medium_assist import (
    SOURCE_CHANGED, complete_medium, load_medium_story, make_server,
    prepare_medium, prepare_medium_story, source_unchanged, validate_medium_url,
)
from publish_to_all.state import PublicationRepository, PublicationStatus, StateError


class Structure(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []
        self.data = []

    def handle_starttag(self, tag, attrs):
        self.tags.append(tag)

    def handle_data(self, data):
        self.data.append(data)


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path.parent / f"{tmp_path.name}-medium-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path.parent / f"{tmp_path.name}-medium-state"))
    (tmp_path / "In").mkdir()
    (tmp_path / "In/story.md").write_text(
        '---\ntitle: "The <Tree>"\nDescription: "A short & useful subtitle"\n---\n'
        '# 01\n\nFirst **bold** paragraph with an *italic* word and '
        '[link](https://example.com).\n\n# 02\n\nSecond paragraph.\n', encoding="utf-8"
    )
    return tmp_path


@contextmanager
def running_server(prepared):
    server = make_server(prepared)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def request(server, path, *, method="GET", token=None, origin=None, host=None):
    connection = HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    headers = {}
    if token:
        headers["X-Assist-Token"] = token
    if origin:
        headers["Origin"] = origin
    if host:
        headers["Host"] = host
    connection.request(method, path, headers=headers)
    response = connection.getresponse()
    result = (response.status, response.read().decode("utf-8"), dict(response.getheaders()))
    connection.close()
    return result


def page_token(html):
    import re
    return re.search(r'<meta name="assist-token" content="([^"]+)">', html).group(1)


def test_discovery_explicit_path_and_immutable_preparation(project):
    source = project / "In/story.md"
    first = load_medium_story(project)
    assert first.metadata.title == "The <Tree>"
    (project / "In/other.md").write_text("---\ntitle: Other\n---\nBody")
    with pytest.raises(StoryError, match="Multiple story"):
        load_medium_story(project)
    explicit = load_medium_story(project, "In/story.md")
    assert explicit.source_hash == first.source_hash
    prepared = prepare_medium_story(explicit)
    assert prepared.subtitle == "A short & useful subtitle"
    assert prepared.source_hash == explicit.source_hash
    assert (prepared.heading_count, prepared.paragraph_count) == (2, 2)
    assert prepared.body_plain == ("01\n\nFirst bold paragraph with an italic word and link.\n\n"
                                   "02\n\nSecond paragraph.")
    assert prepared.character_count == len(prepared.body_plain)
    html = Structure()
    html.feed(prepared.body_html)
    assert html.tags.count("h1") == 2
    assert html.tags.count("p") == 2
    assert {"strong", "em", "a"}.issubset(html.tags)
    assert "# 01" not in prepared.body_html
    assert "<script" not in prepared.body_html
    assert source.exists()


def test_explicit_story_must_be_direct_child(project):
    (project / "Elsewhere").mkdir()
    (project / "Elsewhere/other.md").write_text("---\ntitle: Other\n---\nBody")
    with pytest.raises(StoryError, match="directly in In"):
        load_medium_story(project, "Elsewhere/other.md")
    with pytest.raises(StoryError, match="readable Markdown"):
        load_medium_story(project, "In/missing.md")


def test_missing_and_malformed_story(project):
    (project / "In/story.md").unlink()
    with pytest.raises(StoryError, match="No Markdown"):
        load_medium_story(project)
    (project / "In/story.md").write_text("---\ntitle: [\n---\nBody")
    with pytest.raises(StoryError, match="Invalid YAML"):
        load_medium_story(project)


def test_subtitle_falls_back_to_subtitle_and_escapes_page(project):
    source = project / "In/story.md"
    source.write_text('---\ntitle: "<script>bad</script>"\nsubtitle: "A & B"\n---\nBody')
    prepared = prepare_medium_story(load_medium_story(project))
    assert prepared.subtitle == "A & B"
    with running_server(prepared) as server:
        status, page, headers = request(server, "/")
        assert status == 200
        assert "&lt;script&gt;bad&lt;/script&gt;" in page
        assert "Copy Subtitle" in page
        assert "A &amp; B" in page
        assert "<script>bad</script>" not in page
        assert "Prepared story hash:" in page
        assert "https://medium.com/new-story" in page
        assert headers["Content-Security-Policy"].startswith("default-src 'none'")


def test_page_omits_subtitle_control_when_absent(project):
    (project / "In/story.md").write_text("---\ntitle: Story\n---\nBody")
    prepared = prepare_medium_story(load_medium_story(project))
    with running_server(prepared) as server:
        _, page, _ = request(server, "/")
        assert "Copy Subtitle" not in page


def test_body_html_escapes_raw_markup(project):
    (project / "In/story.md").write_text(
        "---\ntitle: Story\n---\n# 01\n\n<script>alert('bad')</script> & safe text"
    )
    prepared = prepare_medium_story(load_medium_story(project))
    assert "<h1>01</h1>" in prepared.body_html
    assert "<script>" not in prepared.body_html
    assert "&lt;script&gt;" in prepared.body_html
    assert "<script>" not in prepared.preview_html


def test_loopback_server_copy_payload_guard_and_no_directory_listing(project):
    prepared = prepare_medium_story(load_medium_story(project))
    with running_server(prepared) as server:
        assert server.server_address[0] == "127.0.0.1"
        status, page, _ = request(server, "/")
        assert status == 200
        token = page_token(page)
        assert request(server, "/In/story.md")[0] == 404
        assert request(server, "/", host="other.example")[0] == 403
        assert request(server, "/copy/body", method="GET")[0] == 404
        assert request(server, "/copy/body", method="POST")[0] == 403
        assert request(server, "/copy/body", method="POST", token=token,
                       origin="https://medium.com")[0] == 403
        for kind, expected_keys in (("title", {"plain"}), ("subtitle", {"plain"}),
                                    ("body", {"plain", "html"})):
            status, body, _ = request(server, f"/copy/{kind}", method="POST", token=token)
            assert status == 200
            assert set(json.loads(body)) == expected_keys
        status, script, _ = request(server, "/medium-assist.js")
        assert status == 200
        assert "ClipboardItem" in script and "text/html" in script and "text/plain" in script

        (project / "In/story.md").write_text("---\ntitle: Changed\n---\nOther body")
        assert not source_unchanged(prepared)
        for kind in ("title", "subtitle", "body"):
            status, body, _ = request(server, f"/copy/{kind}", method="POST", token=token)
            assert status == 409
            assert json.loads(body)["code"] == SOURCE_CHANGED
            assert "Rerun medium-assist" in body


@pytest.mark.parametrize("url", [
    "http://medium.com/@writer/story", "https://medium.com/",
    "https://medium.com/new-story", "https://medium.com/p/abc/edit",
    "https://example.com/story", "https://medium.com/@writer/story?source=copy",
    "https://user:pass@medium.com/@writer/story",
])
def test_invalid_published_url(url):
    with pytest.raises(StateError, match="published HTTPS medium.com"):
        validate_medium_url(url)


def test_prepare_complete_duplicate_and_changed_version(project):
    prepared, already = prepare_medium(project, "In/story.md")
    assert not already
    repository = PublicationRepository(runtime_paths(project).database)
    record = repository.get_publication(prepared.source_hash, "medium")
    assert record.status == PublicationStatus.NOT_STARTED
    assert record.draft_url is None
    url = "https://medium.com/@writer/the-tree-123456"
    result, duplicate = complete_medium(project, url)
    assert not duplicate
    assert result.status == PublicationStatus.PUBLISHED
    assert result.published_url == url
    assert result.updated_at
    again, duplicate = complete_medium(project, "https://medium.com/@writer/different-987")
    assert duplicate and again.id == result.id and again.published_url == url
    assert len(repository.publication_history(prepared.source_hash, "medium")) == 1
    _, already = prepare_medium(project)
    assert already
    (project / "In/story.md").write_text("---\ntitle: Changed\n---\nBody")
    with pytest.raises(StateError, match="not prepared"):
        complete_medium(project, url)


def test_cli_surface_and_no_browser_open_on_completion(project, monkeypatch, capsys):
    opened = []
    monkeypatch.setattr("publish_to_all.medium_assist.webbrowser.open", lambda url: opened.append(url))
    served = []
    monkeypatch.setattr("publish_to_all.cli.serve_medium_assist", lambda prepared: served.append(prepared))
    assert main(["medium-assist", "In/story.md"]) == 0
    assert served and not opened
    capsys.readouterr()
    assert main(["medium-complete", "https://medium.com/@writer/story-123"]) == 0
    assert "Medium publication recorded" in capsys.readouterr().out
    assert not opened
    assert main(["medium-complete", "https://medium.com/@writer/story-123"]) == 0
    assert "already recorded" in capsys.readouterr().out
    with pytest.raises(SystemExit) as stopped:
        main(["medium-login"])
    assert stopped.value.code == 2


def test_cli_starts_without_playwright_or_browser_automation(project):
    """A fresh interpreter must start local commands with Playwright unavailable."""
    script = """
from http.client import HTTPConnection
import importlib.abc
import sys
from threading import Event, Thread

class NoPlaywright(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'playwright' or fullname.startswith('playwright.'):
            raise ModuleNotFoundError("No module named 'playwright'", name='playwright')

sys.meta_path.insert(0, NoPlaywright())
from publish_to_all import cli
from publish_to_all import medium_assist

def assert_no_browser_automation():
    blocked = ('publish_to_all.browser.session', 'publish_to_all.browser.substack',
               'publish_to_all.publishers.substack', 'publish_to_all.browser.publish_inspect')
    assert not any(name in sys.modules for name in blocked), blocked
    assert 'playwright' not in sys.modules

assert_no_browser_automation()
ready = Event()
servers = []
original_make_server = medium_assist.make_server
def capture_server(prepared):
    server = original_make_server(prepared)
    servers.append(server)
    ready.set()
    return server
medium_assist.make_server = capture_server
medium_assist.webbrowser.open = lambda url: None
results = []
thread = Thread(target=lambda: results.append(cli.main(['medium-assist', 'In/story.md'])), daemon=True)
thread.start()
assert ready.wait(5), 'Medium Assist did not start its local server'
server = servers[0]
try:
    connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
    connection.request('GET', '/')
    response = connection.getresponse()
    assert response.status == 200
    assert b'Copy Title' in response.read()
    connection.close()
finally:
    server.shutdown()
thread.join(timeout=5)
assert not thread.is_alive() and results == [0]
assert_no_browser_automation()
assert cli.main(['medium-complete', 'https://medium.com/@writer/story-123']) == 0
assert_no_browser_automation()
for command in ('check', 'preview', 'status'):
    assert cli.main([command]) == 0
    assert_no_browser_automation()
"""
    env = os.environ.copy()
    source_root = Path(__file__).resolve().parents[1] / "src"
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(source_root), env.get("PYTHONPATH"))))
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=project, env=env,
        capture_output=True, text=True, check=False, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "Medium Assist: http://127.0.0.1:" in result.stdout
    assert "Medium publication recorded" in result.stdout


def test_playwright_is_only_a_substack_install_extra():
    project_file = Path(__file__).resolve().parents[1] / "pyproject.toml"
    metadata = tomllib.loads(project_file.read_text(encoding="utf-8"))["project"]
    assert all(not item.startswith("playwright") for item in metadata["dependencies"])
    assert any(item.startswith("playwright") for item in metadata["optional-dependencies"]["substack"])

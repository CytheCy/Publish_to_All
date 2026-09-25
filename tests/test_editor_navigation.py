"""Rendered-control fixtures, without a network or authenticated browser."""
from unittest.mock import MagicMock

import pytest

from publish_to_all.browser import editor, reconcile, substack
from publish_to_all.errors import BrowserSessionError

URL = 'https://example.substack.com'
DRAFT = URL + '/publish/post/123'


class Locator:
    def __init__(self, nodes=()):
        self.nodes = list(nodes)

    def all(self):
        return self.nodes

    def or_(self, other):
        return Locator(list(dict.fromkeys(self.nodes + other.nodes)))

    def get_by_role(self, role, name=None, exact=False):
        return Locator(n for n in self.nodes if n.role == role and
                       (name is None or (n.label == name if isinstance(name, str) else name.search(n.label))))

    def get_by_text(self, pattern):
        return Locator(n for n in self.nodes if pattern.search(n.label))


class Node(Locator):
    def __init__(self, role, label, href=None, children=(), visible=True):
        super().__init__(children)
        self.role, self.label, self.href = role, label, href
        self.visible = visible
        self.click = MagicMock()

    def is_visible(self):
        return self.visible

    def inner_text(self):
        return self.label

    def get_attribute(self, name):
        return self.href if name == 'href' else None


class Page(Locator):
    url = URL + '/publish/home'

    def __init__(self, nodes):
        super().__init__(nodes)
        self.goto = MagicMock()
        self.wait_for_timeout = MagicMock()

    def get_by_placeholder(self, pattern):
        return Locator()

    def title(self):
        return 'Dashboard'


@pytest.mark.parametrize('role', ['button', 'link', 'menuitem'])
@pytest.mark.parametrize('label', ['New post', 'Create post', 'Write', 'Write a post', 'New article', 'Create'])
def test_rendered_creation_variations(role, label):
    create = Node(role, label)
    title = Node('textbox', 'Title', visible=False)
    page = Page([create, title])
    create.click.side_effect = lambda: setattr(title, 'visible', True)
    editor.open_new_post(page, URL)
    create.click.assert_called_once()


@pytest.mark.parametrize('labels', [[], ['Publish', 'Send', 'Schedule', 'Publish now'], ['Write', 'New post']])
def test_missing_or_ambiguous_never_clicks(labels, monkeypatch):
    page = Page([Node('button', label) for label in labels])
    clock = iter([0, 11, 22])
    monkeypatch.setattr(editor, 'monotonic', lambda: next(clock))
    with pytest.raises(BrowserSessionError):
        editor.open_new_post(page, URL)
    for node in page.nodes:
        node.click.assert_not_called()


def test_rendered_posts_fallback():
    posts = Node('link', 'Posts', URL + '/publish/posts')
    create = Node('button', 'Write', visible=False)
    title = Node('textbox', 'Title', visible=False)
    page = Page([posts, create, title])
    page.goto.side_effect = lambda *a, **kw: setattr(create, 'visible', True)
    create.click.side_effect = lambda: setattr(title, 'visible', True)
    editor.open_new_post(page, URL)
    page.goto.assert_called_once_with(URL + '/publish/posts', wait_until='domcontentloaded')
    create.click.assert_called_once()


def test_existing_editor_never_creates():
    create = Node('button', 'Write')
    page = Page([create])
    page.url = DRAFT
    with pytest.raises(BrowserSessionError, match='existing editor'):
        editor.open_new_post(page, URL)
    create.click.assert_not_called()


def test_diagnostics_redact_secrets_and_include_relevant_controls(tmp_path, monkeypatch):
    page = Page([Node('button', 'Write'), Node('link', 'Posts'), Node('button', 'token=secret')])
    page.url += '?token=secret#password'
    monkeypatch.setattr(substack, 'safe_screenshot', lambda *a: tmp_path / 'safe.png')
    result = substack.collect_diagnostics(page, URL, tmp_path)
    assert result.final_url == URL + '/publish/home'
    assert result.title == 'Dashboard'
    assert result.controls == ('Write', 'Posts')
    assert result.screenshot == tmp_path / 'safe.png'
    assert 'secret' not in result.final_url + result.title + repr(result.controls)
    page.title = lambda: 'password=secret'
    assert substack.collect_diagnostics(page, URL, tmp_path).title == '[nonstandard page title redacted]'


def row(title, url, status='Draft'):
    return Node('row', '', children=[Node('link', title, url), Node('text', status)])


@pytest.mark.parametrize('rows,expected', [
    ([row('Story', DRAFT)], (DRAFT,)),
    ([row('Story', DRAFT), row('Story', DRAFT)], (DRAFT,)),
    ([row('Story', DRAFT), row('Story', URL + '/publish/post/456')], (DRAFT, URL + '/publish/post/456')),
    ([row('Other', DRAFT)], ()), ([row('Story', DRAFT, 'Published')], ()),
    ([row('Story', 'https://other.substack.com/publish/post/123')], ()),
    ([row('Story', URL + '/publish/post/new')], ()), ([], ()),
])
def test_existing_draft_detection(rows, expected):
    page = Page(rows)
    evidence = reconcile.scan_draft_listing(page, URL, 'Story')
    assert evidence.matches == expected
    page.goto.assert_not_called()
    for item in rows:
        for node in item.nodes:
            node.click.assert_not_called()


@pytest.mark.parametrize('href', ['/publish/post/123', '/publish/post/123/publish', '/publish/settings'])
def test_misleading_creation_link_never_clicked(href):
    node = Node('link', 'New post', href)
    with pytest.raises(BrowserSessionError):
        editor.click_creation_control(Page([node]), node, URL, editor.CREATE)
    node.click.assert_not_called()


def test_read_only_listing_navigation(monkeypatch):
    posts = Node('link', 'Posts', '/publish/posts')
    existing = row('Story', DRAFT)
    page = Page([posts, existing])
    monkeypatch.setattr(editor, 'navigate_dashboard', lambda *args: None)
    result = reconcile.inspect_drafts(page, URL, 'Story')
    assert result.matches == (DRAFT,)
    page.goto.assert_called_once_with(URL + '/publish/posts', wait_until='domcontentloaded')
    posts.click.assert_not_called()
    existing.nodes[0].click.assert_not_called()

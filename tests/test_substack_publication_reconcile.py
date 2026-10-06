from dataclasses import asdict, replace
from datetime import datetime, timedelta
from html import escape
import inspect
import json
import sqlite3
from unittest.mock import Mock
from urllib.error import HTTPError

import pytest

from publish_to_all import publication_reconcile as app
from publish_to_all.browser import publication_reconcile as remote
from publish_to_all.browser.body import prepare_story_body
from publish_to_all.config import runtime_paths
from publish_to_all.errors import BrowserSessionError, SubstackRateLimitError
from publish_to_all.state import PublicationRepository, StateError
from publish_to_all.story import load_story


ORIGIN = 'https://example.substack.com'
URL = ORIGIN + '/p/story-cycle-2'
OLD_URL = ORIGIN + '/p/story-cycle-1'
BODY = 'The unique current story continues with substantial content. ' * 30
EXPECTED = remote.Expected(ORIGIN, 222, 'Story', 'Subtitle', BODY,
                           '2026-10-05T17:48:18+00:00', (111,), (OLD_URL,))


def script(data):
    return '<script>window._preloads = JSON.parse(' + json.dumps(json.dumps(data)) + ')</script>'


def public_response(expected=EXPECTED, url=URL, **changes):
    post = {
        'id': expected.draft_id, 'title': expected.title, 'subtitle': expected.subtitle,
        'canonical_url': url, 'is_published': True, 'is_editor_preview': False,
        'audience': 'everyone', 'post_date': (
            datetime.fromisoformat(expected.clicked_at) + timedelta(seconds=2)
        ).isoformat(),
    }
    body = changes.pop('body', expected.body)
    post.update(changes)
    html = (f'<html><head><link rel="canonical" href="{url}">'
            f'<meta property="og:url" content="{url}">'
            f'<meta property="og:title" content="{escape(post["title"], quote=True)}">'
            '</head><body><article aria-label="Post">'
            f'<h1 class="post-title">{escape(post["title"])}</h1>'
            f'<h3 class="subtitle">{escape(post["subtitle"])}</h3>'
            f'<div class="body markup"><p>{escape(body)}</p></div>'
            '</article>' + script({'post': post}) + '</body></html>')
    return remote.Response(url, 200, html)


def archive(expected=EXPECTED, urls=(URL,)):
    posts = [{'id': expected.draft_id, 'title': expected.title, 'canonical_url': u} for u in urls]
    posts.append({'id': 111, 'title': expected.title, 'canonical_url': OLD_URL})
    return remote.Response(ORIGIN + '/archive', 200, script({'newPostsForArchive': {'pub': posts}}))


def two_reads(expected=EXPECTED, url=URL):
    return [remote.verify(public_response(expected, url), expected) for _ in range(2)]


def test_unique_cycle_2_requires_two_fresh_public_reads():
    reads = two_reads()
    assert remote.classify(reads) == (remote.PUBLISHED, URL)
    assert remote.classify(reads[:1]) == (remote.UNKNOWN, None)
    assert remote.classify([reads[0], reads[0]]) == (remote.UNKNOWN, None)
    assert remote.classify([reads[0], replace(reads[1], body_sha256='different')])[0] == remote.UNKNOWN


@pytest.mark.parametrize('change,reason', [
    ({'body': 'Another substantial story. ' * 80}, 'body'),
    ({'title': 'Another title'}, 'title'),
    ({'subtitle': 'Other subtitle'}, 'Subtitle'),
    ({'id': 111}, 'Retired cycle'),
    ({'id': 333}, 'exact attempted draft'),
    ({'id': None}, 'exact attempted draft'),
    ({'post_date': '2026-10-01T16:04:33Z'}, 'timing'),
    ({'is_editor_preview': True}, 'public publication'),
    ({'is_published': False}, 'public publication'),
])
def test_public_candidate_must_strongly_match_cycle_2(change, reason):
    read = remote.verify(public_response(**change), EXPECTED)
    assert not read.accepted
    assert reason in read.reason
    assert remote.classify([read, replace(read, read_id='fresh')])[0] == remote.UNKNOWN


def test_retired_url_is_rejected_even_if_current_id_title_body_are_copied():
    assert not remote.verify(public_response(url=OLD_URL), EXPECTED).accepted
    candidates, excluded = remote.discover(archive(), EXPECTED)
    assert list(candidates) == [URL]
    assert excluded[0]['id'] == 111


def test_multiple_matching_public_candidates_remain_unknown():
    assert remote.classify(two_reads() + two_reads(url=ORIGIN + '/p/another')) == (remote.UNKNOWN, None)


@pytest.mark.parametrize('html', [
    '<html><body>Publish modal disappeared</body></html>',
    '<html><body>HTTP 200 publish response</body></html>',
    '<html><body><textarea>Story</textarea><button>Continue</button>Saved</body></html>',
])
def test_editor_modal_or_http_status_alone_cannot_establish_publication(html):
    read = remote.verify(remote.Response(URL, 200, html), EXPECTED)
    assert remote.classify([read, replace(read, read_id='fresh')]) == (remote.UNKNOWN, None)


@pytest.mark.parametrize('url', ['https://evil.example/p/story', ORIGIN + '/publish/post/222',
                                URL + '?preview=true', URL + '/comments'])
def test_untrusted_or_noncanonical_urls_rejected(url):
    assert not remote.verify(public_response(url=url), EXPECTED).accepted


def test_metadata_body_alone_cannot_replace_publicly_rendered_body():
    response = public_response()
    assert not remote.verify(replace(response, html=response.html.replace(
        'class="body markup"', 'class="body markup" hidden',
    )), EXPECTED).accepted


@pytest.fixture
def cycle(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path.parent / (tmp_path.name + '-data')))
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path.parent / (tmp_path.name + '-state')))
    (tmp_path / 'In').mkdir()
    (tmp_path / 'In/story.md').write_text('---\ntitle: Story\ndescription: Subtitle\n---\n' + BODY)
    (tmp_path / 'config.toml').write_text('[substack]\npublication_url = "' + ORIGIN + '"\n')
    story = load_story(tmp_path / 'In')
    repo = PublicationRepository(runtime_paths(tmp_path).database)
    first = repo.begin_attempt(story, 'substack')
    repo.mark_draft_created(first.id, ORIGIN + '/publish/post/111')
    repo.retire_test_publication(story.source_hash, 'substack', OLD_URL, 'Verified prior test post')
    repo.start_new_cycle(story, 'substack')
    attempt = repo.begin_attempt(story, 'substack')
    draft = repo.mark_draft_created(attempt.id, ORIGIN + '/publish/post/222')
    clicked = repo.mark_final_click_attempted(draft)
    record = repo.mark_publication_uncertain(clicked, 'HTTP 200; modal absent', ('http=200',))
    expected = replace(EXPECTED, clicked_at=record.final_click_attempted_at,
                       body=prepare_story_body(story).text)
    # These must never run during reconciliation, even for conclusive evidence.
    for method in ('mark_final_click_attempted', 'begin_attempt', 'authorize_retry'):
        monkeypatch.setattr(PublicationRepository, method, Mock(side_effect=AssertionError(method)))
    return tmp_path, repo, story, record, expected


def fake_reads(monkeypatch, expected, *, urls=(URL,), change=None, support=None):
    requests = []

    def get(self, url):
        requests.append(url)
        return archive(expected, urls) if url.endswith('/archive') else public_response(
            expected, url, **(change or {}),
        )

    monkeypatch.setattr(remote.PublicReader, 'get', get)
    monkeypatch.setattr(app, 'inspect_supporting', Mock(return_value=support or {
        'editable': True, 'published_sent_status': [], 'public_links': [],
    }))
    return requests


def test_verified_published_preserves_one_attempt_audit_retired_cycle_and_other_fields(cycle, monkeypatch):
    root, repo, story, before, expected = cycle
    old = repo.publication_history(story.source_hash, 'substack')[0]
    audit = repo.audit_history(before.id)
    requests = fake_reads(monkeypatch, expected)
    result = app.reconcile_publication(root)
    after = repo.select_active_cycle(story.source_hash, 'substack')
    assert result['classification'] == remote.PUBLISHED
    assert requests == [ORIGIN + '/archive', URL, URL]
    assert after.published_url == URL and after.publication_verification_status == 'verified'
    assert not after.needs_reconciliation
    assert after.final_click_attempted and after.final_click_attempt_count == 1
    assert after.final_click_attempted_at == before.final_click_attempted_at
    assert result['sqlite_before'] == result['sqlite_after_remote_reads']
    assert result['sqlite_after_reconciliation'] != result['sqlite_before']
    assert repo.audit_history(before.id) == audit
    assert repo.publication_history(story.source_hash, 'substack')[0] == old
    allowed = {'status', 'published_url', 'error_message', 'needs_reconciliation',
               'publication_verification_status', 'publication_verification_evidence',
               'publication_ambiguity_reason', 'updated_at'}
    assert {k for k, v in asdict(before).items() if asdict(after)[k] != v} <= allowed
    assert result['editor']['editable']  # Published editors can remain editable.
    assert result['publication_action_count'] == 1 and result['safe_to_retry_publication'] == 'NO'
    with pytest.raises(StateError):
        repo.require_final_click_eligible(after)


@pytest.mark.parametrize('case', ['body_differs', 'no_linkage', 'cycle1', 'multiple', 'none'])
def test_unknown_preserves_sqlite_and_retry_prohibition(cycle, monkeypatch, case):
    root, repo, story, before, expected = cycle
    changes = {'body_differs': {'body': 'different body ' * 200},
               'no_linkage': {'id': None}, 'cycle1': {'id': 111}}
    fake_reads(monkeypatch, expected, urls=() if case == 'none' else
               (URL, ORIGIN + '/p/other') if case == 'multiple' else (URL,),
               change=changes.get(case))
    old_bytes = repo.path.read_bytes()
    result = app.reconcile_publication(root)
    assert result['classification'] == remote.UNKNOWN
    assert repo.path.read_bytes() == old_bytes
    assert repo.select_active_cycle(story.source_hash, 'substack') == before
    assert result['safe_to_retry_publication'] == 'NO'
    with pytest.raises(StateError):
        repo.require_final_click_eligible(before)


@pytest.mark.parametrize('stage', ['archive', 'second_read', 'editor'])
def test_rate_limiting_stops_immediately_and_never_changes_state(cycle, monkeypatch, stage):
    root, repo, _, _, expected = cycle
    requests = fake_reads(monkeypatch, expected)
    real_fake = remote.PublicReader.get

    def limited(self, url):
        if stage == 'archive' or stage == 'second_read' and len(requests) == 2:
            requests.append('429')
            raise SubstackRateLimitError('429 Too many requests')
        return real_fake(self, url)

    monkeypatch.setattr(remote.PublicReader, 'get', limited)
    if stage == 'editor':
        app.inspect_supporting.side_effect = SubstackRateLimitError('429 Too many requests')
    old = repo.path.read_bytes()
    result = app.reconcile_publication(root)
    assert result['classification'] == remote.UNKNOWN and result['rate_limited']
    assert repo.path.read_bytes() == old
    if stage != 'editor':
        app.inspect_supporting.assert_not_called()
        assert requests[-1] == '429'


def test_public_transport_uses_get_only_and_no_retry_after_429(monkeypatch):
    opener = Mock()
    error = HTTPError(URL, 429, 'Too many requests', {}, None)
    opener.open.side_effect = error
    monkeypatch.setattr(remote, 'build_opener', Mock(return_value=opener))
    reader = remote.PublicReader(ORIGIN)
    for _ in range(2):
        with pytest.raises(SubstackRateLimitError):
            reader.get(URL)
    assert opener.open.call_count == 1
    assert opener.open.call_args.args[0].method == 'GET'
    assert remote._NoRedirect().redirect_request(None) is None


@pytest.mark.parametrize('field,value', [('final_click_attempted', 0),
                                      ('publication_verification_status', 'pending')])
def test_local_guards_run_before_any_remote_activity(cycle, monkeypatch, field, value):
    root, repo, _, before, _ = cycle
    with sqlite3.connect(repo.path) as connection:
        connection.execute(f'UPDATE publications SET {field}=? WHERE id=?', (value, before.id))
    collector = Mock(side_effect=AssertionError('No remote activity allowed'))
    monkeypatch.setattr(app, 'collect_remote', collector)
    old = repo.path.read_bytes()
    with pytest.raises(StateError):
        app.reconcile_publication(root)
    collector.assert_not_called()
    assert repo.path.read_bytes() == old


def test_checksum_guard_is_checked_inside_local_write_transaction(cycle):
    _, repo, _, record, _ = cycle
    old = repo.path.read_bytes()
    with pytest.raises(StateError, match='SQLite changed'):
        repo.reconcile_publication_verified(record, URL, ('verified',), expected_database_sha256='wrong')
    assert repo.path.read_bytes() == old


def test_reconciliation_has_no_action_or_editor_write_calls():
    import ast
    forbidden = {'click', 'fill', 'press', 'type', 'check', 'uncheck', 'select_option',
                 'set_input_files', 'dispatch_event', 'mark_final_click_attempted',
                 'begin_attempt', 'authorize_retry', 'publish_substack'}
    for module in (app, remote):
        tree = ast.parse(inspect.getsource(module))
        calls = {n.func.attr for n in ast.walk(tree)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert not calls & forbidden

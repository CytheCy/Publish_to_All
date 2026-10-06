"""Narrow exceptions distinguish blocked browser traffic from unknown writes."""

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from publish_to_all.browser.publish_inspect import MutationGuard, WriteLikeNetworkObserver
from publish_to_all.errors import BrowserSessionError


ORIGIN = 'https://cyporter.substack.com'
CHALLENGE = ORIGIN + '/cdn-cgi/challenge-platform/h/b/jsd/oneshot/opaque-Challenge-Token'
MEASUREMENT = 'https://ad.doubleclick.net/ccm/s/collect'
ENDPOINTS = [(CHALLENGE, 'xhr'), (MEASUREMENT, 'fetch')]
STAGES = [
    ('AUTHENTICATION', 'AUTHENTICATION'),
    ('DRAFT_EDITOR_LOAD', 'DRAFT_VERIFICATION'),
    ('CONTINUE_NAVIGATION', 'CONTINUE_NAVIGATION'),
    ('PUBLISH_SCREEN_LOAD', 'FINAL_CONFIGURATION'),
    ('FINAL_ACTION_DISCOVERY', 'FINAL_CONFIGURATION'),
    ('PRE_CLICK_REVALIDATION', 'PRE_CLICK'),
]


def observe(url, resource_type, *, stage='AUTHENTICATION', method='POST', redirected_from=None):
    emitted = []
    observer = WriteLikeNetworkObserver([], ORIGIN, diagnostic_sink=emitted.append)
    guard = MutationGuard([], observer.diagnostics, observer=observer)
    guard.set_stage(stage)
    request = SimpleNamespace(method=method, url=url, resource_type=resource_type,
                              redirected_from=redirected_from, initiator=None)
    route = SimpleNamespace(request=request, abort=MagicMock(), continue_=MagicMock())
    guard.handle(route)
    route.abort.assert_called_once_with()
    route.continue_.assert_not_called()
    return observer, guard, emitted


@pytest.mark.parametrize('url, resource_type', ENDPOINTS)
@pytest.mark.parametrize('stage, report_stage', STAGES)
def test_known_browser_posts_remain_blocked_at_every_production_stage(
        url, resource_type, stage, report_stage):
    observer, guard, emitted = observe(url, resource_type, stage=stage)
    assert emitted[0]['classification'] == 'EXPECTED NON-PUBLISHING'
    assert emitted[0]['disposition'] == 'BLOCKED'
    assert emitted[0]['stage'] == report_stage
    assert emitted[0]['stage_detail'] == stage
    assert emitted[0]['relative_to_continue'] == (
        'BEFORE' if report_stage in {'AUTHENTICATION', 'DRAFT_VERIFICATION'} else 'DURING_OR_AFTER'
    )
    assert len(observer.suppressed_telemetry) == 1
    assert observer.methods == []
    guard.require_clear()
    observer.require_clear()


@pytest.mark.parametrize('url', [
    'https://other.substack.com/cdn-cgi/challenge-platform/h/b',
    'https://cyporter.substack.com.example.test/cdn-cgi/challenge-platform/h/b',
    'http://cyporter.substack.com/cdn-cgi/challenge-platform/h/b',
    'https://cyporter.substack.com:8443/cdn-cgi/challenge-platform/h/b',
    ORIGIN + '/cdn-cgi/challenge-platform',
    ORIGIN + '/cdn-cgi/challenge-platform-other/h/b',
    ORIGIN + '/cdn-cgi/challenge-platform%2fh/b',
    ORIGIN + '/cdn-cgi/other',
    ORIGIN + '/cdn-cgi/rum',
    ORIGIN + '/api/v1/session/ping',
    ORIGIN + '/api/v1/draft/settings',
    ORIGIN + '/api/graphql',
    'https://ad.doubleclick.net/ccm/s/collect/',
    'https://ad.doubleclick.net/ccm/s/collect-other',
    'https://ad.doubleclick.net/ccm/collect',
    'https://ad.doubleclick.net/other',
    'https://other.doubleclick.net/ccm/s/collect',
    'https://ad.doubleclick.net.example.test/ccm/s/collect',
    'https://ad.doubleclick.net:8443/ccm/s/collect',
    'http://ad.doubleclick.net/ccm/s/collect',
    'https://unknown.example.test/ccm/s/collect',
])
@pytest.mark.parametrize('stage, _report_stage', STAGES)
def test_unrelated_posts_remain_unknown_and_fail_closed_at_every_stage(url, stage, _report_stage):
    observer, guard, emitted = observe(url, 'fetch', stage=stage)
    assert emitted[0]['classification'] == 'UNKNOWN'
    assert not observer.suppressed_telemetry
    with pytest.raises(BrowserSessionError):
        observer.require_clear()
    with pytest.raises(BrowserSessionError):
        guard.require_clear()


@pytest.mark.parametrize('path', ['/api/v1/posts/218388044', '/api/v1/publish/218388044',
                                  '/api/v1/send/218388044', '/api/v1/schedule/218388044'])
def test_publication_like_posts_remain_blocked(path):
    observer, guard, emitted = observe(ORIGIN + path, 'fetch')
    assert emitted[0]['classification'] == 'PUBLICATION-LIKE'
    with pytest.raises(BrowserSessionError):
        observer.require_clear()
    with pytest.raises(BrowserSessionError):
        guard.require_clear()


@pytest.mark.parametrize('url, resource_type', ENDPOINTS)
@pytest.mark.parametrize('method', ['PUT', 'PATCH', 'DELETE'])
def test_new_classifications_apply_only_to_post(url, resource_type, method):
    observer, guard, emitted = observe(url, resource_type, method=method)
    assert emitted[0]['classification'] == 'UNKNOWN'
    with pytest.raises(BrowserSessionError):
        observer.require_clear()


@pytest.mark.parametrize('url, resource_type', ENDPOINTS)
def test_new_suppression_requires_observed_resource_type_and_preclick_stage(url, resource_type):
    for changed_resource, stage in [('document', 'AUTHENTICATION'), (resource_type, 'AFTER_CLICK')]:
        observer, guard, emitted = observe(url, changed_resource, stage=stage)
        assert emitted[0]['classification'] == 'EXPECTED NON-PUBLISHING'
        assert not observer.suppressed_telemetry
        with pytest.raises(BrowserSessionError):
            observer.require_clear()
        with pytest.raises(BrowserSessionError):
            guard.require_clear()


@pytest.mark.parametrize('url, resource_type', ENDPOINTS)
def test_redirected_browser_posts_remain_unknown(url, resource_type):
    observer, guard, emitted = observe(
        url, resource_type, redirected_from=SimpleNamespace(url=ORIGIN + '/api/unknown'),
    )
    assert emitted[0]['classification'] == 'UNKNOWN'
    with pytest.raises(BrowserSessionError):
        observer.require_clear()


@pytest.mark.parametrize('url, resource_type', ENDPOINTS)
def test_browser_classification_never_reads_or_reports_private_request_data(url, resource_type):
    class PrivateRequest:
        method = 'POST'
        redirected_from = None
        initiator = None

        @property
        def headers(self):
            raise AssertionError('Do not inspect headers or cookies')

        @property
        def post_data(self):
            raise AssertionError('Do not inspect request bodies')

    request = PrivateRequest()
    request.url = url + '?token=private-query&operationName=PrivateOperation#private-fragment'
    request.resource_type = resource_type
    emitted = []
    observer = WriteLikeNetworkObserver([], ORIGIN, diagnostic_sink=emitted.append)
    observer.set_stage('AUTHENTICATION')
    assert observer.observe(request) is False
    diagnostic = observer.suppressed_telemetry[0]
    rendered = json.dumps(emitted) + diagnostic.format() + diagnostic.format_suppressed_telemetry()
    assert emitted[0]['classification'] == 'EXPECTED NON-PUBLISHING'
    for private in ('opaque-Challenge-Token', 'private-query', 'PrivateOperation', 'private-fragment',
                    'cookie', 'headers', 'post_data', 'authorization'):
        assert private not in rendered

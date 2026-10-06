"""Read the final Substack publication UI while making publication impossible."""

from dataclasses import dataclass, field, replace as dataclass_replace
import re
from time import monotonic
from urllib.parse import parse_qsl, urlsplit

from playwright.sync_api import Error as PlaywrightError

from ..errors import BrowserSessionError
from .substack import trusted_page


FIREHOSE_HOST = 'cyporter.substack.com'
FIREHOSE_PATH = '/api/v1/firehose/batch'
SENTRY_HOST = 'o350427.ingest.sentry.io'
SENTRY_PATH = '/api/4504244653457408/envelope/'
CLOUDFLARE_RUM_HOST = 'cloudflareinsights.com'
CLOUDFLARE_RUM_PATH = '/cdn-cgi/rum'
GOOGLE_REMARKETING_HOST = 'www.google.com'
GOOGLE_REMARKETING_PATH = '/rmkt/collect/316245675/'
GOOGLE_CCM_PATH = '/ccm/collect'
CLOUDFLARE_CHALLENGE_ORIGIN = 'https://cyporter.substack.com'
CLOUDFLARE_CHALLENGE_PREFIX = '/cdn-cgi/challenge-platform/'
DOUBLECLICK_MEASUREMENT_ORIGIN = 'https://ad.doubleclick.net'
DOUBLECLICK_MEASUREMENT_PATH = '/ccm/s/collect'
WRITER_REFERRAL_HOST = 'cyporter.substack.com'
WRITER_REFERRAL_PATH = '/api/v1/user/writer_referrals/code'
SUPPRESSED_DRY_RUN_EDITOR_MUTATION = 'SUPPRESSED_DRY_RUN_EDITOR_MUTATION'
WRITE_LIKE_METHODS = {'POST', 'PUT', 'PATCH', 'DELETE'}
PRE_CLICK_STAGES = {
    'AUTHENTICATION', 'DRAFT_EDITOR_LOAD', 'CONTINUE_NAVIGATION', 'PUBLISH_SCREEN_LOAD',
    'FINAL_ACTION_DISCOVERY', 'PRE_CLICK_REVALIDATION',
}
REQUEST_REPORT_STAGES = {
    'AUTHENTICATION': 'AUTHENTICATION',
    'DRAFT_EDITOR_LOAD': 'DRAFT_VERIFICATION',
    'CONTINUE_NAVIGATION': 'CONTINUE_NAVIGATION',
    'PUBLISH_SCREEN_LOAD': 'FINAL_CONFIGURATION',
    'FINAL_ACTION_DISCOVERY': 'FINAL_CONFIGURATION',
    'PRE_CLICK_REVALIDATION': 'PRE_CLICK',
}


FINAL_ACTION = re.compile(
    r'^(?:Publish|Send|Schedule)\b.*$', re.I,
)
SAFE_EXIT = re.compile(r'^(?:Back|Cancel|Close)$', re.I)
MUTATING_CONTROL = re.compile(
    r'(?:\bPublish\b|\bSend\b|\bSchedule\b|\bConfirm\b|^Done$)', re.I,
)
SENSITIVE = re.compile(r'(?:[\w.+-]+@[\w.-]+|(?:token|password|secret)\s*[=:])', re.I)


@dataclass(frozen=True)
class ContinueControl:
    locator: object
    role: str
    label: str
    test_id: str
    element_type: str
    context: str


@dataclass(frozen=True)
class ContinueCandidateEvidence:
    tag: str
    role: str
    accessible_name: str
    visible_text: str
    aria_label: str
    title: str
    test_id: str
    element_type: str
    href: str
    visible: bool
    enabled: bool
    in_dialog: bool
    in_editor: bool
    appears_submit: bool
    form_attributes: str
    ancestor_context: str
    nearby_labels: tuple[str, ...]


@dataclass(frozen=True)
class ContinueDiagnostic:
    candidates: tuple[ContinueCandidateEvidence, ...]

    @property
    def visible_enabled_count(self) -> int:
        return sum(item.visible and item.enabled for item in self.candidates)


@dataclass(frozen=True)
class PublicationControl:
    label: str
    role: str
    control_type: str
    value: str
    required: bool
    optional: bool
    final_action: bool
    accessible_name: str = ''
    enabled: bool = True
    selected: bool | None = None
    group: str = 'Other options'
    semantic_attributes: tuple[str, ...] = ()
    mutates_configuration: bool = False
    safe_navigation: bool = False


@dataclass(frozen=True)
class SchedulingControlEvidence:
    state: str
    semantic_element: str
    role: str
    accessible_name: str
    checked_semantics: str
    enabled: bool | None
    group_identity: str
    stable_attributes: tuple[str, ...]
    ancestry: tuple[str, ...]
    delivery_relationship: str
    exact_name_match_count: int
    visible_exact_name_match_count: int
    hidden_exact_name_match_count: int
    related_controls: tuple[str, ...]


@dataclass(frozen=True)
class AttributeEvidence:
    """A DOM attribute value that keeps absence distinct from an empty value."""

    present: bool
    value: str = ''


@dataclass(frozen=True)
class FinalActionCandidate:
    """Raw evidence for one DOM element with the exact final-action name."""

    tag_name: str
    role: str
    accessible_name: str
    visible_text: str
    visible: bool
    enabled: bool
    effective_type: str
    element_type: AttributeEvidence
    id: AttributeEvidence
    class_name: AttributeEvidence
    data_testid: AttributeEvidence
    name: AttributeEvidence
    value: AttributeEvidence
    href: AttributeEvidence
    target: AttributeEvidence
    rel: AttributeEvidence
    form_attribute: AttributeEvidence
    form_owner: str | None
    form_owner_action: AttributeEvidence
    form_owner_method: AttributeEvidence
    form_owner_effective_method: str | None
    nearest_form: str | None
    nearest_form_action: AttributeEvidence
    nearest_form_method: AttributeEvidence
    nearest_form_effective_method: str | None
    formaction: AttributeEvidence
    formmethod: AttributeEvidence
    aria_labelledby: AttributeEvidence
    aria_describedby: AttributeEvidence
    tabindex: AttributeEvidence
    nearest_dialog: str | None
    nearest_stable_testid_ancestor: str | None
    publish_modal_ancestry: str
    publish_modal_ancestor_count: int
    belongs_to_exactly_one_publish_modal: bool


@dataclass(frozen=True)
class FinalActionEvidence:
    """Uncollapsed exact-name matches captured without interacting with the DOM."""

    exact_accessible_name: str
    candidates: tuple[FinalActionCandidate, ...]
    total_matches: int
    visible_matches: int
    hidden_matches: int
    enabled_visible_matches: int
    disabled_visible_matches: int
    publish_modal_matches: int
    multiple_publish_modals: bool
    ambiguity: bool


@dataclass(frozen=True)
class FinalScreenInspection:
    url: str
    title: str
    controls: tuple[PublicationControl, ...]
    final_actions: tuple[str, ...]
    safe_exit: str | None
    exit_behavior: str
    blocked_mutation_methods: tuple[str, ...] = ()
    screen_kind: str = 'page'
    screen_attributes: tuple[str, ...] = ()
    scheduling: SchedulingControlEvidence | None = None
    final_action_evidence: FinalActionEvidence | None = None
    dialog_evidence: tuple[str, ...] = ()
    visible_headings: tuple[str, ...] = ()
    stable_containers: tuple[str, ...] = ()


@dataclass
class MutationGuard:
    """Block mutations until the executor commits a durable, single click intent.

    Arming changes routing policy, never removes the route or response observer.
    """

    blocked_methods: list[str]
    diagnostics: list['WriteLikeRequestDiagnostic'] = None
    stage: str = 'PRE_DRAFT_NAVIGATION'
    observer: object = None
    _final_action_armed: bool = field(default=False, init=False)

    @property
    def strict_blocking(self) -> bool:
        return not self._final_action_armed

    def arm_final_action(self, click_intent) -> None:
        if (self._final_action_armed or self.observer is None
                or not click_intent.final_click_attempted
                or click_intent.final_click_attempt_count < 1
                or not click_intent.needs_reconciliation
                or click_intent.publication_verification_status != 'pending'):
            raise BrowserSessionError('A durable pending click intent is required to arm the final action.')
        self.require_clear()
        self._final_action_armed = True
        self.set_stage('FINAL_ACTION_ARMED')

    def set_stage(self, stage: str) -> None:
        self.stage = stage
        if self.observer is not None:
            self.observer.set_stage(stage)

    def handle(self, route) -> None:
        method = (route.request.method or '').upper()
        if method in WRITE_LIKE_METHODS:
            if not self.strict_blocking:
                self.observer.observe(route.request, blocking=False)
                route.continue_()
                return
            permitted = False
            if self.observer is not None:
                permitted = self.observer.observe(route.request)
                if self.observer.was_suppressed(route.request):
                    route.abort()
                    return
            elif self.diagnostics is not None:
                self.diagnostics.append(WriteLikeRequestDiagnostic.from_request(
                    route.request, self.stage,
                ))
            if permitted:
                route.continue_()
                return
            if method not in self.blocked_methods:
                self.blocked_methods.append(method)
            route.abort()
        else:
            route.continue_()

    def require_clear(self) -> None:
        if self.blocked_methods:
            detail = ''
            if self.diagnostics:
                detail = f' {self.diagnostics[0].format()}'
            raise BrowserSessionError(
                'Unexpected write-like network activity was blocked during '
                f'publication preflight.{detail} No final action was clicked.'
            )
        if self.observer is not None:
            self.observer.require_clear()


@dataclass
class WriteLikeNetworkObserver:
    """Share exact pre-click exceptions and retain post-boundary network evidence."""

    methods: list[str]
    publication_url: str | None = None
    diagnostics: list['WriteLikeRequestDiagnostic'] = None
    suppressed_telemetry: list['WriteLikeRequestDiagnostic'] = None
    suppressed_editor_mutations: list['WriteLikeRequestDiagnostic'] = None
    stage: str = 'PRE_DRAFT_NAVIGATION'
    _requests: dict[int, 'WriteLikeRequestDiagnostic'] = None
    _suppressed_request_ids: set[int] = None
    _request_refs: dict[int, object] = field(default_factory=dict, repr=False)
    diagnostic_sink: object = field(default=None, repr=False)
    post_boundary_diagnostics: list['WriteLikeRequestDiagnostic'] = field(default_factory=list)
    _post_boundary_request_ids: set[int] = field(default_factory=set, repr=False)

    def __post_init__(self):
        if self.diagnostics is None:
            self.diagnostics = []
        if self.suppressed_telemetry is None:
            self.suppressed_telemetry = []
        if self.suppressed_editor_mutations is None:
            self.suppressed_editor_mutations = []
        if self._requests is None:
            self._requests = {}
        if self._suppressed_request_ids is None:
            self._suppressed_request_ids = set()

    def observe(self, request, *, blocking: bool = True) -> bool:
        method = (getattr(request, 'method', '') or '').upper()
        if method not in WRITE_LIKE_METHODS:
            return False
        # Playwright's temporary API wrappers can otherwise be collected and
        # their Python IDs reused, losing diagnostics or inheriting suppression.
        self._request_refs[id(request)] = request
        item = WriteLikeRequestDiagnostic.from_request(request, self.stage)
        if not blocking:
            if id(request) not in self._requests:
                self._requests[id(request)] = item
                self._post_boundary_request_ids.add(id(request))
                self.post_boundary_diagnostics.append(item)
                self._emit(item, None, blocking=False)
            return False
        suppression = self._suppression_kind(item)
        if suppression is not None:
            self._requests[id(request)] = item
            self._suppressed_request_ids.add(id(request))
            if suppression == 'telemetry':
                self.suppressed_telemetry.append(item)
            else:
                self.suppressed_editor_mutations.append(item)
            self._emit(item, suppression)
            return False
        if method not in self.methods:
            self.methods.append(method)
        key = id(request)
        if key not in self._requests:
            self._requests[key] = item
            self.diagnostics.append(item)
        self._emit(item, None)
        return False

    def _emit(self, item, suppression, *, blocking=True):
        if self.diagnostic_sink is not None:
            # Emit before the route callback returns, even if later browser
            # work stalls or raises. Never pass the request object to the sink.
            report = item.safe_report(suppression=suppression)
            if not blocking:
                report['disposition'] = 'OBSERVED_AFTER_IRREVERSIBLE_BOUNDARY'
            self.diagnostic_sink(report)

    def _suppression_kind(self, item) -> str | None:
        if (
            item.http_method == 'PUT'
            and item.host == WRITER_REFERRAL_HOST
            and item.path == WRITER_REFERRAL_PATH
            and item.resource_type == 'fetch'
            and item.timing_stage == 'DRAFT_EDITOR_LOAD'
        ):
            return 'editor_mutation'
        if item.http_method != 'POST' or item.timing_stage not in PRE_CLICK_STAGES:
            return None
        if item.redirect_target is not None:
            return None
        browser_endpoint = item._known_browser_endpoint()
        if browser_endpoint is not None and item.resource_type == browser_endpoint[1]:
            # Exempt only from the unexpected-mutation failure. The route is
            # still aborted, just like every existing suppressed telemetry POST.
            return 'telemetry'
        firehose = (
            item.host == FIREHOSE_HOST
            and item.path == FIREHOSE_PATH
            and item.resource_type == 'ping'
        )
        sentry = (
            item.host == SENTRY_HOST
            and item.path == SENTRY_PATH
            and item.resource_type == 'fetch'
        )
        cloudflare_rum = (
            item.host == CLOUDFLARE_RUM_HOST
            and item.path == CLOUDFLARE_RUM_PATH
            and item.resource_type == 'xhr'
        )
        google_remarketing = (
            item.host == GOOGLE_REMARKETING_HOST
            and item.path == GOOGLE_REMARKETING_PATH
            and item.resource_type == 'fetch'
        )
        google_ccm = (
            item.host == GOOGLE_REMARKETING_HOST
            and item.path == GOOGLE_CCM_PATH
            and item.resource_type == 'fetch'
        )
        return 'telemetry' if (firehose or sentry or cloudflare_rum or google_remarketing or google_ccm) else None

    def observe_response(self, response) -> None:
        request = getattr(response, 'request', None)
        item = self._requests.get(id(request))
        if item is None:
            return
        updated = item.with_response(
            getattr(response, 'status', None), getattr(response, 'url', ''),
        )
        self._requests[id(request)] = updated
        if id(request) in self._post_boundary_request_ids:
            self.post_boundary_diagnostics[self.post_boundary_diagnostics.index(item)] = updated
            return
        if id(request) in self._suppressed_request_ids:
            if updated.redirect_target is not None:
                self._suppressed_request_ids.remove(id(request))
                suppressed = self.suppressed_telemetry
                if item not in suppressed:
                    suppressed = self.suppressed_editor_mutations
                suppressed.remove(item)
                if item.http_method not in self.methods:
                    self.methods.append(item.http_method)
                self.diagnostics.append(updated)
            else:
                suppressed = self.suppressed_telemetry
                if item not in suppressed:
                    suppressed = self.suppressed_editor_mutations
                suppressed[suppressed.index(item)] = updated
            return
        self.diagnostics[self.diagnostics.index(item)] = updated

    def set_stage(self, stage: str) -> None:
        self.stage = stage

    def was_suppressed(self, request) -> bool:
        return id(request) in self._suppressed_request_ids

    @property
    def first_diagnostic(self):
        return self.diagnostics[0] if self.diagnostics else None

    @property
    def first_suppressed_telemetry(self):
        return self.suppressed_telemetry[0] if self.suppressed_telemetry else None

    @property
    def first_suppressed_editor_mutation(self):
        return self.suppressed_editor_mutations[0] if self.suppressed_editor_mutations else None

    def require_clear(self) -> None:
        if self.methods:
            detail = self.first_diagnostic.format() if self.first_diagnostic else 'Metadata unavailable.'
            raise BrowserSessionError(
                'Unexpected write-like network activity occurred during publication preflight. '
                f'{detail} No final action was clicked.'
            )


@dataclass(frozen=True)
class WriteLikeRequestDiagnostic:
    http_method: str
    host: str
    path: str
    query_parameter_names: tuple[str, ...]
    resource_type: str
    initiator_type: str
    timing_stage: str
    response_status: int | None = None
    redirect_target: str | None = None
    operation_name: str | None = None
    origin: str = '[unknown]'

    @classmethod
    def from_request(cls, request, stage: str):
        raw_url = getattr(request, 'url', '') or ''
        parsed = urlsplit(raw_url)
        query = parse_qsl(parsed.query, keep_blank_values=True)
        operation = next((value for name, value in query
                          if name.lower() in {'operationname', 'operation_name'}
                          and re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,80}', value)), None)
        redirected_from = getattr(request, 'redirected_from', None)
        initiator = getattr(request, 'initiator', None)
        initiator_type = getattr(initiator, 'type', '') if initiator is not None else ''
        host = parsed.hostname or '[unknown]'
        if parsed.port is not None:
            host += f':{parsed.port}'
        return cls(
            http_method=(getattr(request, 'method', '') or '').upper(),
            host=host, path=parsed.path or '/',
            query_parameter_names=tuple(sorted({name for name, _ in query})),
            resource_type=getattr(request, 'resource_type', '') or '[unknown]',
            initiator_type=initiator_type or '[unavailable]', timing_stage=stage,
            redirect_target=cls._safe_target(getattr(redirected_from, 'url', '')),
            operation_name=operation,
            origin=f'{parsed.scheme}://{host}' if parsed.scheme in {'http', 'https'} else '[unknown]',
        )

    def with_response(self, status, response_url):
        redirect = self.redirect_target
        safe_response = self._safe_target(response_url)
        if safe_response and safe_response != f'{self.host}{self.path}':
            redirect = safe_response
        return dataclass_replace(self, response_status=status, redirect_target=redirect)

    @staticmethod
    def _safe_target(raw_url):
        if not raw_url:
            return None
        parsed = urlsplit(raw_url)
        return f'{_request_host(raw_url)}{_safe_endpoint_path(parsed.path or "/")}'

    def _known_browser_endpoint(self) -> tuple[str, str] | None:
        """Return the reason and observed transport for two verified endpoints."""
        if self.http_method != 'POST' or self.redirect_target is not None:
            return None
        if (self.origin == CLOUDFLARE_CHALLENGE_ORIGIN
                and self.path.startswith(CLOUDFLARE_CHALLENGE_PREFIX)):
            return 'Known Cloudflare browser challenge verification', 'xhr'
        if (self.origin == DOUBLECLICK_MEASUREMENT_ORIGIN
                and self.path == DOUBLECLICK_MEASUREMENT_PATH):
            return 'Known Google advertising measurement endpoint', 'fetch'
        return None

    def safe_report(self, *, suppression=None):
        """Classification is diagnostic only; it never grants network access."""
        telemetry_endpoints = {
            (FIREHOSE_HOST, FIREHOSE_PATH), (SENTRY_HOST, SENTRY_PATH),
            (CLOUDFLARE_RUM_HOST, CLOUDFLARE_RUM_PATH),
            (GOOGLE_REMARKETING_HOST, GOOGLE_REMARKETING_PATH),
            (GOOGLE_REMARKETING_HOST, GOOGLE_CCM_PATH),
        }
        browser_endpoint = self._known_browser_endpoint()
        if browser_endpoint is not None:
            classification, reason = 'EXPECTED NON-PUBLISHING', browser_endpoint[0]
        elif self.http_method == 'POST' and (self.host, self.path) in telemetry_endpoints:
            classification, reason = 'EXPECTED NON-PUBLISHING', 'Known telemetry endpoint'
        elif (self.http_method == 'PUT'
              and (self.host, self.path) == (WRITER_REFERRAL_HOST, WRITER_REFERRAL_PATH)):
            classification, reason = 'EXPECTED NON-PUBLISHING', 'Known referral-code initialization'
        elif re.search(r'(?:^|[/_-])(?:posts?|publish|send|schedule|commit)(?:[/_-]|$)',
                       self.path, re.I):
            classification, reason = 'PUBLICATION-LIKE', 'Publication or content mutation endpoint'
        else:
            classification, reason = 'UNKNOWN', 'Endpoint has no verified non-publishing classification'
        if self.redirect_target is not None:
            classification, reason = 'UNKNOWN', 'Redirected mutating request requires review'
        return {
            'method': self.http_method, 'origin': self.origin,
            'path': _safe_endpoint_path(self.path), 'resource_type': self.resource_type,
            'stage': REQUEST_REPORT_STAGES.get(self.timing_stage, self.timing_stage),
            'stage_detail': self.timing_stage,
            'relative_to_continue': 'BEFORE' if self.timing_stage in {
                'PRE_DRAFT_NAVIGATION', 'AUTHENTICATION', 'DRAFT_EDITOR_LOAD',
            } else 'DURING_OR_AFTER',
            'classification': classification, 'reason': reason,
            'disposition': 'BLOCKED', 'existing_suppression': suppression,
        }

    def format(self) -> str:
        fields = [
            f'HTTP method: {self.http_method}', f'Host: {self.host}',
            f'Path: {_safe_endpoint_path(self.path)}',
            f'Resource type: {self.resource_type}', f'Initiator type: {self.initiator_type}',
            f'Timing stage: {self.timing_stage}',
            f'Response status: {self.response_status if self.response_status is not None else "unavailable"}',
            f'Redirect target: {self.redirect_target or "none"}',
        ]
        fields.append(f'Classification: {self.safe_report()["classification"]}')
        return ' '.join(fields)

    def format_suppressed_telemetry(self) -> str:
        return '\n'.join([
            'Suppressed telemetry:', f'{self.http_method} {self.host}{_safe_endpoint_path(self.path)}',
            f'resource type: {self.resource_type}',
            f'stage: {self.timing_stage}',
            f'status: {self.response_status if self.response_status is not None else "unavailable"}',
        ])

    def format_suppressed_editor_mutation(self) -> str:
        return '\n'.join([
            'Suppressed editor mutation:', f'{self.http_method} {self.host}{_safe_endpoint_path(self.path)}',
            f'stage: {self.timing_stage}',
            f'classification: {SUPPRESSED_DRY_RUN_EDITOR_MUTATION}',
            'reason: known referral-code initialization mutation',
        ])


def _safe_endpoint_path(path: str) -> str:
    """Keep endpoint names; redact credential/opaque/encoded path components."""
    parts = path.split('/')
    sensitive = {'token', 'access_token', 'refresh_token', 'secret', 'password', 'code'}
    safe = []
    for index, part in enumerate(parts):
        credential = index > 0 and parts[index - 1].lower() in sensitive
        recognizable = (not part or bool(re.fullmatch(r'[a-z][a-z_-]{0,31}|v[0-9]+|[0-9]{1,16}', part)))
        safe.append(part if recognizable and not credential else '[redacted]')
    return '/'.join(safe)


def _request_host(publication_url: str) -> str:
    parsed = urlsplit(publication_url)
    host = parsed.hostname or '[unknown]'
    if parsed.port is not None:
        host += f':{parsed.port}'
    return host


def install_mutation_guard(page, *, observer=None) -> MutationGuard:
    guard = MutationGuard([], observer.diagnostics if observer is not None else None,
                          observer.stage if observer is not None else 'PRE_DRAFT_NAVIGATION', observer)
    page.route('**/*', guard.handle)
    return guard


def _visible_enabled(items) -> list:
    result = []
    for item in items:
        try:
            if item.is_visible() and item.is_enabled():
                result.append(item)
        except (PlaywrightError, AttributeError, TypeError):
            continue
    return result


def inspect_continue_candidates(page) -> ContinueDiagnostic:
    """Read safe DOM semantics for exact accessible-name Continue buttons; never click."""
    candidates = page.get_by_role('button', name='Continue', exact=True).all()
    evidence = []
    for control in candidates:
        try:
            raw = control.evaluate(
                """node => {
                    const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
                    const labelledBy = clean(node.getAttribute('aria-labelledby'));
                    const labelledText = labelledBy ? labelledBy.split(/\\s+/).map(id =>
                        clean(document.getElementById(id)?.innerText ||
                              document.getElementById(id)?.textContent)).filter(Boolean).join(' ') : '';
                    const accessibleName = clean(node.getAttribute('aria-label')) || labelledText ||
                        clean(node.innerText) || clean(node.value) || clean(node.getAttribute('title'));
                    const style = getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    const visible = style.visibility !== 'hidden' && style.display !== 'none' &&
                        (rect.width > 0 || rect.height > 0);
                    const disabled = !!node.disabled || node.getAttribute('aria-disabled') === 'true';
                    const form = node.form || node.closest('form');
                    const safeAttrs = element => {
                        if (!element) return '';
                        const attrs = [
                            'id', 'class', 'role', 'aria-label', 'title', 'data-testid',
                            'name', 'method'
                        ];
                        return element.tagName.toLowerCase() + attrs.map(name => {
                            const value = clean(element.getAttribute(name));
                            return value ? ` ${name}=${JSON.stringify(value)}` : '';
                        }).join('');
                    };
                    const ancestors = [];
                    for (let item = node.parentElement; item && ancestors.length < 8;
                            item = item.parentElement) {
                        ancestors.push(safeAttrs(item));
                    }
                    const nearby = [];
                    let area = node.parentElement;
                    for (let depth = 0; area && depth < 5; depth++, area = area.parentElement) {
                        if (area.querySelectorAll(
                                'button, [role="button"], a[href], h1, h2, h3, label, legend'
                            ).length > 1) break;
                    }
                    if (area) {
                        for (const item of area.querySelectorAll(
                                'h1, h2, h3, label, legend, button, [role="button"], [aria-label]')) {
                            if (item === node) continue;
                            const label = clean(item.getAttribute('aria-label')) || clean(item.innerText);
                            if (label && label.length <= 80 && !nearby.includes(label)) nearby.push(label);
                            if (nearby.length === 8) break;
                        }
                    }
                    const typeAttr = clean(node.getAttribute('type')).toLowerCase();
                    const effectiveType = node.tagName === 'BUTTON' ? (typeAttr || 'submit') : typeAttr;
                    return {
                        tag: node.tagName.toLowerCase(),
                        role: clean(node.getAttribute('role')) ||
                            (node.tagName === 'BUTTON' ? 'button' : ''),
                        accessibleName, text: clean(node.innerText),
                        ariaLabel: clean(node.getAttribute('aria-label')),
                        title: clean(node.getAttribute('title')),
                        testId: clean(node.getAttribute('data-testid')),
                        type: typeAttr, href: clean(node.getAttribute('href')),
                        visible, enabled: !disabled,
                        inDialog: !!node.closest('[role="dialog"]'),
                        inEditor: !!node.closest('.editor.newsletter-post-editor'),
                        appearsSubmit: effectiveType === 'submit' || !!node.getAttribute('formaction'),
                        form: safeAttrs(form), ancestors, nearby
                    };
                }"""
            )
        except (PlaywrightError, AttributeError, TypeError):
            continue
        safe_href = ''
        raw_href = str(raw.get('href') or '')
        if raw_href and not re.match(r'^(?:javascript:|data:)', raw_href, re.I):
            parsed = urlsplit(raw_href)
            safe_href = parsed.path or raw_href if not parsed.query and not parsed.fragment else parsed.path
        evidence.append(ContinueCandidateEvidence(
            tag=_safe_text(raw.get('tag'), '[unknown]'),
            role=_safe_text(raw.get('role'), '[none]'),
            accessible_name=_safe_text(raw.get('accessibleName'), '[empty]'),
            visible_text=_safe_text(raw.get('text'), '[empty]'),
            aria_label=_safe_text(raw.get('ariaLabel'), '[none]'),
            title=_safe_text(raw.get('title'), '[none]'),
            test_id=_safe_text(raw.get('testId'), '[none]'),
            element_type=_safe_text(raw.get('type'), '[not set]'),
            href=_safe_text(safe_href, '[none]'),
            visible=bool(raw.get('visible')), enabled=bool(raw.get('enabled')),
            in_dialog=bool(raw.get('inDialog')), in_editor=bool(raw.get('inEditor')),
            appears_submit=bool(raw.get('appearsSubmit')),
            form_attributes=_safe_text(raw.get('form'), '[none]'),
            ancestor_context=' > '.join(
                _safe_text(item) for item in raw.get('ancestors', []) if _safe_text(item)
            ) or '[none]',
            nearby_labels=tuple(
                _safe_text(item) for item in raw.get('nearby', []) if _safe_text(item)
            ),
        ))
    return ContinueDiagnostic(tuple(evidence))


def select_continue_control(page) -> ContinueControl:
    """Require the exact observed editor navigation control and reject lookalikes."""
    candidates = _visible_enabled(
        page.get_by_role('button', name='Continue', exact=True).all()
    )
    if len(candidates) != 1:
        raise BrowserSessionError(
            'The editor-scoped Continue control was missing or ambiguous; it was not clicked.'
        )
    control = candidates[0]
    try:
        evidence = control.evaluate(
            """node => ({
                tag: node.tagName.toLowerCase(),
                type: (node.getAttribute('type') || '').toLowerCase(),
                testId: node.getAttribute('data-testid') || '',
                inDialog: !!node.closest('[role="dialog"]'),
                inEditor: !!node.closest('.editor.newsletter-post-editor'),
                inForm: !!(node.form || node.closest('form')),
                formAction: node.getAttribute('formaction') || '',
                href: node.getAttribute('href') || '',
                text: (node.innerText || node.getAttribute('aria-label') || '').trim()
            })"""
        )
    except (PlaywrightError, AttributeError, TypeError):
        raise BrowserSessionError(
            'Continue control semantics could not be verified; it was not clicked.'
        ) from None
    exact_text = evidence.get('text') == 'Continue'
    navigation_semantics = (
        evidence.get('tag') == 'button'
        and evidence.get('type') == 'button'
        and evidence.get('testId') == 'publish-button'
        and not evidence.get('formAction')
        and not evidence.get('href')
        and not evidence.get('inForm')
        and bool(evidence.get('inEditor'))
    )
    if evidence.get('inDialog') or not exact_text or not navigation_semantics:
        raise BrowserSessionError(
            'Continue did not have unambiguous editor-navigation semantics; it was not clicked.'
        )
    return ContinueControl(
        control, 'button', 'Continue', 'publish-button',
        'button', 'div.editor.newsletter-post-editor',
    )


def _safe_text(value: object, fallback: str = '') -> str:
    text = ' '.join(str(value or '').split())[:240]
    if not text:
        return fallback
    if SENSITIVE.search(text):
        return '[sensitive value redacted]'
    return text


def _legacy_raw_controls(scope) -> list[dict]:
    """Collect visible semantic controls and labels from the bounded final-screen scope."""
    return scope.evaluate(
        """root => {
            const visible = node => {
                const s = getComputedStyle(node);
                const r = node.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none' &&
                    (r.width > 0 || r.height > 0);
            };
            const roleFor = node => node.getAttribute('role') || ({
                BUTTON: 'button', A: 'link', INPUT: 'input', SELECT: 'combobox',
                TEXTAREA: 'textbox', LABEL: 'label', LEGEND: 'group-label',
                H1: 'heading', H2: 'heading', H3: 'heading', H4: 'heading'
            }[node.tagName] || 'text');
            const labelFor = node => {
                if (node.getAttribute('aria-label')) return node.getAttribute('aria-label');
                if (node.labels?.length) return [...node.labels].map(x => x.innerText).join(' ');
                if (node.getAttribute('aria-labelledby')) {
                    return node.getAttribute('aria-labelledby').split(/\\s+/)
                        .map(id => document.getElementById(id)?.innerText || '').join(' ');
                }
                return node.innerText || node.getAttribute('placeholder') || node.name || '';
            };
            const selector = 'button, a[href], input, select, textarea, label, legend, '
                + 'h1, h2, h3, h4, [role="button"], [role="radio"], '
                + '[role="checkbox"], [role="switch"], [role="combobox"]';
            return [...root.querySelectorAll(selector)].filter(visible).map(node => {
                const tag = node.tagName.toLowerCase();
                const type = (node.getAttribute('type') || tag).toLowerCase();
                let value = '';
                if (type === 'checkbox' || type === 'radio' ||
                        node.getAttribute('role') === 'checkbox' ||
                        node.getAttribute('role') === 'radio' ||
                        node.getAttribute('role') === 'switch') {
                    const checked = node.checked ?? node.getAttribute('aria-checked') === 'true';
                    value = checked ? 'Selected' : 'Not selected';
                } else if (tag === 'select') {
                    value = node.selectedOptions?.[0]?.text || '';
                } else if (tag === 'input' || tag === 'textarea') {
                    value = ['password', 'hidden', 'file'].includes(type) ? '' : node.value;
                }
                return {
                    label: labelFor(node), role: roleFor(node), type, value,
                    required: !!node.required || node.getAttribute('aria-required') === 'true',
                    disabled: !!node.disabled || node.getAttribute('aria-disabled') === 'true'
                };
            });
        }"""
    )


def _legacy_publication_controls(scope) -> tuple[PublicationControl, ...]:
    by_label = {}
    for raw in _legacy_raw_controls(scope):
        label = _safe_text(raw.get('label'))
        if not label:
            continue
        role = _safe_text(raw.get('role'), 'unknown')
        final = role in {'button', 'link'} and bool(FINAL_ACTION.fullmatch(label))
        required = bool(raw.get('required'))
        candidate = PublicationControl(
            label=label,
            role=role,
            control_type=_safe_text(raw.get('type'), 'unknown'),
            value=_safe_text(raw.get('value'), 'None'),
            required=required,
            optional=not required and not final,
            final_action=final,
        )
        existing = by_label.get(label)
        static_roles = {'label', 'group-label', 'heading', 'text'}
        if existing is None or (existing.role in static_roles and candidate.role not in static_roles):
            by_label[label] = candidate
    return tuple(by_label.values())


# Keeping the extraction in one DOM evaluation avoids opening dropdowns or focusing fields.
def _raw_publication_controls(scope) -> list[dict]:
    return scope.evaluate(
        """root => {
            const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
            const visible = node => {
                const s = getComputedStyle(node), r = node.getBoundingClientRect();
                return s.visibility !== 'hidden' && s.display !== 'none' && s.opacity !== '0' &&
                    (r.width > 0 || r.height > 0);
            };
            const labelledText = node => {
                const ids = clean(node.getAttribute('aria-labelledby'));
                return ids ? ids.split(/\\s+/).map(id =>
                    clean(document.getElementById(id)?.innerText)).filter(Boolean).join(' ') : '';
            };
            const roleFor = node => node.getAttribute('role') || ({
                BUTTON: 'button', A: 'link',
                INPUT: ({checkbox: 'checkbox', radio: 'radio', range: 'slider'}[
                    (node.getAttribute('type') || 'text').toLowerCase()] || 'textbox'),
                SELECT: 'combobox', TEXTAREA: 'textbox'
            }[node.tagName] || 'unknown');
            const labels = node => clean(node.labels?.length ?
                [...node.labels].map(item => item.innerText).join(' ') : '');
            const accessibleName = node => clean(node.getAttribute('aria-label')) ||
                clean(labelledText(node)) || labels(node) || clean(node.innerText) ||
                clean(node.getAttribute('placeholder')) || clean(node.name);
            const visibleLabel = node => labels(node) || clean(node.innerText) ||
                clean(labelledText(node)) || clean(node.getAttribute('aria-label')) ||
                clean(node.getAttribute('placeholder')) || clean(node.name);
            const groupName = node => {
                const owner = node.closest('fieldset, [role="group"], [role="radiogroup"], section');
                if (owner) {
                    const named = clean(owner.getAttribute('aria-label')) || clean(labelledText(owner));
                    if (named) return named;
                    const heading = owner.querySelector(':scope > legend, :scope > h1, :scope > h2, '
                        + ':scope > h3, :scope > h4');
                    if (heading && !heading.contains(node)) return clean(heading.innerText);
                }
                let sibling = node.closest('div, li')?.previousElementSibling;
                while (sibling) {
                    if (/^H[1-4]$/.test(sibling.tagName)) return clean(sibling.innerText);
                    sibling = sibling.previousElementSibling;
                }
                return '';
            };
            const selector = 'button, a[href], input:not([type="hidden"]), select, textarea, '
                + '[role="button"], [role="radio"], [role="checkbox"], [role="switch"], '
                + '[role="combobox"], [role="textbox"], [role="slider"], [role="spinbutton"]';
            return [...root.querySelectorAll(selector)].filter(visible).map(node => {
                const tag = node.tagName.toLowerCase();
                const type = (node.getAttribute('type') || tag).toLowerCase();
                const role = roleFor(node);
                const checked = node.checked !== undefined ? !!node.checked :
                    node.getAttribute('aria-checked') === 'true' ? true :
                    node.getAttribute('aria-checked') === 'false' ? false : null;
                const selected = node.getAttribute('aria-selected') === 'true' ? true :
                    node.getAttribute('aria-selected') === 'false' ? false : checked;
                let value = '';
                if (['checkbox', 'radio', 'switch'].includes(role)) {
                    value = selected ? 'Selected' : 'Not selected';
                } else if (tag === 'select') {
                    value = node.selectedOptions?.[0]?.text || '';
                } else if (tag === 'input' || tag === 'textarea') {
                    value = ['password', 'hidden', 'file'].includes(type) ? '' : node.value;
                } else if (role === 'combobox') {
                    value = node.querySelector('[aria-selected="true"]')?.innerText ||
                        node.getAttribute('aria-valuetext') || node.getAttribute('data-value') || '';
                } else if (node.getAttribute('aria-valuetext')) {
                    value = node.getAttribute('aria-valuetext');
                } else if (node.getAttribute('aria-pressed') !== null) {
                    value = node.getAttribute('aria-pressed') === 'true' ? 'Pressed' : 'Not pressed';
                } else if (node.getAttribute('aria-expanded') !== null) {
                    value = node.getAttribute('aria-expanded') === 'true' ? 'Expanded' : 'Collapsed';
                }
                const attributes = [];
                for (const name of ['data-testid', 'name', 'type', 'role', 'aria-checked',
                        'aria-selected', 'aria-pressed', 'aria-expanded', 'aria-required']) {
                    const attribute = clean(node.getAttribute(name));
                    if (attribute) attributes.push(`${name}=${attribute}`);
                }
                const form = node.form || node.closest('form');
                return {
                    label: visibleLabel(node), accessibleName: accessibleName(node), role, type,
                    value, selected, group: groupName(node), attributes,
                    required: !!node.required || node.getAttribute('aria-required') === 'true',
                    disabled: !!node.disabled || node.getAttribute('aria-disabled') === 'true',
                    inForm: !!form, formAction: clean(node.getAttribute('formaction')),
                    href: clean(node.getAttribute('href'))
                };
            });
        }"""
    )


def _publication_controls(scope) -> tuple[PublicationControl, ...]:
    controls = []
    seen = set()
    for raw in _raw_publication_controls(scope):
        label = _safe_text(raw.get('label'))
        accessible_name = _safe_text(raw.get('accessibleName'))
        if not label and not accessible_name:
            continue
        label = label or accessible_name
        accessible_name = accessible_name or label
        role = _safe_text(raw.get('role'), 'unknown')
        final = role in {'button', 'link'} and bool(FINAL_ACTION.fullmatch(accessible_name))
        required = bool(raw.get('required'))
        safe_navigation = (
            role in {'button', 'link'} and bool(SAFE_EXIT.fullmatch(accessible_name))
            and not raw.get('inForm') and not raw.get('formAction') and not raw.get('href')
            and _safe_text(raw.get('type'), 'button') == 'button'
        )
        mutates = (
            final or role == 'button' or bool(MUTATING_CONTROL.search(accessible_name)) or
            role in {'checkbox', 'radio', 'switch', 'combobox', 'textbox', 'slider', 'spinbutton'}
        ) and not safe_navigation
        attributes = tuple(
            _safe_text(item) for item in raw.get('attributes', ()) if _safe_text(item)
        )
        raw_group = _safe_text(raw.get('group'))
        name_attribute = next(
            (item.removeprefix('name=') for item in attributes if item.startswith('name=')), ''
        )
        semantic_group = {
            'audience': 'Audience',
            'commentLevel': 'Comments',
            'scheduled-at': 'Scheduling',
        }.get(name_attribute)
        if semantic_group is None:
            semantic_group = (
                'Delivery' if re.search(r'email|Substack app', accessible_name, re.I) else
                'Tags' if re.search(r'\btags?\b', accessible_name, re.I) else
                'Social preview' if re.search(r'\bSocial preview\b', accessible_name, re.I) else
                'Other options'
            )
        candidate = PublicationControl(
            label=label, accessible_name=accessible_name, role=role,
            control_type=_safe_text(raw.get('type'), 'unknown'),
            value=_safe_text(raw.get('value'), 'None'), required=required,
            optional=not required and not final, final_action=final,
            enabled=not bool(raw.get('disabled')),
            selected=raw.get('selected') if isinstance(raw.get('selected'), bool) else None,
            group=raw_group or semantic_group,
            semantic_attributes=attributes,
            mutates_configuration=mutates, safe_navigation=safe_navigation,
        )
        identity = (
            candidate.accessible_name, candidate.role, candidate.control_type,
            candidate.group, candidate.semantic_attributes,
        )
        if identity not in seen:
            controls.append(candidate)
            seen.add(identity)
    return tuple(controls)


def _screen_evidence(scope, scope_kind: str) -> tuple[str, tuple[str, ...]]:
    raw = scope.evaluate(
        """root => {
            const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
            const ids = clean(root.getAttribute?.('aria-labelledby'));
            const labelled = ids ? ids.split(/\\s+/).map(id =>
                clean(document.getElementById(id)?.innerText)).filter(Boolean).join(' ') : '';
            const heading = [...root.querySelectorAll('h1, h2, h3, [role="heading"]')]
                .find(node => {
                    const s = getComputedStyle(node), r = node.getBoundingClientRect();
                    return s.visibility !== 'hidden' && s.display !== 'none' &&
                        (r.width > 0 || r.height > 0);
                });
            const attributes = [];
            for (const name of ['role', 'data-testid', 'aria-label', 'aria-labelledby']) {
                const value = clean(root.getAttribute?.(name));
                if (value) attributes.push(`${name}=${value}`);
            }
            return {
                title: clean(root.getAttribute?.('aria-label')) || labelled ||
                    clean(heading?.innerText), attributes
            };
        }"""
    )
    attributes = (f'scope={scope_kind}', *(
        _safe_text(item) for item in raw.get('attributes', ()) if _safe_text(item)
    ))
    return _safe_text(raw.get('title')), tuple(attributes)


def _screen_identity(page) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """Read visible dialog, heading, and stable-container identity without interaction."""
    try:
        raw = page.evaluate(
        """() => {
            const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
            const visible = node => {
                const style = getComputedStyle(node), rect = node.getBoundingClientRect();
                return !node.hidden && style.visibility !== 'hidden' && style.display !== 'none' &&
                    style.opacity !== '0' && (rect.width > 0 || rect.height > 0);
            };
            const name = node => clean(node.getAttribute('aria-label')) ||
                clean(node.getAttribute('aria-labelledby')?.split(/\\s+/).map(id =>
                    document.getElementById(id)?.innerText || '').join(' ')) ||
                clean(node.innerText || node.textContent);
            const signature = node => {
                const attrs = [];
                for (const key of ['role', 'data-testid', 'aria-label', 'aria-labelledby']) {
                    if (node.hasAttribute(key)) attrs.push(`${key}=${clean(node.getAttribute(key))}`);
                }
                const classes = [...node.classList].filter(item => item.length <= 80).slice(0, 8);
                if (classes.length) attrs.push(`class=${classes.join(' ')}`);
                return node.tagName.toLowerCase() + (attrs.length ? '[' + attrs.join(', ') + ']' : '');
            };
            const dialogs = [...document.querySelectorAll('[role="dialog"]')].filter(visible)
                .map(node => `${signature(node)}; name=${JSON.stringify(name(node))}`);
            const headings = [...document.querySelectorAll('h1, h2, h3, h4, [role="heading"]')]
                .filter(visible).map(name).filter(Boolean).slice(0, 32);
            const containers = [...document.querySelectorAll('[data-testid], [role="dialog"]')]
                .filter(visible).map(signature).filter((item, index, all) => all.indexOf(item) === index)
                .slice(0, 32);
            return {dialogs, headings, containers};
        }"""
        )
    except (PlaywrightError, AttributeError, TypeError, ValueError):
        return (), (), ()
    return (
        tuple(_safe_text(item) for item in raw.get('dialogs', ()) if _safe_text(item)),
        tuple(_safe_text(item) for item in raw.get('headings', ()) if _safe_text(item)),
        tuple(_safe_text(item) for item in raw.get('containers', ()) if _safe_text(item)),
    )


def _scheduling_evidence(scope) -> SchedulingControlEvidence:
    """Capture the scheduling checkbox's live DOM semantics without interacting with it."""
    raw = scope.evaluate(
        """root => {
            const scheduleName = 'Schedule time to email and publish';
            const deliveryName = 'Send via email and the Substack app';
            const clean = value => (value || '').replace(/\\s+/g, ' ').trim();
            const labelledText = node => {
                const ids = clean(node.getAttribute('aria-labelledby'));
                return ids ? ids.split(/\\s+/).map(id =>
                    clean(document.getElementById(id)?.innerText ||
                          document.getElementById(id)?.textContent)).filter(Boolean).join(' ') : '';
            };
            const labels = node => clean(node.labels?.length ?
                [...node.labels].map(item => item.innerText || item.textContent).join(' ') : '');
            const accessibleName = node => clean(node.getAttribute('aria-label')) ||
                labelledText(node) || labels(node) || clean(node.innerText) ||
                clean(node.getAttribute('placeholder')) || clean(node.name);
            const visible = node => {
                const style = getComputedStyle(node), rect = node.getBoundingClientRect();
                return style.visibility !== 'hidden' && style.display !== 'none' &&
                    style.opacity !== '0' && (rect.width > 0 || rect.height > 0);
            };
            const roleFor = node => node.getAttribute('role') || ({
                BUTTON: 'button',
                INPUT: ({checkbox: 'checkbox', radio: 'radio'}[
                    (node.getAttribute('type') || 'text').toLowerCase()] || 'textbox')
            }[node.tagName] || 'unknown');
            const stable = node => {
                const result = [];
                for (const name of ['id', 'data-testid', 'name', 'type', 'role', 'aria-label',
                        'aria-labelledby', 'aria-checked', 'aria-selected', 'aria-pressed',
                        'aria-disabled', 'disabled']) {
                    const value = clean(node.getAttribute(name));
                    if (value || node.hasAttribute(name)) result.push(`${name}=${value}`);
                }
                return result;
            };
            const signature = node => {
                if (!node) return '[none]';
                const attrs = stable(node);
                return node.tagName.toLowerCase() + (attrs.length ? '[' + attrs.join(', ') + ']' : '');
            };
            const describe = node => {
                const checked = node.checked !== undefined ? !!node.checked :
                    node.getAttribute('aria-checked') === 'true' ? true :
                    node.getAttribute('aria-checked') === 'false' ? false : null;
                const disabled = !!node.disabled || node.getAttribute('aria-disabled') === 'true';
                return `${signature(node)}; name=${JSON.stringify(accessibleName(node))}; ` +
                    `visible=${visible(node)}; enabled=${!disabled}; checked=${checked}`;
            };
            const controls = [...root.querySelectorAll(
                'button, input, [role="checkbox"], [role="radio"], [role="switch"]'
            )];
            const matches = controls.filter(node => accessibleName(node) === scheduleName);
            const visibleMatches = matches.filter(visible);
            const target = visibleMatches.length === 1 ? visibleMatches[0] : null;
            const deliveryMatches = controls.filter(node => accessibleName(node) === deliveryName);
            const visibleDelivery = deliveryMatches.filter(visible);
            const delivery = visibleDelivery.length === 1 ? visibleDelivery[0] : null;
            const native = [...root.querySelectorAll('[name="scheduled-at"]')];
            const ancestors = [];
            for (let node = target?.parentElement; node && node !== root && ancestors.length < 8;
                    node = node.parentElement) ancestors.push(signature(node));
            if (target && root.contains(target)) ancestors.push(signature(root));
            let common = null, targetDistance = null, deliveryDistance = null;
            if (target && delivery) {
                const targetAncestors = [];
                for (let node = target; node; node = node.parentElement) targetAncestors.push(node);
                for (let node = delivery, distance = 0; node; node = node.parentElement, distance++) {
                    const found = targetAncestors.indexOf(node);
                    if (found >= 0) {
                        common = node; targetDistance = found; deliveryDistance = distance; break;
                    }
                }
            }
            const checked = target?.checked !== undefined ? !!target.checked :
                target?.getAttribute('aria-checked') === 'true' ? true :
                target?.getAttribute('aria-checked') === 'false' ? false : null;
            const disabled = target ?
                (!!target.disabled || target.getAttribute('aria-disabled') === 'true') : null;
            return {
                target: target ? {
                    element: target.tagName.toLowerCase(), role: roleFor(target),
                    name: accessibleName(target), checked, enabled: !disabled,
                    attributes: stable(target), ancestors
                } : null,
                matchCount: matches.length,
                visibleMatchCount: visibleMatches.length,
                hiddenMatchCount: matches.length - visibleMatches.length,
                native: native.map(describe),
                delivery: delivery ? describe(delivery) : '[not uniquely identified]',
                relationship: common ?
                    `same ${signature(common)} ancestor; scheduling distance=${targetDistance}; ` +
                    `delivery distance=${deliveryDistance}; scheduling follows delivery=${
                        !!(delivery.compareDocumentPosition(target) & Node.DOCUMENT_POSITION_FOLLOWING)}` :
                    '[no unique common relationship]',
                deliveryMatchCount: deliveryMatches.length
            };
        }"""
    )
    target = raw.get('target') or {}
    checked = target.get('checked')
    unique = (
        raw.get('matchCount') == 1 and raw.get('visibleMatchCount') == 1
        and raw.get('hiddenMatchCount') == 0
    )
    semantic_identity = (
        target.get('element') == 'button'
        and target.get('role') == 'checkbox'
        and target.get('name') == 'Schedule time to email and publish'
        and 'data-testid=scheduled-at' in target.get('attributes', [])
    )
    state = 'OFF' if unique and semantic_identity and checked is False else (
        'ON' if unique and semantic_identity and checked is True else 'UNKNOWN'
    )
    return SchedulingControlEvidence(
        state=state,
        semantic_element=_safe_text(target.get('element'), '[not uniquely identified]'),
        role=_safe_text(target.get('role'), '[unknown]'),
        accessible_name=_safe_text(target.get('name'), '[unknown]'),
        checked_semantics=(
            'false' if checked is False else 'true' if checked is True else 'unknown'
        ),
        enabled=target.get('enabled') if isinstance(target.get('enabled'), bool) else None,
        group_identity=(
            'No semantic group element exposed; logical Delivery sibling set'
            if raw.get('deliveryMatchCount') == 1 else 'Unknown'
        ),
        stable_attributes=tuple(
            _safe_text(item) for item in target.get('attributes', []) if _safe_text(item)
        ),
        ancestry=tuple(
            _safe_text(item) for item in target.get('ancestors', []) if _safe_text(item)
        ),
        delivery_relationship=_safe_text(raw.get('relationship'), '[unknown]'),
        exact_name_match_count=int(raw.get('matchCount') or 0),
        visible_exact_name_match_count=int(raw.get('visibleMatchCount') or 0),
        hidden_exact_name_match_count=int(raw.get('hiddenMatchCount') or 0),
        related_controls=tuple(
            [_safe_text(raw.get('delivery'), '[unknown]')]
            + [_safe_text(item) for item in raw.get('native', []) if _safe_text(item)]
        ),
    )


def _attribute_evidence(raw: object) -> AttributeEvidence:
    if not isinstance(raw, dict):
        return AttributeEvidence(False)
    value = raw.get('value')
    exact_value = '' if value is None else str(value)[:1000]
    if SENSITIVE.search(exact_value):
        exact_value = '[sensitive value redacted]'
    return AttributeEvidence(bool(raw.get('present')), exact_value)


def inspect_final_action_evidence(page) -> FinalActionEvidence:
    """Capture every exact-name final action without focusing or changing any element."""
    raw = page.evaluate(
        """exactName => {
            const clean = value => String(value ?? '').replace(/\\s+/g, ' ').trim();
            const attr = (node, name) => ({
                present: !!node && node.hasAttribute(name),
                value: node && node.hasAttribute(name) ? node.getAttribute(name) : ''
            });
            const labelledText = node => {
                const ids = clean(node.getAttribute('aria-labelledby'));
                return ids ? ids.split(/\\s+/).map(id => {
                    const label = document.getElementById(id);
                    return clean(label?.innerText || label?.textContent);
                }).filter(Boolean).join(' ') : '';
            };
            const labels = node => clean(node.labels?.length ?
                [...node.labels].map(label => label.innerText || label.textContent).join(' ') : '');
            const accessibleName = node => clean(node.getAttribute('aria-label')) ||
                labelledText(node) || labels(node) || clean(node.innerText || node.textContent) ||
                clean(node.getAttribute('alt')) || clean(node.getAttribute('title')) ||
                clean(node.getAttribute('placeholder')) || clean(node.getAttribute('name'));
            const roleFor = node => clean(node.getAttribute('role')) || ({
                BUTTON: 'button', A: node.hasAttribute('href') ? 'link' : '',
                INPUT: ({button: 'button', submit: 'button', reset: 'button', image: 'button',
                    checkbox: 'checkbox', radio: 'radio', range: 'slider'}[
                    clean(node.getAttribute('type') || 'text').toLowerCase()] || 'textbox'),
                SELECT: 'combobox', TEXTAREA: 'textbox'
            }[node.tagName] || '');
            const visible = node => {
                const style = getComputedStyle(node), rect = node.getBoundingClientRect();
                return !node.hidden && style.visibility !== 'hidden' && style.display !== 'none' &&
                    style.opacity !== '0' && (rect.width > 0 || rect.height > 0);
            };
            const signature = node => {
                if (!node) return null;
                const bits = [node.tagName.toLowerCase()];
                for (const name of ['id', 'data-testid', 'name', 'role']) {
                    if (node.hasAttribute(name)) {
                        bits.push(`${name}=${JSON.stringify(node.getAttribute(name))}`);
                    }
                }
                return bits.join(' ');
            };
            const nearestTestIdAncestor = node => {
                for (let item = node.parentElement; item; item = item.parentElement) {
                    if (item.hasAttribute('data-testid')) return signature(item);
                }
                return null;
            };
            const selector = 'button, a, input, select, textarea, [role], [contenteditable="true"]';
            const nodes = [...document.querySelectorAll(selector)]
                .filter(node => accessibleName(node) === exactName);
            const publishModals = [...document.querySelectorAll(
                'div[role="dialog"][data-testid="publish-modal"]'
            )];
            const candidates = nodes.map(node => {
                const owner = node.form || null;
                const nearestForm = node.closest('form');
                const nearestDialog = node.closest('[role="dialog"]');
                const modalAncestors = publishModals.filter(modal => modal.contains(node));
                const closestModal = node.closest('div[role="dialog"][data-testid="publish-modal"]');
                const relation = !closestModal ? 'absent' :
                    node.parentElement === closestModal ? 'direct' : 'indirect';
                const typeAttribute = attr(node, 'type');
                const effectiveType = node.tagName === 'BUTTON' ?
                    clean(typeAttribute.present ? typeAttribute.value : 'submit').toLowerCase() :
                    clean(typeAttribute.value).toLowerCase();
                const disabled = node.matches(':disabled') ||
                    node.getAttribute('aria-disabled') === 'true';
                const method = attr(nearestForm, 'method');
                const ownerMethod = attr(owner, 'method');
                return {
                    tagName: node.tagName.toLowerCase(), role: roleFor(node),
                    accessibleName: accessibleName(node), visibleText: clean(node.innerText),
                    visible: visible(node), enabled: !disabled, effectiveType,
                    type: typeAttribute, id: attr(node, 'id'), className: attr(node, 'class'),
                    dataTestid: attr(node, 'data-testid'), name: attr(node, 'name'),
                    value: attr(node, 'value'), href: attr(node, 'href'),
                    target: attr(node, 'target'), rel: attr(node, 'rel'),
                    formAttribute: attr(node, 'form'), formOwner: signature(owner),
                    formOwnerAction: attr(owner, 'action'), formOwnerMethod: ownerMethod,
                    formOwnerEffectiveMethod: owner ? clean(
                        ownerMethod.present && ownerMethod.value ? ownerMethod.value : 'get'
                    ).toLowerCase() : null,
                    nearestForm: signature(nearestForm),
                    nearestFormAction: attr(nearestForm, 'action'),
                    nearestFormMethod: method,
                    nearestFormEffectiveMethod: nearestForm ?
                        clean(method.present && method.value ? method.value : 'get').toLowerCase() : null,
                    formaction: attr(node, 'formaction'), formmethod: attr(node, 'formmethod'),
                    ariaLabelledby: attr(node, 'aria-labelledby'),
                    ariaDescribedby: attr(node, 'aria-describedby'),
                    tabindex: attr(node, 'tabindex'), nearestDialog: signature(nearestDialog),
                    nearestStableTestidAncestor: nearestTestIdAncestor(node),
                    publishModalAncestry: relation,
                    publishModalAncestorCount: modalAncestors.length,
                    belongsToExactlyOnePublishModal: modalAncestors.length === 1
                };
            });
            return {candidates, publishModalMatches: publishModals.length};
        }""",
        'Send to everyone now',
    )
    candidates = tuple(
        FinalActionCandidate(
            tag_name=_safe_text(item.get('tagName'), '[unknown]'),
            role=_safe_text(item.get('role'), '[none]'),
            accessible_name=_safe_text(item.get('accessibleName'), '[empty]'),
            visible_text=_safe_text(item.get('visibleText'), '[empty]'),
            visible=bool(item.get('visible')), enabled=bool(item.get('enabled')),
            effective_type=_safe_text(item.get('effectiveType'), '[none]'),
            element_type=_attribute_evidence(item.get('type')),
            id=_attribute_evidence(item.get('id')),
            class_name=_attribute_evidence(item.get('className')),
            data_testid=_attribute_evidence(item.get('dataTestid')),
            name=_attribute_evidence(item.get('name')),
            value=_attribute_evidence(item.get('value')),
            href=_attribute_evidence(item.get('href')),
            target=_attribute_evidence(item.get('target')),
            rel=_attribute_evidence(item.get('rel')),
            form_attribute=_attribute_evidence(item.get('formAttribute')),
            form_owner=_safe_text(item.get('formOwner')) or None,
            form_owner_action=_attribute_evidence(item.get('formOwnerAction')),
            form_owner_method=_attribute_evidence(item.get('formOwnerMethod')),
            form_owner_effective_method=(
                _safe_text(item.get('formOwnerEffectiveMethod')) or None
            ),
            nearest_form=_safe_text(item.get('nearestForm')) or None,
            nearest_form_action=_attribute_evidence(item.get('nearestFormAction')),
            nearest_form_method=_attribute_evidence(item.get('nearestFormMethod')),
            nearest_form_effective_method=(
                _safe_text(item.get('nearestFormEffectiveMethod')) or None
            ),
            formaction=_attribute_evidence(item.get('formaction')),
            formmethod=_attribute_evidence(item.get('formmethod')),
            aria_labelledby=_attribute_evidence(item.get('ariaLabelledby')),
            aria_describedby=_attribute_evidence(item.get('ariaDescribedby')),
            tabindex=_attribute_evidence(item.get('tabindex')),
            nearest_dialog=_safe_text(item.get('nearestDialog')) or None,
            nearest_stable_testid_ancestor=(
                _safe_text(item.get('nearestStableTestidAncestor')) or None
            ),
            publish_modal_ancestry=_safe_text(item.get('publishModalAncestry'), 'absent'),
            publish_modal_ancestor_count=int(item.get('publishModalAncestorCount') or 0),
            belongs_to_exactly_one_publish_modal=bool(
                item.get('belongsToExactlyOnePublishModal')
            ),
        )
        for item in raw.get('candidates', ())
    )
    visible_matches = sum(item.visible for item in candidates)
    enabled_visible_matches = sum(item.visible and item.enabled for item in candidates)
    publish_modal_matches = int(raw.get('publishModalMatches') or 0)
    return FinalActionEvidence(
        exact_accessible_name='Send to everyone now', candidates=candidates,
        total_matches=len(candidates), visible_matches=visible_matches,
        hidden_matches=len(candidates) - visible_matches,
        enabled_visible_matches=enabled_visible_matches,
        disabled_visible_matches=sum(item.visible and not item.enabled for item in candidates),
        publish_modal_matches=publish_modal_matches,
        multiple_publish_modals=publish_modal_matches > 1,
        ambiguity=(len(candidates) != 1 or visible_matches != 1 or
                   any(not item.visible for item in candidates)),
    )


def _final_scope(page):
    dialogs = []
    for dialog in page.locator('[role="dialog"]').all():
        try:
            if dialog.is_visible():
                dialogs.append(dialog)
        except PlaywrightError:
            continue
    if len(dialogs) > 1:
        matching = [dialog for dialog in dialogs if any(
            FINAL_ACTION.fullmatch(_safe_text(item.get('label')))
            for item in _raw_publication_controls(dialog)
        )]
        if len(matching) != 1:
            raise BrowserSessionError('The final publication screen was ambiguous.')
        return matching[0], 'dialog'
    if len(dialogs) == 1:
        return dialogs[0], 'dialog'
    return page.locator('body'), 'page'


def _safe_url(page, publication_url: str) -> str:
    if not trusted_page(page.url, publication_url):
        return '[unrecognized URL redacted]'
    parsed = urlsplit(page.url)
    return f'https://{parsed.hostname}{parsed.path.rstrip("/")}'


def inspect_open_final_publication_screen(page, publication_url: str) -> FinalScreenInspection:
    """Inspect an already-open final dialog without clicking or closing anything."""
    try:
        scope, scope_kind = _final_scope(page)
        controls = _publication_controls(scope)
        final_actions = tuple(item.label for item in controls if item.final_action)
        if not final_actions:
            raise BrowserSessionError('No final publication action was identified.')
        screen_title, screen_attributes = _screen_evidence(scope, scope_kind)
        scheduling = _scheduling_evidence(scope)
        action_evidence = inspect_final_action_evidence(page)
        dialog_evidence, visible_headings, stable_containers = _screen_identity(page)
        browser_title = page.title()
    except BrowserSessionError:
        raise
    except (PlaywrightError, AttributeError, TypeError, ValueError):
        raise BrowserSessionError(
            'The open final publication screen could not be inspected safely.'
        ) from None
    return FinalScreenInspection(
        _safe_url(page, publication_url),
        screen_title or _safe_text(browser_title, 'Final publication configuration'),
        controls, final_actions, None,
        'Browser remains on the final publication screen.', (),
        scope_kind, screen_attributes, scheduling, action_evidence,
        dialog_evidence, visible_headings, stable_containers,
    )

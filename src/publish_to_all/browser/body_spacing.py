"""Exact-signature deletion of the confirmed cycle-2 spacing defect."""

from dataclasses import dataclass
from hashlib import sha256
from time import monotonic

from . import body, body_inspect, editor, reconcile, subtitle
from ..errors import BrowserSessionError

DRAFT_URL = 'https://cyporter.substack.com/publish/post/218388044'
PUBLICATION_URL = 'https://cyporter.substack.com'
STORY_HASH = '21a534049f2257b8f941d3f35167e399b06e82ce6cf15debc4a465a3d2a55cfb'
TITLE = 'Saturation of Artificial Intelligence'
SUBTITLE = ('Hiding from a dark past on the roof of a fully automated shoe factory, '
            'an aging recluse builds an emotional lifeline to a woman online, '
            'a relationship that could lead to his undoing.')

# A whitelist, not an empty-text heuristic. No attributes, wrappers, links,
# inline content, mentions, attachments, embeds, images or hidden children.
_SNAPSHOT_FUNCTION = r"""
function snapshot(root) {
  const visible = n => {
    const s = getComputedStyle(n);
    return !n.hidden && s.display !== 'none' && s.visibility === 'visible'
      && Number(s.opacity) !== 0 && s.contentVisibility !== 'hidden'
      && n.getBoundingClientRect().height > 0;
  };
  const safeBlank = n => n.parentElement === root && n.tagName === 'P'
    && n.attributes.length === 0 && visible(n) && n.childNodes.length === 2
    && n.childNodes[0].nodeType === 1 && n.childNodes[0].tagName === 'BR'
    && n.childNodes[0].attributes.length === 0 && n.childNodes[0].childNodes.length === 0
    && n.childNodes[1].nodeType === 1 && n.childNodes[1].tagName === 'BR'
    && n.childNodes[1].attributes.length === 1
    && n.childNodes[1].getAttribute('class') === 'ProseMirror-trailingBreak'
    && n.childNodes[1].childNodes.length === 0;
  return {html:root.innerHTML, editable:root.isContentEditable, visible:visible(root),
    directContent:[...root.childNodes].some(n => n.nodeType !== 1 &&
      (n.nodeType !== 3 || n.nodeValue.trim())),
    blocks:[...root.children].map((n,i) => ({index:i+1, html:n.outerHTML,
      text:n.textContent, tag:n.tagName, safe:safeBlank(n)}))};
}
"""
_SNAPSHOT = '(root) => {' + _SNAPSHOT_FUNCTION + 'return snapshot(root);}'
_DELETE = '(root, expected) => {' + _SNAPSHOT_FUNCTION + r"""
  const before = snapshot(root);
  if (location.href !== expected.url || !before.editable || !before.visible
      || before.directContent || before.html !== expected.html)
    throw new Error('spacing repair snapshot is stale');
  const candidates = before.blocks.filter(b => b.safe);
  if (candidates.length !== 36 || JSON.stringify(candidates.map(b => b.index))
      !== JSON.stringify(expected.indexes)) throw new Error('blank candidates changed');
  // All checks precede the first deletion. Keep references to approved nodes;
  // never select, clear, replace, paste or rebuild the article.
  const children = [...root.children];
  const doomed = candidates.map(b => children[b.index-1]);
  const retained = children.filter(n => !doomed.includes(n));
  const originals = retained.map(n => [n.outerHTML, n.textContent]);
  for (const node of doomed) node.remove();
  if (retained.some((n,i) => n.parentElement !== root
      || n.outerHTML !== originals[i][0] || n.textContent !== originals[i][1]))
    throw new Error('meaningful content changed during spacing repair');
  // ProseMirror's existing DOM observer consumes the childList deletions.
  // No fabricated paste/input event and no second edit are needed.
  return doomed.length;
}
"""


@dataclass(frozen=True)
class SpacingInspection:
    report: body_inspect.FormattingReport
    snapshot: dict
    candidates: tuple[int, ...]

    @property
    def fingerprint(self):
        return sha256(self.snapshot['html'].encode()).hexdigest()

    @property
    def meaningful_exact(self):
        return tuple((b['html'], b['text']) for b in self.snapshot['blocks'] if not b['safe'])


def inspect_spacing(surface, prepared, *, repaired=False) -> SpacingInspection:
    """Read and cross-check semantic alignment and exact live DOM signatures."""
    before = surface.evaluate(_SNAPSHOT)
    report = body_inspect.inspect_body_formatting(surface, prepared)
    after = surface.evaluate(_SNAPSHOT)
    return validate_spacing_inspection(surface, prepared, report, before, after, repaired=repaired)


def validate_spacing_inspection(surface, prepared, report, before, after, *, repaired=False):
    """Share the exact semantic/signature guard with read-only reconciliation."""
    if before != after or not after['editable'] or not after['visible'] or after['directContent']:
        raise BrowserSessionError('Spacing repair: body changed or is not an editable visible surface.')
    local, remote = report.prepared, report.remote
    wanted_blanks = 0 if repaired else 36
    if (len(local.meaningful) != 36 or len(remote.meaningful) != 36
            or len(local.blocks) != 36 or len(remote.blocks) != 36 + wanted_blanks
            or local.unsupported or remote.unsupported
            or local.blanks or len(remote.blanks) != wanted_blanks
            or remote.top_level_count != 36 + wanted_blanks
            or remote.raw_paragraph_count != 18 + wanted_blanks
            or report.headings_expected != 18 or report.headings_matched != 18
            or sum(b.kind == 'paragraph' for b in local.meaningful) != 18
            or sum(b.kind == 'paragraph' for b in remote.meaningful) != 18
            or not report.complete_text_matches or report.break_mismatch_indexes
            or len(report.alignment) != 36
            or any(a.status != 'match' or len(a.remote_indexes) != 1 for a in report.alignment)
            or any(r.classification == 'actual duplicate article content' for r in report.repeated_text)
            or any(b.path != str(b.top_level_index) for b in remote.blocks)
            or body.normalize_visible_text(surface.inner_text()) != body.normalize_visible_text(prepared.text)):
        raise BrowserSessionError('Spacing repair: exact meaningful body or blank counts do not match; stopped.')
    candidates = tuple(b['index'] for b in after['blocks'] if b['safe'])
    if (len(candidates) != wanted_blanks
            or candidates != report.unexpected_blank_indexes
            or candidates != tuple(b.top_level_index for b in remote.blanks)):
        raise BrowserSessionError('Spacing repair: unsafe/ambiguous blank candidates; stopped.')
    if not repaired:
        # Match the known defect relative to individually matched story blocks.
        # Position is corroborating evidence only, never the deletion guard.
        if candidates != tuple(a.remote_indexes[0] + 1 for a in report.alignment):
            raise BrowserSessionError('Spacing repair: blanks do not follow the matched meaningful blocks.')
    return SpacingInspection(report, after, candidates)


def require_metadata(page, monitor, *, require_saved=True):
    monitor.require_clear(page)
    editor.require_publication(page, PUBLICATION_URL)
    field = editor.unique_visible(editor.title_fields(page))
    _, actual_subtitle = subtitle.inspect_subtitle(page)
    if (page.url != DRAFT_URL or field is None or not field.is_editable()
            or editor.title_value(field) != TITLE or actual_subtitle != SUBTITLE):
        raise BrowserSessionError('Spacing repair: exact URL, title or subtitle changed.')
    if require_saved and (not editor.save_visible(page) or editor.saving_visible(page)):
        raise BrowserSessionError('Spacing repair: editor is not Saved.')


def open_verified(page, monitor):
    evidence = reconcile.verify_supplied_draft(
        page, PUBLICATION_URL, DRAFT_URL, rate_limit_monitor=monitor,
    )
    monitor.require_clear(page)
    if (evidence.rate_limited or not evidence.verified or evidence.inspection is None
            or reconcile.verified_not_published_evidence(evidence.inspection, DRAFT_URL) is None
            or evidence.inspection.body_classification != 'substantial'):
        raise BrowserSessionError('Spacing repair: saved editable unpublished substantial draft not verified.')
    require_metadata(page, monitor)
    return body.locate_body_surface(page)


def delete_approved_blanks(surface, inspection):
    removed = surface.evaluate(_DELETE, {
        'url': DRAFT_URL, 'html': inspection.snapshot['html'],
        'indexes': list(inspection.candidates),
    })
    if removed != 36:
        raise BrowserSessionError('Spacing repair deletion outcome is uncertain; do not retry.')
    return removed


def confirm_spacing_save(page, surface, prepared, before, monitor, *, timeout=30):
    """Use the production observer: a fresh Saving -> Saved transition is required."""
    deadline = monotonic() + timeout
    while True:
        require_metadata(page, monitor, require_saved=False)
        current = inspect_spacing(surface, prepared, repaired=True)
        if current.meaningful_exact != before.meaningful_exact:
            raise BrowserSessionError('Spacing repair: meaningful HTML/text changed; reconciliation required.')
        saving, saved_after = editor.observed_save_transition(page)
        if saving and saved_after and editor.save_visible(page) and not editor.saving_visible(page):
            return current
        if monotonic() >= deadline:
            raise BrowserSessionError('Spacing repair: fresh save is uncertain; reconciliation required, no retry.')
        page.wait_for_timeout(100)

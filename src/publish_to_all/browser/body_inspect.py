"""Read-only semantic body diagnostics, independent of insertion/save guards.

The production PreparedBody HTML is parsed in a detached document. The live
editor is only read; no focus, selection, event, editor state, or DOM writes.
"""

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from enum import StrEnum
from hashlib import sha256

from .body import PreparedBody, normalize_visible_text


class FormattingClassification(StrEnum):
    VERIFIED = 'FORMATTING VERIFIED — DOM COUNT DIFFERENCE IS HARMLESS'
    SPLIT = 'CONTENT VERIFIED — PARAGRAPH SPLITTING PRESENT'
    DEFECT = 'FORMATTING DEFECT CONFIRMED'
    UNKNOWN = 'FORMATTING STATE UNKNOWN'


# Walk content leaves once, unwrapping layout/list containers. Never aggregate
# parent text and then count that same text again in child paragraph nodes.
# aria-hidden alone is not a visual hiding rule. An ordinary empty paragraph
# containing a trailingBreak is still an article blank, not an editor helper.
_STRUCTURE = r"""
(root, html) => {
  const norm = s => s.replace(/\s+/gu, ' ').trim();
  function inspect(root, live) {
    const blocks = [], excluded = [], unsupported = [];
    const helper = '.ProseMirror-widget,.ProseMirror-gapcursor,.ProseMirror-separator';
    const semantic = 'p,h1,h2,h3,h4,h5,h6,pre,hr,[role="paragraph"],[role="heading"]';
    const wrappers = new Set(['div','section','article','main','ul','ol','li','blockquote']);
    let helpers = 0, trailingBreaks = 0, hiddenParagraphs = 0, helperParagraphs = 0;
    function hidden(n) {
      if (!live) return false;
      const s = getComputedStyle(n);
      return n.hidden || s.display === 'none' || s.visibility !== 'visible'
        || Number(s.opacity) === 0 || s.contentVisibility === 'hidden';
    }
    function exclude(n, path) {
      if (hidden(n) || n.matches('script,style,template')) {
        hiddenParagraphs += Number(n.matches('p')) + n.querySelectorAll('p').length;
        excluded.push({path, kind:'hidden'}); return true;
      }
      if (n.matches(helper)) {
        helpers++;
        helperParagraphs += Number(n.matches('p')) + n.querySelectorAll('p').length;
        excluded.push({path, kind:'editor_helper'}); return true;
      }
      return false;
    }
    function inline(n, path) {
      if (n.nodeType === 3) return n.nodeValue;
      if (n.nodeType !== 1 || exclude(n, path)) return '';
      if (n.matches('br.ProseMirror-trailingBreak')) { trailingBreaks++; return ''; }
      if (n.tagName === 'BR') return '\u0000';
      if (n.matches('img,video,audio,iframe,canvas,svg,object,embed')) {
        unsupported.push(path + ':media'); return '';
      }
      return [...n.childNodes].map((c,i) => inline(c,path+'.'+i)).join('');
    }
    function add(n, path, top, containers, text, kind, level) {
      const s = live ? getComputedStyle(n) : null;
      const r = live ? n.getBoundingClientRect() : null;
      // Whitespace newlines in CommonMark may remain soft breaks or become
      // editor BRs. Record their exact text offsets without splitting blocks.
      const hardBreaks = [...text.matchAll(/\u0000/gu)].map(m =>
        norm(text.slice(0,m.index).replaceAll('\u0000','\n')).length);
      text = text.replaceAll('\u0000','\n');
      const parts = text.split('\n'), breaks = [];
      let prefix = '';
      parts.forEach((part,i) => {
        if (i) { breaks.push(norm(prefix).length); prefix += '\n'; }
        prefix += part;
      });
      blocks.push({index:blocks.length+1, top_level_index:top, path, kind,
        level, containers, text:norm(text), break_offsets:breaks, hard_break_offsets:hardBreaks,
        tag:n.tagName.toLowerCase(), height:r ? r.height : null,
        margin_bottom:s ? s.marginBottom : null,
        br_count:n.querySelectorAll('br:not(.ProseMirror-trailingBreak)').length});
    }
    function walk(n, path, top, containers) {
      if (n.nodeType !== 1 || exclude(n,path)) return;
      const tag = n.tagName.toLowerCase();
      const context = ['ul','ol','li','blockquote'].includes(tag)
        ? [...containers,tag] : containers;
      const heading = /^h[1-6]$/.test(tag) || n.getAttribute('role') === 'heading';
      const isSemantic = n.matches(semantic);
      const nested = tag !== 'pre' && [...n.children].some(c =>
        c.matches(semantic) || wrappers.has(c.tagName.toLowerCase()) || c.querySelector(semantic));
      if (nested && (isSemantic || wrappers.has(tag))) {
        // Mixed direct text and nested blocks need an explicit model; do not
        // silently drop it and certify a match from a partial extraction.
        for (const c of n.childNodes) {
          if (c.nodeType === 3 && norm(c.nodeValue)) unsupported.push(path+':mixed_text');
          if (c.nodeType === 1 && !c.matches(semantic) && !wrappers.has(c.tagName.toLowerCase())
              && !c.querySelector(semantic) && norm(c.textContent) && !hidden(c)
              && !c.matches(helper)) unsupported.push(path+':mixed_inline');
        }
        [...n.children].forEach((c,i) => walk(c,path+'.'+i,top,context));
      } else if (isSemantic || wrappers.has(tag)) {
        const kind = heading ? 'heading' : tag === 'pre' ? 'code'
          : tag === 'hr' ? 'rule' : 'paragraph';
        const level = heading ? Number(tag.slice(1)) || Number(n.getAttribute('aria-level')) || null : null;
        add(n,path,top,context,inline(n,path),kind,level);
      } else if (norm(n.textContent) || n.querySelector('img,iframe,video')
                 || n.matches('img,iframe,video,br')) unsupported.push(path+':'+tag);
    }
    if (hidden(root)) unsupported.push('body:hidden');
    [...root.childNodes].forEach(n => {
      if (n.nodeType === 3 && norm(n.nodeValue)) unsupported.push('body:direct_text');
    });
    [...root.children].forEach((n,i) => walk(n,String(i+1),i+1,[]));
    return {blocks, excluded, unsupported, raw_paragraph_count:root.querySelectorAll('p').length,
      top_level_count:root.children.length, helper_elements:helpers,
      trailing_break_helpers:trailingBreaks, hidden_paragraphs:hiddenParagraphs,
      helper_paragraphs:helperParagraphs};
  }
  const prepared = new DOMParser().parseFromString(html,'text/html').body;
  return {prepared:inspect(prepared,false), remote:inspect(root,true)};
}
"""


@dataclass(frozen=True)
class ContentBlock:
    index: int
    top_level_index: int
    path: str
    kind: str
    level: int | None
    containers: tuple[str, ...]
    normalized_text: str = field(repr=False)
    text_hash: str
    text_length: int
    break_offsets: tuple[int, ...]
    hard_break_offsets: tuple[int, ...]
    tag: str
    height: float | None
    margin_bottom: str | None
    br_count: int

    @property
    def blank(self) -> bool:
        return not self.normalized_text and self.kind == 'paragraph'

    @property
    def signature(self) -> tuple:
        return self.kind, self.level, self.containers, self.normalized_text


@dataclass(frozen=True)
class BodyStructure:
    blocks: tuple[ContentBlock, ...]
    raw_paragraph_count: int
    top_level_count: int
    helper_elements: int
    trailing_break_helpers: int
    hidden_paragraphs: int
    helper_paragraphs: int
    unsupported: tuple[str, ...]

    @property
    def meaningful(self) -> tuple[ContentBlock, ...]:
        return tuple(b for b in self.blocks if not b.blank)

    @property
    def blanks(self) -> tuple[ContentBlock, ...]:
        return tuple(b for b in self.blocks if b.blank)

    @property
    def consecutive_blank_pairs(self) -> int:
        return sum(a.blank and b.blank for a, b in zip(self.blocks, self.blocks[1:]))


@dataclass(frozen=True)
class BlockAlignment:
    prepared_indexes: tuple[int, ...]
    remote_indexes: tuple[int, ...]
    status: str


@dataclass(frozen=True)
class RepeatedText:
    text_hash: str
    remote_indexes: tuple[int, ...]
    classification: str


@dataclass(frozen=True)
class FormattingReport:
    classification: FormattingClassification
    prepared: BodyStructure
    remote: BodyStructure
    complete_text_matches: bool
    headings_matched: int
    headings_expected: int
    alignment: tuple[BlockAlignment, ...]
    repeated_text: tuple[RepeatedText, ...]
    unexpected_blank_indexes: tuple[int, ...]
    break_mismatch_indexes: tuple[int, ...]


def _structure(data: dict) -> BodyStructure:
    blocks = []
    for item in data['blocks']:
        text = normalize_visible_text(item['text'])
        blocks.append(ContentBlock(
            item['index'], item['top_level_index'], item['path'], item['kind'],
            item['level'], tuple(item['containers']), text,
            sha256(text.encode('utf-8')).hexdigest(), len(text),
            tuple(item['break_offsets']), tuple(item['hard_break_offsets']), item['tag'], item['height'],
            item['margin_bottom'], item['br_count'],
        ))
    return BodyStructure(tuple(blocks), *(data[key] for key in (
        'raw_paragraph_count', 'top_level_count', 'helper_elements',
        'trailing_break_helpers', 'hidden_paragraphs', 'helper_paragraphs',
    )), tuple(data['unsupported']))


def _align(prepared, remote) -> tuple[BlockAlignment, ...]:
    """Exact blocks first; allow only consecutive paragraph splits, never merges."""
    matches = []
    i = j = 0
    while i < len(prepared) and j < len(remote):
        local, actual = prepared[i], remote[j]
        if local.signature == actual.signature:
            matches.append(BlockAlignment((local.index,), (actual.index,), 'match'))
            i += 1
            j += 1
            continue
        end, parts = j, []
        if local.kind == 'paragraph':
            while end < len(remote):
                part = remote[end]
                if part.kind != 'paragraph' or part.containers != local.containers:
                    break
                parts.append(part.normalized_text)
                joined = ' '.join(parts)
                end += 1
                if joined == local.normalized_text:
                    break
                if not local.normalized_text.startswith(joined + ' '):
                    break
        if len(parts) > 1 and ' '.join(parts) == local.normalized_text:
            matches.append(BlockAlignment(
                (local.index,), tuple(b.index for b in remote[j:end]), 'paragraph_split',
            ))
            i += 1
            j = end
            continue
        break
    # Preserve useful exact anchors around actual insertions/deletions/reorders.
    matcher = SequenceMatcher(None, [b.signature for b in prepared[i:]],
                              [b.signature for b in remote[j:]], autojunk=False)
    for kind, a, b, c, d in matcher.get_opcodes():
        if kind == 'equal':
            matches.extend(BlockAlignment((x.index,), (y.index,), 'match')
                           for x, y in zip(prepared[i+a:i+b], remote[j+c:j+d]))
        else:
            matches.append(BlockAlignment(tuple(x.index for x in prepared[i+a:i+b]),
                                          tuple(x.index for x in remote[j+c:j+d]), kind))
    return tuple(matches)


def compare_structures(prepared: BodyStructure, remote: BodyStructure) -> FormattingReport:
    local, actual = prepared.meaningful, remote.meaningful
    text_matches = bool(local) and ' '.join(b.normalized_text for b in local) == ' '.join(
        b.normalized_text for b in actual)
    alignment = _align(local, actual)
    heading_local = [b.signature for b in local if b.kind == 'heading']
    heading_remote = [b.signature for b in actual if b.kind == 'heading']
    headings_matched = sum(a == b for a, b in zip(heading_local, heading_remote))
    repeats = []
    counts = Counter(b.text_hash for b in local if b.normalized_text)
    groups = defaultdict(list)
    for block in actual:
        if block.normalized_text:
            groups[block.text_hash].append(block)
    for digest, blocks in groups.items():
        if len(blocks) < 2:
            continue
        if len(blocks) > counts[digest]:
            category = 'actual duplicate article content'
        elif all(b.kind == 'heading' for b in blocks):
            category = 'expected heading repetition'
        elif blocks[0].text_length <= 80:
            category = 'expected repeated short text'
        else:
            category = 'expected repetition in prepared body'
        repeats.append(RepeatedText(digest, tuple(b.index for b in blocks), category))
    # Compare blank positions relative to meaningful content, not raw DOM indexes.
    def blank_positions(structure):
        result = defaultdict(list)
        anchor = 0
        for block in structure.blocks:
            if block.blank:
                result[anchor].append(block.index)
            else:
                anchor += 1
        return result
    expected_blanks = blank_positions(prepared)
    extra_blanks = tuple(index for anchor, indexes in blank_positions(remote).items()
                         for index in indexes[len(expected_blanks[anchor]):])
    local_by_id = {b.index: b for b in local}
    remote_by_id = {b.index: b for b in actual}
    def breaks_differ(a):
        before = local_by_id[a.prepared_indexes[0]]
        after = remote_by_id[a.remote_indexes[0]]
        # CommonMark soft newlines may collapse to spaces or become BRs.
        # Explicit hard breaks must survive; extra BR positions are defects.
        return bool(Counter(before.hard_break_offsets) - Counter(after.break_offsets)
                    or Counter(after.hard_break_offsets) - Counter(before.break_offsets))
    break_mismatches = tuple(a.remote_indexes[0] for a in alignment
                            if a.status == 'match' and breaks_differ(a))
    structural_error = any(a.status not in {'match', 'paragraph_split'} for a in alignment)
    if prepared.unsupported or remote.unsupported or not local or not actual:
        classification = FormattingClassification.UNKNOWN
    elif (not text_matches or structural_error or extra_blanks or break_mismatches
          or heading_local != heading_remote):
        classification = FormattingClassification.DEFECT
    elif any(a.status == 'paragraph_split' for a in alignment):
        classification = FormattingClassification.SPLIT
    else:
        classification = FormattingClassification.VERIFIED
    return FormattingReport(classification, prepared, remote, text_matches,
                            headings_matched, len(heading_local), alignment,
                            tuple(repeats), extra_blanks, break_mismatches)


def inspect_body_formatting(surface, prepared: PreparedBody) -> FormattingReport:
    snapshot = surface.evaluate(_STRUCTURE, prepared.html)
    return compare_structures(_structure(snapshot['prepared']), _structure(snapshot['remote']))


def format_body_formatting(report: FormattingReport) -> str:
    """Bounded diagnostics: indexes/hashes only, never the complete story."""
    return '\n'.join([
        f'Formatting classification: {report.classification.value}',
        f'Prepared meaningful blocks: {len(report.prepared.meaningful)}',
        f'Remote meaningful blocks: {len(report.remote.meaningful)}',
        f'Prepared raw paragraph count: {report.prepared.raw_paragraph_count}',
        f'Remote raw paragraph count: {report.remote.raw_paragraph_count}',
        f'Complete normalized text matches: {report.complete_text_matches}',
        f'Headings matched: {report.headings_matched} / {report.headings_expected}',
        f'Visible empty paragraphs: {len(report.remote.blanks)}',
        f'Unexpected blank block indexes: {report.unexpected_blank_indexes}',
        f'Consecutive blank pairs: {report.remote.consecutive_blank_pairs}',
        f'Hidden paragraphs: {report.remote.hidden_paragraphs}',
        f'Editor helper paragraphs: {report.remote.helper_paragraphs}',
        f'Caret helper breaks: {report.remote.trailing_break_helpers}',
        f'Line-break mismatches: {report.break_mismatch_indexes}',
        f'Repeated meaningful text hashes: {len(report.repeated_text)}',
        *[f'Prepared {a.prepared_indexes} → remote {a.remote_indexes}: {a.status}'
          for a in report.alignment[:40]],
        *[f'Repeated text at {r.remote_indexes}: {r.classification}'
          for r in report.repeated_text[:20]],
        *[f'Unsupported structure: {item}' for item in
          (*report.prepared.unsupported, *report.remote.unsupported)[:20]],
    ])

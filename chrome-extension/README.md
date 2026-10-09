# Publish to All Medium Chrome extension

This Manifest V3 extension inspects Medium in the normal signed-in Chrome session.
One real Import activation has already occurred for the verified Substack source.
Its result remains `IMPORT_RESULT_AMBIGUOUS`; the known attempt count is **1** and retry
is disabled. A live Drafts reconciliation separately reported `NO_DRAFT_FOUND`.
That finding does not reset or reclassify the click history. Attempt 1 used
`HTMLElement.click()` on Medium's native button. Chrome 154's local fixture
reported `isTrusted: false` for that click. This may help explain the empty
Drafts page, but no Medium-side cause has been established. Leave the old
`/p/import` tab unchanged. Do not click Import or Publish.

## Fill-only manual check

1. Reload the unpacked `chrome-extension/` in `chrome://extensions/`.
2. Open a **new** Chrome tab and navigate to `https://medium.com/p/import`.
3. Confirm Medium shows its normal `http://www.yoursite.org/your-post` sample URL.
4. Click the extension-owned **Fill verified Substack URL** once.
5. The **Prepare one manual Import attempt** control is disabled. Do not click
   Medium's native **Import**.
6. Run **Inspect Current Medium Page** in the extension popup and return its report.
7. Stop there. The report should show `EXACT_SOURCE_URL_PRESENT`, exact fingerprint,
   an application input signal, `VERIFIED` Fill synchronization, one known Import
   attempt, no retry, and a disabled extension Import control.

The Fill report records bounded `beforeinput`, `input`, `change`, `focus`, and
`blur` event metadata from the selected source editor. It reports event type,
target identity, bubbles, cancelable, composed, trusted, input type, and data
length. It never records event data or a full URL. Listeners are removed when
Fill finishes.

The helper freshly resolves the editor, verifies the known Medium sample and
its fingerprint, focuses and selects its contents, then uses Chrome's
`execCommand("insertText")` editing operation. It requires exact normalized DOM
text, the expected SHA-256 fingerprint, and a bubbling input event targeted at
that editor for `VERIFIED`. If the DOM and fingerprint match but the browser
provided no usable input event, it dispatches exactly one bubbling, composed
`InputEvent("input")` with `inputType: "insertText"`. It never dispatches a
synthetic `beforeinput` or `change`. `VERIFIED` does not authorize Import.

The local Chrome fixture observed one trusted, bubbling `input` from
`execCommand("insertText")`, with no `beforeinput` or `change`. Physical keyboard
typing produced `beforeinput` and `input`, with no `change` after blur. This is
browser evidence only; the new-tab Fill report will show what Medium receives.

## Read-only reconciliation

The popup also inspects visible confirmation, draft/edit links, and validation or
error text on `/p/import`. On Stories it waits boundedly for visible tab controls,
reports each tab's safe attributes, ancestor state, computed style, and linked panels.
It locates the post-tab branch within a bounded Stories container, then reports
only visible content from that branch, including promo text, loading and error
signals, and story card structures. A tab is classified as selected only with a
unique structural signal; identical hrefs and visual style alone do not select it.
It reads entries only when Drafts is positively selected and the corresponding
content region is identified. One
exact-title draft entry with a usable Medium href yields
`VERIFIED_DRAFT_CANDIDATE`, which is evidence for a later manual editor check.
It does not select a tab or open a candidate. A published story, duplicate
exact-title drafts, unhydrated UI, or incomplete list remains `AMBIGUOUS`.
`NO_DRAFT_FOUND` requires selected Drafts, a rendered post-tab content region,
no loading or error signal, no story entries, and explicit no-drafts text or a
dedicated promo block occupying that region. A lone promotional heading or a
promo block outside the Drafts region cannot confirm an empty list.
Reconciliation never changes the stored ambiguous Import attempt or enables a retry.

## Attempt storage and safety

The previous version used page `sessionStorage`, and the real attempt's saved
value remained `IMPORT_NOT_STARTED` with zero clicks while in-memory state was
ambiguous with one click. This version uses `chrome.storage.session` for an
extension-wide record. The manifest adds only the narrow `storage` permission.
A small background worker owns the record because Chrome does not expose
`storage.session` to content scripts by default. It rejects stale transitions
from the known ambiguous state.
The attempt 1 record stores the source SHA-256 fingerprint, source hostname, click attempts,
lifecycle state, activation method, `NO_DRAFT_FOUND` reconciliation, click-intent
timestamp, and starting/current Medium paths. It
never stores cookies, tokens, source bodies, or authentication data. It uses no
`storage.local` account record.

Chrome may clear `storage.session` on extension reload. A missing or invalid
record is therefore seeded as the **known real ambiguous attempt with one click**,
not as a fresh attempt. This explicit safety migration stays in place until the
real attempt has been reconciled. The old automatic native-button activation
has been removed. The separate attempt 2 design requires the exact live source,
verified Fill synchronization, one verified native Import button, no visible
draft or validation error, and attempt 1's `NO_DRAFT_FOUND` reconciliation.
Readiness never authorizes Import. The extension-owned preparation control and
background authorization gate both remain disabled in this build.

When authorization is deliberately enabled in a later change, preparation will
save an `ARMED` attempt 2 reservation to `chrome.storage.local` before any
user action on Medium's button. The reservation records the fingerprint,
hostname, Fill verification, reconciliation, timestamp, and a document token,
with zero observed native clicks. It never activates Medium's button. A capture
listener only classifies a later click on the same verified button and records
safe trust and identity evidence. It never prevents, replays, or retries a click.
The first authorized trusted click synchronously locks the document and records
one observed click separately from the prior reservation. A reload gets a new
document token and cannot reuse an armed reservation. An unknown result remains
locked. Attempt 1's historical record remains separate and consumed.

The local Chrome fixture uses an ordinary submit button. `HTMLElement.click()`
and `dispatchEvent(new MouseEvent(...))` each produced an untrusted `click` and
a trusted `submit`; Playwright browser input produced trusted pointer, mouse,
click, and submit events. The trusted `submit` after a script click means this
fixture supports click trust as a plausible difference, not a proved explanation
of Medium's behavior.

The historical activation path was: user pressed the extension's shadow DOM
**Import verified story** button; its listener re-inspected the page and the
source/button identity; it persisted `IMPORT_CLICK_INTENT` with one click attempt;
it rechecked the native targets; it called `targets.action.click()` on Medium's
native button; then it persisted `IMPORT_CLICKED` and watched for draft evidence.
No physical user gesture reached Medium's native button in that path. The path
used `HTMLElement.click()`, not `dispatchEvent()`. The current build has no
automatic native-button click call.

The popup and reconciliation inspect only. They make no Medium API calls and
have no Publish capability.

## Local tests

```sh
node --test chrome-extension/test/*.test.js
.venv/bin/python chrome-extension/test/chrome_contenteditable_fixture.py
.venv/bin/python chrome-extension/test/chrome_click_trust_fixture.py
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q src tests
git diff --check
```

The extension tests use local DOM fixtures and do not contact Medium.

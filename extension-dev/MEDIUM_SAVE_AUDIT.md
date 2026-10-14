# Medium save-state audit (local only)

The live recovery report establishes a complete, exact editor DOM body (36 blocks,
58,757 normalized characters, `fnv1a32:342d11eb`). It also reports no observed
body autosave indicator, and the tab displays Medium's cannot-save warning.
Therefore the current state is `BODY_DOM_VERIFIED_SAVE_UNVERIFIED`. Keep the
current tab open and unchanged. Do not refresh, close, navigate, rerun either
body operation, open Settings, or publish.

## Where the earlier workflow could be read as a save claim

- `medium-body-recovery.js` `resume`: `MEDIUM_BODY_RECOVERY_VERIFIED` and
  `MEDIUM_BODY_VERIFIED` follow exact DOM, title, draft route, and Publish checks.
  No server read or autosave check occurs. The states remain useful DOM evidence.
- `medium-body-fill.js` `fill`: `MEDIUM_BODY_VERIFIED` follows exact DOM and
  observed `input`; the save indicator is sampled but not required. A synthetic
  `input` fallback, when used, is untrusted and is not server proof.
- `content.js` inspection: `workflowState = "MEDIUM_BODY_VERIFIED"` comes from
  `reconcileBody`'s canonical DOM match. This is DOM evidence only.
- `medium-editor-inspector.js` `reconcileBody`: `currentBodyExactCanonicalMatch`
  checks browser DOM text and formatting. The `draftSaveIndicator` field in
  `inspect` reads visible UI text, not a persisted body. The older
  `titleVerifiedInPersistedDraft` name refers to title plus draft route; it
  must not be extended to the body.
- `popup.js` `formatBodyDiagnostics`: the workflow label and historical
  `Autosave evidence after body fill` can appear beside one another. The new
  save section makes the independent status explicit and places the tab warning
  first.
- `chrome-extension/README.md`: the old manual discovery steps requested a
  refresh. Those steps are superseded below and must not be followed for the
  current tab.

## Browser editing semantics observed locally

`chrome_medium_save_fixture.py` exercises a real headless Chrome contenteditable,
with the same selection and `execCommand` types. `insertText` (including a title
comparison), `insertParagraph`, and `formatBlock` each produced a trusted `input`
event and changed the DOM. `formatBlock`'s `inputType` was empty; `insertText`
reported `insertText`; `insertParagraph` reported `insertParagraph`. None of
those operations produced a `beforeinput` or `change` event in this fixture.
Selection/range manipulation itself merely positions the browser selection.
The normal-fill `delete` command produced a trusted `input` event of type
`deleteContentBackward`. A no-op paragraph `formatBlock` emitted a trusted
`input` event even though the DOM did not change.

Recovery did not dispatch a synthetic event. Normal fill has an untrusted
synthetic `input` fallback after `insertText` if it observes no `insertText`
event. The live recovery's 212 post-edit stabilization reads verify DOM
stability, not autosave. Those reads include bounded 80 ms waits and do not
prove that Medium's editor model or backend accepted the changes.

These results rule out the simple claim that Chrome's recovery commands emit no
`input` events in a contenteditable. They do not test Medium's application
listeners, internal model, save requests, server response, rate limits, or
whether 36 edits were too fast for its autosave. The visible error confirms a
save failure was reported by the UI; the underlying cause remains unknown.
A DOM/model divergence is possible, particularly around format conversion or
later blocks, but has not been observed directly. No local fixture can decide
whether rapid edits caused the error.

## Safest independent verification design (not executed)

Leave the current editor tab untouched. A future verifier should use a separate
HTTP client to perform only a read-only draft data request for `fd6c1824c4de`,
once a specific endpoint and authentication contract are verified. Opening a
second editor page risks a conflicting session and is not an acceptable fallback.
Before any live use, confirm in an isolated test account that the request sends
no write or navigation instruction, does not alter the original editor session,
and yields the entire server-stored body. Compare all 36 block kinds and texts
and the canonical fingerprint. A visible `Saved` indicator or disappearing
warning cannot promote state to `MEDIUM_PERSISTENCE_VERIFIED`. If no safe
independent read is available, keep persistence unknown. No such fetch or
second-page inspection has been implemented or run.

## Server persistence read-path decision (offline, 2026-10-13)

This section records the decision before the later manual throwaway captures.
The current read and authentication findings are recorded below; the production
draft's server persistence remains unknown.

The repository and local fixtures do **not** establish a safe Medium draft-data
read endpoint. `src/publish_to_all/browser/medium.py` lists `/_/graphql` and
`/_/api/posts` for sanitized request diagnostics, not as draft readers. Its
`ReadOnlyGuard` blocks a draft-looking API path even when the method is GET.
`tests/test_medium.py` uses mocked `/_/graphql` POST requests and a mocked
`/_/api/posts/draft` GET to check those blocks. Neither test contains a real
draft response, request parameters, operation name, or authentication contract.
The Chrome fixtures emulate editor DOM and browser events; they contain no
captured server response. The known `/p/fd6c1824c4de/edit` route is an editor
document, and requesting it elsewhere could create another editor session.

The mechanism used by Medium to load the draft is **unidentified**. Local
evidence is insufficient to select REST GET, a GraphQL query, bootstrap JSON,
XHR/fetch, or an HTML document response. A named path alone is not evidence
that it reads this draft or returns all persisted blocks.

Decision: stop before implementing or exercising a live-capable persistence
verifier. The requested mock server-response fixtures would assume a response
shape that the permitted evidence does not establish.

### Candidate safety classification

`Unknown` below means the property cannot be established from the permitted
offline sources. `Safe enough` requires a verified endpoint, response shape,
authentication method, and isolation from editor creation.

Approach: Authenticated HTTP GET/fetch of a Medium draft data endpoint

- Read-only by protocol: Yes, if the actual request is GET or HEAD and the endpoint is verified as a read.
- Could trigger Medium editor autosave: Unknown until the endpoint is identified; a direct data request would not execute editor code.
- Could alter current tab: No, if performed by a separate HTTP client.
- Could create editor-session conflict: No, if it remains a data request and never loads an editor document.
- Requires current tab navigation: No.
- Safe enough for future live test: No; endpoint, parameters, response schema, and authentication are unknown.

Approach: Read existing Medium bootstrap/page data without navigating the current tab

- Read-only by protocol: Unknown; reading a previously captured local response would require no network request, but none exists.
- Could trigger Medium editor autosave: No for an offline capture; Unknown for a new page load.
- Could alter current tab: No if a separate capture already exists; inspecting the current live page is prohibited.
- Could create editor-session conflict: Unknown if obtaining the data requires loading the draft page.
- Requires current tab navigation: No.
- Safe enough for future live test: No; no independent captured bootstrap data or verified page-read method exists.

Approach: Open the same draft in a second normal Medium editor tab

- Read-only by protocol: Unknown; the document GET can run editor JavaScript and subsequent requests.
- Could trigger Medium editor autosave: Unknown.
- Could alter current tab: No directly; shared server state and browser storage may still affect it.
- Could create editor-session conflict: Yes.
- Requires current tab navigation: No.
- Safe enough for future live test: No.

Approach: Isolated browser context/profile opening the draft editor

- Read-only by protocol: Unknown; loading the editor can send more than a document GET.
- Could trigger Medium editor autosave: Unknown.
- Could alter current tab: No directly.
- Could create editor-session conflict: Unknown; isolation does not prove Medium has no server-side edit lease.
- Requires current tab navigation: No.
- Safe enough for future live test: No; isolation alone does not make an editor load a data read.

Approach: Existing application-code Medium endpoints (`/_/graphql`, `/_/api/posts`, or `/me/stories/drafts`)

- Read-only by protocol: Unknown; no verified GET operation retrieves this draft. The observed GraphQL examples use POST.
- Could trigger Medium editor autosave: Unknown.
- Could alter current tab: No if called by a separate client.
- Could create editor-session conflict: Unknown; behavior and response are unverified.
- Requires current tab navigation: No.
- Safe enough for future live test: No.

### Required verifier boundary when a read path is established

Build the verifier as a separate component accepting the draft ID as an input.
It must have no `window`, `document`, `editor_7`, selection, `contenteditable`,
or live-tab mutation API reference. Its transport must reject every method
other than GET and HEAD **before transmission**; reject unknown hosts and
redirects; and report method, host, path, and allowlist result. The host
allowlist should contain only the exact Medium host proven necessary by the
read contract, not arbitrary Medium subdomains. Authentication must come from
a separately authorized, isolated credential source; do not read or manipulate
the current tab. No editor route, `/new-story`, body filling, recovery, save,
publish, or settings operation belongs in the verifier.

After a genuine server response is understood, normalize its title and body
into the extension's canonical block model (18 level-one headings and 18
paragraphs). Compare all 36 ordered block kinds and exact normalized texts,
the exact title and title fingerprint `fnv1a32:5e81665d`, body length 58,757,
and body fingerprint `fnv1a32:342d11eb`. A title-only response, title plus
`01`, empty body, or any shorter canonical prefix must report the last matched
block and next expected block. Malformed/authentication/network/redirect
failures must fail closed. Only independent server data can yield
`MEDIUM_PERSISTENCE_VERIFIED`; DOM verification remains a separate column.
The proposed local response and network-safety fixtures cannot be meaningful
until the actual read contract and response schema are known, so none have
been fabricated.

## Current throwaway read finding (manual, 2026-10-13)

Three sanitized HAR summaries show that `GET medium.com/p/{draft_id}/edit`
returned `200` HTML containing the matching throwaway draft ID, title, and all
body markers before either later POST. The two POST patterns remain **UNKNOWN**;
their order does not establish whether they write. No request was replayed.

In the logged-in throwaway Chrome profile, a later manually reopened Draft A
`GET` returned `200 OK` with the correct title and all body markers. DevTools
showed a Request Cookies section and a `Cookie` request header, with these names
only: `_cfuvid`, `_ga`, `_ga_7JY7T788PK`, `cf_clearance`, `g_state`, `pr`,
`sid`, `sz`, `tz`, `uid`, `xsrf`. There was no `Authorization` request header.
The request and final URL had no query parameters; Medium did not add `source`.
Thus `source` is not required for this observed read, although it appeared in
the three earlier captures.

The same throwaway Draft A URL in a fresh, logged-out Incognito window redirected
to sign-in and exposed no draft title, body markers, or editor. Authentication
was required for the observed read. **Cookie** is supported as the mechanism by
the actual request-cookie evidence, without proving which cookie matters or
whether cookies alone suffice. The next design choice is
`BROWSER_COOKIE_AUTHENTICATED_GET_REQUIRED`; a raw unauthenticated GET is not
supported by the observation. The unimplemented design uses a dedicated
throwaway Chrome profile and a request-only browser transport that lets Chrome
attach its ephemeral cookies to one allowlisted GET, rejects redirects, and
never executes returned editor HTML or JavaScript. Its feasibility under
Medium's cookie rules is unverified. Cookie values must never enter code,
configuration, shell history, chat, or logs. No such mechanism or replay
harness exists yet.

Current DOM: VERIFIED. Medium server persistence: UNKNOWN
(`MEDIUM_PERSISTENCE_UNKNOWN`). Current draft-read classification:
`READ_CONTRACT_FOUND_REPLAY_SAFETY_UNKNOWN` at the time of this finding. The
production draft and tab were untouched.

## Replay-safety update and production verifier (local, 2026-10-13)

The user reports two successful throwaway tests of the fixed authenticated GET,
one each for Draft A and Draft B. Each made exactly one GET, zero writes,
returned HTTP 200 and complete HTML with the expected ID/title/body markers,
and left the draft unchanged on manual follow-up. The resulting read-contract
classification is `READ_CONTRACT_FOUND_CANDIDATE_SAFE`.

`extension-dev/medium-persistence-verifier/` packages the validated canonical
title and body. Its service worker permits one attempt per worker lifetime and
makes a single fixed `GET https://medium.com/p/fd6c1824c4de/edit` with
`credentials: "include"`, `redirect: "manual"`, and no body or custom headers.
It never opens an editor page or touches a tab. The response is read as inert
text, with scripts excluded from body extraction. It verifies exact title,
36 ordered heading/paragraph blocks, normalized length 58,757, and both
canonical fingerprints. Its bounded report gives the last complete matching
block and next expected block when persistence is partial. The previously
observed DOM state and independently read server state are separate fields.

Local tests use mocked responses and do not contact Medium. The production
GET has not been run. Current DOM evidence: previously VERIFIED. Current server
persistence: UNKNOWN (`MEDIUM_PERSISTENCE_UNKNOWN`).

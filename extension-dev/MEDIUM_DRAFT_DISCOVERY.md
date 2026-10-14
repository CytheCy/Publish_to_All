# Manual Medium draft-read discovery

The 2026-10-13 Playwright-controlled isolated Chrome attempt stopped at a Medium
403 during sign-in. It produced no draft or read evidence; its decision was
`READ_CONTRACT_NOT_FOUND`. The automated recorder has been removed.

This experiment uses a new, ordinary Chrome profile created **by the user in
Chrome's profile menu**, a throwaway Medium account, and two throwaway drafts.
Never use the production profile, its cookies, the production tab, production
story text, or production draft `fd6c1824c4de`. Discovery tooling does not
launch Chrome, navigate it, sign in, open drafts, replay requests, or trigger
autosave. The user performs every browser action manually.

## Capture boundary

Chrome's [Network panel documentation](https://developer.chrome.com/docs/devtools/network/reference)
describes **Export HAR (sanitized)**. It excludes Cookie, Set-Cookie, and
Authorization headers by default. Use this option only; do not enable
"Allow to generate HAR with sensitive data." Clear the Network panel
immediately before each reopen, start recording, manually reopen the draft,
stop after loading settles, and export one sanitized HAR per reopen. Do not
capture sign-in. Do not paste a HAR or credentials into chat.

The raw HAR can still include other sensitive headers, URL parameters, request
payloads, response bodies, and account data. Keep it local. The offline analyzer
rejects any occurrence of the forbidden production ID, ignores every header
except the Content-Type MIME type, discards URL query values and opaque path
segments, keeps only safe query parameter names, drops unrelated traffic, and
saves only bounded fingerprints, lengths, marker presence, structural field
paths, and semantic classification.
It writes a sanitized derivative with permissions 0600. The original HAR
remains local. If a HAR omits a response body, the analyzer cannot infer saved
draft content from that exchange.

## Disposable markers

Draft A title: `DISCOVERY TEST A 74291`

Draft A body:

```text
MEDIUM_DISCOVERY_A_START_74291
Disposable draft-read discovery text.
MEDIUM_DISCOVERY_A_MIDDLE_74291
Second disposable paragraph.
MEDIUM_DISCOVERY_A_END_74291
```

Draft B title: `DISCOVERY TEST B 39518`

Draft B body:

```text
MEDIUM_DISCOVERY_B_START_39518
Second disposable draft-read discovery text.
MEDIUM_DISCOVERY_B_MIDDLE_39518
Another disposable paragraph.
MEDIUM_DISCOVERY_B_END_39518
```

The interactive sequence is profile creation, throwaway sign-in, Draft A
creation and save, Draft A reopen #1, Draft A reopen #2, Draft B creation and
save, Draft B reopen #1. Give the user **one browser instruction at a time** and
wait for the response.

## Local analyzer

From the repository root, analyze one capture at a time:

```sh
.venv/bin/python -B extension-dev/medium_draft_discovery.py analyze /path/to/manual-a1.har \
  --phase a1 --draft-id THROWAWAY_A_ID \
  --title-marker 'DISCOVERY TEST A 74291' \
  --body-marker MEDIUM_DISCOVERY_A_START_74291 \
  --body-marker MEDIUM_DISCOVERY_A_MIDDLE_74291 \
  --body-marker MEDIUM_DISCOVERY_A_END_74291 \
  --output extension-dev/.medium-draft-discovery/observations/a1.json
```

Repeat for `a2` with the same A ID and markers. Repeat for `b` with the
distinct B ID and B markers. The analyzer accepts only HAR JSON and has no
network transport. The output file must be new. Review the sanitized JSON
locally. Each retained entry reports method, redacted host/path, status,
content type, resource type, request and response body fingerprints and
lengths, GraphQL type/name when extractable, throwaway marker presence,
response field paths, representation, classification, and semantic operation.
Sensitive header values and full bodies are never printed.

After all three captures:

```sh
.venv/bin/python -B extension-dev/medium_draft_discovery.py report \
  --a1 extension-dev/.medium-draft-discovery/observations/a1.json \
  --a2 extension-dev/.medium-draft-discovery/observations/a2.json \
  --b extension-dev/.medium-draft-discovery/observations/b.json
```

A read contract requires matching response evidence in A reopen #1, A reopen
#2, and B reopen #1: the throwaway draft ID, title, and all body markers must
be present, with JSON field paths or observed HTML element paths. A POST GraphQL
query can be a semantic read. Mutations and autosave writes are classified
separately. An unlocated HTML text match, missing response body, unmatched
paths, or one draft alone leaves
`READ_CONTRACT_NOT_FOUND`. Matching evidence gives
`READ_CONTRACT_FOUND_REPLAY_SAFETY_UNKNOWN`, because passive capture cannot
establish replay safety. The other possible final labels,
`READ_CONTRACT_FOUND_REPLAY_UNSAFE` and
`READ_CONTRACT_FOUND_CANDIDATE_SAFE`, require later safety evidence and are
never inferred from passive HAR observations alone. This section describes
the earlier passive stage, before the verifier was built.

## Manual observation result

Three distinct sanitized HAR captures from the ordinary throwaway Chrome
profile were analyzed locally: Draft A reopen #1, Draft A reopen #2, and Draft
B reopen #1. The throwaway draft IDs and markers differ between A and B.
The evidence files are in the ignored local directory
`extension-dev/.medium-draft-discovery/observations/`, including
`a1-html.json`, `a2.json`, `b.json`, and `report.json`.

| Observation | Successful read candidate | Response body length | ID location | Title location | Body marker locations |
| --- | --- | ---: | --- | --- | --- |
| Draft A reopen #1 | GET medium.com /p/{draft_id}/edit | 38,567 bytes | HTML script | HTML title | HTML paragraphs |
| Draft A reopen #2 | GET medium.com /p/{draft_id}/edit | 38,567 bytes | HTML script | HTML title | HTML paragraphs |
| Draft B reopen #1 | GET medium.com /p/{draft_id}/edit | 38,631 bytes | HTML script | HTML title | HTML paragraphs |

All three candidate responses returned HTTP 200 with `text/html` content and
the matching throwaway ID, title, and all three body markers. The requests had
no body. The URL carried a `source` query parameter whose value was discarded.
No GraphQL operation was observed for these candidates. Their semantic
operation is **READ**, and the body representation is **HTML**.

Each capture also retained other exchanges linked to its throwaway ID,
including two POSTs classified **UNKNOWN**. None had enough evidence to call it
an autosave or mutation. This does not prove autosave was absent. No request was
replayed, and passive capture cannot establish whether replaying this editor
page would instantiate an editor, cause a write, or otherwise be safe.

Final classification:

```text
READ_CONTRACT_FOUND_REPLAY_SAFETY_UNKNOWN
```

The production Medium tab and draft were not used. No production persistence
verifier was built or run.

## Manual authentication and `source` observations (2026-10-13)

In the normal logged-in throwaway Chrome profile, the user reopened Draft A
from Chrome History and inspected its document request in DevTools. The selected
`GET /p/{draft_id}/edit` returned `200 OK`. DevTools showed a **Request Cookies**
section and a `Cookie` request header, but no `Authorization` request header.
Only cookie names were reported: `_cfuvid`, `_ga`, `_ga_7JY7T788PK`,
`cf_clearance`, `g_state`, `pr`, `sid`, `sz`, `tz`, `uid`, `xsrf`. No cookie or
header values were copied into the documentation.

The three earlier successful HAR candidates carried the query parameter name
`source`. The manually reopened Draft A URL had **no query parameters**. Its
logged-in GET still returned `200 OK`, and the editor showed the exact Draft A
title and all three body markers. The address bar remained on `/edit` without
`source`; no redirect added it. Therefore `SOURCE_PARAMETER_REQUIRED = No` for
this observed draft read.

The user then opened a fresh ordinary Chrome Incognito window with no other
Incognito windows or Medium login and manually navigated to the same throwaway
Draft A URL. Chrome changed to a sign-in path. Only the sign-in prompt was
visible: no Draft A title, body markers, or editor. This supports
`AUTHENTICATION_REQUIRED_FOR_OBSERVED_READ`. The successful logged-in request
carried cookies and no Authorization header, so **Cookie** is the authentication
mechanism supported by the request evidence. These observations do not prove
which cookie is necessary or that cookies alone are sufficient.

Decision for later design: `BROWSER_COOKIE_AUTHENTICATED_GET_REQUIRED`.
`RAW_UNAUTHENTICATED_GET_POSSIBLE` is not supported by the Incognito result.
The unimplemented credential design is a dedicated throwaway Chrome profile
whose own request stack attaches its ephemeral Medium cookies to a single
allowlisted GET. The transport must neither export cookies nor execute returned
editor HTML or JavaScript; it must stop on redirects and report only bounded
response evidence. Whether such a transport works with Medium's cookie rules
is still unverified. No values may enter source, configuration, shell history,
chat, or logs. No credential mechanism or replay harness was implemented here.
The two later POST patterns remain **UNKNOWN** and were neither replayed nor
classified as writes. The production draft and tab remained untouched.

At this point, before the throwaway replay-safety tests, the classification was
`READ_CONTRACT_FOUND_REPLAY_SAFETY_UNKNOWN`.

## Throwaway replay-safety result (user-reported, 2026-10-13)

The fixed browser-cookie-authenticated GET was run against each of the two
throwaway drafts. Each run initiated exactly one GET and zero
POST/PUT/PATCH/DELETE requests. Both returned HTTP 200 and complete HTML with
the correct draft ID, title, and all body markers. A manual follow-up after
each run confirmed that its draft was unchanged. This is user-reported
replay-safety evidence for the fixed request pattern; the local verifier did
not independently inspect those live runs.

Current classification:

```text
READ_CONTRACT_FOUND_CANDIDATE_SAFE
```

The production verifier is a separate extension in
`extension-dev/medium-persistence-verifier/`. It fixes the host, path, draft ID,
and GET options in source; Chrome attaches cookies without exposing their
values. It does not access tabs or execute returned HTML. Its bounded report
compares the server-returned title and 36 ordered body blocks against packaged
canonical assets. Local mocked tests cover full persistence, partial prefixes,
formatting and text mismatches, malformed HTML, redirects, and HTTP failures.
The production GET has not been run, and production persistence remains unknown.

The user-reported replay observations are recorded without IDs, credentials,
or response bodies in `extension-dev/medium-replay-safety-evidence.json`. To
reproduce the current classification from the three local sanitized HAR
observations, run:

```sh
.venv/bin/python -B extension-dev/medium_draft_discovery.py report \
  --a1 extension-dev/.medium-draft-discovery/observations/a1-html.json \
  --a2 extension-dev/.medium-draft-discovery/observations/a2.json \
  --b extension-dev/.medium-draft-discovery/observations/b.json \
  --replay-evidence extension-dev/medium-replay-safety-evidence.json
```

Without the replay-evidence flag, the analyzer still reports the earlier
passive-capture classification.

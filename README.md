# Publish to All

A local Python utility for preparing a finished Markdown story and its cover
image for publishing destinations. Phase 1 targets Substack drafts for manual
review, with no automatic final publication.

**Current milestone: guarded title, body, and cover stages for a linked Substack draft.**
`publish-to-all substack` still creates a title-only draft. The separate
`publish-to-all substack-title` command repairs an empty title on an existing
linked draft, `publish-to-all substack-body` adds the parsed story body, and
`publish-to-all substack-image` uploads its matching Social Preview image. None publishes,
sends, schedules, or configures publication settings.

## Structure

```text
In/
├── 01_01_2040.md
├── 01_01_2040.png
└── Social.png
src/publish_to_all/
    application.py            # project inspection and local status queries
    cli.py                    # argparse command entry point
    presentation.py           # human-readable reports
    config.py                 # local TOML and external runtime paths
    errors.py                 # expected application errors
    story.py                  # discovery, parsing, validation, word count
    state.py                  # SQLite migrations, attempts, duplicate protection
    browser/session.py        # persistent Chromium lifecycle and failure screenshots
    browser/substack.py       # manual login and conservative authentication checks
    browser/editor.py         # guarded new-post navigation, title entry, save confirmation
    browser/body.py           # body inspection, formatted insertion, save confirmation
    browser/image.py          # quarantined historical attachment-thumbnail helpers
    browser/image_observe.py  # Social Preview inspection, upload, and save confirmation
    browser/title.py          # empty-title inspection, insertion, save confirmation
    publishers/substack.py    # draft, title, body, and Social Preview workflows
    publishers/base.py        # abstract provider contract using validated Story
tests/                       # configuration, story, state, and CLI tests
config.example.toml
pyproject.toml
```

Only one Markdown story should be directly inside `In/` at a time. Its optional
general story image shares the Markdown base filename; for example,
`01_01_2040.png`. Supported general-image extensions are `.png`, `.jpg`, `.jpeg`,
and `.webp` (extensions are case-insensitive). `Social.png` is a separate,
special-purpose image and is the preferred Substack Social Preview source. When
`Social.png` is absent, Substack falls back only to the matching general story
image. It never selects another unrelated image. These files serve different
purposes: the general image remains available to Hugo, Medium, Royal Road, and
other workflows, while `Social.png` supplies the Substack article card/share
image. `Social.png` is validated but is never resized, cropped, recompressed, or
otherwise modified. Nested stories and unrelated images are ignored. Multiple
Markdown stories or multiple matching general images fail clearly. A missing
general image produces a warning. Original files are never renamed, moved, or
rewritten.

## Setup

Use Python 3.11 or newer, from the project directory:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m pytest
```

Runtime dependencies are PyYAML for safe YAML parsing, markdown-it-py for
CommonMark parsing and HTML rendering, Pillow to validate cover image contents,
and Playwright for Chromium sessions. pytest is a development dependency.

## Configuration

Copy the template to the Git-ignored local configuration:

```bash
cp config.example.toml config.toml
```

Edit the publication root URL:

```toml
[substack]
publication_url = "https://MY-PUBLICATION.substack.com"
```

Custom HTTPS publication domains are accepted. Credentials, ports, article
paths, query strings, and fragments are rejected. No connectivity is checked.
Missing configuration is allowed for offline checks; all Substack browser
commands require a publication URL and fail clearly if it is missing. Do not put passwords, cookies, or tokens in configuration.

## Story format and validation

```markdown
---
title: "The Last Tree"
subtitle: "Optional subtitle"
description: "Optional description"
series: "Horizons"
episode: 4
tags:
  - science fiction
---

# The beginning

A paragraph with **bold**, *italics*, and a [link](https://example.com).
```

The title must be non-empty text in YAML front matter. Optional text fields may
be omitted or null; episode must be a positive integer if supplied, and tags
must be a list of non-empty strings. Unknown story metadata is ignored so
existing source files can retain fields such as `audio_id`. Duplicate YAML keys,
malformed YAML, invalid UTF-8, and empty article bodies produce clear errors.

Paragraphs, headings, emphasis, block quotes, ordered/unordered lists,
horizontal rules, and links are parsed into Markdown tokens and rendered as
HTML. Raw HTML is escaped. Word counts are approximate counts of parsed text,
excluding markup and link destinations. The body command inserts this rendered
representation in one editor operation instead of typing a long story one
character at a time.

An existing invalid or unreadable cover is an error. Its contents must match its
extension. Validation does not fetch links or embedded Markdown images.

## Check and preview

Activate the virtual environment and run from the project directory:

```bash
publish-to-all --help
publish-to-all check
publish-to-all preview
```

Both commands read `config.toml` and `In/` in the current directory, validate the
story and any matching cover, and calculate the source hash. `check` reports the
filename, title, image, word count, configuration status, and warnings. `preview`
also shows supplied subtitle, description, series, episode, tags, and destination.
Absent optional metadata is omitted. Readiness refers only to local validation;
no network or login check occurs. Missing configuration permits offline use and
is explicitly reported as not configured.

Exit codes: `0` for success (including a missing-cover warning), `1` for an
application validation/read error, and `2` for invalid command arguments. Expected
errors appear on stderr without Python tracebacks. These commands create no
runtime state, modify no inputs, and publish nothing.

Word counts use parsed article-body text, including headings and code, excluding
YAML front matter, Markdown syntax, image alt text, and link destinations.
Apostrophes and internal hyphens stay within words; inline emphasis does not
split a word. Counts are approximate for prose.

## Publication status and runtime storage

```bash
publish-to-all status
```

Run from the project directory to inspect the current validated story in `In/`.
The report shows its filename, title, complete hash, and Substack state, including
stored draft/published URLs and any error. An unseen version reports
`Substack: Not started`. This command may create the runtime data directory and
initialize or migrate the database, but does not register a story or create a
publication attempt. It never changes a stored publication status or publishes
anything. `check` and `preview` remain read-only and do not access the database.
Expected database errors appear on stderr without a traceback (exit code 1).

The database is outside the repository:

| Runtime data | Location (default) | Override |
| --- | --- | --- |
| SQLite database | `~/.local/share/publish-to-all/publications.sqlite3` | `$XDG_DATA_HOME/publish-to-all/publications.sqlite3` |
| Substack browser profile | `~/.local/state/publish-to-all/browser-profile/substack/` | `$XDG_STATE_HOME/publish-to-all/browser-profile/substack/` |
| Future logs | `~/.local/state/publish-to-all/logs/` | `$XDG_STATE_HOME/publish-to-all/logs/` |
| Browser failure screenshots | `~/.local/state/publish-to-all/diagnostics/` | `$XDG_STATE_HOME/publish-to-all/diagnostics/` |

Unset or empty XDG variables use the defaults; non-empty values must be absolute
paths. Runtime path resolution creates no files and rejects paths resolving
inside the project, including symlinks into it. The database directory is created
automatically when opening the database repository. Browser commands create the
Substack profile directory; diagnostics are created on browser failure if a page
can be captured. The logs directory remains reserved.

The current schema version is **6**, stored in SQLite's `PRAGMA user_version`.
Ordered migrations run transactionally when opening the database; a newer,
unsupported version fails clearly without downgrading it. Tests use temporary
databases and isolated XDG directories, never the user's application database.

Story rows contain an internal ID, latest observed source filename/title, unique
source hash, creation time, and last-seen time. Publication rows represent
individual attempts with a story ID, destination, status, optional draft and
published URLs, creation/update/attempt timestamps, body and image stage state
and timestamps, optional sanitized errors, and a reconciliation-required flag.
Version 3 adds body-insertion tracking. Version 4 adds an audit table for
cross-version publication reassociations while preserving existing records.
Version 5 adds cover-image upload tracking and version 6 adds image-attempt audit
history.
Timestamps are UTC ISO 8601 values. Attempt time is when that attempt was
reserved; transitions update its update time. Retries create new attempt rows,
preserving history. No unused destination records are created. The generic
destination field can later support `medium`, `royalroad`, or `hugo` as well as
`substack`; the Substack provider prepares title-only drafts and can add the
body to an already-linked draft in a separate guarded step.

The Markdown file remains canonical. The database stores no story body,
passwords, cookies, tokens, credentials, or browser-session data. Future adapters
must sanitize errors before storing them and must not log browser secrets.

The loader calculates SHA-256 from exact Markdown source bytes, including front
matter, independently of the filename. Changes to metadata or line endings
change this hash; changes to the separate cover image do not. Each different hash is a separate story version even when its filename and title
are unchanged. Renaming identical content retains the same version. Editing a
story therefore does not inherit the earlier version's publication state.

The repository provides registration, attempt reservation, lifecycle updates,
state lookup, and duplicate lookup. Before an adapter creates a draft,
`begin_attempt` checks the exact hash and destination and reserves the attempt in
one write transaction. A `draft_created` or `published` record blocks another
copy even if its URL is unavailable. Known URLs and unresolved `draft_creating`
or `publishing` attempts also block duplicates, across the entire attempt history.
Refusal includes the title and available URLs. There is no `--force` behavior.

Centralized states are `not_started` (no attempt), `draft_creating`,
`draft_created`, `publishing`, `published`, and `failed`. Remote operations with unknown outcomes are marked `failed` with
`needs_reconciliation` set, blocking retries even without a URL. Interrupted
processes may leave `draft_creating`, which also blocks retries. A confirmed
failure without a known or uncertain remote copy can be retried; failure
retains known URLs, which continue to block duplicate creation. Reconciliation
can record a recovered draft or publication on the original failed attempt.
These are local bookkeeping operations only; none performs a network request.

Body insertion has its own `not_started`, `body_inserting`, `body_inserted`, and
`body_insertion_failed` state on the same publication record. The exact draft URL
and story hash association remain intact. An uncertain body result sets the
reconciliation flag and blocks another automatic insertion; the full body is
never stored in SQLite.

Social Preview image upload has its own `image_not_started`, `image_uploading`, `image_uploaded`,
and `image_upload_failed` state. An upload is eligible only after body insertion
is complete. Once file selection begins, any uncertain outcome requires manual
inspection and blocks automatic re-upload. SQLite stores no image bytes.

The abstract publisher contract consumes the existing validated `Story`, a
reserved publication record, and the repository. Draft preparation and final
publication are separate methods so future providers can be implemented
independently. Final publication remains disabled.

## Installing Playwright

From the project directory, use the same virtual environment as the CLI:

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m playwright install chromium
```

The dependency installation includes the Python Playwright package; the second
command downloads its matching Chromium runtime. Repeat the browser installation
when upgrading Playwright. Substack browser commands require a graphical desktop
because they launch a visible browser. On supported Linux systems, missing system
libraries can be installed with `python -m playwright install-deps chromium`
(this may require administrator privileges). See the official
[Playwright installation guide](https://playwright.dev/python/docs/library) and
[browser documentation](https://playwright.dev/python/docs/browsers).

## Logging into Substack

Configure `[substack].publication_url` in `config.toml`, then run:

```bash
publish-to-all substack-login
```

Chromium opens the configured publication's dashboard entry page (`/publish/home`)
with a dedicated persistent profile. Substack may redirect to its login page.
Complete login manually in the browser, including email verification, MFA, or
passkeys. If needed, use Substack's Sign in control yourself. The application
never asks for a password in the terminal, types a password, or reads or stores
your password. Do not save passwords in this dedicated browser profile.

Keep the browser open and press Enter in the terminal when you finish. There is
no login time limit. The application loads the publication and then its dashboard, checks visible
account/navigation controls, reports the result, and closes Chromium cleanly.
Enter only triggers verification; it does not count as evidence of authentication.
Ctrl+C or terminal EOF cancels and closes the browser. If you close Chromium
first, verification may fail; rerun the command to check the saved session.

The implementation uses Playwright's
[persistent browser context](https://playwright.dev/python/docs/api/class-browsertype#browser-type-launch-persistent-context).
Use only one browser command at a time: Chromium cannot share an open profile
between processes. This profile is separate from your everyday browser.

## Checking the session

```bash
publish-to-all substack-session
```

This opens headed Chromium with the same profile, visits the configured publication,
then navigates to its `/publish/home` dashboard entry page. It reports
`Authenticated`, `Not authenticated`, `Unknown`, or `Temporarily rate limited`. Both visits allow ten seconds
for hydration and redirects after navigation (navigation has a 30-second limit).
The checker combines visible account/profile controls, dashboard links, reader
navigation, publisher navigation, login forms, and login redirects. Access to the
configured publication's dashboard with multiple creator controls is strong
authentication evidence. A URL, avatar, or Dashboard link alone is insufficient.
Contradictory evidence within or between pages returns `Unknown`.

An HTTP 429 response, an exact visible `Too many requests` message, or the same
recognized Substack error-page title returns `Temporarily rate limited`. The
checker stops at the first such signal and does not refresh or retry. It reports
the redacted final URL, the allowlisted page title, and that rate limiting was
detected. Retry `publish-to-all substack-session` manually later.

When the result is `Unknown`, the command automatically prints the final URL,
page title, up to 16 recognized visible control labels, and a diagnostic screenshot
path. URL queries, fragments, and unfamiliar paths are redacted. Nonstandard page
titles are redacted, and control labels use a fixed vocabulary so account names,
email addresses, and private content are not recorded. Screenshots preserve layout
but redact text, form values, images, and embedded content. Capture failure is
reported without changing the authentication result. To show these diagnostics
for any result, run:

```bash
publish-to-all substack-session --debug
```

Unknown does not mean the session has expired. Unfamiliar/translated UI can still
require a detector update. Authentication does not establish ownership or write
permissions. HTTPS redirects among the configured publication/custom domain and
Substack hosts are accepted for classification. No controls are clicked and no
undocumented API calls are made. Exit status is `0` only for Authenticated and `1`
for Not authenticated, Unknown, Temporarily rate limited, cancellation, or a
configuration/browser error.

The profile is stored at:

```text
~/.local/state/publish-to-all/browser-profile/substack/
```

If `XDG_STATE_HOME` is set, it is stored at
`$XDG_STATE_HOME/publish-to-all/browser-profile/substack/` instead. The profile
contains sensitive cookies and other session information: keep it local, never
share or commit it. Publish to All stores no password and never copies browser
secrets into SQLite or logs browser-storage contents. The `substack-login` and `substack-session` commands do not
read stories or open/change the story database.

Browser failures attempt to save a timestamped screenshot under
`~/.local/state/publish-to-all/diagnostics/` (or
`$XDG_STATE_HOME/publish-to-all/diagnostics/`) and report its path. These directories
are private to the current user and screenshots use private file permissions.
Screenshot text, form values, images, and embedded content are redacted. If Chromium cannot launch or a page cannot be captured,
the error explicitly says that a screenshot is unavailable. Raw browser exception
text is omitted to avoid exposing login tokens in URLs.

No draft is created. No story content is typed, no image is uploaded, no settings
are changed, and nothing is published by either command.

## Verification

Automated tests mock Playwright and do not require a real Substack account:

```bash
python -m pytest
python -m compileall -q src tests
publish-to-all --help
publish-to-all check
publish-to-all preview
publish-to-all status
```

For a separate manual browser verification, configure your publication and run
`substack-login`, finish login, then press Enter. Run `substack-session` in a new
process to verify persistence after the first browser closes. Check the reported
authentication state; do not create or open a draft. Live Substack UI changes may
require updates to the conservative detection rules. Manual account verification
is not part of the automated test suite.

## Create a title-only Substack draft

After the automated checks pass, activate the virtual environment and run from
the project directory. This is the separate, manual first live draft test:

```bash
source .venv/bin/activate
publish-to-all substack
```

The command reads and validates `config.toml`, the single Markdown story in `In/`,
and any matching cover, and calculates the existing exact-source SHA-256 hash.
It checks duplicate state and requires an existing browser profile before opening
headed Chromium. A saved profile alone is not proof of authentication: the command
verifies the live session before reserving an attempt or creating a post. This
preflight goes directly to `/publish/home`, and the editor workflow reuses that
verified page instead of loading the dashboard again. If login cannot be confirmed,
run `publish-to-all substack-login` and try again.

If the preflight receives HTTP 429 or recognized rendered rate-limit evidence, it
stops without reserving an attempt, creating a first-run database, opening an
editor, or changing publication state. It does not retry automatically. Run
`publish-to-all substack-session` manually later to check whether the limit cleared.

The browser uses the configured publication's dashboard and visible creation
controls (Create → Article, or the older New post workflow), following
[Substack's documented editor workflow](https://support.substack.com/hc/en-us/articles/360037831771-How-do-I-publish-a-new-post-on-Substack).
The parsed front-matter title is the only field filled. The command deliberately
does **not** insert the story body, subtitle, description, cover image, tags,
series, or episode, and does not change audience, email, SEO, or scheduling.
Publish, Send, Schedule, and Continue controls are never used. The publisher's
final-publication method is disabled.

The command starts observing navigation before it clicks the creation control.
As soon as the page enters a numeric `/publish/post/<id>` editor URL, that
sanitized URL is persisted while the attempt remains `draft_creating`. It is
provisional identity, not creation success. If title entry or save confirmation
then fails, the failed attempt retains the URL for deterministic reconciliation.

The editing tab stays open while the command waits for observable save evidence.
After confirming one editable title input, it fills the exact title, resolves the
current visible input again to catch an editor rerender, and continuously checks
that the exact title and numeric URL remain unchanged. Success requires an
observed fresh `Saving → Saved` transition caused after save observation began.
A stale Saved label or an editor URL alone does not prove success. The command
does not reload or open another tab to confirm the save. The wait is bounded;
uncertainty fails the attempt while retaining the provisional URL and blocking
automatic retry.

The success report explicitly lists `Added: Title`, `Not added yet: Story body,
Image`, and `Nothing was published`. Inspect local state with:

```bash
publish-to-all status
```

An exact story version with a draft or published record is refused, including
records with no URL. Attempt reservation repeats the duplicate check atomically
after authentication. There is no force option. Changes to the Markdown produce
a different version; changing only the cover does not.

Failures stop automation immediately, report the high-level step, attempt to
capture a private redacted screenshot under the diagnostics directory described
above, and mark the attempt failed with a sanitized error. If Chromium is closed
or capture fails, the report says the screenshot is unavailable. A failure after
new-post creation begins may have left a remote draft: such attempts block retry
even without a URL. Review the publication's drafts manually and reconcile the
original record before retrying; there is no automatic retry or reconciliation
CLI. A database write failure is reported explicitly and may leave the attempt
in progress. Do not remove state or alter the story merely to bypass protection.

The selectors deliberately stop on unfamiliar, ambiguous, or different-publication
UI. Custom-domain redirects to a different editor host also stop until that host
can be safely tied to the configured publication. Live UI compatibility still
requires your manual test; automated tests never use your real account or profile.

## Reassociate an untouched draft after a local correction

If a local title correction changes the story hash after an empty draft was
created, explicitly transfer that untouched draft association to the current
story version with the exact old hash:

```bash
publish-to-all substack-reassociate-version \
  --from-hash OLD_EXACT_STORY_HASH
```

This is a local SQLite transaction. It does not open a browser or contact
Substack. It requires exactly one old Substack record in `draft_created`, a
numeric draft editor URL, untouched body state, no publication, in-progress,
error, or reconciliation state, no Substack record on the current hash, and one
local owner for the draft URL. The publication row moves to the current story
version; the old story row remains and a separate audit row records the transfer.
The new version keeps duplicate protection and the old version no longer owns
the active draft association. Any failed precondition rolls the transaction back.

## Repair an empty title on the linked Substack draft

Use the dedicated repair command when the exact current story already owns a
numeric `draft_created` URL but that draft's title is empty:

```bash
publish-to-all substack-title
```

The command recalculates the current Markdown hash, loads only its existing
Substack record, and opens only the stored numeric editor URL through the saved
authenticated profile. It refuses missing, stale, failed, ambiguous, published,
or reconciliation-required state. It never enters the new-post creation flow.

Before editing, it requires stable evidence of the intended authenticated draft
editor and confirms that both the editable title and body are empty. It inserts
the exact parsed front-matter title without changing the Markdown source. A
nonempty title is reported and never overwritten. Existing or uncertain body
content also stops the command for manual review.

The repair stays on the same editor page and requires the exact visible title
plus a fresh bounded autosave signal. It does not reload, reopen, or navigate
away after insertion. It does not touch the body, upload an image, change any
settings, or click Continue, Publish, Send, or Schedule. Nothing is published.
Rate limiting stops the command immediately without retrying. If rate limiting
or another failure occurs after editing begins, the retained draft association
is marked for manual reconciliation because the title may or may not have saved.

## Add the body to the linked Substack draft

Run this only after the exact current story has a reconciled `draft_created`
record with a numeric editor URL:

```bash
publish-to-all substack-body
```

This command never uses the new-post flow. It validates the current Markdown,
recalculates its exact source hash, loads the matching publication record, and
requires a linked URL with no failed or reconciliation-required condition. It
opens exactly that URL with the saved persistent browser profile and requires a
stable authenticated draft editor, the configured publication host, an editable
title and body, multiple creator controls, and the exact visible story title.

Before writing, it classifies the existing rendered body. An empty editor may
proceed. Minimal, substantial, unknown, or already matching body content stops
the command for manual review, so it neither appends a duplicate nor clears or
overwrites remote text. The parsed Markdown body excludes YAML front matter and
is inserted as rendered HTML, preserving paragraphs, headings, bold, italics,
block quotes, lists, horizontal rules, and links. The source Markdown is not
changed.

After the one-shot insertion, the command waits on the same page for an exact
rendered-body check and a fresh `Saving` to `Saved` transition or equivalent
fresh saved state. It does not reload, reopen the editor, navigate away, retry,
or click Continue, Publish, Send, or Schedule. It does not upload the cover
image or change the title, audience, or publication settings. Nothing is
published.

HTTP 429 or an exact rendered `Too many requests` signal stops the command
immediately without navigation or retry. If insertion has begun, the draft may
contain some or all of the story; local state is marked conservatively to block
another automatic insertion. Use `substack-inspect-draft` with the retained URL
to inspect an uncertain result before any recovery.

## Upload the article Social Preview image

Substack's rendered editor distinguishes several unrelated image features. In
particular, `File Settings → Thumbnail` edits the thumbnail of an embedded file
attachment. It is not the article image. Inline body images and the publication
cover are separate again. For an article card/share image, the current rendered
path is `Post settings → Social preview → Edit social preview → Image`.

After the body has been inserted into an existing linked draft, run:

```bash
publish-to-all substack-image
```

The command recalculates the exact current story hash and accepts only its
existing `draft_created` record with a unique numeric draft URL, `body_inserted`,
`image_not_started`, and no reconciliation condition. It uses `In/Social.png`
when present, otherwise falls back to the same-basename general story image. It
validates the selected image data locally, verifies the saved authenticated
session, and opens only that stored draft URL. It confirms the exact title and a
substantial body before any remote write. HTTP 429 stops the operation immediately.

The only upload route is `Post settings → Social preview → Edit social preview →
Image`. Selectors stay within the Social Preview settings row and editor dialog.
The File Settings attachment thumbnail, inline body image controls, publication
cover/logo controls, and generic attachments are rejected; there is no fallback
to those features.

File selection occurs once on the exact Social Preview image input. Success
requires one input event, one change event, input replacement or equivalent
Social Preview rerender evidence, processing or a preview-related mutation, and
an appearing image preview. The command then clicks only the Save/Done control
inside the Social Preview editor and requires a fresh `Saving → Saved` transition
from the main editor. It verifies the URL, title, and substantial unchanged body
without reloading or navigating away. It never clicks Continue, Publish, Send,
or Schedule, and it does not publish.

If anything fails after file selection begins, local state changes to an uncertain
failed image attempt and blocks retry. The title, body stage, draft URL, story hash,
and duplicate protection remain intact. The command never retries file selection.
On confirmed success it records `image_uploaded`, clears the image error, and
records the upload timestamp.

The manual observer remains available for UI diagnostics:

```bash
publish-to-all substack-image-observe
```

The observer opens only the numeric draft URL already linked to the exact current
Markdown hash. It verifies the title and populated body, opens the social preview
image editor, installs safe DOM and response counters, and stops before selecting
a file. Select and save the image yourself in the visible browser, then press
Enter in the terminal. The observer records which file input received events,
input replacement, DOM mutations, processing/preview signals, dialog rerendering,
safe response counts, navigation, and fresh save signals. It never reads file
names or contents, cookies, tokens, request bodies, URLs from observed responses,
or response headers. It never clicks Continue, Publish, Send, or Schedule, and it
does not update local image state.

To verify only the navigation path and current Social Preview image state, run:

```bash
publish-to-all substack-social-preview-inspect \
  --draft-url "https://YOUR-PUBLICATION.substack.com/publish/post/123456"
```

This headed read-only command opens only the supplied numeric draft URL. It waits
for the editor-specific `Settings` button, the portal-backed `Post settings`
dialog, its exact `Social preview` row and `Edit` button, and the `Edit social
preview` dialog. It reports the image state and local Save/Done control without
selecting a file or clicking Save, Done, Continue, Publish, Send, or Schedule.

The older File Settings → Thumbnail implementation is quarantined in the
historical helper module and is not reachable from `substack-image`.

Resolve that block only after a fresh read-only inspection of the linked draft:

```bash
publish-to-all substack-image-reconcile --story-hash "EXACT-CURRENT-STORY-HASH"
```

The required hash must exactly match the current Markdown. The command accepts
either a clean `image_not_started` record or an uncertain `image_upload_failed`
record with a unique stored numeric draft URL, opens only that URL, and inspects
the article Social Preview image state. It never selects a file or changes the
remote draft. A positively present Social Preview image records
`image_uploaded`. A positively empty image resets a failed record to
`image_not_started` and leaves an already clean not-started record unchanged.
An unknown result or rate limit always leaves local image state unchanged. An
earlier failure remains in local image-attempt audit history even when its active
error and reconciliation flag are cleared.


### Inspect final publication options

After a linked draft has its title, substantial body, and Social Preview image fully prepared, run:

```bash
publish-to-all substack-publish-inspect
```

This opens only the already linked numeric draft, verifies the prepared editor and authenticated
session, and opens the final publication configuration screen through its editor-scoped `Continue`
control. It reports the visible options, current defaults, final action controls, and a safe exit.
The command makes no local or remote changes, does not send email, does not schedule, and does not
publish. It is intended to inform a later, separately authorized publication automation stage.

### Forget a draft manually deleted in Substack

If a linked draft was manually deleted in Substack while SQLite still reports it
as active, run the dedicated recovery command with its complete historical hash:

```bash
publish-to-all substack-forget-deleted-draft \
  --story-hash "EXACT-HISTORICAL-STORY-HASH"
```

The command requires that historical hash to exist in SQLite and requires exactly
one Substack record with a stored numeric draft URL for that story version. That
record must still be in the draft-created workflow. It refuses the cleanup if any
Substack record for the hash has a published URL or local published state. Using
the saved authenticated Playwright profile, it opens only the stored URL once and
looks for strong deletion evidence,
such as an authenticated 404/410 response, an explicit not-found/deleted state,
the exact missing-post response inside Substack's creator editor shell, or a
redirect to the authenticated Posts listing where that numeric URL is absent.
Any redirect to a public `/p/...` post refuses cleanup.
An editor that still exists, an authentication failure, a timeout, a generic error,
or any unknown result leaves SQLite unchanged. HTTP 429 or `Too many requests`
stops the check immediately without a retry or reload and also leaves SQLite
unchanged.

After positive verification, one transaction removes the obsolete publication row,
its body/image/error/reconciliation state, image-attempt rows, and any reassociation
rows owned by that publication. This also removes duplicate protection for the
deleted draft. The historical story-version row is removed when no remaining
publication or reassociation references it. The current Markdown and all unrelated
story versions and publication records are untouched, so an edited current version
without a record continues to show `Substack: Not started`.

This recovery does not create a replacement draft, modify the remote publication,
or publish anything. Run a future draft workflow separately only when you intend
to create a new draft.

### Reconcile an uncertain Substack attempt

Inspect any known numeric draft editor URL without changing the draft or SQLite:

```bash
publish-to-all substack-inspect-draft --draft-url "https://YOUR-PUBLICATION.substack.com/publish/post/123456"
```

The report includes the visible title, an `empty`/`minimal`/`partial`/`substantial`
classification of editable body text, the separate Social Preview image state,
whether an editor or legacy cover/thumbnail image is visible,
the visible save state, conservative draft-editor verification, and allowlisted
URL/title diagnostics. Run it once for each draft you want to compare. It clicks
only the settings controls needed to inspect Social Preview state; it does not
type, save, upload, reload, or access SQLite. HTTP 429 and rendered
`Too many requests` evidence stop inspection immediately without retrying.

From the project directory, with the virtual environment active, run:

```bash
publish-to-all substack-reconcile
```

When the failed attempt already records a numeric draft URL, this opens and
verifies that exact URL directly with the authenticated browser profile. It does
not create a draft, edit content, delete anything, or publish. The default command
leaves publication state unchanged. An empty or different visible title does not
defeat exact-URL identity; the page must still remain at that URL and show stable,
corroborating authenticated draft-editor evidence. Public, published,
inaccessible, wrong-publication, ambiguous, and rate-limited pages are rejected.

When no numeric URL was captured, the command falls back to the rendered Posts
listing and reports exact-title links only when their semantic row explicitly
says `Draft` and the link identifies a numeric editor URL on the configured
publication.

If exactly one matching URL also matches the URL recorded by the failed attempt,
the command reports that evidence. You can then explicitly link the local record:

```bash
publish-to-all substack-reconcile --link
```

Linking updates the same local attempt to `draft_created` and clears its resolved
active failure. Creating a duplicate remains blocked. A changed attempt, multiple
listing matches, no visible listing match, unsupported listing structure, or an
authentication/navigation uncertainty leaves state unchanged. A listing
can be incomplete or paginated, and an early failure may have left an untitled
draft. Therefore no visible match means **Unknown**, never proof of absence.
This command does not clear failed attempts or authorize a fresh creation. If it
reports Unknown, inspect the publication's drafts manually before any further
reconciliation work; do not retry `substack` or delete local state to bypass the block.

If you manually identify the draft left by an unresolved attempt, supply its
numeric editor URL directly:

```bash
publish-to-all substack-reconcile --draft-url "https://YOUR-PUBLICATION.substack.com/publish/post/123456"
```

This mode accepts only an HTTPS numeric editor URL on the configured publication.
It opens that URL with the saved authenticated browser profile and requires stable,
explicit draft-editor evidence before linking the existing failed attempt. It never
edits the page or clicks Publish, Send, Schedule, or similar controls. Malformed,
different-domain, public-post, inaccessible, published, and ambiguous pages leave
SQLite unchanged. A successful link records the sanitized editor URL, moves the
same attempt to `draft_created`, clears its reconciliation-required flag and
resolved current error, preserves its original attempt timestamps, updates its modification
timestamp, and keeps duplicate protection active.

When the exact current story already has a linked `draft_created` record, supplying
another verified URL alone leaves that association unchanged. Replace only the
local association with the explicit flag:

```bash
publish-to-all substack-reconcile \
  --draft-url "https://YOUR-PUBLICATION.substack.com/publish/post/654321" \
  --replace-linked-draft
```

This compare-and-set update applies only to the current story hash and its existing
Substack record. It does not modify or delete either remote draft, and the updated
record continues to block duplicate creation.

The `Open new-post editor` step recognizes exact accessible creation labels
including `New post`, `Create post`, `Create`, `Write`, `Write a post`, and
`New article`, with article/text-post choices when a menu opens. A rendered
Posts link can lead to the creator listing. Ambiguous controls stop the operation.
Existing editor URLs stop creation. Creation links must target a recognized
new-editor path on the configured publication. Publishing, sending, scheduling,
and continuation controls remain forbidden.

Failures at this step now report and retain a sanitized final URL, allowlisted
page title and visible control labels, and a redacted screenshot path. Query
strings, fragments, nonstandard titles/paths, arbitrary labels, credentials,
and browser storage are not logged. Unsupported titles are explicitly marked
redacted. Screenshots continue to hide text, form values, and images.

The first reported failure's screenshot was inspected during debugging. Its
redacted layout resembles a dashboard, but it cannot establish the final URL,
control labels, or remote draft existence. The original step could fail either
while finding a creation control or while waiting for the subsequent article
choice/title field. There is insufficient evidence to distinguish these cases.
The local failed attempt has no recorded draft URL, so automatic association
with a remote draft is intentionally unavailable for that attempt.

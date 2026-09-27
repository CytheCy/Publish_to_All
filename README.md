# Publish to All

A local Python utility for preparing a finished Markdown story and its cover
image for publishing destinations. Phase 1 targets Substack drafts for manual
review, with no automatic final publication.

**Current milestone: title-only Substack drafts.** Local checks, preview,
versioned SQLite publication state, manual login, and saved-session verification
are implemented. `publish-to-all substack` creates a draft containing **title
only**. It deliberately does not add the story body or image and never publishes,
sends, schedules, or configures publication settings. A live draft has not been
created during development of this stage.

## Structure

```text
In/
├── any-story-name.md
└── any-story-name.png
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
    publishers/substack.py    # title-only adapter and failure recovery
    publishers/base.py        # abstract provider contract using validated Story
tests/                       # configuration, story, state, and CLI tests
config.example.toml
pyproject.toml
```

Only one Markdown story should be directly inside `In/` at a time. The Markdown
file and optional image must share the same base filename. Supported cover
extensions are `.png`, `.jpg`, `.jpeg`, and `.webp` (extensions are case-insensitive).
Nested stories and unrelated images are ignored. Multiple Markdown stories or
multiple matching covers fail clearly. A missing cover produces a warning.
Original files are never renamed, moved, or rewritten.

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
excluding markup and link destinations. Rendering here does not yet establish
how formatting will transfer into the live Substack editor.

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

The current schema version is **2**, stored in SQLite's `PRAGMA user_version`.
Ordered migrations run transactionally when opening the database; a newer,
unsupported version fails clearly without downgrading it. Tests use temporary
databases and isolated XDG directories, never the user's application database.

Story rows contain an internal ID, latest observed source filename/title, unique
source hash, creation time, and last-seen time. Publication rows represent
individual attempts with a story ID, destination, status, optional draft and
published URLs, creation/update/attempt timestamps, and an optional sanitized
error, and a reconciliation-required flag. Version 2 adds that flag while
preserving existing records. Timestamps are UTC ISO 8601 values. Attempt time is when that attempt was
reserved; transitions update its update time. Retries create new attempt rows,
preserving history. No unused destination records are created. The generic
destination field can later support `medium`, `royalroad`, or `hugo` as well as
`substack`; the Substack provider currently prepares title-only drafts.

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

The abstract publisher contract consumes the existing validated `Story`, a
reserved publication record, and the repository. Draft preparation and final
publication are separate methods so future providers can be implemented
independently. No draft has been created by this application.

## Installing Playwright

From the project directory, use the same virtual environment as the CLI:

```bash
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m playwright install chromium
```

The dependency installation includes the Python Playwright package; the second
command downloads its matching Chromium runtime. Repeat the browser installation
when upgrading Playwright. All three Substack commands require a graphical desktop
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

The editing tab stays open while the command waits for observable save evidence.
Success requires a fresh saved indication after title entry, or the exact title
in a separately loaded copy of an observed stable draft URL. A stale Saved label
or an editor URL alone does not prove success. The wait is bounded; there is no
long fixed sleep or terminal confirmation. Chromium closes after verification.
Only recognized URLs from the configured publication are recorded; queries and
fragments are stripped. If save evidence is sufficient but no stable URL is
recognized, SQLite records `draft_created` with a null URL and the report says
`Draft URL: Not available`.

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


### Reconcile an uncertain Substack attempt

From the project directory, with the virtual environment active, run:

```bash
publish-to-all substack-reconcile
```

This uses the authenticated browser profile to inspect the rendered Posts listing.
It does not open an editor, create a draft, edit content, delete anything, or publish.
The default command leaves publication state unchanged. It reports exact-title
links only when their semantic listing row also explicitly says `Draft` and the
link identifies a numeric editor URL on the configured publication.

If exactly one matching URL also matches the URL recorded by the failed attempt,
the command reports that evidence. You can then explicitly link the local record:

```bash
publish-to-all substack-reconcile --link
```

Linking preserves the original failure message and updates the local attempt to
`draft_created`. Creating a duplicate remains blocked. A changed attempt, missing
recorded URL, multiple matches, no visible match, unsupported listing structure,
or an authentication/navigation uncertainty leaves state unchanged. A listing
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
same attempt to `draft_created`, clears its reconciliation-required flag, preserves
its failure history and original attempt timestamps, updates its modification
timestamp, and keeps duplicate protection active.

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

# Publish to All Medium inspector

This Manifest V3 extension inspects Medium's normal story editor and offers separately guarded title and body fills. The body control is limited to verified draft `/p/fd6c1824c4de/edit`. For the source story `https://cyporter.substack.com/p/saturation-of-artificial-intelligence-f8d`, URL Import is terminally retired. The extension does not change settings or publish.

## Preserved URL Import history

| Attempt | Activation | Reconciled result |
| --- | --- | --- |
| 1 | Programmatic `HTMLElement.click()` on Medium's native Import button; untrusted click | `NO_DRAFT_FOUND` |
| 2 | User's physical click on Medium's native Import button; observed trusted click with valid button identity | `NO_DRAFT_FOUND` |

Attempt 2's legacy reservation storage still reads `ARMED`, zero observed clicks, and `PENDING`. That storage did not consume the physical click. The extension reports the stored reservation and the observed real-world result separately. A fresh, conclusively loaded Drafts list contained no visible entries or exact-title matches after attempt 2. There have been **two real attempts**; a third URL Import attempt is forbidden.

The immutable `MEDIUM_URL_IMPORT_RETIRED` history lives in `medium-retirement.js`. Existing attempt records and preparation diagnostics remain readable. Inspection does not clear, reset, migrate, or rewrite them. The background worker rejects both legacy write messages. On-page Fill and Prepare controls are disabled and have no active click listeners in the retired workflow. The old diagnostic code remains available for historical analysis.

## Normal editor inspection

The user opens a blank draft using Medium's **Write** control. The extension does not click it. **Inspect Current Medium Page** reports the editor URL shape and draft identity, visible editable candidates, title and body classification evidence, empty/nonempty state, Publish control, draft/save indicator, and visible story/settings controls. It does not click, fill, or modify any Medium control.

`/new-story` is recognized as an editor route, but route alone never verifies a draft. The inspection reports structural evidence, candidate ancestry and DOM order, contenteditable attributes, focusability attributes, child structure, CSS pseudo-element prompts, and text provenance. It prints short text only when the DOM clearly marks it as editor scaffolding; other text is represented by a fingerprint and counts.

`BLANK_DRAFT_EDITOR_VERIFIED` supports separate title/body editables and Medium's single composite contenteditable. A composite root must have the `/new-story` or exact `/p/<12-hex-id>/edit` route, an `article.postArticle` ancestor, textbox and contenteditable semantics, multiple editor markers, a section child, and one visible Publish control. Within that root, the inspector inventories story blocks and only treats spans with both `defaultValue` and `defaultValue--root` classes as Medium-owned prompt scaffolding. A unique title and body subregion, empty or scaffolding-only content throughout the root, and no conflicting editable are required for blank verification. Body-level empty contenteditables with `tabindex=-1` and no editor ancestry are reported as auxiliary. Unresolved editor text or title/body identity yields `EDITOR_STRUCTURE_AMBIGUOUS`. `NOT_MEDIUM_EDITOR` means structural evidence is absent. Existing confirmed content yields `NONEMPTY_DRAFT`.

After Medium assigns a draft edit route, the read-only inspector recognizes the authored title through its story block, exact packaged title, and fingerprint. `MEDIUM_TITLE_VERIFIED_BODY_BLANK` requires one composite root, one title and body block in order, the expected title fingerprint, the remaining body prompt or an empty body, no other story content, and one Publish control. It reports the draft ID and continuation safety without editing the draft. The extension stores successful title input evidence by draft ID for later inspection when the content script reloads; the DOM and route guards must still pass before that stored evidence is shown.

The extension places a title control in its own Shadow DOM panel on `/new-story`. It uses the same fresh title readiness result as the popup and fill handler. Inspection reports the control's disabled state, CSS, hit test, listener generation, and any separate UI blocking reason. Reinjection replaces stale panels and detaches old listeners. Clicking it reacquires and verifies the DOM, selects only the title block, and inserts the packaged story title with browser editing semantics. It checks the exact title and fingerprint, application input event, intact body markup and prompt, and story editor ancestry. A bounded read-only interval observes autosave. Any changed or ambiguous structure stops the workflow; no automatic repair runs. The button is disabled after an edit attempt. The packaged `story-title.js` is generated from the same `load_story(In/)` pipeline as publishing with `.venv/bin/python -B extension-dev/export-story-title.py`; validation fails if it is stale.

The body control uses the same Shadow DOM and fresh readiness pattern on the verified draft route. The Python `load_story(In/)` pipeline exports the canonical body and Markdown token analysis to `story-body.js` with `.venv/bin/python -B extension-dev/export-story-body.py`. The current source has 18 level-one numbered headings and 18 paragraphs; other structural features are absent. Unsupported Markdown disables the body control. The inspector resolves the live body paragraph once per inspection; readiness and the click guard use that same result, with separate diagnostics for route, draft, root, title, body, authored content, continuation safety, and Markdown support. The composite contenteditable root is reported separately from the body paragraph. On click, the extension rechecks draft identity, exact title, fingerprint, unique body block, prompt, and blank state. Browser editing commands insert each block, including heading structure and paragraph boundaries. It reacquires the live editor after each command, checks every inserted block, then verifies the complete body fingerprint, title, route, and input evidence. It observes the autosave indicator read-only. A failed check leaves an ambiguous result and disables repeat fill. No Save, settings, or Publish action is invoked.

**Resume verified Medium body** is a separate recovery operation. Readiness requires the exact persisted `/p/fd6c1824c4de/edit` checkpoint: verified title and fingerprint, unique composite editor and body region, exactly one paragraph with canonical text `01` and fingerprint `fnv1a32:20ed70ba`, an exact canonical-prefix reconciliation through block 1, no other body content, and no observed Publish action. The normal fill stays blocked. On activation, recovery checks the checkpoint again, changes block 1 to an actual heading with the editing command, and verifies its unchanged text and heading format before inserting block 2. Each subsequent edit occurs once, followed by at most eight read-only DOM reacquisitions. Every completed block is verified by kind, normalized length, and fingerprint; a failed check stops immediately and records the last verified block. A successful result requires all 36 blocks, the exact full-body fingerprint `fnv1a32:342d11eb`, 18 headings, 18 paragraphs, unchanged title and draft, and no Publish action. Recovery progress is stored locally and a second invocation is blocked. The operation does not open Settings or Publish.

Recovery inspection reports each prerequisite as PASS or FAIL, including route, draft ID, title text and fingerprint, packaged source and full-body fingerprint, canonical blocks, partial reconciliation and fingerprint, body block count and formatting, Publish state, editor structure, unique body subregion, and unexpected content. A blocked result includes the first failed guard and its specific reason. Recovery uses the same packaged title and body values, normalization, and source validation as normal body readiness; it also pins the exact recovery checkpoint. The local `01` paragraph fixture passes all guards without editing the page. A live inspection report is needed to identify any remaining DOM-specific failure.

The future canonical-link target is `https://cyporter.substack.com/p/saturation-of-artificial-intelligence-f8d`. Settings controls are inventoried only.

## Manual discovery

1. Reload the unpacked `chrome-extension/` in `chrome://extensions/`, then refresh the existing draft `/p/fd6c1824c4de/edit` without editing it.
2. Run **Inspect Current Medium Page** only and return the complete report. It should show `MEDIUM_TITLE_VERIFIED`, `MEDIUM_BODY_PARTIAL`, `Partial body recovery classification: EXACT_CANONICAL_PREFIX`, current body safe text `01`, fingerprint `fnv1a32:20ed70ba`, `Body recovery eligibility: READY`, `Body recovery reason: None`, `Recovery start block: 1`, `Recovery next canonical block: 2`, `Block 1 requires formatting repair: Yes`, `Body recovery control interactability: ENABLED`, `Body fill eligibility: NOT_READY`, and `Publish capability: No`.
3. Stop. Do not click **Resume verified Medium body**, **Fill verified Medium body**, **Publish**, or **Settings**.

The first packaged body block is the heading text `01` (two characters, fingerprint `fnv1a32:20ed70ba`); the source Markdown marker `# ` is not sent to Medium. The prior `BLOCK_POSTCHECK_FAILED` occurred after prompt deletion and the first `insertText`, before heading formatting. Its three possible immediate causes are missing/reordered block, text other than `01`, or a transient title mismatch. The prior report did not capture the text or block order at that instant, so the next read-only inspection can identify the remaining content but cannot prove a past transient. A `font` wrapper alone does not make the local Chrome postcheck fail. The local fixture also models a transient block reflow that fails the postcheck yet leaves only `<font>01</font>` afterward, as well as a two-character noncanonical artifact.

The persisted-state fixture covers direct paragraph text, `<font>` and `<span>` wrappers, whitespace, separate sections, and a wrapped title with no fill telemetry. Inspection reads the resolved paragraph text as the first canonical block while reporting its paragraph formatting discrepancy separately. If body reconciliation is unavailable, the popup says `Partial body detected: Not observed` instead of displaying a false negative.

## Local validation

```sh
node --test chrome-extension/test/*.test.js
.venv/bin/python -B extension-dev/fixtures/chrome_medium_body_fixture.py
.venv/bin/python -B extension-dev/fixtures/chrome_medium_partial_fixture.py
.venv/bin/python -B extension-dev/fixtures/chrome_medium_recovery_fixture.py
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q src tests
git diff --check
.venv/bin/python -B extension-dev/extension_tree.py check
```

The extension tests use local DOM fixtures and do not contact Medium.
Python exporters and Chrome fixtures live under `extension-dev/`, outside the load-unpacked directory. Pytest disables bytecode writes during collection for any future Python fixture it imports. The `compileall` command targets only `src` and `tests`; it does not enter `chrome-extension/`.

The final `check` command fails on `__pycache__`, `.pyc`, `.pyo`, or any other underscore-prefixed entry in `chrome-extension/`. It never removes files. For explicit cleanup of generated Python artifacts only, run `.venv/bin/python -B extension-dev/extension_tree.py clean`, then run `check` again. Cleanup preserves all other files, including source files inside a cache directory, so any remaining forbidden entry is reported.

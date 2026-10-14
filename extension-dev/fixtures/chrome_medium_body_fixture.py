"""Network-free Chrome exercise of the verified draft body operation."""

import json
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright


EXTENSION = Path(__file__).resolve().parents[2] / "chrome-extension"
URL = "https://medium.com/p/fd6c1824c4de/edit"
HTML = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<article class="postArticle postArticle--full"><div id="editor_7" role="textbox"
 contenteditable="true" class="postArticle-content js-postField js-notesSource editable">
<section class="section-inner sectionLayout--insetColumn"><h3 class="graf graf--h3 graf--leading graf--title"
 data-testid="editorTitleParagraph"><font>Saturation of Artificial Intelligence</font></h3>
<p class="graf graf--p graf-after--h3 graf--trailing" data-testid="editorParagraphText"><span
 class="defaultValue defaultValue--root">Tell your story…</span></p></section>
</div></article><button id="publish">Publish</button><button id="settings">Settings</button>
<span role="status">Saved</span></body></html>"""


def main():
    chrome = shutil.which("google-chrome")
    if not chrome:
        raise SystemExit("google-chrome required")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, headless=True)

        def exercise(mode):
            page = browser.new_page()
            page.route("https://medium.com/**", lambda route: route.fulfill(
                status=200, content_type="text/html", body=HTML))
            page.goto(URL if mode != "wrongDraft" else
                      "https://medium.com/p/aaaaaaaaaaaa/edit")
            for file in ("story-title.js", "story-body.js", "medium-editor-inspector.js",
                         "medium-body-fill.js", "medium-title-controls.js"):
                page.add_script_tag(path=str(EXTENSION / file))
            result = page.evaluate("""async mode => {
              const metadataBefore = document.head.innerHTML;
              const editor = document.querySelector('#editor_7');
              const title = editor.querySelector('[data-testid="editorTitleParagraph"]');
              const body = editor.querySelector('[data-testid="editorParagraphText"]');
              let publish = 0, settings = 0;
              document.querySelector('#publish').addEventListener('click', () => publish++);
              document.querySelector('#settings').addEventListener('click', () => settings++);
              if (mode === 'wrongTitle') title.textContent = 'Wrong title';
              if (mode === 'authored') body.textContent = 'Existing body';
              if (mode === 'media') body.innerHTML = '<img alt="existing content">';
              if (mode === 'missingBody') body.remove();
              if (mode === 'duplicateBody') body.after(body.cloneNode(true));
              if (['extraEmptyBlock', 'extraEmptyBlockFill'].includes(mode))
                body.before(document.createElement('h1'));
              if (mode === 'dialog') document.body.insertAdjacentHTML('beforeend',
                '<div role="dialog">Publishing settings</div>');
              let source = PublishToAllBody;
              if (mode === 'unsupported') source = { ...source, unsupported: ['link_open'] };
              if (mode === 'wrongFingerprint') source = { ...source,
                fingerprint: 'fnv1a32:00000000' };
              const inspectBefore = MediumEditorInspector.inspect(document, location,
                PublishToAllStory);
              let resolutionCalls = 0;
              const sharedResolver = MediumEditorInspector.resolveBodySubregion;
              MediumEditorInspector.resolveBodySubregion = inspection => {
                resolutionCalls++;
                return sharedResolver(inspection);
              };
              const shared = MediumEditorInspector.resolveBodySubregion(inspectBefore);
              const sameResolution = shared?.selected === inspectBefore.selectedBodySubregion &&
                shared?.body === body;
              const beforeReadinessDOM = editor.innerHTML;
              if (['rebuild', 'titleMutation', 'promptRemoved', 'firstInsertion',
                   'fontCanonical', 'mediumReflow', 'transientReflow',
                   'twoCharacterArtifact'].includes(mode)) {
                const original = document.execCommand.bind(document);
                let insertions = 0;
                document.execCommand = (...args) => {
                  const outcome = original(...args);
                  if (args[0] === 'insertText') insertions++;
                  if (args[0] === 'delete' && mode === 'promptRemoved')
                    throw Error('INTERRUPTED_AFTER_PROMPT_REMOVAL');
                  if (args[0] === 'insertText' && mode === 'rebuild') {
                    const current = document.querySelector('#editor_7');
                    current.replaceWith(current.cloneNode(true));
                  }
                  if (args[0] === 'insertText' && mode === 'titleMutation')
                    title.textContent = 'Changed title';
                  if (args[0] === 'insertText' && mode === 'firstInsertion')
                    throw Error('INTERRUPTED_AFTER_FIRST_INSERTION');
                  if (args[0] === 'insertText' && mode === 'fontCanonical' &&
                      insertions === 1)
                    document.querySelector('[data-testid="editorParagraphText"]')
                      .innerHTML = '<font>01</font>';
                  if (args[0] === 'insertText' &&
                      ['mediumReflow', 'transientReflow',
                       'twoCharacterArtifact'].includes(mode)) {
                    const current = document.querySelector(
                      '[data-testid="editorParagraphText"]');
                    const value = mode === 'twoCharacterArtifact' ? '##' : '01';
                    current.innerHTML = '<font>' + value + '</font>';
                    if (['mediumReflow', 'transientReflow'].includes(mode)) {
                      const extra = document.createElement('p');
                      current.before(extra);
                      if (mode === 'transientReflow') queueMicrotask(() => extra.remove());
                    }
                  }
                  return outcome;
                };
              }
              const host = MediumTitleControls.mountBody(document, location,
                () => MediumBodyFill.eligibility(document, MediumEditorInspector,
                  location, PublishToAllStory, source), async () => {});
              const controlBefore = host?.getBodyDiagnostics();
              const before = MediumBodyFill.eligibility(document, MediumEditorInspector,
                location, PublishToAllStory, source, false, mode === 'publishObserved');
              const readinessDOMUnchanged = editor.innerHTML === beforeReadinessDOM;
              if (mode === 'extraEmptyBlock') return { before, sameResolution,
                readinessDOMUnchanged, controlBefore, publish, settings,
                resolutionCalls,
                inspection: { bodySubregionCandidateCount:
                  inspectBefore.bodySubregionCandidateCount,
                  bodyStillBlank: inspectBefore.bodyStillBlank,
                  draftContinuationSafe: inspectBefore.draftContinuationSafe } };
              const result = await MediumBodyFill.fill(document, MediumEditorInspector,
                location, PublishToAllStory, source, false, async () => {},
                () => mode === 'publishObserved');
              host?.refreshBodyPanel();
              const controlAfter = host?.getBodyDiagnostics();
              const again = MediumBodyFill.eligibility(document, MediumEditorInspector,
                location, PublishToAllStory, source, result.bodyFillAttempted);
              const inspectedAfter = MediumEditorInspector.inspect(document, location,
                PublishToAllStory);
              const partial = MediumEditorInspector.reconcileBody(document,
                inspectedAfter, source);
              return { before, result, again, controlBefore, controlAfter,
                partial, inspectedAfter: {
                  draftId: inspectedAfter.draftId,
                  bodySubregionCandidateCount: inspectedAfter.bodySubregionCandidateCount,
                  titleVerifiedInPersistedDraft: inspectedAfter.titleVerifiedInPersistedDraft },
                sameResolution, readinessDOMUnchanged, resolutionCalls,
                publish, settings, metadataUnchanged: metadataBefore === document.head.innerHTML,
                canonical: document.querySelectorAll('link[rel="canonical"]').length,
                title: document.querySelector('[data-testid="editorTitleParagraph"]')?.textContent,
                bodyBlocks: document.querySelector('#editor_7 section')?.children.length,
                firstBlocks: Array.from(document.querySelector('#editor_7 section')?.children || [])
                  .slice(0, 4).map(node => ({tag: node.tagName,
                    html: node.outerHTML.slice(0, 160)})) };
            }""", mode)
            page.close()
            return result

        normal = exercise("normal")
        assert normal["before"]["ready"], normal
        assert normal["sameResolution"] and normal["readinessDOMUnchanged"], normal
        assert normal["resolutionCalls"] >= 5, normal
        assert normal["controlBefore"]["interactability"] == "ENABLED", normal
        assert normal["controlBefore"]["listenerAttached"], normal
        assert normal["result"]["workflow"] == "MEDIUM_BODY_VERIFIED", normal
        assert normal["result"]["bodyFingerprintMatch"], normal
        assert normal["partial"]["currentBodyCanonicalFormattingComplete"], normal
        assert normal["result"]["titleUnchangedAfterBodyFill"], normal
        assert normal["result"]["trustedBodyInputEventCount"] > 0, normal
        assert normal["again"]["reason"] == "BODY_ALREADY_FILLED_BY_WORKFLOW", normal
        assert normal["controlAfter"]["disabledProperty"], normal
        assert normal["bodyBlocks"] == 37, normal
        assert normal["publish"] == normal["settings"] == normal["canonical"] == 0, normal
        assert normal["metadataUnchanged"], normal
        for mode in ("wrongTitle", "wrongDraft", "authored", "media", "missingBody",
                     "duplicateBody", "unsupported", "wrongFingerprint", "dialog",
                     "publishObserved"):
            blocked = exercise(mode)
            assert not blocked["before"]["ready"], (mode, blocked)
            assert not blocked["result"]["bodyFillAttempted"], (mode, blocked)
            assert blocked["publish"] == blocked["settings"] == blocked["canonical"] == 0
            assert blocked["readinessDOMUnchanged"], (mode, blocked)
        reasons = {"wrongTitle": "TITLE_CHANGED", "wrongDraft": "DRAFT_ROUTE_MISMATCH",
                   "authored": "BODY_ALREADY_AUTHORED",
                   "media": "BODY_ALREADY_AUTHORED",
                   "missingBody": "BODY_SUBREGION_MISSING",
                   "duplicateBody": "BODY_SUBREGION_AMBIGUOUS",
                   "unsupported": "UNSUPPORTED_MARKDOWN_STRUCTURE"}
        for mode, reason in reasons.items():
            blocked = exercise(mode)
            assert blocked["before"]["reason"] == reason, (mode, blocked)
            assert blocked["readinessDOMUnchanged"], (mode, blocked)
            assert blocked["publish"] == blocked["settings"] == 0, (mode, blocked)
        media = exercise("media")
        assert media["partial"]["partialBodyDetected"], media
        assert media["partial"]["partialBodyRecoveryClassification"] == "UNKNOWN", media
        assert media["partial"]["currentBodyMediaPresent"], media
        extra = exercise("extraEmptyBlock")
        assert extra["sameResolution"], extra
        assert extra["inspection"] == {"bodySubregionCandidateCount": 1,
                                       "bodyStillBlank": True,
                                       "draftContinuationSafe": True}, extra
        assert extra["before"]["ready"], extra
        assert extra["before"]["reason"] == "None", extra
        assert extra["before"]["diagnostics"]["bodySubregionCandidateCount"] == 1, extra
        assert extra["controlBefore"]["interactability"] == "ENABLED", extra
        assert extra["readinessDOMUnchanged"], extra
        assert extra["resolutionCalls"] >= 3, extra
        assert extra["publish"] == extra["settings"] == 0, extra
        extra_fill = exercise("extraEmptyBlockFill")
        assert extra_fill["before"]["ready"], extra_fill
        assert extra_fill["result"]["workflow"] == "MEDIUM_BODY_VERIFIED", extra_fill
        assert extra_fill["publish"] == extra_fill["settings"] == 0, extra_fill
        rebuilt = exercise("rebuild")
        assert rebuilt["result"]["workflow"] == "MEDIUM_BODY_VERIFIED", rebuilt
        assert rebuilt["result"]["titleNodeRebuilt"], rebuilt
        assert rebuilt["publish"] == rebuilt["settings"] == 0
        changed = exercise("titleMutation")
        assert changed["result"]["workflow"] == "MEDIUM_BODY_FILL_AMBIGUOUS", changed
        assert not changed["result"]["titleUnchangedAfterBodyFill"], changed
        assert changed["bodyBlocks"] < 37, changed
        assert changed["title"] == "Changed title", changed
        prompt_removed = exercise("promptRemoved")
        assert prompt_removed["result"]["bodyFillFailure"] == \
            "INTERRUPTED_AFTER_PROMPT_REMOVAL", prompt_removed
        assert not prompt_removed["partial"]["partialBodyDetected"], prompt_removed
        assert prompt_removed["again"]["reason"] == \
            "BODY_ALREADY_FILLED_BY_WORKFLOW", prompt_removed
        first_insertion = exercise("firstInsertion")
        assert first_insertion["result"]["bodyFillFailure"] == \
            "INTERRUPTED_AFTER_FIRST_INSERTION", first_insertion
        assert first_insertion["partial"]["partialBodyRecoveryClassification"] == \
            "EXACT_CANONICAL_PREFIX", first_insertion
        assert first_insertion["partial"]["currentBodyNormalizedLength"] == 2
        assert first_insertion["partial"]["canonicalPrefixMatchedThroughBlock"] == 1
        assert not first_insertion["partial"]["currentBodyCanonicalFormattingComplete"]
        assert first_insertion["again"]["reason"] == \
            "BODY_ALREADY_FILLED_BY_WORKFLOW", first_insertion
        font_canonical = exercise("fontCanonical")
        assert font_canonical["result"]["workflow"] == "MEDIUM_BODY_VERIFIED", \
            font_canonical
        reflow = exercise("mediumReflow")
        assert reflow["result"]["bodyFillFailure"] == "BLOCK_POSTCHECK_FAILED", reflow
        assert reflow["partial"]["currentBodyNormalizedLength"] == 2, reflow
        assert reflow["partial"]["currentBodySafeText"] == "01", reflow
        assert reflow["partial"]["currentBodyBlockCount"] == 2, reflow
        assert not reflow["partial"]["currentBodyCanonicalPrefixMatch"], reflow
        assert reflow["inspectedAfter"]["bodySubregionCandidateCount"] == 2, reflow
        assert not reflow["partial"]["partialBodyDetected"], reflow
        assert reflow["partial"]["partialBodyRecoveryClassification"] == "UNKNOWN", reflow
        transient = exercise("transientReflow")
        assert transient["result"]["bodyFillFailure"] == \
            "BLOCK_POSTCHECK_FAILED", transient
        assert transient["partial"]["currentBodyBlockCount"] == 1, transient
        assert transient["partial"]["currentBodySafeText"] == "01", transient
        assert transient["partial"]["currentBodyCanonicalPrefixMatch"], transient
        assert transient["partial"]["currentBodyDOMStructure"][0]["children"] == \
            ["font"], transient
        artifact = exercise("twoCharacterArtifact")
        assert artifact["result"]["bodyFillFailure"] == "BLOCK_POSTCHECK_FAILED", artifact
        assert artifact["partial"]["currentBodyNormalizedLength"] == 2, artifact
        assert artifact["partial"]["currentBodySafeText"] == "##", artifact
        assert artifact["partial"]["currentBodyMatchesMarkdownHeadingMarkup"], artifact
        assert artifact["partial"]["partialBodyRecoveryClassification"] == \
            "NONCANONICAL_PARTIAL_CONTENT", artifact
        for case in (prompt_removed, first_insertion, reflow, transient, artifact):
            assert case["inspectedAfter"]["draftId"] == "fd6c1824c4de", case
            assert case["inspectedAfter"]["titleVerifiedInPersistedDraft"], case
            assert case["publish"] == case["settings"] == 0, case
        browser.close()
        print(json.dumps({"normal": normal, "extraEmptyBlock": extra, "rebuild": rebuilt,
                          "titleMutation": changed, "promptRemoved": prompt_removed,
                          "firstInsertion": first_insertion, "fontCanonical": font_canonical,
                          "mediumReflow": reflow, "transientReflow": transient,
                          "twoCharacterArtifact": artifact}, indent=2))


if __name__ == "__main__":
    main()

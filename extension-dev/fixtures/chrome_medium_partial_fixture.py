"""Read-only Chrome inspection starting at the persisted post-failure Medium DOM."""

import json
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright


EXTENSION = Path(__file__).resolve().parents[2] / "chrome-extension"
URL = "https://medium.com/p/fd6c1824c4de/edit"
HTML = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<article class="postArticle postArticle--full"><div id="editor_7" role="textbox"
 contenteditable="true" class="postArticle-content js-postField js-notesSource editable">
<section><h3 data-testid="editorTitleParagraph"><font>Saturation of Artificial Intelligence</font></h3>
<p data-testid="editorParagraphText"><font>01</font></p></section></div></article>
<button id="publish">Publish</button><button id="settings">Settings</button>
<span role="status">Saved</span></body></html>"""


def inspect(page, mode):
    return page.evaluate("""mode => {
      const editor = document.querySelector('#editor_7');
      const body = document.querySelector('[data-testid="editorParagraphText"]');
      const title = document.querySelector('[data-testid="editorTitleParagraph"]');
      if (mode === 'direct') body.textContent = '01';
      if (mode === 'span') body.innerHTML = '<span>01</span>';
      if (mode === 'heading') body.outerHTML =
        '<h1 data-testid="editorParagraphText">01</h1>';
      if (mode === 'whitespace') body.textContent = ' \\n 01 \\u00a0';
      if (mode === 'noncanonicalDirect') body.textContent = 'XY';
      if (mode === 'separateSections') {
        const next = document.createElement('section');
        editor.append(next);
        next.append(body);
      }
      if (mode === 'wrappedTitle') {
        const wrapper = document.createElement('div');
        title.replaceWith(wrapper);
        wrapper.append(title);
      }
      if (mode === 'noncanonical') body.innerHTML = '<font>XY</font>';
      if (mode === 'empty') body.innerHTML = '';
      if (mode === 'scaffolding') body.innerHTML =
        '<span class="defaultValue defaultValue--root">Tell your story…</span>';
      if (mode === 'wrongTitle') title.textContent = 'Wrong title';
      if (mode === 'full') {
        const section = editor.querySelector('section');
        body.remove();
        for (const block of PublishToAllBody.blocks) {
          const node = document.createElement(block.kind === 'heading' ? 'h1' : 'p');
          node.textContent = block.text;
          section.append(node);
        }
      }
      let publishClicks = 0, settingsClicks = 0;
      document.querySelector('#publish').addEventListener('click', () => publishClicks++);
      document.querySelector('#settings').addEventListener('click', () => settingsClicks++);
      const before = editor.innerHTML;
      const editorInspection = MediumEditorInspector.inspect(document, location,
        PublishToAllStory);
      const reconciliation = MediumEditorInspector.reconcileBody(document,
        editorInspection, PublishToAllBody);
      const eligibility = MediumBodyFill.eligibility(document, MediumEditorInspector,
        location, PublishToAllStory, PublishToAllBody);
      const host = MediumTitleControls.mountBody(document, location,
        () => MediumBodyFill.eligibility(document, MediumEditorInspector,
          location, PublishToAllStory, PublishToAllBody), async () => {
            throw Error('body fill invoked');
          });
      const control = host?.getBodyDiagnostics();
      const after = editor.innerHTML;
      return { editorInspection: {
        editorRouteKind: editorInspection.editorRouteKind,
        draftId: editorInspection.draftId,
        titleVerifiedInPersistedDraft: editorInspection.titleVerifiedInPersistedDraft,
        titleExactMatch: editorInspection.titleExactMatch,
        titleFingerprintMatch: editorInspection.titleFingerprintMatch,
        bodySubregionCandidateCount: editorInspection.bodySubregionCandidateCount,
        bodySubregionState: editorInspection.bodySubregionState,
        selectedBodySubregion: editorInspection.selectedBodySubregion,
        normalizedResolvedBodyText: MediumEditorInspector.extractBodyText(
          MediumEditorInspector.resolveBodySubregion(editorInspection)?.body) },
        reconciliation, eligibility: { ready: eligibility.ready, reason: eligibility.reason },
        control: { disabledProperty: control?.disabledProperty,
          interactability: control?.interactability },
        domUnchanged: before === after, publishClicks, settingsClicks };
    }""", mode)


def main():
    chrome = shutil.which("google-chrome")
    if not chrome:
        raise SystemExit("google-chrome required")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, headless=True)
        results = {}
        for mode in ("canonical", "direct", "span", "heading", "whitespace", "noncanonical",
                     "noncanonicalDirect", "separateSections", "wrappedTitle",
                     "empty", "scaffolding", "full", "wrongTitle", "wrongDraft"):
            page = browser.new_page()
            page.route("https://medium.com/**", lambda route: route.fulfill(
                status=200, content_type="text/html", body=HTML))
            page.goto(URL if mode != "wrongDraft" else
                      "https://medium.com/p/aaaaaaaaaaaa/edit")
            for name in ("story-title.js", "story-body.js", "medium-editor-inspector.js",
                         "medium-body-fill.js", "medium-title-controls.js"):
                page.add_script_tag(path=str(EXTENSION / name))
            results[mode] = inspect(page, mode)
            page.close()
        # A fresh page has no current-session body-fill result or stored attempt.
        page = browser.new_page()
        page.route("https://medium.com/**", lambda route: route.fulfill(
            status=200, content_type="text/html", body=HTML))
        page.goto(URL)
        for name in ("story-title.js", "story-body.js", "medium-editor-inspector.js",
                     "medium-body-fill.js", "medium-title-controls.js"):
            page.add_script_tag(path=str(EXTENSION / name))
        results["reload"] = inspect(page, "canonical")
        page.close()
        browser.close()

    canonical = results["canonical"]
    partial = canonical["reconciliation"]
    assert canonical["editorInspection"]["bodySubregionState"] == "UNKNOWN", canonical
    assert partial["partialBodyDetected"] and partial["bodyInspectionReliable"], canonical
    assert partial["currentBodySafeText"] == "01", canonical
    assert partial["currentBodyNormalizedLength"] == 2, canonical
    assert partial["currentBodyFingerprint"] == "fnv1a32:20ed70ba", canonical
    assert partial["currentBodyBlockCount"] == 1, canonical
    assert partial["currentBodyDOMStructure"][0]["children"] == ["font"], canonical
    assert partial["currentBodyCanonicalTextPrefix"], canonical
    assert not partial["currentBodyCanonicalFormattingComplete"], canonical
    assert partial["formattingDiscrepancy"] == \
        "Expected heading block; observed paragraph/font wrapper", canonical
    assert partial["canonicalPrefixMatchedThroughBlock"] == 1, canonical
    assert partial["canonicalPrefixMatchedCharacterCount"] == 2, canonical
    assert partial["expectedNextCanonicalBlock"]["number"] == 2, canonical
    assert partial["partialBodyRecoveryClassification"] == "EXACT_CANONICAL_PREFIX", canonical
    heading = results["heading"]["reconciliation"]
    assert heading["partialBodyDetected"], results["heading"]
    assert heading["currentBodyCanonicalTextPrefix"], results["heading"]
    assert heading["currentBodyCanonicalFormattingComplete"], results["heading"]
    assert heading["formattingDiscrepancy"] is None, results["heading"]
    assert not results["heading"]["eligibility"]["ready"], results["heading"]
    for mode, children in (("direct", []), ("canonical", ["font"]),
                           ("span", ["span"]), ("whitespace", []),
                           ("separateSections", ["font"]),
                           ("wrappedTitle", ["font"])):
        case = results[mode]
        actual = case["reconciliation"]
        assert case["editorInspection"]["normalizedResolvedBodyText"] == "01", case
        assert case["editorInspection"]["selectedBodySubregion"][
            "normalizedDescendantTextLength"] >= 2, case
        assert case["editorInspection"]["selectedBodySubregion"][
            "normalizedOwnTextLength"] == (2 if not children else 0), case
        assert actual["bodyInspectionReliable"] and actual["partialBodyDetected"], case
        assert actual["currentBodySafeText"] == "01", case
        assert actual["currentBodyNormalizedLength"] == 2, case
        assert actual["currentBodyFingerprint"] == "fnv1a32:20ed70ba", case
        assert actual["currentBodyDOMStructure"][0]["children"] == children, case
        assert actual["currentBodyCanonicalTextPrefix"], case
        assert not actual["currentBodyCanonicalFormattingComplete"], case
        if mode == "direct":
            assert actual["formattingDiscrepancy"] == \
                "Expected heading block; observed paragraph", case
        assert actual["canonicalPrefixMatchedThroughBlock"] == 1, case
        assert actual["canonicalPrefixMatchedCharacterCount"] == 2, case
        assert actual["expectedNextCanonicalBlock"]["number"] == 2, case
        assert actual["partialBodyRecoveryClassification"] == "EXACT_CANONICAL_PREFIX", case
        assert not case["eligibility"]["ready"], case
    assert results["reload"]["reconciliation"] == partial, results["reload"]
    other = results["noncanonical"]["reconciliation"]
    assert other["partialBodyDetected"] and not other["currentBodyCanonicalTextPrefix"], other
    assert other["partialBodyRecoveryClassification"] == "NONCANONICAL_PARTIAL_CONTENT", other
    direct_other = results["noncanonicalDirect"]["reconciliation"]
    assert direct_other["currentBodySafeText"] == "XY", direct_other
    assert direct_other["partialBodyRecoveryClassification"] == \
        "NONCANONICAL_PARTIAL_CONTENT", direct_other
    for mode in ("empty", "scaffolding", "full", "wrongTitle", "wrongDraft"):
        assert not results[mode]["reconciliation"]["partialBodyDetected"], (mode, results[mode])
    assert results["full"]["reconciliation"]["currentBodyExactCanonicalMatch"]
    for mode, result in results.items():
        assert result["domUnchanged"] and result["publishClicks"] == 0 and \
            result["settingsClicks"] == 0, (mode, result)
        if mode not in ("empty", "scaffolding"):
            assert not result["eligibility"]["ready"], (mode, result)
            if mode != "wrongDraft":
                assert result["control"]["disabledProperty"], (mode, result)
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()

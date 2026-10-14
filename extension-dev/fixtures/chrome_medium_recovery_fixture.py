"""Network-free Chrome exercise of the exact persisted Medium body recovery."""

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
<p data-testid="editorParagraphText">01</p></section></div></article>
<button id="publish">Publish</button><button id="settings">Settings</button>
<span role="status">Saved</span></body></html>"""
WRAPPED_HTML = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<article class="postArticle postArticle--full"><div id="editor_7" role="textbox"
 contenteditable="true" class="postArticle-content js-postField js-notesSource editable">
<section class="section--body"><div class="section-content"><div class="section-inner sectionLayout--insetColumn">
<h3 data-testid="editorTitleParagraph">Saturation of Artificial Intelligence</h3>
<p data-testid="editorParagraphText">01</p>
</div></div></section></div></article>
<div contenteditable="true" tabindex="-1"></div>
<button id="publish">Publish</button><button id="settings">Settings</button>
<span role="status">Saved</span></body></html>"""

SCRIPT = r"""async mode => {
  const editor = document.querySelector('#editor_7');
  const first = editor.querySelector('[data-testid="editorParagraphText"]');
  const title = editor.querySelector('[data-testid="editorTitleParagraph"]');
  let publish = 0, settings = 0, formatCount = 0, paragraphCount = 0;
  let textCount = 0, reflowCount = 0;
  document.querySelector('#publish').addEventListener('click', () => publish++);
  document.querySelector('#settings').addEventListener('click', () => settings++);
  if (['font', 'rebuild', 'transient', 'transientInsert'].includes(mode))
    first.innerHTML = '<font>01</font>';
  if (mode === 'wrongText') first.textContent = 'XY';
  if (mode === 'missingBody') first.remove();
  if (mode === 'duplicateBody') first.after(first.cloneNode(true));
  if (mode === 'extraAuthored') {
    const extra = document.createElement('p');
    extra.textContent = 'Additional authored content';
    first.after(extra);
  }
  if (mode === 'extraHeading' || mode === 'hiddenHeading') {
    const extra = document.createElement('h2');
    extra.textContent = 'Unexpected heading';
    if (mode === 'hiddenHeading') extra.hidden = true;
    first.after(extra);
  }
  if (mode === 'strayText') first.parentElement.append(' stray authored text');
  if (mode === 'strayBeforeTitle') title.before(' stray authored text');
  if (mode === 'bodyExtraText') first.textContent = '01 extra';
  if (mode === 'hiddenBodyText') first.insertAdjacentHTML('beforeend',
    '<span hidden>hidden authored text</span>');
  if (mode === 'extraBlock') first.after(document.createElement('p'));
  if (mode === 'wrongTitle') title.textContent = 'Wrong title';
  if (mode === 'heading') first.outerHTML =
    '<h1 data-testid="editorParagraphText">01</h1>';
  if (mode === 'wrongFingerprint') PublishToAllStory = {
    ...PublishToAllStory, fingerprint: 'fnv1a32:00000000' };
  const original = document.execCommand.bind(document);
  document.execCommand = (...args) => {
    const outcome = original(...args);
    if (args[0] === 'formatBlock') formatCount++;
    if (args[0] === 'insertParagraph') paragraphCount++;
    if (args[0] === 'insertText') textCount++;
    if (args[0] === 'insertText' && textCount === 1 && mode === 'rebuild') {
      const current = document.querySelector('#editor_7');
      current.replaceWith(current.cloneNode(true));
      reflowCount++;
    }
    if (args[0] === 'formatBlock' && formatCount === 1 && mode === 'transient') {
      const current = document.querySelector('#editor_7 section');
      const extra = document.createElement('p');
      current.insertBefore(extra, current.children[1]);
      reflowCount++;
      setTimeout(() => extra.remove(), 90);
    }
    if (args[0] === 'insertText' && textCount === 1 && mode === 'transientInsert') {
      const current = document.querySelector('#editor_7 section').lastElementChild;
      const extra = document.createElement('p');
      current.before(extra);
      reflowCount++;
      setTimeout(() => extra.remove(), 90);
    }
    if (args[0] === 'formatBlock' && formatCount === 1 && mode === 'timeout') {
      const current = document.querySelector('#editor_7 section');
      current.append(document.createElement('p'));
      reflowCount++;
    }
    if (args[0] === 'insertText' && mode === 'failN' && textCount === 2) {
      document.querySelector('#editor_7 section').append(document.createElement('p'));
      reflowCount++;
    }
    return outcome;
  };
  const story = mode === 'wrongExpectedTitle' ?
    { ...PublishToAllStory, title: 'Wrong expected title' } : PublishToAllStory;
  const source = mode === 'missingSource' ? null :
    mode === 'wrongFullBodyFingerprint' ?
      { ...PublishToAllBody, fingerprint: 'fnv1a32:00000000' } : PublishToAllBody;
  const inspector = ['wrongPartialFingerprint', 'wrongBlockCount',
    'classificationBug'].includes(mode) ? {
    ...MediumEditorInspector,
    reconcileBody: (...args) => ({
      ...MediumEditorInspector.reconcileBody(...args),
      ...(mode === 'wrongPartialFingerprint' ?
        { currentBodyFingerprint: 'fnv1a32:00000000' } :
        mode === 'wrongBlockCount' ? { currentBodyBlockCount: 2 } :
          { currentBodyOtherContentPresent: true })
    })
  } : MediumEditorInspector;
  const beforeHTML = editor.innerHTML;
  const before = MediumBodyRecovery.eligibility(document, inspector,
    location, story, source, { publishObserved: mode === 'publishObserved' });
  const normal = MediumBodyFill.eligibility(document, MediumEditorInspector,
    location, story, source);
  const host = MediumTitleControls.mountRecovery(document, location,
    () => MediumBodyRecovery.eligibility(document, inspector,
      location, story, source, { publishObserved: mode === 'publishObserved' }),
    async () => {});
  const control = host?.getRecoveryDiagnostics();
  const noMutationOnInspection = beforeHTML === editor.innerHTML;
  let result = null, again = null;
  let saveState = null, afterWarning = null;
  if (mode === 'changedBeforeClick') {
    first.textContent = 'XY';
    result = await MediumBodyRecovery.resume(document, MediumEditorInspector,
      location, story, source);
  }
  if (['direct', 'font', 'rebuild', 'transient', 'transientInsert',
       'timeout', 'failN'].includes(mode)) {
    result = await MediumBodyRecovery.resume(document, MediumEditorInspector,
      location, story, source, { wait: ms => new Promise(resolve => setTimeout(resolve, ms)),
        onProgress: async progress => {
          if (['font', 'rebuild', 'transient', 'transientInsert'].includes(mode) &&
              progress.lastVerifiedCanonicalBlock === 2)
            throw Error('FIXTURE_STOP_AFTER_VERIFIED_BLOCK_TWO');
        } });
    again = MediumBodyRecovery.eligibility(document, MediumEditorInspector,
      location, story, source, { used: result.bodyRecoveryAttempted,
        verified: result.workflow === 'MEDIUM_BODY_RECOVERY_VERIFIED' });
    if (mode === 'direct') {
      const reconciliation = MediumEditorInspector.reconcileBody(document,
        MediumEditorInspector.inspect(document, location, story), source);
      saveState = MediumSaveState.inspect(document, reconciliation);
      const warning = document.createElement('div');
      warning.setAttribute('role', 'alert'); warning.className = 'save-error';
      warning.textContent = 'Something is wrong and we cannot save your story.';
      document.body.append(warning);
      afterWarning = MediumSaveState.inspect(document, reconciliation);
      const second = await MediumBodyRecovery.resume(document, MediumEditorInspector,
        location, story, source, { used: true });
      again.secondInvocation = { attempted: second.bodyRecoveryAttempted,
        reason: second.bodyRecoveryFailure };
      const normalAfter = MediumBodyFill.eligibility(document, MediumEditorInspector,
        location, story, source);
      again.normalFill = { ready: normalAfter.ready, reason: normalAfter.reason };
    }
  }
  return { before, normal: { ready: normal.ready, reason: normal.reason },
    postInspection: (() => { const i = MediumEditorInspector.inspect(document, location, story);
      return { architecture: i.editorArchitecture, titleVerified: i.titleVerifiedInPersistedDraft,
        titleCount: i.titleSubregionCandidateCount, bodyCount: i.bodySubregionCandidateCount,
        root: i.compositeEditorRootDetected, publish: i.publishControlCount }; })(),
    control: { present: control?.present, interactability: control?.interactability,
      disabledProperty: control?.disabledProperty,
      listenerAttached: control?.listenerAttached }, noMutationOnInspection,
    result, again, saveState, afterWarning, publish, settings, formatCount, paragraphCount,
    textCount, reflowCount,
    firstBlock: document.querySelector('#editor_7 section')?.children[1]?.outerHTML.slice(0, 120),
    finalBlocks: document.querySelector('#editor_7 section')?.children.length - 1,
    secondBlock: document.querySelector('#editor_7 section')?.children[2]?.outerHTML.slice(0, 200) };
}"""


def main():
    chrome = shutil.which("google-chrome")
    if not chrome:
        raise SystemExit("google-chrome required")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, headless=True)
        report = {}
        for mode in ("direct", "font", "rebuild", "transient", "transientInsert",
                     "timeout", "failN",
                     "wrapped", "missingBody", "duplicateBody", "extraAuthored",
                     "extraHeading", "hiddenHeading", "strayText",
                     "strayBeforeTitle", "bodyExtraText", "hiddenBodyText",
                     "wrongBlockCount", "classificationBug",
                     "wrongText", "extraBlock", "wrongDraft", "wrongTitle",
                     "wrongExpectedTitle", "wrongFingerprint", "missingSource",
                     "wrongFullBodyFingerprint", "wrongPartialFingerprint",
                     "heading", "publishObserved",
                     "changedBeforeClick"):
            page = browser.new_page()
            page.route("https://medium.com/**", lambda route, mode=mode: route.fulfill(
                status=200, content_type="text/html",
                body=WRAPPED_HTML if mode in ("wrapped", "missingBody",
                                             "duplicateBody", "extraAuthored",
                                             "extraHeading", "hiddenHeading",
                                             "strayText", "strayBeforeTitle",
                                             "bodyExtraText", "hiddenBodyText",
                                             "wrongBlockCount") else HTML))
            page.goto(URL if mode != "wrongDraft" else
                      "https://medium.com/p/aaaaaaaaaaaa/edit")
            for name in ("story-title.js", "story-body.js", "medium-editor-inspector.js",
                         "medium-save-state.js", "medium-body-fill.js", "medium-body-recovery.js",
                         "medium-title-controls.js"):
                page.add_script_tag(path=str(EXTENSION / name))
            report[mode] = page.evaluate(SCRIPT, mode)
            page.close()
        browser.close()
    print(json.dumps(report))


if __name__ == "__main__":
    main()

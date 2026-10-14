"""Network-free Chrome save-warning and editing-event semantics probe."""
import json
import shutil
from pathlib import Path
from playwright.sync_api import sync_playwright

EXTENSION = Path(__file__).resolve().parents[2] / "chrome-extension"
HTML = """<!doctype html><html><body><article class="postArticle postArticle--full">
<div id="editor" contenteditable="true" role="textbox" class="postArticle-content js-postField js-notesSource editable">
<section><h3 data-testid="editorTitleParagraph">Saturation of Artificial Intelligence</h3>
<p data-testid="editorParagraphText">01</p></section></div></article>
<button id="publish">Publish</button><button id="settings">Settings</button></body></html>"""
SCRIPT = r"""async () => {
  const errors = []; window.addEventListener('error', e => errors.push(e.message));
  const editor = document.querySelector('#editor');
  const section = editor.querySelector('section');
  const initial = section.innerHTML;
  const events = [];
  for (const type of ['beforeinput', 'input', 'change'])
    editor.addEventListener(type, event => events.push({ type, inputType: event.inputType || '',
      isTrusted: event.isTrusted }), true);
  const select = (node, end = false) => {
    const range = document.createRange(); range.selectNodeContents(node);
    if (end) range.collapse(false);
    const selection = document.getSelection();
    selection.removeAllRanges(); selection.addRange(range);
  };
  const operations = [];
  for (const [name, command, value, selector, end] of [
    ['title insertText', 'insertText', 'Probe', 'h3', false],
    ['recovery formatBlock h1', 'formatBlock', 'h1', 'p', false],
    ['recovery insertParagraph', 'insertParagraph', null, 'p', true],
    ['recovery insertText', 'insertText', 'Probe', 'p', false],
    ['recovery formatBlock p', 'formatBlock', 'p', 'p', false],
    ['normal-fill delete', 'delete', null, 'p', false]
  ]) {
    section.innerHTML = initial;
    const node = section.querySelector(selector);
    editor.focus(); select(node, end);
    events.length = 0;
    const before = section.innerHTML;
    const succeeded = document.execCommand(command, false, value);
    operations.push({ operation: name, commandSucceeded: succeeded,
      beforeinputObserved: events.some(item => item.type === 'beforeinput'),
      inputObserved: events.some(item => item.type === 'input'),
      changeObserved: events.some(item => item.type === 'change'),
      inputTypes: [...new Set(events.map(item => item.inputType).filter(Boolean))],
      isTrusted: events.map(item => item.isTrusted), domChanged: section.innerHTML !== before,
      events: events.slice() });
  }
  section.innerHTML = initial;
  const htmlBefore = editor.innerHTML;
  let mutationCallbacks = 0; const probeObserver = new MutationObserver(() => mutationCallbacks++);
  probeObserver.observe(document.documentElement, { childList: true, subtree: true });
  const telemetry = MediumSaveState.observe(document);
  const passiveEvent = new InputEvent('beforeinput', { bubbles: true,
    cancelable: true, inputType: 'insertText', data: 'probe' });
  const eventAllowed = editor.dispatchEvent(passiveEvent);
  const mutationProbe = document.createTextNode('local probe');
  section.append(mutationProbe);
  await new Promise(resolve => setTimeout(resolve, 0));
  mutationProbe.remove();
  await new Promise(resolve => setTimeout(resolve, 0));
  const earlyTelemetry = telemetry.read();
  const withoutWarning = MediumSaveState.inspect(document, { currentBodyExactCanonicalMatch: true });
  const warning = document.createElement('div');
  warning.className = 'toast error'; warning.setAttribute('role', 'alert');
  warning.style.color = 'red';
  warning.textContent = 'Something is wrong and we cannot save your story. Learn more about how to fix this';
  document.body.append(warning);
  await new Promise(resolve => setTimeout(resolve, 0));
  const withWarning = MediumSaveState.inspect(document, { currentBodyExactCanonicalMatch: true });
  const warningTelemetry = telemetry.read();
  warning.remove();
  await new Promise(resolve => setTimeout(resolve, 0));
  const warningGone = MediumSaveState.inspect(document, { currentBodyExactCanonicalMatch: true });
  const goneTelemetry = telemetry.read();
  const indicator = document.createElement('span'); indicator.setAttribute('role', 'status');
  indicator.textContent = 'Saving…'; document.body.append(indicator);
  const pending = MediumSaveState.inspect(document, { currentBodyExactCanonicalMatch: true });
  indicator.textContent = 'Saved';
  await new Promise(resolve => setTimeout(resolve, 0));
  const indicated = MediumSaveState.inspect(document, { currentBodyExactCanonicalMatch: true });
  indicator.remove();
  for (let index = 0; index < 12; index++) {
    const extra = document.createElement('div');
    extra.setAttribute('role', 'alert'); extra.className = 'save-error';
    extra.textContent = 'Draft save failed'; document.body.append(extra);
  }
  const bounded = MediumSaveState.inspect(document, { currentBodyExactCanonicalMatch: true });
  for (let index = 0; index < 100; index++)
    editor.dispatchEvent(new InputEvent('input', { bubbles: true, inputType: 'insertText' }));
  await new Promise(resolve => setTimeout(resolve, 0));
  const telemetryEvents = telemetry.read(); telemetry.dispose();
  const noEditorMutationOnInspection = editor.innerHTML === htmlBefore;
  return { operations, withoutWarning, withWarning, warningGone, pending, indicated, bounded,
    telemetryEvents, earlyTelemetry, warningTelemetry, goneTelemetry,
    eventAllowed, defaultPrevented: passiveEvent.defaultPrevented,
    noEditorMutationOnInspection, errors, mutationCallbacks, observerAvailable: Boolean(document.defaultView.MutationObserver) };
}"""


def main():
    chrome = shutil.which("google-chrome")
    if not chrome:
        raise SystemExit("google-chrome required")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, headless=True)
        page = browser.new_page()
        page_errors = []
        page.on('pageerror', lambda error: page_errors.append(str(error)))
        page.route("https://medium.com/**", lambda route: route.fulfill(
            status=200, content_type="text/html", body=HTML))
        page.goto("https://medium.com/p/fd6c1824c4de/edit")
        page.add_script_tag(path=str(EXTENSION / "medium-save-state.js"))
        result = page.evaluate(SCRIPT)
        result['pageErrors'] = page_errors
        browser.close()
    print(json.dumps(result))


if __name__ == "__main__":
    main()

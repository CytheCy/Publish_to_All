"""Local, network-free Chrome check of contenteditable editing events."""

import json
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> None:
    chrome = shutil.which("google-chrome")
    if chrome is None:
        raise SystemExit("google-chrome is required for this fixture")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, headless=True)
        page = browser.new_page()
        page.set_content('<div id="editor" contenteditable="true">sample</div>')
        result = page.evaluate("""() => {
          const editor = document.querySelector('#editor');
          const events = [];
          for (const type of ['beforeinput', 'input', 'change', 'focus', 'blur']) {
            editor.addEventListener(type, event => events.push({
              type: event.type, targetIsEditor: event.target === editor,
              bubbles: event.bubbles, cancelable: event.cancelable,
              composed: event.composed, isTrusted: event.isTrusted,
              inputType: event.inputType ?? null,
              dataLength: typeof event.data === 'string' ? event.data.length : null
            }));
          }
          editor.focus();
          const range = document.createRange();
          range.selectNodeContents(editor);
          const selection = document.getSelection();
          selection.removeAllRanges();
          selection.addRange(range);
          const succeeded = document.execCommand('insertText', false, 'replacement');
          selection.removeAllRanges();
          editor.blur();
          return { succeeded, textExact: editor.textContent === 'replacement', events };
        }""")
        assert result["succeeded"] and result["textExact"]
        inputs = [event for event in result["events"] if event["type"] == "input"]
        assert len(inputs) == 1 and inputs[0]["targetIsEditor"]
        assert inputs[0]["bubbles"] and inputs[0]["isTrusted"]
        assert not any(event["type"] in ("beforeinput", "change")
                       for event in result["events"])

        page.set_content('<div id="editor" contenteditable="true"></div>')
        page.evaluate("""() => {
          const editor = document.querySelector('#editor');
          window.editEvents = [];
          for (const type of ['beforeinput', 'input', 'change']) {
            editor.addEventListener(type, event => window.editEvents.push(event.type));
          }
          editor.focus();
        }""")
        page.keyboard.type("x")
        page.evaluate("document.querySelector('#editor').blur()")
        typed = page.evaluate("window.editEvents")
        assert typed == ["beforeinput", "input"], typed
        page.set_content('<div id="editor" contenteditable="true">sample</div>')
        page.add_script_tag(path=str(Path(__file__).resolve().parents[1] / "medium-fill.js"))
        helper = page.evaluate("""() => {
          const source = 'replacement';
          return MediumFill.editSource(document, {
            resolveEditor: () => document.querySelector('#editor'),
            verifyDefault: editor => editor.textContent === 'sample',
            sourceUrl: source, fingerprint: 'fixture-fingerprint',
            normalize: text => text.trim(),
            hash: text => text === source ? 'fixture-fingerprint' : 'wrong'
          });
        }""")
        assert helper["synchronizationStatus"] == "VERIFIED", helper
        assert helper["browserInputObserved"] and not helper["syntheticInputFallbackUsed"]
        assert helper["counts"]["input"] == 1
        print(json.dumps({"chromeVersion": browser.version,
                          "execCommandSucceeded": result["succeeded"],
                          "browserInputEventObserved": True,
                          "browserBeforeinputEventObserved": False,
                          "browserInputIsTrusted": inputs[0]["isTrusted"],
                          "browserInputBubbles": inputs[0]["bubbles"],
                          "contenteditableChangeObserved": False,
                          "keyboardEvents": typed,
                          "helperSynchronizationStatus": helper["synchronizationStatus"],
                          "helperSyntheticFallbackUsed": helper["syntheticInputFallbackUsed"],
                          "events": result["events"]}, indent=2))
        browser.close()


if __name__ == "__main__":
    main()

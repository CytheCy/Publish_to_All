"""Network-free Chrome fixture for Medium title control diagnostics."""

import json
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright


EXTENSION = Path(__file__).resolve().parents[2] / "chrome-extension"
HTML = """<!doctype html><html><head><meta charset="utf-8"></head><body>
<article class="postArticle postArticle--full">
  <div id="editor_7" role="textbox" contenteditable="true"
    class="postArticle-content js-postField js-notesSource editable">
    <section>
      <h3 class="graf graf--h3 graf--leading graf--title"
        data-testid="editorTitleParagraph"><span class="defaultValue defaultValue--root">Title</span></h3>
      <p class="graf graf--p" data-testid="editorParagraphText"><span
        class="defaultValue defaultValue--root">Tell your story…</span></p>
    </section>
  </div>
</article><button id="publish">Publish</button>
</body></html>"""


def main() -> None:
    chrome = shutil.which("google-chrome")
    if not chrome:
        raise SystemExit("google-chrome required")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, headless=True)
        page = browser.new_page(viewport={"width": 1200, "height": 800})
        page.route("https://medium.com/new-story", lambda route: route.fulfill(
            status=200, content_type="text/html", body=HTML))
        page.goto("https://medium.com/new-story")
        for file in ("story-title.js", "medium-editor-inspector.js",
                     "medium-title-fill.js", "medium-title-controls.js"):
            page.add_script_tag(path=str(EXTENSION / file))
        result = page.evaluate("""async () => {
          const title = document.querySelector('[data-testid="editorTitleParagraph"]');
          const body = document.querySelector('[data-testid="editorParagraphText"]');
          const before = [title.innerHTML, body.innerHTML];
          let publishClicks = 0, activationCalls = 0;
          document.querySelector('#publish').addEventListener('click', () => publishClicks++);
          let ready = true;
          const authoritative = () => ready ? MediumTitleFill.eligibility(
            document, MediumEditorInspector, location, PublishToAllStory) :
            {ready: false, reason: 'FIXTURE_NOT_READY'};
          const activate = async () => { activationCalls++; };
          const first = MediumTitleControls.mount(document, location, authoritative, activate);
          const readyState = first.getTitleDiagnostics();
          ready = false;
          const notReadyState = first.getTitleDiagnostics();
          ready = true;
          const oldButton = first.titleControl;
          const second = MediumTitleControls.mount(document, location, authoritative, activate);
          const secondState = second.getTitleDiagnostics();
          oldButton.dispatchEvent(new Event('click'));
          const oldListenerInvoked = activationCalls !== 0;
          ready = false;
          second.refreshTitlePanel();
          second.titleControl.dispatchEvent(new Event('click'));
          const freshCheckBlocked = activationCalls === 0;
          ready = true;
          const overlay = document.createElement('div');
          overlay.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:transparent';
          document.body.append(overlay);
          const overlayState = second.getTitleDiagnostics();
          overlay.remove();
          second.style.pointerEvents = 'none';
          const pointerState = second.getTitleDiagnostics();
          second.style.pointerEvents = '';
          const finalState = second.getTitleDiagnostics();
          return {readyState, notReadyState, secondState, oldListenerInvoked,
            freshCheckBlocked, overlayState, pointerState, finalState,
            hostCount: document.querySelectorAll(
              '[id="publish-to-all-medium-title-controls"]').length,
            oldConnected: first.isConnected, activationCalls, publishClicks,
            titleUnchanged: title.innerHTML === before[0],
            bodyUnchanged: body.innerHTML === before[1]};
        }""")
        print(json.dumps(result, indent=2))
        assert result["readyState"]["logicalEligibility"] == "READY", result
        assert result["readyState"]["interactability"] == "ENABLED", result
        assert result["readyState"]["disabledProperty"] is False, result
        assert result["readyState"]["disabledAttribute"] is False, result
        assert result["readyState"]["ariaDisabled"] == "false", result
        assert result["readyState"]["pointerEvents"] == "auto", result
        assert result["readyState"]["cursor"] == "pointer", result
        assert result["readyState"]["hitTest"] == "BUTTON", result
        assert result["notReadyState"]["logicalEligibility"] == "NOT_READY", result
        assert result["notReadyState"]["disabledProperty"] is True, result
        assert result["notReadyState"]["disabledAttribute"] is True, result
        assert result["notReadyState"]["ariaDisabled"] == "true", result
        assert result["hostCount"] == 1 and not result["oldConnected"], result
        assert result["secondState"]["listenerCount"] == 1, result
        assert result["secondState"]["currentHostUnique"], result
        assert not result["oldListenerInvoked"], result
        assert result["freshCheckBlocked"], result
        assert result["overlayState"]["interactability"] == "BLOCKED", result
        assert result["pointerState"]["interactability"] == "BLOCKED", result
        assert result["finalState"]["interactability"] == "ENABLED", result
        assert result["activationCalls"] == result["publishClicks"] == 0, result
        assert result["titleUnchanged"] and result["bodyUnchanged"], result
        browser.close()


if __name__ == "__main__":
    main()

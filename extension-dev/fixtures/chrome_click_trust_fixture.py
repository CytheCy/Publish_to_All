"""Network-free Chrome event evidence for three native submit-button activations."""

import json
import shutil

from playwright.sync_api import sync_playwright


def main() -> None:
    chrome = shutil.which("google-chrome")
    if chrome is None:
        raise SystemExit("google-chrome is required")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, headless=True)
        page = browser.new_page()
        page.set_content("""<form id="form"><button id="import" type="submit">Import</button></form>
          <script>
            window.events = [];
            // Capture records defaultPrevented before the submit handler cancels navigation.
            for (const type of ['pointerdown', 'mousedown', 'pointerup', 'mouseup',
                                'click', 'submit']) {
              document.addEventListener(type, event => {
                window.events.push({type: event.type, isTrusted: event.isTrusted,
                  bubbles: event.bubbles, cancelable: event.cancelable,
                  defaultPrevented: event.defaultPrevented});
                if (type === 'submit') event.preventDefault();
              }, true);
            }
          </script>""")

        def capture(label, action):
            page.evaluate("window.events = []")
            action()
            return {"activation": label, "events": page.evaluate("window.events")}

        cases = [
            capture("HTMLElement.click()", lambda: page.evaluate(
                "document.querySelector('#import').click()")),
            capture("dispatchEvent(MouseEvent)", lambda: page.evaluate(
                "document.querySelector('#import').dispatchEvent("
                "new MouseEvent('click', {bubbles: true, cancelable: true}))")),
            capture("Playwright mouse click (browser input)", lambda: page.locator(
                "#import").click()),
        ]
        assert [[event["type"] for event in case["events"]] for case in cases] == [
            ["click", "submit"], ["click", "submit"],
            ["pointerdown", "mousedown", "pointerup", "mouseup", "click", "submit"]]
        assert [case["events"][-2 if len(case["events"]) > 2 else 0]["isTrusted"]
                for case in cases] == [False, False, True]
        assert all(case["events"][-1]["type"] == "submit" and
                   case["events"][-1]["isTrusted"] for case in cases)
        assert all(event["bubbles"] and event["cancelable"] and
                   not event["defaultPrevented"]
                   for case in cases for event in case["events"])
        print(json.dumps({"chromeVersion": browser.version, "cases": cases}, indent=2))
        browser.close()


if __name__ == "__main__":
    main()

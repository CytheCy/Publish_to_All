"""Network-free Chrome exercise of the composite Medium title edit."""

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
</article>
<button>Publish</button>
</body></html>"""
POST_TITLE_HTML = HTML.replace(
    '<span class="defaultValue defaultValue--root">Title</span>',
    '<font>Saturation of Artificial Intelligence</font>',
)


def main() -> None:
    chrome = shutil.which("google-chrome")
    if not chrome:
        raise SystemExit("google-chrome required")
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(executable_path=chrome, headless=True)
        def exercise(mode: str):
            page = browser.new_page()
            page.route("https://medium.com/new-story", lambda route: route.fulfill(
                status=200, content_type="text/html", body=HTML))
            page.goto("https://medium.com/new-story")
            for file in ("story-title.js", "medium-editor-inspector.js", "medium-title-fill.js"):
                page.add_script_tag(path=str(EXTENSION / file))
            result = page.evaluate("""async mode => {
          const editor = document.querySelector('#editor_7');
          const title = document.querySelector('[data-testid="editorTitleParagraph"]');
          const body = document.querySelector('[data-testid="editorParagraphText"]');
          let publishClicks = 0;
          document.querySelector('button').addEventListener('click', () => publishClicks++);
          if (mode === 'nonblankTitle') title.textContent = 'Existing title';
          if (mode === 'nonblankBody') body.textContent = 'Existing story';
          if (mode === 'ambiguousRoot') document.body.append(editor.closest('article').cloneNode(true));
          if (mode === 'freshTitle') title.replaceWith(title.cloneNode(true));
          if (['missingInput', 'wrongTarget'].includes(mode)) {
            editor.addEventListener('input', event => {
              if (event.isTrusted) event.stopImmediatePropagation();
            }, true);
          }
          if (mode === 'navigation') editor.addEventListener('input', event => {
            if (event.isTrusted && event.inputType === 'insertText' &&
                title.textContent === PublishToAllStory.title)
              history.replaceState({}, '', '/p/fd6c1824c4de/edit');
          });
          if (['wrongTarget', 'bodyMutation', 'rebuiltTitle'].includes(mode)) {
            const original = document.execCommand.bind(document);
            document.execCommand = (...args) => {
              const success = original(...args);
              if (mode === 'wrongTarget') body.dispatchEvent(new InputEvent('input', {
                bubbles: true, composed: true, inputType: 'insertText',
                data: PublishToAllStory.title }));
              if (mode === 'bodyMutation') body.textContent = 'Story unexpectedly changed';
              if (mode === 'rebuiltTitle') {
                const current = document.querySelector('[data-testid="editorTitleParagraph"]');
                current.replaceWith(current.cloneNode(true));
              }
              return success;
            };
          }
          const story = mode === 'wrongFingerprint' ?
            { ...PublishToAllStory, fingerprint: 'fnv1a32:00000000' } : PublishToAllStory;
          const before = MediumTitleFill.eligibility(document, MediumEditorInspector,
            location, story);
          const filled = await MediumTitleFill.fill(document, MediumEditorInspector,
            location, story, false, async () => {});
          return { before, filled, title: document.querySelector(
            '[data-testid="editorTitleParagraph"]')?.textContent,
            body: document.querySelector('[data-testid="editorParagraphText"]')?.innerHTML,
            publishClicks, canonicalCount: document.querySelectorAll(
              'link[rel="canonical"]').length };
        }""", mode)
            page.close()
            return result

        result = exercise("normal")
        assert result["before"]["ready"], result
        assert result["filled"]["workflow"] == "MEDIUM_TITLE_VERIFIED", result
        assert result["filled"]["titleBrowserGeneratedInputObserved"], result
        assert not result["filled"]["titleSyntheticInputFallbackUsed"], result
        assert result["title"] == "Saturation of Artificial Intelligence", result
        assert "Tell your story" in result["body"], result
        assert result["publishClicks"] == result["canonicalCount"] == 0, result
        for mode in ("nonblankTitle", "nonblankBody", "ambiguousRoot", "wrongFingerprint"):
            refused = exercise(mode)
            assert not refused["before"]["ready"], (mode, refused)
            assert not refused["filled"]["titleFillAttempted"], (mode, refused)
            assert refused["publishClicks"] == refused["canonicalCount"] == 0, refused
        fresh = exercise("freshTitle")
        assert fresh["filled"]["workflow"] == "MEDIUM_TITLE_VERIFIED", fresh
        missing = exercise("missingInput")
        assert missing["filled"]["workflow"] == "MEDIUM_TITLE_VERIFIED", missing
        assert missing["filled"]["titleSyntheticInputFallbackUsed"], missing
        assert not missing["filled"]["titleBrowserGeneratedInputObserved"], missing
        assert sum(event["type"] == "input" for event in missing["filled"]["events"]) == 1
        wrong = exercise("wrongTarget")
        assert wrong["filled"]["workflow"] == "MEDIUM_TITLE_VERIFIED", wrong
        assert wrong["filled"]["titleSyntheticInputFallbackUsed"], wrong
        assert any(event["target"] == "other" for event in wrong["filled"]["events"]), wrong
        rebuilt = exercise("rebuiltTitle")
        assert rebuilt["filled"]["workflow"] == "MEDIUM_TITLE_VERIFIED", rebuilt
        assert rebuilt["filled"]["titleNodeRebuilt"], rebuilt
        navigation = exercise("navigation")
        assert navigation["filled"]["workflow"] == "MEDIUM_TITLE_VERIFIED", navigation
        assert navigation["filled"]["navigationOccurred"], navigation
        assert navigation["filled"]["draftIdentityEstablished"], navigation
        assert navigation["filled"]["draftRouteEstablishedByMedium"], navigation
        mutated = exercise("bodyMutation")
        assert mutated["filled"]["workflow"] == "MEDIUM_TITLE_FILL_AMBIGUOUS", mutated
        assert not mutated["filled"]["bodyUnchangedAfterTitleFill"], mutated
        assert mutated["filled"]["titleFillFailure"] == "BODY_CHANGED", mutated
        page = browser.new_page()
        route_path = "https://medium.com/p/fd6c1824c4de/edit"
        page.route(route_path, lambda route: route.fulfill(
            status=200, content_type="text/html", body=POST_TITLE_HTML))
        page.goto(route_path)
        for file in ("story-title.js", "medium-editor-inspector.js"):
            page.add_script_tag(path=str(EXTENSION / file))
        post_title = page.evaluate("""() => {
          let publishClicks = 0;
          document.querySelector('button').addEventListener('click', () => publishClicks++);
          const body = document.querySelector('[data-testid="editorParagraphText"]');
          const before = body.innerHTML;
          const inspection = MediumEditorInspector.inspect(document, location);
          return { inspection, bodyUnchanged: body.innerHTML === before,
            publishClicks, canonicalCount: document.querySelectorAll(
              'link[rel="canonical"]').length };
        }""")
        assert post_title["inspection"]["draftContinuationSafe"], post_title
        assert post_title["inspection"]["workflowState"] == \
            "MEDIUM_TITLE_VERIFIED_BODY_BLANK", post_title
        assert post_title["inspection"]["bodySubregionState"] == \
            "SCAFFOLDING_ONLY", post_title
        assert post_title["bodyUnchanged"], post_title
        assert post_title["publishClicks"] == post_title["canonicalCount"] == 0, post_title
        result["postTitle"] = post_title
        page.close()
        browser.close()
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

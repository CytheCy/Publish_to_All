"""Offline Chromium request-event fixture; all .invalid URLs are fulfilled locally."""

import importlib.util
import json
import shutil
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright


SOURCE = Path(__file__).resolve().parents[1] / "medium_draft_discovery.py"
spec = importlib.util.spec_from_file_location("medium_draft_discovery_fixture", SOURCE)
discovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(discovery)
ID = "fixtureDraft12345"
TITLE = "Fixture title 84172"
BODY = "Fixture body 39204"


def main():
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        raise SystemExit("Chrome or Chromium required")
    rows = []
    with tempfile.TemporaryDirectory(prefix="medium-discovery-fixture-") as profile, sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            profile, executable_path=chrome, headless=True,
            args=["--disable-extensions", "--no-first-run"])
        assert context.cookies() == []
        page = context.pages[0] if context.pages else context.new_page()

        def fulfill(route):
            if route.request.url.endswith("/start"):
                route.fulfill(status=200, content_type="text/html", body="<html><body>fixture</body></html>")
            else:
                payload = {"data": {"story": {"id": ID, "title": TITLE,
                          "content": [{"type": "paragraph", "text": BODY}]}}}
                route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

        page.route("https://discovery.invalid/**", fulfill)

        def observe(response):
            request = response.request
            if response.url.endswith("/start"):
                return
            row = discovery.capture(
                {"url": request.url, "method": request.method,
                 "content_type": request.header_value("content-type"),
                 "body": request.post_data_buffer or b"", "resource_type": request.resource_type},
                {"status": response.status, "content_type": response.header_value("content-type"),
                 "body": response.body()}, phase="a1", draft_id=ID,
                title_marker=TITLE, body_marker=BODY)
            rows.append(row)

        page.on("response", observe)
        page.goto("https://discovery.invalid/start")
        page.evaluate("""async () => {
            await fetch('/read');
            await fetch('/graphql', {method: 'POST', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({operationName: 'ReadDraft',
                query: 'query ReadDraft { story { id title body } }', variables: {id: 'fixtureDraft12345'}})});
            await fetch('/graphql', {method: 'POST', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({operationName: 'SaveDraft',
                query: 'mutation SaveDraft { saveStory { id } }',
                variables: {id: 'fixtureDraft12345', body: 'Fixture body 39204'}})});
        }""")
        page.wait_for_timeout(100)
        context.close()
    assert [row["semantic_operation"] for row in rows] == ["READ", "READ", "WRITE / AUTOSAVE"]
    assert [row["method"] for row in rows] == ["GET", "POST", "POST"]
    assert all(row["response_evidence"]["observed_throwaway_body_marker_match"] for row in rows)
    print(json.dumps({"requests": len(rows), "methods": [row["method"] for row in rows],
                      "operations": [row["semantic_operation"] for row in rows]}))


if __name__ == "__main__":
    main()

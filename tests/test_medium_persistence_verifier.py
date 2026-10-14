"""Offline checks for the fixed production verifier; no Medium requests."""

import json
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VERIFIER = ROOT / "extension-dev" / "medium-persistence-verifier"


def test_canonical_assets_match_validated_extension_assets():
    assert (VERIFIER / "canonical-title.js").read_bytes() == (
        ROOT / "chrome-extension" / "story-title.js"
    ).read_bytes()
    assert (VERIFIER / "canonical-body.js").read_bytes() == (
        ROOT / "chrome-extension" / "story-body.js"
    ).read_bytes()


def test_manifest_and_fixed_transport_have_no_tab_or_cookie_api():
    manifest = json.loads((VERIFIER / "manifest.json").read_text())
    assert manifest["host_permissions"] == ["https://medium.com/*"]
    assert manifest["background"] == {"service_worker": "background.js"}
    assert manifest["action"] == {"default_popup": "popup.html"}
    assert "permissions" not in manifest
    assert "content_scripts" not in manifest
    assert {p.name for p in VERIFIER.iterdir()} == {
        "manifest.json", "background.js", "popup.html", "popup.js",
        "canonical-title.js", "canonical-body.js",
    }
    code = (VERIFIER / "background.js").read_text()
    popup = (VERIFIER / "popup.js").read_text()
    assert code.count("fetch(") == 1
    assert 'method: "GET"' in code
    assert 'credentials: "include"' in code
    assert 'redirect: "manual"' in code
    assert 'const DRAFT_ID = "fd6c1824c4de"' in code
    assert "innerHTML" not in code + popup
    for pattern in (
        r"chrome\s*\.\s*(?:tabs|cookies|scripting)\b",
        r"document\s*\.\s*cookie\b",
        r"\bXMLHttpRequest\b",
        r"\bwindow\s*\.\s*open\s*\(",
        r"\beval\s*\(",
        r"\bnew\s+Function\s*\(",
        r"method\s*:\s*['\"](?:POST|PUT|PATCH|DELETE)['\"]",
    ):
        assert not re.search(pattern, code + popup, re.I), pattern


def test_node_verifier_harness():
    completed = subprocess.run(
        ["node", "--test", str(ROOT / "tests/medium_persistence_verifier.test.js")],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

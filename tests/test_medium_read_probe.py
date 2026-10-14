"""Offline extension safety checks; never contacts Medium."""

import json
import hashlib
import re
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "extension-dev" / "medium-read-probe"
OBSERVATIONS = ROOT / "extension-dev" / ".medium-draft-discovery" / "observations"


def test_probe_allowlist_matches_observed_throwaway_drafts():
    code = (PROBE / "background.js").read_text()
    ids = {
        phase: re.search(rf'\b{slot}: "([a-f0-9]{{12}})"', code).group(1)
        for phase, slot in (("a1", "A"), ("a2", "A"), ("b", "B"))
    }
    assert ids["a1"] == ids["a2"]
    assert ids["a1"] != ids["b"]
    for phase, draft_id in ids.items():
        observed = json.loads((OBSERVATIONS / f"{phase}.json").read_text())
        digest = "sha256:" + hashlib.sha256(draft_id.encode()).hexdigest()[:16]
        assert digest == observed["draft_id_fingerprint"]


def test_probe_manifest_is_minimal_and_scoped():
    manifest = json.loads((PROBE / "manifest.json").read_text())
    assert manifest["manifest_version"] == 3
    assert manifest["host_permissions"] == ["https://medium.com/*"]
    assert manifest["background"] == {"service_worker": "background.js"}
    assert manifest["action"] == {"default_popup": "popup.html"}
    assert "permissions" not in manifest
    assert "content_scripts" not in manifest
    assert {p.name for p in PROBE.iterdir()} == {
        "manifest.json", "background.js", "popup.html", "popup.js"}


def test_probe_static_safety_audit():
    code = "\n".join(p.read_text() for p in PROBE.glob("*.js"))
    popup_html = (PROBE / "popup.html").read_text()
    forbidden = {
        "chrome.cookies usage": r"chrome\s*\.\s*cookies\b",
        "document.cookie usage": r"document\s*\.\s*cookie\b",
        "Cookie header construction": r"\bCookie\s*:",
        "Authorization header construction": r"\bAuthorization\s*:",
        "fetch POST capability": r"\bPOST\b\s*[,;}\]]|method\s*:\s*['\"]POST",
        "XMLHttpRequest write capability": r"XMLHttpRequest|\.open\s*\(",
        "tab creation": r"chrome\s*\.\s*tabs|tabs\s*\.\s*create",
        "window.open": r"window\s*\.\s*open\s*\(",
        "iframe creation": r"<iframe\b|createElement\s*\(\s*['\"]iframe",
        "eval": r"\beval\s*\(|new\s+Function\s*\(",
    }
    for label, pattern in forbidden.items():
        assert not re.search(pattern, code, re.I), label
    assert code.count("fetch(") == 1
    assert 'method: "GET"' in code
    assert 'redirect: "manual"' in code
    assert 'credentials: "include"' in code
    assert "innerHTML" not in code
    assert 'message.type !== "RUN_DRAFT_A"' in code
    assert 'message.type !== "RUN_DRAFT_B"' in code
    assert 'type: index === 0 ? "RUN_DRAFT_A" : "RUN_DRAFT_B"' in code
    assert '<button id="run-a" type="button">Run one Draft A GET</button>' in popup_html
    assert '<button id="run-b" type="button">Run one Draft B GET</button>' in popup_html


def test_probe_node_harness():
    completed = subprocess.run(
        ["node", "--test", str(ROOT / "tests/medium_read_probe.test.js")],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr

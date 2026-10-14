"""Offline fixtures only: these tests never launch a browser or contact Medium."""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "extension-dev" / "medium_draft_discovery.py"
spec = importlib.util.spec_from_file_location("medium_draft_discovery", SOURCE)
discovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(discovery)
A = ("throwawayA12345", "DISCOVERY TEST A 74291",
     ("MEDIUM_DISCOVERY_A_START_74291", "MEDIUM_DISCOVERY_A_MIDDLE_74291",
      "MEDIUM_DISCOVERY_A_END_74291"))
B = ("throwawayB67890", "DISCOVERY TEST B 39518",
     ("MEDIUM_DISCOVERY_B_START_39518", "MEDIUM_DISCOVERY_B_MIDDLE_39518",
      "MEDIUM_DISCOVERY_B_END_39518"))


def entry(draft=A, *, method="GET", operation=None, response=True, path=None,
          resource="fetch", headers=None, request_body=None):
    draft_id, title, markers = draft
    if path is None:
        path = f"/p/{draft_id}/edit"
    if operation == "query":
        request_body = json.dumps({"operationName": "ReadDraft", "query":
                                   "query ReadDraft { story { id title content } }",
                                   "variables": {"id": draft_id}})
    if operation == "mutation":
        request_body = json.dumps({"operationName": "SaveDraft", "query":
                                   "mutation SaveDraft { saveStory { id } }",
                                   "variables": {"id": draft_id, "body": markers[0]}})
    content = {"data": {"story": {"id": draft_id, "title": title,
                                  "content": {"blocks": [{"type": "paragraph", "text": marker}
                                                         for marker in markers]}}}}
    return {"_resourceType": resource,
            "request": {"method": method, "url": "https://medium.com" + path + "?csrf=TOP_SECRET",
                        "headers": headers or [{"name": "Cookie", "value": "SESSION_SECRET"},
                                               {"name": "Authorization", "value": "Bearer TOP_SECRET"},
                                               {"name": "X-CSRF-Token", "value": "CSRF_SECRET"}],
                        "postData": {"text": request_body} if request_body is not None else {}},
            "response": {"status": 200, "headers": [{"name": "Content-Type", "value": "application/json"},
                                                    {"name": "Set-Cookie", "value": "SESSION_SECRET"}],
                         "content": {"mimeType": "application/json",
                                     "text": json.dumps(content) if response else "{}"}}}


def write_har(tmp_path, entries, name="capture.har"):
    path = tmp_path / name
    path.write_text(json.dumps({"log": {"version": "1.2", "_fixture": name, "entries": entries}}))
    return path


def analyze(tmp_path, draft=A, phase="a1", entries=None, name="capture.har"):
    path = write_har(tmp_path, [entry(draft)] if entries is None else entries, name)
    return discovery.analyze_har(path, phase=phase, draft_id=draft[0],
                                 title=draft[1], markers=draft[2])


def test_get_read_has_structured_saved_draft_evidence(tmp_path):
    data = analyze(tmp_path)
    row = data["entries"][0]
    assert (row["method"], row["semantic_operation"], row["classification"]) == (
        "GET", "READ", "DRAFT_READ_CANDIDATE")
    assert row["path"] == "/p/{draft_id}/edit"
    assert row["status"] == 200
    assert row["response_evidence"]["draft_identifier_field_path"] == "$.data.story.id"
    assert row["response_evidence"]["title_field_path"] == "$.data.story.title"
    assert len(row["response_evidence"]["body_field_paths"]) == 3
    assert row["response_evidence"]["body_representation"] == "block JSON"


def test_post_graphql_query_is_read_and_mutation_is_write(tmp_path):
    data = analyze(tmp_path, entries=[entry(method="POST", operation="query", path="/_/graphql"),
                                      entry(method="POST", operation="mutation", path="/_/graphql")])
    read, write = data["entries"]
    assert (read["method"], read["graphql_operation_type"], read["graphql_operation_name"],
            read["semantic_operation"], read["classification"]) == (
                "POST", "QUERY", "ReadDraft", "READ", "DRAFT_READ_CANDIDATE")
    assert (write["graphql_operation_type"], write["semantic_operation"], write["classification"]) == (
        "MUTATION", "WRITE", "AUTOSAVE_WRITE")
    assert read["request_body_length"] > 0
    assert read["request_body_fingerprint"].startswith("sha256:")


def test_generic_mutation_and_payload_write_are_separate(tmp_path):
    generic = entry(method="POST", path="/_/graphql", request_body=json.dumps({
        "operationName": "ChangeStory", "query": "mutation ChangeStory { changeStory { id } }",
        "variables": {"id": A[0]}}))
    payload = entry(method="PATCH", path="/p/" + A[0] + "/edit",
                    request_body=json.dumps({"content": A[2][0]}), response=False)
    rows = analyze(tmp_path, entries=[generic, payload])["entries"]
    assert [row["classification"] for row in rows] == ["MUTATION", "AUTOSAVE_WRITE"]
    assert all(row["semantic_operation"] == "WRITE" for row in rows)


def test_sensitive_data_and_unrelated_sign_in_are_not_retained(tmp_path):
    signin = entry(method="POST", path="/signin/opaque123456",
                   request_body=json.dumps({"password": "PASSWORD_SECRET"}), response=False)
    signin["request"]["url"] = "https://medium.com/signin/opaque123456?token=TOP_SECRET"
    data = analyze(tmp_path, entries=[signin, entry()])
    serialized = json.dumps(data)
    assert len(data["entries"]) == 1
    for value in ("SESSION_SECRET", "TOP_SECRET", "CSRF_SECRET", "PASSWORD_SECRET",
                  "Bearer", "Set-Cookie", "Authorization", "?csrf", A[0], A[1], *A[2]):
        assert value not in serialized
    assert data["raw_headers_retained"] is False
    assert data["raw_bodies_retained"] is False
    output = tmp_path / "sanitized.json"
    discovery.save_sanitized(data, output)
    assert os.stat(output).st_mode & 0o777 == 0o600
    assert "SESSION_SECRET" not in output.read_text()


def test_unrelated_traffic_categories_do_not_become_reads():
    for path, resource, expected in (("/app.js", "script", "STATIC_ASSET"),
                                     ("/collect", "fetch", "ANALYTICS"),
                                     ("/account", "fetch", "ACCOUNT_STATE"),
                                     ("/other", "fetch", "UNKNOWN")):
        item = entry(path=path, resource=resource, response=False)
        row = discovery.analyze_entry(item, *A)
        assert row["classification"] == expected
        assert row["semantic_operation"] == "UNKNOWN"


def test_response_body_missing_or_html_cannot_establish_contract(tmp_path):
    item = entry()
    item["response"]["content"].pop("text")
    assert analyze(tmp_path, entries=[item])["entries"][0]["classification"] == "UNKNOWN"
    html = entry()
    html["response"]["content"] = {"mimeType": "text/html", "text":
                                   "<html>" + A[0] + A[1] + "".join(A[2]) + "</html>"}
    html["response"]["headers"] = [{"name": "Content-Type", "value": "text/html"}]
    row = analyze(tmp_path, entries=[html], name="html.har")["entries"][0]
    assert row["classification"] == "DRAFT_READ_CANDIDATE"
    assert row["response_evidence"]["draft_identifier_field_path"] is None


def test_html_element_paths_can_be_compared_without_leaking_content(tmp_path):
    captures = {}
    for phase, draft in (("a1", A), ("a2", A), ("b", B)):
        item = entry(draft)
        draft_id, title, markers = draft
        item["response"]["content"] = {
            "mimeType": "text/html",
            "text": "<html><head><title>" + title + "</title><script>" + draft_id +
                    "</script></head><body>" + "".join("<p>" + marker + "</p>" for marker in markers) +
                    "</body></html>"}
        item["response"]["headers"] = [{"name": "Content-Type", "value": "text/html"}]
        data = analyze(tmp_path, draft=draft, phase=phase, entries=[item], name=phase + "-html.har")
        evidence = data["entries"][0]["response_evidence"]
        assert evidence["draft_identifier_field_path"] == "HTML:<script>"
        assert evidence["title_field_path"] == "HTML:<title>"
        assert set(evidence["body_field_paths"].values()) == {"HTML:<p>"}
        assert evidence["body_representation"] == "HTML"
        assert title not in json.dumps(data)
        output = tmp_path / (phase + "-html.json")
        discovery.save_sanitized(data, output)
        captures[phase] = output
    assert discovery.report(captures)["decision"] == "READ_CONTRACT_FOUND_REPLAY_SAFETY_UNKNOWN"


def test_malformed_capture_and_production_id_rejection(tmp_path):
    malformed = tmp_path / "bad.har"
    malformed.write_text("{")
    with pytest.raises(ValueError, match="Malformed HAR"):
        discovery.analyze_har(malformed, phase="a1", draft_id=A[0], title=A[1], markers=A[2])
    path = write_har(tmp_path, [entry()], "forbidden.har")
    path.write_text(path.read_text().replace(A[0], "fd6c1824c4de"))
    with pytest.raises(discovery.ForbiddenProductionDraft):
        discovery.analyze_har(path, phase="a1", draft_id=A[0], title=A[1], markers=A[2])
    with pytest.raises(discovery.ForbiddenProductionDraft):
        discovery.analyze_har(path, phase="a1", draft_id="fd6c1824c4de", title=A[1], markers=A[2])


def test_three_reopens_require_two_distinct_drafts_and_matching_structure(tmp_path):
    captures = {}
    for phase, draft in (("a1", A), ("a2", A), ("b", B)):
        data = analyze(tmp_path, draft=draft, phase=phase, name=phase + ".har")
        output = tmp_path / (phase + ".json")
        discovery.save_sanitized(data, output)
        captures[phase] = output
    result = discovery.report(captures)
    assert result["decision"] == "READ_CONTRACT_FOUND_REPLAY_SAFETY_UNKNOWN"
    assert result["same_contract_in_three_reopens"] is True
    assert result["replay_performed"] is False
    wrong = analyze(tmp_path, draft=A, phase="b", name="wrong.har")
    wrong_path = tmp_path / "wrong.json"
    discovery.save_sanitized(wrong, wrong_path)
    captures["b"] = wrong_path
    assert discovery.report(captures)["decision"] == "READ_CONTRACT_NOT_FOUND"
    with pytest.raises(ValueError, match="Exactly"):
        discovery.report({"a1": captures["a1"]})


def test_user_reported_replay_evidence_promotes_only_matching_throwaway_contract(tmp_path):
    captures = {}
    fingerprints = {}
    for phase, draft in (("a1", A), ("a2", A), ("b", B)):
        data = analyze(tmp_path, draft=draft, phase=phase, name=phase + "-replay.har")
        output = tmp_path / (phase + "-replay.json")
        discovery.save_sanitized(data, output)
        captures[phase] = output
        fingerprints[phase] = data["draft_id_fingerprint"]
    expected = {"get_count": 1, "write_method_count": 0, "http_status": 200,
                "complete_html": True, "correct_id_title_body_markers": True,
                "manual_followup_unchanged": True}
    replay = {"format": "medium-manual-replay-safety-v1",
              "source": "user_reported_2026-10-13",
              "contract": {"method": "GET", "host": "medium.com",
                           "path": "/p/{draft_id}/edit", "response_format": "HTML"},
              "drafts": {
                  "A": {"draft_id_fingerprint": fingerprints["a1"], **expected},
                  "B": {"draft_id_fingerprint": fingerprints["b"], **expected}}}
    path = tmp_path / "replay.json"
    path.write_text(json.dumps(replay))
    result = discovery.report(captures, path)
    assert result["decision"] == "READ_CONTRACT_FOUND_CANDIDATE_SAFE"
    assert result["replay_performed"] is True
    for changed in ({"get_count": 2}, {"write_method_count": 1},
                    {"manual_followup_unchanged": False},
                    {"draft_id_fingerprint": "sha256:wrong"}):
        bad = json.loads(json.dumps(replay))
        bad["drafts"]["B"].update(changed)
        path.write_text(json.dumps(bad))
        with pytest.raises(ValueError, match="Invalid replay safety evidence"):
            discovery.report(captures, path)


def test_cli_has_no_record_or_browser_replay_capability(tmp_path):
    source = SOURCE.read_text()
    for forbidden in ("playwright", "selenium", "webdriver", "subprocess", "requests.",
                      "httpx.", "urlopen(", "launch(", "goto(", "fetch(", "route("):
        assert forbidden not in source.lower()
    help_result = subprocess.run([sys.executable, "-B", str(SOURCE), "--help"],
                                 capture_output=True, text=True, check=True)
    assert "analyze" in help_result.stdout and "report" in help_result.stdout
    assert "record" not in help_result.stdout
    assert discovery.main(["analyze", str(tmp_path / "missing.har"), "--phase", "a1",
                           "--draft-id", A[0], "--title-marker", A[1],
                           "--body-marker", A[2][0], "--body-marker", A[2][1],
                           "--output", str(tmp_path / "out.json")]) == 2
    assert not (tmp_path / "out.json").exists()

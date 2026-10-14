"""Offline contract tests; no Medium account or network requests."""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "extension-dev" / "medium_draft_discovery.py"
spec = importlib.util.spec_from_file_location("medium_draft_discovery", SOURCE)
discovery = importlib.util.module_from_spec(spec)
spec.loader.exec_module(discovery)

A_ID = "throwawayA123456"
B_ID = "throwawayB654321"
A_TITLE = "Contract title alpha 87361"
A_BODY = "Contract body alpha 28471"
B_TITLE = "Contract title beta 51944"
B_BODY = "Contract body beta 90216"


def exchange(*, method="GET", draft_id=A_ID, title=A_TITLE, body=A_BODY,
             phase="a1", request_body="", operation=None, response_body=None,
             path=None):
    if operation:
        request_body = json.dumps({"operationName": operation,
                                   "query": f"query {operation} {{ story {{ id title body }} }}",
                                   "variables": {"id": draft_id}})
        method = "POST"
    if response_body is None:
        response_body = json.dumps({"data": {"story": {"id": draft_id,
            "title": title, "content": {"blocks": [{"type": "paragraph",
                "text": body, "markups": []}]}}}})
    return discovery.capture(
        {"url": f"https://medium.com{path or f'/_/api/posts/{draft_id}'}?token=SECRET&mode=edit",
         "method": method, "content_type": "application/json",
         "body": request_body, "resource_type": "fetch"},
        {"status": 200, "content_type": "application/json",
         "body": response_body, "length": len(response_body)},
        phase=phase, draft_id=draft_id, title_marker=title, body_marker=body)


def test_production_id_rejected_in_inputs_request_and_response():
    forbidden = discovery.PRODUCTION_DRAFT_FORBIDDEN
    with pytest.raises(discovery.ForbiddenProductionDraft):
        discovery.capture({"url": f"https://medium.com/p/{forbidden}/edit", "method": "GET"},
                          {}, phase="a1", draft_id=A_ID)
    encoded = "".join(f"%{ord(char):02x}" for char in forbidden)
    with pytest.raises(discovery.ForbiddenProductionDraft):
        discovery.capture({"url": f"https://medium.com/p/{encoded}/edit", "method": "GET"},
                          {}, phase="a1", draft_id=A_ID)
    with pytest.raises(discovery.ForbiddenProductionDraft):
        discovery.capture({"url": "https://medium.com/x", "method": "POST",
                           "body": forbidden}, {}, phase="a1")
    with pytest.raises(discovery.ForbiddenProductionDraft):
        discovery.capture({"url": "https://medium.com/x", "method": "GET"},
                          {"body": forbidden}, phase="a1")
    with pytest.raises(discovery.ForbiddenProductionDraft):
        discovery.capture({"url": "https://medium.com/x", "method": "GET"},
                          {}, phase="a1", draft_id=forbidden)


def test_isolated_profile_rejects_existing_data_and_symlinks(tmp_path):
    root = tmp_path / "discovery"
    data = discovery.prepare_profile("throwaway", root)
    assert data == root / "profiles" / "throwaway" / "chrome-data"
    assert (data.parent / ".isolated-medium-discovery").read_text() == "isolated-v1\n"
    assert discovery.prepare_profile("throwaway", root) == data
    unmarked = root / "profiles" / "existing"
    unmarked.mkdir()
    (unmarked / "Cookies").write_text("production-cookie")
    with pytest.raises(ValueError, match="unmarked"):
        discovery.prepare_profile("existing", root)
    (root / "profiles" / "linked").symlink_to(unmarked, target_is_directory=True)
    with pytest.raises(ValueError, match="Symlinked"):
        discovery.prepare_profile("linked", root)
    assert not (data / "Cookies").exists()


def test_bounded_metadata_and_no_header_or_secret_values():
    row = exchange()
    serialized = json.dumps(row)
    assert row["query_parameter_names"] == ["mode", "token"]
    assert row["request_body_length"] == 0
    assert row["response_length"] > 0
    assert row["host"] == "medium.com"
    assert "SECRET" not in serialized
    assert A_TITLE not in serialized
    assert A_BODY not in serialized
    assert "headers" not in serialized.lower()
    huge = discovery.capture(
        {"url": "https://medium.com/asset", "method": "GET", "resource_type": "script"},
        {"content_type": "application/json", "body": b"x" * (discovery.MAX_BODY_BYTES + 1)},
        phase="write")
    assert huge["response_evidence"]["observed_throwaway_body_marker_match"] is False


def test_get_semantic_read_and_schema_summary():
    row = exchange()
    evidence = row["response_evidence"]
    assert (row["classification"], row["semantic_operation"]) == ("DRAFT_READ_CANDIDATE", "READ")
    assert evidence["response_format"] == "JSON"
    assert evidence["top_level_response_keys"] == ["data"]
    assert evidence["draft_identifier_path"] == "$.data.story.id"
    assert evidence["title_field_path"] == "$.data.story.title"
    assert evidence["body_content_field_path"] == "$.data.story.content.blocks[0].text"
    assert evidence["body_representation"] == "block JSON"
    assert evidence["formatting_representation"] == ["blocks", "markups", "type"]
    assert all(evidence[key] for key in (
        "observed_throwaway_draft_id_match", "observed_throwaway_title_match",
        "observed_throwaway_body_marker_match"))


def test_post_graphql_query_is_semantic_read_without_replay():
    row = exchange(operation="ReadDraft", path="/_/graphql")
    assert row["method"] == "POST"
    assert row["graphql_operation_type"] == "QUERY"
    assert row["graphql_operation_name"] == "ReadDraft"
    assert row["classification"] == "DRAFT_READ_CANDIDATE"
    assert row["semantic_operation"] == "READ"
    assert row["request_body_length"] > 0
    assert row["request_body_fingerprint"].startswith("sha256:")
    assert row["response_evidence"]["response_format"] == "GraphQL"


def test_post_mutation_and_autosave_are_writes():
    payload = json.dumps({"operationName": "SaveDraft", "query":
                          "mutation SaveDraft { saveStory { id } }",
                          "variables": {"id": A_ID, "body": A_BODY}})
    row = exchange(method="POST", request_body=payload, phase="write",
                   path="/_/graphql")
    assert row["graphql_operation_type"] == "MUTATION"
    assert row["request_contains_modified_story_data"] is True
    assert (row["classification"], row["semantic_operation"]) == (
        "MUTATION_CANDIDATE", "WRITE / AUTOSAVE")
    assert A_BODY not in json.dumps(row)
    row = exchange(method="POST", request_body=json.dumps({"body": A_BODY}), phase="write")
    assert row["semantic_operation"] == "WRITE / AUTOSAVE"
    marker_free = json.dumps({"operationName": "TouchDraft", "query":
                              "mutation TouchDraft { touchStory { id } }",
                              "variables": {"id": A_ID}})
    row = exchange(method="POST", request_body=marker_free, phase="write",
                   path="/_/graphql")
    assert row["request_contains_modified_story_data"] is False
    assert row["semantic_operation"] == "WRITE / AUTOSAVE"


def test_static_analytics_and_unknown_do_not_become_reads():
    for path, resource, expected in (("/app.js", "script", "STATIC_ASSET"),
                                      ("/collect", "fetch", "ANALYTICS"),
                                      ("/other", "fetch", "UNKNOWN")):
        row = discovery.capture({"url": "https://medium.com" + path,
                                 "method": "GET", "resource_type": resource},
                                {"status": 200}, phase="a1", draft_id=A_ID,
                                title_marker=A_TITLE, body_marker=A_BODY)
        assert row["classification"] == expected


def test_html_marker_match_lacks_field_schema_and_cannot_establish_contract(tmp_path):
    rows = []
    for phase, draft_id, title, body in (("a1", A_ID, A_TITLE, A_BODY),
                                          ("a2", A_ID, A_TITLE, A_BODY),
                                          ("b", B_ID, B_TITLE, B_BODY)):
        row = discovery.capture(
            {"url": f"https://medium.com/p/{draft_id}/edit", "method": "GET"},
            {"content_type": "text/html", "body":
             f"<html><script>{draft_id} {title} {body}</script></html>"},
            phase=phase, draft_id=draft_id, title_marker=title, body_marker=body)
        assert row["classification"] == "DRAFT_READ_CANDIDATE"
        assert row["response_evidence"]["body_content_field_path"] is None
        rows.append(row)
    file = tmp_path / "html.jsonl"
    file.write_text("\n".join(json.dumps(row) for row in rows))
    assert discovery.report([file])["decision"] == "READ_CONTRACT_NOT_FOUND"


def test_two_distinct_drafts_and_second_reopen_required(tmp_path):
    rows = [exchange(phase="a1"), exchange(phase="a2"),
            exchange(draft_id=B_ID, title=B_TITLE, body=B_BODY, phase="b")]
    file = tmp_path / "capture.jsonl"
    file.write_text("\n".join(json.dumps(row) for row in rows) + "\n")
    result = discovery.report([file])
    assert result["decision"] == "READ_CONTRACT_FOUND_REPLAY_SAFETY_UNKNOWN"
    assert result["distinct_throwaway_drafts_tested"] == 2
    assert result["same_contract_across_reopens_and_drafts"] is True
    assert result["replay_performed"] is False
    file.write_text("\n".join(json.dumps(row) for row in rows[:-1]) + "\n")
    assert discovery.report([file])["decision"] == "READ_CONTRACT_NOT_FOUND"
    wrong_b = exchange(phase="b")
    file.write_text("\n".join(json.dumps(row) for row in rows[:2] + [wrong_b]) + "\n")
    assert discovery.report([file])["distinct_throwaway_drafts_tested"] == 0
    assert discovery.report([file])["decision"] == "READ_CONTRACT_NOT_FOUND"


def test_no_replay_editor_mutation_or_production_extension_capabilities():
    source = SOURCE.read_text()
    for forbidden in (".goto(", ".click(", ".fill(", ".evaluate(",
                      "route.fetch(", "requests.", "httpx.", "urlopen(",
                      "chrome-extension/", "execCommand", "localStorage",
                      "sessionStorage", "indexedDB", "Publish", "Settings"):
        assert forbidden not in source
    assert "--disable-extensions" in source
    assert "route.abort()" in source


def test_offline_chrome_discovery_fixture():
    result = subprocess.run(
        [sys.executable, "-B", str(ROOT / "extension-dev" / "fixtures" /
                                   "chrome_medium_discovery_fixture.py")],
        capture_output=True, text=True, check=True)
    assert json.loads(result.stdout)["operations"] == [
        "READ", "READ", "WRITE / AUTOSAVE"]

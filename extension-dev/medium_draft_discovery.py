"""Passive, isolated Medium draft network discovery. Never sends a request itself."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit


PRODUCTION_DRAFT_FORBIDDEN = "fd6c1824c4de"
ROOT = Path(__file__).resolve().parent / ".medium-draft-discovery"
MAX_BODY_BYTES = 2_000_000
MAX_PATH = 512
SAFE_NAME = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
SAFE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
GRAPHQL_OPERATION = re.compile(r"\b(query|mutation|subscription)\s+([A-Za-z_][A-Za-z0-9_]{0,79})?")


class ForbiddenProductionDraft(ValueError):
    pass


def reject_production(value: object) -> None:
    if PRODUCTION_DRAFT_FORBIDDEN in unquote(str(value)).lower():
        raise ForbiddenProductionDraft("PRODUCTION_DRAFT_FORBIDDEN")


def profile_path(name: str, root: Path = ROOT) -> Path:
    reject_production(name)
    if not SAFE_NAME.fullmatch(name):
        raise ValueError("Profile name must use letters, digits, underscore, or hyphen")
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise ValueError("Discovery root is unsafe")
    path = root / "profiles" / name
    if path.parent.is_symlink() or path.is_symlink():
        raise ValueError("Symlinked discovery profile is forbidden")
    return path


def prepare_profile(name: str, root: Path = ROOT) -> Path:
    path = profile_path(name, root)
    marker = path / ".isolated-medium-discovery"
    if path.exists():
        if not marker.is_file() or marker.is_symlink() or marker.read_text() != "isolated-v1\n":
            raise ValueError("Existing unmarked browser profile is forbidden")
    else:
        path.mkdir(parents=True, mode=0o700)
        marker.write_text("isolated-v1\n")
    data = path / "chrome-data"
    if data.is_symlink():
        raise ValueError("Symlinked browser data is forbidden")
    data.mkdir(exist_ok=True, mode=0o700)
    return data


def _safe_path(url: str) -> dict:
    reject_production(url)
    parts = urlsplit(url)
    path = parts.path[:MAX_PATH]
    def safe_segment(segment: str) -> str:
        if len(segment) > 80:
            return "[long-segment]"
        if len(segment) >= 24 and re.fullmatch(r"[A-Za-z0-9_.-]+", segment):
            return "[opaque-segment]"
        return segment
    path = "/".join(safe_segment(segment) for segment in path.split("/"))
    names = sorted({key for key, _ in parse_qsl(parts.query, keep_blank_values=True)
                    if SAFE_KEY.fullmatch(key)})[:32]
    return {"host": (parts.hostname or "")[:128], "path": path,
            "query_parameter_names": names}


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()[:16]


def _operation(body: str) -> tuple[str | None, str | None]:
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict):
        name = payload.get("operationName")
        if not isinstance(name, str) or not SAFE_KEY.fullmatch(name):
            name = None
        query = payload.get("query", "")
        match = GRAPHQL_OPERATION.search(query) if isinstance(query, str) else None
        return (match.group(1).upper() if match else None,
                name or (match.group(2) if match else None))
    match = GRAPHQL_OPERATION.search(body)
    return (match.group(1).upper(), match.group(2)) if match else (None, None)


def _matches(value: str, marker: str) -> bool:
    return bool(marker and marker in value)


def summarize_response(body: bytes, content_type: str, draft_id: str,
                       title_marker: str, body_marker: str) -> dict:
    """Return field paths and marker booleans, never source values."""
    reject_production(body)
    result = {"response_format": "other", "top_level_response_keys": [],
              "draft_identifier_path": None, "title_field_path": None,
              "body_content_field_path": None, "body_representation": "Unknown",
              "formatting_representation": "Unknown",
              "observed_throwaway_draft_id_match": False,
              "observed_throwaway_title_match": False,
              "observed_throwaway_body_marker_match": False}
    lowered = content_type.lower()
    if "html" in lowered:
        result["response_format"] = "HTML"
    elif "json" in lowered:
        result["response_format"] = "GraphQL" if "graphql" in lowered else "JSON"
    try:
        value = json.loads(body) if "json" in lowered else None
    except ValueError:
        value = None
    if value is None:
        decoded = body.decode("utf-8", "replace")
        for marker, flag in ((draft_id, "observed_throwaway_draft_id_match"),
                             (title_marker, "observed_throwaway_title_match"),
                             (body_marker, "observed_throwaway_body_marker_match")):
            result[flag] = _matches(decoded, marker)
        # HTML can include unrelated matches; document-level evidence is not a field schema.
        return result
    if isinstance(value, dict):
        result["top_level_response_keys"] = sorted(
            key for key in value if SAFE_KEY.fullmatch(key))[:40]
        if "data" in value and ("errors" in value or "extensions" in value):
            result["response_format"] = "GraphQL"
    stack = [(value, "$", ())]
    visited = 0
    while stack and visited < 30_000:
        node, path, ancestors = stack.pop()
        visited += 1
        if isinstance(node, dict):
            keys = tuple(key for key in node if isinstance(key, str) and SAFE_KEY.fullmatch(key))
            for key in reversed(keys[:100]):
                stack.append((node[key], f"{path}.{key}", ancestors + (keys,)))
        elif isinstance(node, list):
            for index in reversed(range(min(len(node), 1000))):
                stack.append((node[index], f"{path}[{index}]", ancestors))
        elif isinstance(node, (str, int)):
            text = str(node)
            for marker, flag, field in (
                (draft_id, "observed_throwaway_draft_id_match", "draft_identifier_path"),
                (title_marker, "observed_throwaway_title_match", "title_field_path"),
                (body_marker, "observed_throwaway_body_marker_match", "body_content_field_path"),
            ):
                if _matches(text, marker):
                    result[flag] = True
                    if result[field] is None:
                        result[field] = path
                        if field == "body_content_field_path":
                            if re.search(r"<\w+[^>]*>", text):
                                result["body_representation"] = "HTML"
                            elif re.search(r"(?m)^#{1,6} |\*\*[^*]+\*\*", text):
                                result["body_representation"] = "Markdown"
                            elif "[" in path and ancestors:
                                result["body_representation"] = "block JSON"
                            else:
                                result["body_representation"] = "other"
                            formatting = {key for group in ancestors for key in group
                                          if key.lower() in {"type", "markups", "format",
                                                             "formatting", "style", "paragraphs", "blocks"}}
                            if formatting:
                                result["formatting_representation"] = sorted(formatting)
    return result


def classify(record: dict) -> tuple[str, str]:
    path = record["path"].lower()
    content_type = record["response_content_type"].lower()
    resource = record["resource_type"].lower()
    marker = record["response_evidence"]
    has_draft = marker["observed_throwaway_draft_id_match"]
    has_content = marker["observed_throwaway_title_match"] or marker["observed_throwaway_body_marker_match"]
    operation = record["graphql_operation_type"]
    if operation == "MUTATION" or record["request_contains_modified_story_data"]:
        return "MUTATION_CANDIDATE", "WRITE / AUTOSAVE"
    if (has_draft and has_content and record["method"] == "GET"
            and not record["request_body_length"]):
        return "DRAFT_READ_CANDIDATE", "READ"
    if has_draft and has_content and record["method"] == "POST" and operation == "QUERY":
        return "DRAFT_READ_CANDIDATE", "READ"
    if resource in {"stylesheet", "font", "image", "media", "script"}:
        return "STATIC_ASSET", "UNKNOWN"
    if any(word in path for word in ("analytics", "tracking", "collect", "metrics")):
        return "ANALYTICS", "UNKNOWN"
    if "json" in content_type and not has_draft and any(
            word in path for word in ("account", "user", "viewer", "session")):
        return "ACCOUNT_STATE", "UNKNOWN"
    return "UNKNOWN", "UNKNOWN"


def capture(request: dict, response: dict, *, phase: str, draft_id: str = "",
            title_marker: str = "", body_marker: str = "") -> dict:
    """Pure adapter for one browser-observed exchange; no transport APIs."""
    for value in (request, response, phase, draft_id, title_marker, body_marker):
        reject_production(value)
    method = request["method"].upper()
    url = _safe_path(request["url"])
    body = request.get("body") or b""
    if isinstance(body, str):
        body = body.encode()
    response_body = response.get("body") or b""
    if isinstance(response_body, str):
        response_body = response_body.encode()
    if len(response_body) > MAX_BODY_BYTES:
        response_body = b""
    request_text = body.decode("utf-8", "replace")
    operation_type, operation_name = _operation(request_text)
    request_has_story = bool((title_marker and title_marker in request_text)
                             or (body_marker and body_marker in request_text))
    content_type = str(response.get("content_type") or "")[:100].split(";", 1)[0]
    evidence = summarize_response(response_body, content_type, draft_id,
                                  title_marker, body_marker)
    if operation_type in {"QUERY", "MUTATION"} and evidence["response_format"] == "JSON":
        evidence["response_format"] = "GraphQL"
    redirect = response.get("redirect_url") or ""
    result = {"timestamp": datetime.now(timezone.utc).isoformat(), "phase": phase,
              "method": method, **url,
              "path_template": url["path"].replace(draft_id, "{draft_id}") if draft_id else url["path"],
              "draft_id_fingerprint": _digest(draft_id.encode()) if draft_id else None,
              "request_content_type": str(request.get("content_type") or "")[:100].split(";", 1)[0],
              "request_body_length": len(body),
              "request_body_fingerprint": _digest(body) if body else None,
              "response_status": response.get("status"),
              "response_content_type": content_type,
              "response_length": response.get("length", len(response_body)),
              "redirect_target": _safe_path(redirect) if redirect else None,
              "resource_type": str(request.get("resource_type") or "")[:40],
              "initiator_type": str(request.get("initiator_type") or "")[:40],
              "graphql_operation_type": operation_type,
              "graphql_operation_name": operation_name,
              "request_contains_modified_story_data": request_has_story,
              "response_evidence": evidence}
    result["classification"], result["semantic_operation"] = classify(result)
    return result


def _validate_markers(args) -> None:
    for item in (args.draft_id, args.title_marker, args.body_marker):
        reject_production(item)
    if args.phase != "write" and not all((args.draft_id, args.title_marker, args.body_marker)):
        raise ValueError("Reopen phases require throwaway draft ID, title marker, and body marker")
    if args.phase != "write" and any(len(item) < 8 for item in (args.title_marker, args.body_marker)):
        raise ValueError("Markers must be at least eight characters")


def record(args) -> Path:
    _validate_markers(args)
    data_dir = prepare_profile(args.profile)
    profile = data_dir.parent
    output = profile / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{args.phase}.jsonl"
    chrome = shutil.which("google-chrome") or shutil.which("chromium")
    if not chrome:
        raise RuntimeError("Chrome or Chromium is required")
    from playwright.sync_api import sync_playwright

    fatal = []
    with output.open("x", encoding="utf-8") as stream, sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            str(data_dir), executable_path=chrome, headless=False,
            args=["--disable-extensions", "--no-first-run", "--no-default-browser-check"])

        # The only interception is a fail-closed production-ID guard. Continuing
        # other browser-originated requests does not create or replay a request.
        def guard_request(route):
            try:
                reject_production(route.request.url)
                reject_production(route.request.post_data_buffer or b"")
            except ForbiddenProductionDraft as exc:
                fatal.append(exc)
                route.abort()
                return
            route.continue_()

        context.route("**/*", guard_request)

        def on_response(response):
            if fatal:
                return
            try:
                request = response.request
                reject_production(request.url)
                req_body = request.post_data_buffer or b""
                reject_production(req_body)
                mime = response.header_value("content-type") or ""
                length_header = response.header_value("content-length") or ""
                expected_length = int(length_header) if length_header.isdigit() else None
                host = urlsplit(response.url).hostname or ""
                if host != "medium.com" and not host.endswith(".medium.com"):
                    return
                inspect_body = ("json" in mime.lower() or "html" in mime.lower()) and (
                    expected_length is None or expected_length <= MAX_BODY_BYTES)
                body = response.body() if inspect_body else b""
                reject_production(body)
                row = capture(
                    {"url": request.url, "method": request.method,
                     "content_type": request.header_value("content-type"), "body": req_body,
                     "resource_type": request.resource_type},
                    {"status": response.status, "content_type": mime,
                     "length": (len(body) if inspect_body else None)
                     if expected_length is None else expected_length,
                     "body": body, "redirect_url": response.header_value("location")},
                    phase=args.phase, draft_id=args.draft_id,
                    title_marker=args.title_marker, body_marker=args.body_marker)
                stream.write(json.dumps(row, sort_keys=True) + "\n")
                stream.flush()
            except Exception as exc:  # close the browser after the event callback returns
                fatal.append(exc)

        context.on("response", on_response)
        print("Isolated discovery Chrome opened on a blank page. Drive it manually.")
        print("Press Enter after this observation phase is complete.")
        done = threading.Event()
        threading.Thread(target=lambda: (input(), done.set()), daemon=True).start()
        try:
            while not done.is_set() and not fatal:
                # Pump Playwright events while the user drives the browser.
                context.pages[0].wait_for_timeout(250)
        finally:
            context.close()
    if fatal:
        output.unlink(missing_ok=True)
        raise fatal[0]
    return output


def report(paths: list[Path]) -> dict:
    observations = []
    for path in paths:
        reject_production(path)
        for line in path.read_text().splitlines():
            reject_production(line)
            observations.append(json.loads(line))
    reads = [row for row in observations if row["classification"] == "DRAFT_READ_CANDIDATE"]
    by_phase = {phase: [row for row in reads if row["phase"] == phase]
                for phase in ("a1", "a2", "b")}
    signature = lambda row: (row["method"], row["host"], row["path_template"],
                             tuple(row["query_parameter_names"]),
                             row["request_content_type"],
                             row["graphql_operation_name"],
                             row["response_evidence"]["response_format"],
                             tuple(row["response_evidence"]["top_level_response_keys"]),
                             row["response_evidence"]["draft_identifier_path"],
                             row["response_evidence"]["title_field_path"],
                             row["response_evidence"]["body_content_field_path"],
                             row["response_evidence"]["body_representation"],
                             str(row["response_evidence"]["formatting_representation"]))
    common = set(map(signature, by_phase["a1"])) & set(map(signature, by_phase["a2"])) & set(map(signature, by_phase["b"]))
    keys = ("observed_throwaway_draft_id_match", "observed_throwaway_title_match",
            "observed_throwaway_body_marker_match")
    a_ids = {row["draft_id_fingerprint"] for phase in ("a1", "a2") for row in by_phase[phase]}
    b_ids = {row["draft_id_fingerprint"] for row in by_phase["b"]}
    distinct = bool(a_ids and b_ids and len(a_ids) == 1 and len(b_ids) == 1
                    and a_ids.isdisjoint(b_ids) and None not in a_ids | b_ids)
    complete = bool(distinct and common and all(any(signature(row) in common and
        all(row["response_evidence"][key] for key in keys) and
        all(row["response_evidence"][key] for key in (
            "draft_identifier_path", "title_field_path", "body_content_field_path"))
        for row in by_phase[phase]) for phase in by_phase))
    # Browser observation alone does not establish replay safety.
    decision = "READ_CONTRACT_FOUND_REPLAY_SAFETY_UNKNOWN" if complete else "READ_CONTRACT_NOT_FOUND"
    writes = [row for row in observations if row["semantic_operation"] == "WRITE / AUTOSAVE"]
    return {"decision": decision, "observation_count": len(reads),
            "distinct_throwaway_drafts_tested": 2 if distinct else 0,
            "draft_a_first_read": bool(by_phase["a1"]),
            "draft_a_second_read": bool(by_phase["a2"]),
            "draft_b_read": bool(by_phase["b"]),
            "same_contract_across_reopens_and_drafts": bool(common and distinct),
            "same_semantic_read_request_observed": bool(common and distinct),
            "same_path_or_operation": bool(common and distinct),
            "same_response_schema": bool(common and distinct),
            "same_draft_identifier_field": bool(common and distinct),
            "same_title_field": bool(common and distinct),
            "same_body_representation": bool(common and distinct),
            "draft_a_correctly_identified": bool(by_phase["a1"] and by_phase["a2"] and distinct),
            "draft_b_correctly_identified": bool(by_phase["b"] and distinct),
            "candidate_signatures": [list(item) for item in sorted(common, key=str)],
            "write_candidate_count": len(writes),
            "write_candidates": [{"method": row["method"], "host": row["host"],
                                  "path": row["path_template"], "trigger_phase": row["phase"],
                                  "trigger": "manual create/edit during write phase" if row["phase"] == "write"
                                  else "observed during reopen phase",
                                  "response_status": row["response_status"],
                                  "request_contains_modified_story_data": row[
                                      "request_contains_modified_story_data"]}
                                 for row in writes[:100]],
            "replay_safety": {
                "would_instantiate_editor": "Unknown",
                "could_modify_draft": "Unknown",
                "request_contains_mutation_data": "Unknown",
                "response_alone_contains_stored_draft_body": "Unknown",
                "read_causes_autosave": "Unknown",
                "requires_write_capable_graphql_operation": "No" if complete else "Unknown"},
            "endpoint_provenance": "Observed from Medium's own editor while reopening an existing saved throwaway draft" if complete else "Not established",
            "production_verifier_implemented": False,
            "replay_performed": False}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    recorder = sub.add_parser("record", help="Observe one manually driven phase")
    recorder.add_argument("--profile", required=True)
    recorder.add_argument("--phase", choices=("write", "a1", "a2", "b"), required=True)
    recorder.add_argument("--draft-id", default="")
    recorder.add_argument("--title-marker", default="")
    recorder.add_argument("--body-marker", default="")
    summary = sub.add_parser("report", help="Summarize saved observation files offline")
    summary.add_argument("files", nargs="+", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "record":
            print(record(args))
        else:
            print(json.dumps(report(args.files), indent=2))
    except (ValueError, RuntimeError, ForbiddenProductionDraft) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

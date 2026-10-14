"""Offline analysis of manually exported Chrome HAR files. No browser or network APIs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

PRODUCTION_DRAFT_FORBIDDEN = "fd6c1824c4de"
MAX_HAR_BYTES = 50_000_000
MAX_BODY_BYTES = 2_000_000
PHASES = ("a1", "a2", "b")
IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
GRAPHQL = re.compile(r"^\s*(?:#.*\n\s*)*(query|mutation|subscription)\b(?:\s+([A-Za-z_][A-Za-z0-9_]{0,63}))?", re.I)
SECRET_WORD = re.compile(r"token|secret|password|auth|session|csrf|cookie|credential|nonce|code", re.I)
STATIC_TYPES = {"script", "stylesheet", "font", "image", "media", "manifest"}
SAFE_ROUTE_SEGMENTS = {"_", "graphql", "p", "edit", "draft", "drafts", "story", "stories",
                       "write", "api", "v1", "v2", "me", "analytics", "collect",
                       "tracking", "metrics", "beacon", "account", "signin", "login",
                       "session", "viewer"}
HTML_TAGS = {"title", "script", "p", "h1", "h2", "h3", "div", "article", "section"}


class ForbiddenProductionDraft(ValueError):
    pass


def reject_production(value: object) -> None:
    candidate = str(value)
    for _ in range(3):
        if PRODUCTION_DRAFT_FORBIDDEN in candidate.lower():
            raise ForbiddenProductionDraft("PRODUCTION_DRAFT_FORBIDDEN")
        candidate = unquote(candidate)


def digest(value: str | bytes) -> str:
    data = value.encode() if isinstance(value, str) else value
    return "sha256:" + hashlib.sha256(data).hexdigest()[:16]


def safe_path(url: str, draft_id: str) -> tuple[str, str]:
    """Discard URL credentials, query values, fragments, and opaque path segments."""
    parts = urlsplit(url)
    original_host = (parts.hostname or "").lower()
    if original_host != "medium.com" and not original_host.endswith(".medium.com"):
        return original_host, ""
    host = original_host if original_host in {"medium.com", "www.medium.com"} else "{medium-subdomain}.medium.com"
    segments = []
    for raw in parts.path.split("/"):
        segment = unquote(raw)
        if draft_id and segment.lower() == draft_id.lower():
            segments.append("{draft_id}")
        elif segment.lower() in SAFE_ROUTE_SEGMENTS:
            segments.append(segment.lower())
        elif not segment:
            segments.append("")
        else:
            segments.append("{opaque}")
    return host, "/".join(segments)[:256]


def content_type(headers: object, fallback: object = "") -> str:
    value = fallback if isinstance(fallback, str) else ""
    if isinstance(headers, list):
        for item in headers:
            if isinstance(item, dict) and str(item.get("name", "")).lower() == "content-type":
                value = item.get("value", "")
                break
    mime = str(value).split(";", 1)[0].strip().lower()
    return mime[:80] if re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", mime) else ""


def body_text(container: object, key: str) -> str | None:
    if not isinstance(container, dict):
        return None
    body = container.get(key)
    if not isinstance(body, dict) or not isinstance(body.get("text"), str) or body.get("encoding") == "base64":
        return None
    value = body["text"]
    return value if len(value.encode()) <= MAX_BODY_BYTES else None


def graphql_operation(body: str | None) -> tuple[str | None, str | None]:
    try:
        payload = json.loads(body) if body else None
    except ValueError:
        payload = None
    if not isinstance(payload, dict) or not isinstance(payload.get("query"), str):
        return None, None
    match = GRAPHQL.match(payload["query"])
    if not match:
        return None, None
    name = payload.get("operationName") or match.group(2)
    if (not isinstance(name, str) or not IDENT.fullmatch(name) or SECRET_WORD.search(name)
            or match.group(2) and name != match.group(2)):
        name = None
    return match.group(1).upper(), name


def html_marker_paths(body: str, draft_id: str, title: str,
                      markers: tuple[str, ...]) -> dict[str, str]:
    """Record actual element types containing markers, never HTML or attributes."""
    class MarkerParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.paths = {}

        def handle_starttag(self, tag, attrs):
            if tag not in {"area", "base", "br", "col", "embed", "hr", "img", "input",
                           "link", "meta", "param", "source", "track", "wbr"}:
                self.stack.append(tag)

        def handle_endtag(self, tag):
            if tag in self.stack:
                self.stack = self.stack[:len(self.stack) - 1 - self.stack[::-1].index(tag)]

        def handle_data(self, data):
            tag = next((tag for tag in reversed(self.stack) if tag in HTML_TAGS), None)
            if not tag:
                return
            for marker in (draft_id, title, *markers):
                if marker in data:
                    current = self.paths.get(marker)
                    # Visible title/paragraph elements take precedence over bootstrap scripts.
                    priority = {"title": 0, "h1": 0, "h2": 0, "h3": 0, "p": 0,
                                "article": 1, "section": 1, "div": 1, "script": 2}
                    if current is None or priority.get(tag, 3) < priority.get(current, 3):
                        self.paths[marker] = tag

    parser = MarkerParser()
    parser.feed(body)
    return {marker: f"HTML:<{tag}>" for marker, tag in parser.paths.items()}


def response_evidence(body: str | None, mime: str, draft_id: str,
                      title: str, markers: tuple[str, ...], graphql_type: str | None) -> dict:
    result = {"response_format": "GraphQL" if graphql_type and "json" in mime else
              "JSON" if "json" in mime else "HTML" if "html" in mime else "other",
              "body_present_in_har": body is not None,
              "draft_identifier_field_path": None, "title_field_path": None,
              "body_field_paths": {}, "body_representation": "Unknown",
              "draft_id_present": False, "title_present": False,
              "body_markers_present": {digest(marker): False for marker in markers}}
    if body is None:
        return result
    try:
        parsed = json.loads(body) if "json" in mime else None
    except ValueError:
        parsed = None
    if parsed is None:
        result["draft_id_present"] = draft_id in body
        result["title_present"] = title in body
        result["body_markers_present"] = {digest(marker): marker in body for marker in markers}
        if "html" in mime:
            paths = html_marker_paths(body, draft_id, title, markers)
            result["draft_identifier_field_path"] = paths.get(draft_id)
            result["title_field_path"] = paths.get(title)
            result["body_field_paths"] = {digest(marker): paths[marker]
                                          for marker in markers if marker in paths}
            if result["body_field_paths"]:
                result["body_representation"] = "HTML"
        return result
    stack = [(parsed, "$", ())]
    visited = 0
    while stack and visited < 30_000:
        node, path, ancestors = stack.pop()
        visited += 1
        if isinstance(node, dict):
            for key, value in list(node.items())[:200]:
                if isinstance(key, str) and IDENT.fullmatch(key) and not SECRET_WORD.search(key):
                    stack.append((value, path + "." + key, ancestors + (key,)))
        elif isinstance(node, list):
            for index, value in enumerate(node[:1000]):
                stack.append((value, f"{path}[{index}]", ancestors))
        elif isinstance(node, (str, int)) and not isinstance(node, bool):
            value = str(node)
            if draft_id in value:
                result["draft_id_present"] = True
                result["draft_identifier_field_path"] = result["draft_identifier_field_path"] or path
            if title in value:
                result["title_present"] = True
                result["title_field_path"] = result["title_field_path"] or path
            for marker in markers:
                if marker in value:
                    result["body_markers_present"][digest(marker)] = True
                    result["body_field_paths"][digest(marker)] = path
                    if result["body_representation"] == "Unknown":
                        result["body_representation"] = (
                            "HTML" if re.search(r"<\w+[^>]*>", value) else
                            "Markdown" if re.search(r"(?m)^#{1,6} |\*\*[^*]+\*\*", value) else
                            "block JSON" if "[" in path or "blocks" in ancestors else "string")
    return result


def classify(method: str, path: str, resource: str, operation: str | None,
             name: str | None, request_body: str | None, evidence: dict) -> tuple[str, str]:
    if resource in STATIC_TYPES:
        return "STATIC_ASSET", "UNKNOWN"
    has_marker = (evidence["draft_id_present"] or evidence["title_present"]
                  or any(evidence["body_markers_present"].values()))
    if (resource in {"fetch", "xhr", "ping", "beacon"} and not has_marker
            and re.search(r"(?:^|/)(?:analytics|collect|tracking|metrics|beacon)(?:/|$)", path)):
        return "ANALYTICS", "UNKNOWN"
    if operation == "MUTATION":
        return ("AUTOSAVE_WRITE" if name and re.search(r"autosave|save|update", name, re.I)
                else "MUTATION"), "WRITE"
    if method in {"POST", "PUT", "PATCH"} and request_body and operation != "QUERY":
        try:
            payload = json.loads(request_body)
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            values = payload.get("variables", payload)
            if isinstance(values, dict) and any(
                    key.lower() in {"body", "content", "title", "text", "paragraphs", "blocks"}
                    for key in values):
                return "AUTOSAVE_WRITE", "WRITE"
    has_content = evidence["title_present"] and all(evidence["body_markers_present"].values())
    if evidence["draft_id_present"] and has_content and (
            operation == "QUERY" or method == "GET" and not request_body):
        return "DRAFT_READ_CANDIDATE", "READ"
    if (not has_marker and evidence["response_format"] in {"JSON", "GraphQL"}
            and re.search(r"(?:^|/)(?:account|signin|login|session|me|viewer)(?:/|$)", path)):
        return "ACCOUNT_STATE", "UNKNOWN"
    return "UNKNOWN", "UNKNOWN"


def analyze_entry(entry: dict, draft_id: str, title: str, markers: tuple[str, ...]) -> dict | None:
    request, response = entry.get("request"), entry.get("response")
    if not isinstance(request, dict) or not isinstance(response, dict) or not isinstance(request.get("url"), str):
        return None
    host, path = safe_path(request["url"], draft_id)
    if not path:
        return None
    query_names = sorted({key for key, _ in parse_qsl(urlsplit(request["url"]).query,
                                                 keep_blank_values=True, max_num_fields=100)
                          if IDENT.fullmatch(key) and not SECRET_WORD.search(key)})[:32]
    method = request.get("method", "")
    method = method.upper() if isinstance(method, str) else ""
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
        method = "UNKNOWN"
    request_body, response_body = body_text(request, "postData"), body_text(response, "content")
    request_has_draft = draft_id in request["url"] or bool(request_body and draft_id in request_body)
    request_has_title = bool(request_body and title in request_body)
    request_has_body = bool(request_body and any(marker in request_body for marker in markers))
    operation, name = graphql_operation(request_body)
    content = response.get("content")
    mime = content_type(response.get("headers"), content.get("mimeType", "") if isinstance(content, dict) else "")
    resource = entry.get("_resourceType", "")
    resource = resource.lower() if isinstance(resource, str) and IDENT.fullmatch(resource) else "unknown"
    evidence = response_evidence(response_body, mime, draft_id, title, markers, operation)
    classification, semantic = classify(method, path, resource, operation, name, request_body, evidence)
    status = response.get("status")
    return {"method": method, "host": host, "path": path, "query_parameter_names": query_names,
            "status": status if isinstance(status, int) and not isinstance(status, bool) and 0 <= status <= 599 else None,
            "content_type": mime, "resource_type": resource,
            "request_body_length": len(request_body.encode()) if request_body is not None else None,
            "request_body_fingerprint": digest(request_body) if request_body is not None else None,
            "response_body_length": len(response_body.encode()) if response_body is not None else None,
            "response_body_fingerprint": digest(response_body) if response_body is not None else None,
            "graphql_operation_type": operation, "graphql_operation_name": name,
            "request_has_draft_id": request_has_draft,
            "request_has_title_marker": request_has_title,
            "request_has_body_marker": request_has_body,
            "classification": classification, "semantic_operation": semantic,
            "response_evidence": evidence}


def analyze_har(path: Path, *, phase: str, draft_id: str, title: str,
                markers: tuple[str, ...]) -> dict:
    if phase not in PHASES or len(draft_id) < 8 or not title or len(markers) < 2:
        raise ValueError("A reopen phase, throwaway ID, title, and at least two body markers are required")
    if len(set(markers)) != len(markers) or any(len(marker) < 8 for marker in markers):
        raise ValueError("Body markers must be distinct and at least eight characters")
    for value in (path, draft_id, title, markers):
        reject_production(value)
    if path.stat().st_size > MAX_HAR_BYTES:
        raise ValueError("Capture exceeds 50 MB limit")
    raw = path.read_bytes()
    reject_production(raw.decode("utf-8", "replace"))
    try:
        har = json.loads(raw)
    except ValueError as exc:
        raise ValueError("Malformed HAR JSON") from exc
    reject_production(har)  # Also catches JSON-escaped occurrences.
    if not isinstance(har, dict) or not isinstance(har.get("log"), dict) or not isinstance(har["log"].get("entries"), list):
        raise ValueError("Malformed HAR: log.entries must be a list")
    entries = har["log"]["entries"]
    if len(entries) > 10000:
        raise ValueError("Capture has too many entries")
    rows = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Malformed HAR entry")
        row = analyze_entry(entry, draft_id, title, markers)
        if row and (row["request_has_draft_id"] or row["request_has_title_marker"]
                    or row["request_has_body_marker"] or row["response_evidence"]["draft_id_present"]
                    or row["response_evidence"]["title_present"]
                    or any(row["response_evidence"]["body_markers_present"].values())):
            rows.append(row)
    return {"format": "medium-manual-har-evidence-v1", "phase": phase,
            "capture_fingerprint": digest(raw),
            "draft_id_fingerprint": digest(draft_id), "title_marker_fingerprint": digest(title),
            "body_marker_fingerprints": [digest(marker) for marker in markers],
            "source_entry_count": len(entries), "retained_entry_count": len(rows),
            "entries": rows, "raw_headers_retained": False, "raw_bodies_retained": False,
            "replay_performed": False}


def save_sanitized(data: dict, path: Path) -> None:
    if path.is_symlink():
        raise ValueError("Symlinked output is forbidden")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        json.dump(data, stream, indent=2, sort_keys=True)
        stream.write("\n")


def signature(row: dict) -> tuple:
    evidence = row["response_evidence"]
    return (row["method"], row["host"], row["path"], tuple(row["query_parameter_names"]),
            row["graphql_operation_type"],
            row["graphql_operation_name"], evidence["response_format"],
            evidence["draft_identifier_field_path"], evidence["title_field_path"],
            tuple(sorted(set(evidence["body_field_paths"].values()))), evidence["body_representation"])


def report(paths: dict[str, Path], replay_evidence: Path | None = None) -> dict:
    if set(paths) != set(PHASES):
        raise ValueError("Exactly a1, a2, and b sanitized observations are required")
    captures = {}
    for phase, path in paths.items():
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("format") != "medium-manual-har-evidence-v1" or data.get("phase") != phase:
            raise ValueError("Expected sanitized observation for phase " + phase)
        captures[phase] = data
    a1, a2, b = (captures[phase] for phase in PHASES)
    distinct = (len({data["capture_fingerprint"] for data in captures.values()}) == 3
                and a1["draft_id_fingerprint"] == a2["draft_id_fingerprint"]
                and a1["title_marker_fingerprint"] == a2["title_marker_fingerprint"]
                and a1["body_marker_fingerprints"] == a2["body_marker_fingerprints"]
                and a1["draft_id_fingerprint"] != b["draft_id_fingerprint"]
                and a1["title_marker_fingerprint"] != b["title_marker_fingerprint"]
                and set(a1["body_marker_fingerprints"]).isdisjoint(b["body_marker_fingerprints"]))
    candidates = {}
    for phase, data in captures.items():
        candidates[phase] = [row for row in data["entries"] if row["classification"] == "DRAFT_READ_CANDIDATE"
                             and row["status"] == 200 and row["response_evidence"]["draft_identifier_field_path"]
                             and row["response_evidence"]["title_field_path"]
                             and len(row["response_evidence"]["body_field_paths"]) == len(data["body_marker_fingerprints"])]
    common = {signature(row) for row in candidates["a1"]}
    for phase in ("a2", "b"):
        common &= {signature(row) for row in candidates[phase]}
    found = bool(distinct and common)
    replay_safe = False
    if replay_evidence is not None:
        replay = json.loads(replay_evidence.read_text(encoding="utf-8"))
        if (replay.get("format") != "medium-manual-replay-safety-v1"
                or replay.get("source") != "user_reported_2026-10-13"
                or replay.get("contract") != {"method": "GET", "host": "medium.com",
                                               "path": "/p/{draft_id}/edit", "response_format": "HTML"}):
            raise ValueError("Invalid replay safety evidence")
        drafts = replay.get("drafts")
        if not isinstance(drafts, dict) or set(drafts) != {"A", "B"}:
            raise ValueError("Invalid replay safety evidence")
        required = {"get_count": 1, "write_method_count": 0, "http_status": 200,
                    "complete_html": True, "correct_id_title_body_markers": True,
                    "manual_followup_unchanged": True}
        for letter, capture in (("A", a1), ("B", b)):
            draft = drafts[letter]
            if (not isinstance(draft, dict)
                    or draft.get("draft_id_fingerprint") != capture["draft_id_fingerprint"]
                    or any(type(draft.get(key)) is not type(value) or draft.get(key) != value
                           for key, value in required.items())):
                raise ValueError("Invalid replay safety evidence")
        replay_safe = found
    decision = ("READ_CONTRACT_FOUND_CANDIDATE_SAFE" if replay_safe else
                "READ_CONTRACT_FOUND_REPLAY_SAFETY_UNKNOWN" if found else
                "READ_CONTRACT_NOT_FOUND")
    return {"decision": decision,
            "distinct_throwaway_drafts": distinct, "same_contract_in_three_reopens": found,
            "read_candidates_per_reopen": {phase: len(candidates[phase]) for phase in PHASES},
            "write_or_mutation_count_per_reopen": {phase: sum(row["classification"] in {"AUTOSAVE_WRITE", "MUTATION"}
                                                        for row in captures[phase]["entries"]) for phase in PHASES},
            "matching_contracts": [list(item) for item in sorted(common, key=str)] if distinct else [],
            "replay_safety": "User-reported candidate safe" if replay_safe else "Unknown",
            "replay_performed": replay_safe,
            "production_persistence_verifier_implemented": (
                Path(__file__).with_name("medium-persistence-verifier").joinpath("manifest.json").is_file())}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    ingest = sub.add_parser("analyze", help="Sanitize and analyze one manual Chrome HAR locally")
    ingest.add_argument("capture", type=Path)
    ingest.add_argument("--phase", choices=PHASES, required=True)
    ingest.add_argument("--draft-id", required=True)
    ingest.add_argument("--title-marker", required=True)
    ingest.add_argument("--body-marker", action="append", required=True)
    ingest.add_argument("--output", type=Path, required=True)
    summary = sub.add_parser("report", help="Compare three sanitized manual reopen observations")
    for phase in PHASES:
        summary.add_argument("--" + phase, type=Path, required=True)
    summary.add_argument("--replay-evidence", type=Path,
                         help="Optional local user-reported throwaway replay observations")
    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            data = analyze_har(args.capture, phase=args.phase, draft_id=args.draft_id,
                               title=args.title_marker, markers=tuple(args.body_marker))
            save_sanitized(data, args.output)
            print(json.dumps({"sanitized_file": str(args.output), "retained_entry_count": data["retained_entry_count"],
                              "entries": data["entries"]}, indent=2))
        else:
            print(json.dumps(report({phase: getattr(args, phase) for phase in PHASES},
                                    args.replay_evidence), indent=2))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, ForbiddenProductionDraft):
            message = "PRODUCTION_DRAFT_FORBIDDEN"
        elif isinstance(exc, OSError):
            message = "Cannot read capture or write sanitized output"
        else:
            message = (str(exc) if str(exc).startswith((
                "Malformed HAR", "Capture exceeds", "Capture has too many",
                "A reopen", "Body markers", "Exactly a1", "Expected sanitized",
                "Symlinked output", "Invalid replay safety"))
                else "Invalid capture or observation")
        print(message, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

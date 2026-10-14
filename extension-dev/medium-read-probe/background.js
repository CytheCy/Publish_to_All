"use strict";

// This source file is the explicit allowlist; no message can supply an ID or URL.
const ALLOWED_THROWAWAY_DRAFTS = Object.freeze({
  A: "7371e61f831a",
  B: "040be01331a3"
});
const PRODUCTION_DRAFT_ID = "fd6c1824c4de";
const EXPECTED_EVIDENCE = Object.freeze({
  A: Object.freeze({
    title: "DISCOVERY TEST A 74291",
    markers: Object.freeze([
      "MEDIUM_DISCOVERY_A_START_74291",
      "MEDIUM_DISCOVERY_A_MIDDLE_74291",
      "MEDIUM_DISCOVERY_A_END_74291"
    ])
  }),
  B: Object.freeze({
    title: "DISCOVERY TEST B 39518",
    markers: Object.freeze([
      "MEDIUM_DISCOVERY_B_START_39518",
      "MEDIUM_DISCOVERY_B_MIDDLE_39518",
      "MEDIUM_DISCOVERY_B_END_39518"
    ])
  })
});
const MAX_RESPONSE_BYTES = 1024 * 1024;
const MAX_REDIRECT_FIELD_LENGTH = 200;

function freshCounts() {
  return { GET: 0, POST: 0, PUT: 0, PATCH: 0, DELETE: 0 };
}

function reportBase() {
  return {
    outcome: "NOT_ATTEMPTED",
    browserManagedAuthenticatedRequestAttempted: false,
    outboundRequestsInitiatedByProbe: 0,
    methods: freshCounts()
  };
}

function approvedUrl(draft) {
  if (draft !== "A" && draft !== "B") throw new Error("REQUEST_NOT_ALLOWLISTED");
  const ids = Object.values(ALLOWED_THROWAWAY_DRAFTS);
  if (ids.some((id) => typeof id === "string" && id.includes(PRODUCTION_DRAFT_ID))) {
    throw new Error("PRODUCTION_DRAFT_FORBIDDEN");
  }
  if (ids.length !== 2 || new Set(ids).size !== 2 ||
      !ids.every((id) => /^[a-f0-9]{12}$/.test(id))) {
    throw new Error("THROWAWAY_ALLOWLIST_NOT_CONFIGURED");
  }
  const draftId = ALLOWED_THROWAWAY_DRAFTS[draft];
  if (draftId === PRODUCTION_DRAFT_ID) throw new Error("PRODUCTION_DRAFT_FORBIDDEN");
  const url = new URL(`https://medium.com/p/${draftId}/edit`);
  if (url.protocol !== "https:" || url.hostname !== "medium.com" ||
      url.port || url.pathname !== `/p/${draftId}/edit` ||
      url.search !== "" || url.hash !== "") {
    throw new Error("REQUEST_NOT_ALLOWLISTED");
  }
  return url;
}

function safeRedirect(response, requestUrl) {
  const result = { outcome: "AUTHENTICATED_READ_NOT_ESTABLISHED",
                   detail: "REDIRECT_OBSERVED", status: response.status,
                   destinationHost: "unavailable", destinationPath: "unavailable" };
  const location = response.headers.get("Location");
  if (location) {
    try {
      const destination = new URL(location, requestUrl);
      result.destinationHost = destination.hostname.slice(0, MAX_REDIRECT_FIELD_LENGTH);
      result.destinationPath = destination.pathname.slice(0, MAX_REDIRECT_FIELD_LENGTH);
    } catch { /* Keep the bounded unavailable fields. */ }
  }
  return result;
}

function utf8Length(value) {
  return new TextEncoder().encode(value).length;
}

async function fingerprint(value) {
  const bytes = new TextEncoder().encode(value);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return "sha256:" + Array.from(new Uint8Array(digest), (byte) =>
    byte.toString(16).padStart(2, "0")).join("");
}

function paragraphText(html) {
  const paragraphs = html.match(/<p(?:\s[^>]*)?>[\s\S]*?<\/p\s*>/gi) || [];
  return paragraphs.map((part) => part.replace(/<[^>]*>/g, " ")).join(" ");
}

async function readEvidence(html, draftId, expected) {
  const completeHtml = /<html(?:\s[^>]*)?>[\s\S]*<\/html\s*>/i.test(html) &&
    /<head(?:\s[^>]*)?>[\s\S]*<\/head\s*>/i.test(html) &&
    /<body(?:\s[^>]*)?>[\s\S]*<\/body\s*>/i.test(html);
  const titleMatch = /<title(?:\s[^>]*)?>([\s\S]*?)<\/title\s*>/i.exec(html);
  const titlePresent = Boolean(titleMatch && titleMatch[1].includes(expected.title));
  const body = paragraphText(html);
  const markers = expected.markers.map((marker) => body.includes(marker));
  return {
    completeHtml,
    responseByteLength: utf8Length(html),
    responseFingerprint: await fingerprint(html),
    draftIdPresent: html.includes(draftId),
    title: titlePresent ? expected.title : "unavailable",
    titleFingerprint: titlePresent ? await fingerprint(expected.title) : "unavailable",
    bodyMarkerCount: markers.filter(Boolean).length,
    startMarkerPresent: markers[0],
    middleMarkerPresent: markers[1],
    endMarkerPresent: markers[2]
  };
}

async function runDraft(draft) {
  const report = reportBase();
  let url;
  try { url = approvedUrl(draft); }
  catch (error) { report.outcome = error.message; return report; }

  // This is the only network call. The literal options have no body or headers.
  report.outboundRequestsInitiatedByProbe = 1;
  report.methods.GET = 1;
  report.browserManagedAuthenticatedRequestAttempted = true;
  let response;
  try {
    response = await fetch(url.href, {
      method: "GET", credentials: "include", redirect: "manual", cache: "no-store"
    });
  } catch {
    report.outcome = "AUTHENTICATED_READ_NOT_ESTABLISHED";
    report.detail = "NETWORK_ERROR";
    return report;
  }

  if (response.type === "opaqueredirect" || (response.status >= 300 && response.status < 400)) {
    return Object.assign(report, safeRedirect(response, url));
  }
  report.httpStatus = response.status;
  const contentType = response.headers.get("Content-Type") || "unavailable";
  report.contentType = /^text\/html(?:\s*;|\s*$)/i.test(contentType) ? "text/html" : "other";
  if (response.status !== 200 || report.contentType !== "text/html") {
    report.outcome = "AUTHENTICATED_READ_NOT_ESTABLISHED";
    return report;
  }

  let html;
  try { html = await response.text(); }
  catch { report.outcome = "AUTHENTICATED_READ_NOT_ESTABLISHED";
          report.detail = "BODY_READ_FAILED"; return report; }
  if (utf8Length(html) > MAX_RESPONSE_BYTES) {
    report.outcome = "AUTHENTICATED_READ_NOT_ESTABLISHED";
    report.detail = "RESPONSE_TOO_LARGE";
    return report;
  }
  try { Object.assign(report, await readEvidence(html, ALLOWED_THROWAWAY_DRAFTS[draft],
    EXPECTED_EVIDENCE[draft])); }
  catch { report.outcome = "AUTHENTICATED_READ_NOT_ESTABLISHED";
          report.detail = "EVIDENCE_PARSE_FAILED"; return report; }
  report.outcome = report.completeHtml && report.draftIdPresent && report.title !== "unavailable" &&
    report.bodyMarkerCount === 3 ? "THROWAWAY_DRAFT_READ_EVIDENCE_FOUND" :
    "AUTHENTICATED_READ_NOT_ESTABLISHED";
  return report;
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || Object.keys(message).length !== 1 ||
      (message.type !== "RUN_DRAFT_A" && message.type !== "RUN_DRAFT_B")) {
    sendResponse({ outcome: "UNSUPPORTED_REQUEST", outboundRequestsInitiatedByProbe: 0,
                   methods: freshCounts() });
    return false;
  }
  runDraft(message.type === "RUN_DRAFT_A" ? "A" : "B")
    .then(sendResponse, () => sendResponse({ outcome: "PROBE_FAILED",
    outboundRequestsInitiatedByProbe: 0, methods: freshCounts() }));
  return true;
});

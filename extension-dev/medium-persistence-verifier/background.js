"use strict";

importScripts("canonical-title.js", "canonical-body.js");

const DRAFT_ID = "fd6c1824c4de";
const FIXED_URL = `https://medium.com/p/${DRAFT_ID}/edit`;
const MAX_RESPONSE_BYTES = 1024 * 1024;
const TITLE = globalThis.PublishToAllStory;
const BODY = globalThis.PublishToAllBody;
let attempted = false;

function fingerprint(value) {
  let hash = 2166136261;
  for (const character of String(value)) {
    hash ^= character.codePointAt(0);
    hash = Math.imul(hash, 16777619);
  }
  return `fnv1a32:${(hash >>> 0).toString(16).padStart(8, "0")}`;
}

function clean(value) {
  return String(value || "").replace(/[\u200b-\u200d\u2060\ufeff]/g, "")
    .replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
}

function expectedBlock(index) {
  const block = BODY.blocks[index];
  return block ? { number: index + 1, kind: block.kind,
    normalizedLength: clean(block.text).length,
    fingerprint: fingerprint(clean(block.text)) } : null;
}

function canonicalValid() {
  if (TITLE.title !== "Saturation of Artificial Intelligence" ||
      TITLE.fingerprint !== "fnv1a32:5e81665d" ||
      fingerprint(TITLE.title) !== TITLE.fingerprint ||
      BODY.blocks.length !== 36 || BODY.headingCount !== 18 ||
      BODY.paragraphCount !== 18 || BODY.normalizedCharacterCount !== 58757 ||
      BODY.fingerprint !== "fnv1a32:342d11eb") return false;
  if (!BODY.blocks.every((block, index) => block.kind ===
      (index % 2 === 0 ? "heading" : "paragraph") &&
      block.level === (index % 2 === 0 ? 1 : null))) return false;
  const joined = BODY.blocks.map((block) => clean(block.text)).join("\n\n");
  return joined.length === 58757 && fingerprint(joined) === BODY.fingerprint;
}

const ENTITIES = Object.freeze({ amp: "&", lt: "<", gt: ">", quot: '"',
  apos: "'", nbsp: " ", rsquo: "’", lsquo: "‘", rdquo: "”", ldquo: "“",
  mdash: "—", ndash: "–", hellip: "…", bull: "•", copy: "©", reg: "®" });

function decode(value) {
  return value.replace(/&(#(?:x[0-9a-f]+|[0-9]+)|[a-z][a-z0-9]+);/gi,
    (entity, key) => {
      if (key[0] !== "#") return ENTITIES[key.toLowerCase()] ?? entity;
      const codepoint = key[1].toLowerCase() === "x" ?
        parseInt(key.slice(2), 16) : parseInt(key.slice(1), 10);
      return codepoint > 0 && codepoint <= 0x10ffff &&
        !(codepoint >= 0xd800 && codepoint <= 0xdfff) ?
        String.fromCodePoint(codepoint) : entity;
    });
}

// Parse only inert response text. No DOM, iframe, editor, or page execution occurs.
function parseHtml(html) {
  const complete = /<html\b[^>]*>[\s\S]*<\/html\s*>/i.test(html) &&
    /<head\b[^>]*>[\s\S]*<\/head\s*>/i.test(html) &&
    /<body\b[^>]*>[\s\S]*<\/body\s*>/i.test(html);
  if (!complete) return null;
  const titleMatch = /<title\b[^>]*>([\s\S]*?)<\/title\s*>/i.exec(html);
  const bodyMatch = /<body\b[^>]*>([\s\S]*?)<\/body\s*>/i.exec(html);
  if (!titleMatch || !bodyMatch) return null;
  const inertBody = bodyMatch[1].replace(/<!--[^]*?-->/g, "")
    .replace(/<(script|style|template|noscript)\b[^>]*>[^]*?<\/\1\s*>/gi, "");
  const articles = Array.from(inertBody.matchAll(/<article\b[^>]*>([\s\S]*?)<\/article\s*>/gi));
  if (articles.length > 1) return null;
  const inert = articles.length ? articles[0][1] : inertBody;
  const blocks = [];
  let active = null;
  let malformed = false;
  let outsideText = false;
  let unsupportedContent = false;
  for (const token of inert.match(/<[^>]*>|[^<]+/g) || []) {
    if (token.startsWith("<")) {
      const match = /^<\s*(\/?)\s*([a-z][a-z0-9]*)\b[^>]*>$/i.exec(token);
      if (!match) { malformed = true; break; }
      const closing = Boolean(match[1]);
      const tag = match[2].toLowerCase();
      if (!closing && /^(img|video|iframe|figure|canvas|table)$/.test(tag))
        unsupportedContent = true;
      if (tag === "br" && active) active.text += " ";
      if (!/^h[1-6]$/.test(tag) && tag !== "p") continue;
      if (!closing) {
        if (active) { malformed = true; break; }
        active = { tag, text: "" };
      } else {
        if (!active || active.tag !== tag) {
          malformed = true; break;
        }
        blocks.push({ tag: active.tag,
          kind: active.tag === "p" ? "paragraph" : "heading",
          text: clean(decode(active.text)) });
        active = null;
      }
    } else if (active) active.text += token;
    else if (clean(decode(token))) outsideText = true;
  }
  if (active || malformed) return null;
  return { title: clean(decode(titleMatch[1].replace(/<[^>]*>/g, ""))),
    blocks, outsideText, unsupportedContent };
}

function compare(html) {
  const parsed = parseHtml(html);
  if (!parsed) return { outcome: "MEDIUM_PERSISTENCE_UNKNOWN", detail: "MALFORMED_HTML" };
  const titleExact = parsed.title === TITLE.title;
  const titleFingerprint = fingerprint(parsed.title);
  const titleBlock = parsed.blocks[0]?.tag === "h1" &&
    parsed.blocks[0].text === TITLE.title;
  const observed = parsed.blocks.slice(titleBlock ? 1 : 0);
  let through = 0;
  while (through < observed.length && through < BODY.blocks.length &&
      observed[through].tag === (BODY.blocks[through].kind === "heading" ? "h1" : "p") &&
      observed[through].text === clean(BODY.blocks[through].text)) through++;
  const normalized = observed.map((block) => block.text).join("\n\n");
  const fullMatch = titleExact && !parsed.outsideText && !parsed.unsupportedContent &&
    through === 36 && observed.length === 36 &&
    normalized.length === 58757 && fingerprint(normalized) === BODY.fingerprint;
  let detail = "EXACT_CANONICAL_MATCH";
  if (!titleExact) detail = "WRONG_TITLE";
  else if (!observed.length) detail = parsed.blocks.length ? "NO_CANONICAL_START" : "EMPTY_BODY";
  else if (through < observed.length && through < 36)
    detail = observed[through].text === clean(BODY.blocks[through].text) ?
      "WRONG_FORMATTING" : "ALTERED_BODY";
  else if (through < 36) detail = "CANONICAL_PREFIX_ONLY";
  else if (observed.length !== 36) detail = "EXTRA_BODY_BLOCKS";
  else if (parsed.outsideText || parsed.unsupportedContent) detail = "EXTRA_BODY_CONTENT";
  return {
    outcome: fullMatch ? "MEDIUM_PERSISTENCE_VERIFIED" :
      titleExact && (detail === "EMPTY_BODY" ||
        detail === "CANONICAL_PREFIX_ONLY") ? "MEDIUM_PERSISTENCE_PARTIAL" :
      "MEDIUM_PERSISTENCE_MISMATCH",
    detail, titleExact, titleFingerprint,
    bodyBlockCount: observed.length,
    headingCount: observed.filter((block) => block.kind === "heading").length,
    paragraphCount: observed.filter((block) => block.kind === "paragraph").length,
    normalizedBodyLength: normalized.length,
    bodyFingerprint: fingerprint(normalized),
    lastMatchingCanonicalBlock: through ? expectedBlock(through - 1) : null,
    nextExpectedCanonicalBlock: expectedBlock(through),
    firstDifferentObservedBlock: through < observed.length ? {
      number: through + 1, kind: observed[through].kind,
      tag: observed[through].tag,
      normalizedLength: observed[through].text.length,
      fingerprint: fingerprint(observed[through].text)
    } : null
  };
}

function reportBase() {
  return { outcome: "MEDIUM_PERSISTENCE_UNKNOWN",
    domVerificationState: "BODY_DOM_VERIFIED_PREVIOUS_OBSERVATION",
    serverPersistenceState: "MEDIUM_PERSISTENCE_UNKNOWN",
    method: "GET", host: "medium.com", path: `/p/${DRAFT_ID}/edit`,
    allowlisted: true, outboundRequestsInitiatedByVerifier: 0,
    methods: { GET: 0, POST: 0, PUT: 0, PATCH: 0, DELETE: 0 } };
}

async function verify() {
  const report = reportBase();
  if (attempted) { report.detail = "ALREADY_ATTEMPTED"; return report; }
  attempted = true;
  let approved = false;
  try { approved = canonicalValid() && new URL(FIXED_URL).href === FIXED_URL &&
    new URL(FIXED_URL).hostname === "medium.com"; }
  catch { /* Fail before transmission. */ }
  if (!approved) {
    report.detail = "CANONICAL_OR_URL_GUARD_FAILED";
    return report;
  }
  report.outboundRequestsInitiatedByVerifier = 1;
  report.methods.GET = 1;
  let response;
  try {
    response = await fetch(FIXED_URL, {
      method: "GET", credentials: "include", redirect: "manual", cache: "no-store"
    });
  } catch { report.detail = "NETWORK_ERROR"; return report; }
  let status;
  let responseType;
  let contentType;
  try {
    status = response.status;
    responseType = response.type;
    contentType = response.headers.get("Content-Type") || "";
  } catch { report.detail = "RESPONSE_METADATA_ERROR"; return report; }
  if (responseType === "opaqueredirect" ||
      (status >= 300 && status < 400)) {
    report.detail = "REDIRECT_OBSERVED";
    report.httpStatus = status;
    return report;
  }
  report.httpStatus = status;
  report.contentType = /^text\/html(?:\s*;|\s*$)/i.test(contentType) ?
    "text/html" : "other";
  if (status !== 200 || report.contentType !== "text/html") {
    report.detail = status === 403 ? "HTTP_403" :
      status === 404 ? "HTTP_404" :
      status >= 500 ? "HTTP_5XX" : "HTTP_OR_CONTENT_TYPE_ERROR";
    return report;
  }
  let html;
  try { html = await response.text(); }
  catch { report.detail = "BODY_READ_FAILED"; return report; }
  try { report.responseByteLength = new TextEncoder().encode(html).length; }
  catch { report.detail = "BODY_READ_FAILED"; return report; }
  if (report.responseByteLength > MAX_RESPONSE_BYTES) {
    report.detail = "RESPONSE_TOO_LARGE";
    return report;
  }
  if (!html.includes(DRAFT_ID)) { report.detail = "DRAFT_ID_MISSING"; return report; }
  try { Object.assign(report, compare(html)); }
  catch { report.detail = "EVIDENCE_PARSE_FAILED"; return report; }
  report.serverPersistenceState = report.outcome;
  return report;
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (!message || Object.keys(message).length !== 1 ||
      message.type !== "VERIFY_PRODUCTION_PERSISTENCE") {
    sendResponse({ outcome: "UNSUPPORTED_REQUEST", outboundRequestsInitiatedByVerifier: 0 });
    return false;
  }
  verify().then(sendResponse, () => sendResponse({
    outcome: "MEDIUM_PERSISTENCE_UNKNOWN", detail: "VERIFIER_FAILED",
    outboundRequestsInitiatedByVerifier: 0
  }));
  return true;
});

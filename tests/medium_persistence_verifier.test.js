"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const DIR = path.join(__dirname, "../extension-dev/medium-persistence-verifier");
const source = fs.readFileSync(path.join(DIR, "background.js"), "utf8");
const bodySource = fs.readFileSync(path.join(DIR, "canonical-body.js"), "utf8");
const titleSource = fs.readFileSync(path.join(DIR, "canonical-title.js"), "utf8");
const canonical = {};
vm.runInNewContext(titleSource + "\n" + bodySource +
  "\nthis.title = globalThis.PublishToAllStory; this.body = globalThis.PublishToAllBody;",
  canonical);

const escapeHtml = (value) => value.replace(/&/g, "&amp;").replace(/</g, "&lt;")
  .replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function html(blocks = canonical.body.blocks, title = canonical.title.title) {
  const body = blocks.map((block) => `<${block.kind === "heading" ? "h1" : "p"}>` +
    `${escapeHtml(block.text)}</${block.kind === "heading" ? "h1" : "p"}>`).join("");
  return `<!doctype html><html><head><title>${escapeHtml(title)}</title>` +
    `<script>"fd6c1824c4de"</script></head><body><article>${body}</article></body></html>`;
}

function response(status = 200, text = html(), type = "text/html", responseType = "basic") {
  return { status, type: responseType,
    headers: { get: (name) => name === "Content-Type" ? type : null },
    text: async () => text };
}

function harness(value = response()) {
  const calls = [];
  let listener;
  const context = vm.createContext({ URL, TextEncoder,
    fetch: async (...args) => { calls.push(args); if (value instanceof Error) throw value;
      return value; },
    chrome: { runtime: { onMessage: { addListener(fn) { listener = fn; } } } } });
  context.importScripts = (...names) => {
    assert.deepEqual(names, ["canonical-title.js", "canonical-body.js"]);
    vm.runInContext(titleSource, context);
    vm.runInContext(bodySource, context);
  };
  vm.runInContext(source, context);
  return { calls, send(message = { type: "VERIFY_PRODUCTION_PERSISTENCE" }) {
    return new Promise((resolve) => listener(message, {}, resolve));
  } };
}

function oneGet(h, result) {
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0][0], "https://medium.com/p/fd6c1824c4de/edit");
  assert.deepEqual(JSON.parse(JSON.stringify(h.calls[0][1])), {
    method: "GET", credentials: "include", redirect: "manual", cache: "no-store"
  });
  assert.equal(result.outboundRequestsInitiatedByVerifier, 1);
  assert.deepEqual(JSON.parse(JSON.stringify(result.methods)), {
    GET: 1, POST: 0, PUT: 0, PATCH: 0, DELETE: 0
  });
  assert.equal(result.domVerificationState, "BODY_DOM_VERIFIED_PREVIOUS_OBSERVATION");
  assert.ok(JSON.stringify(result).length < 1800);
  assert.ok(!JSON.stringify(result).includes(canonical.body.blocks[1].text.slice(0, 30)));
}

test("exact canonical title, formatting, and all 36 blocks verify server persistence", async () => {
  const h = harness();
  const result = await h.send();
  oneGet(h, result);
  assert.equal(result.outcome, "MEDIUM_PERSISTENCE_VERIFIED");
  assert.equal(result.serverPersistenceState, "MEDIUM_PERSISTENCE_VERIFIED");
  assert.equal(result.titleFingerprint, "fnv1a32:5e81665d");
  assert.equal(result.bodyFingerprint, "fnv1a32:342d11eb");
  assert.equal(result.bodyBlockCount, 36);
  assert.equal(result.headingCount, 18);
  assert.equal(result.paragraphCount, 18);
  assert.equal(result.normalizedBodyLength, 58757);
  assert.equal(result.lastMatchingCanonicalBlock.number, 36);
  assert.equal(result.nextExpectedCanonicalBlock, null);
  const again = await h.send();
  assert.equal(again.detail, "ALREADY_ATTEMPTED");
  assert.equal(h.calls.length, 1);
});

for (const [name, blocks, expectedThrough, detail] of [
  ["title-only", [], 0, "EMPTY_BODY"],
  ["block-1-only", canonical.body.blocks.slice(0, 1), 1, "CANONICAL_PREFIX_ONLY"],
  ["partial canonical prefix", canonical.body.blocks.slice(0, 17), 17,
    "CANONICAL_PREFIX_ONLY"]
]) {
  test(name, async () => {
    const h = harness(response(200, html(blocks)));
    const result = await h.send();
    oneGet(h, result);
    assert.equal(result.detail, detail);
    assert.equal(result.lastMatchingCanonicalBlock?.number || 0, expectedThrough);
    assert.equal(result.nextExpectedCanonicalBlock.number, expectedThrough + 1);
    assert.equal(result.outcome, "MEDIUM_PERSISTENCE_PARTIAL");
  });
}

test("wrong formatting identifies the first different block", async () => {
  const blocks = canonical.body.blocks.map((block) => ({ ...block }));
  blocks[1].kind = "heading";
  const h = harness(response(200, html(blocks)));
  const result = await h.send();
  oneGet(h, result);
  assert.equal(result.detail, "WRONG_FORMATTING");
  assert.equal(result.lastMatchingCanonicalBlock.number, 1);
  assert.equal(result.nextExpectedCanonicalBlock.number, 2);
  assert.equal(result.firstDifferentObservedBlock.kind, "heading");
});

test("wrong heading level cannot verify matching text", async () => {
  const changed = html().replace("<h1>01</h1>", "<h2>01</h2>");
  const h = harness(response(200, changed));
  const result = await h.send();
  oneGet(h, result);
  assert.equal(result.detail, "WRONG_FORMATTING");
  assert.equal(result.firstDifferentObservedBlock.tag, "h2");
  assert.equal(result.nextExpectedCanonicalBlock.number, 1);
});

test("extra story blocks before or after the canonical sequence cannot verify", async () => {
  for (const changed of [
    html().replace("<article>", "<article><p>unexpected</p>"),
    html().replace("</article>", "<p>unexpected</p></article>")
  ]) {
    const h = harness(response(200, changed));
    const result = await h.send();
    oneGet(h, result);
    assert.notEqual(result.outcome, "MEDIUM_PERSISTENCE_VERIFIED");
  }
});

test("extra text outside canonical blocks cannot verify", async () => {
  const h = harness(response(200, html().replace("</article>",
    "<div>unexpected</div></article>")));
  const result = await h.send();
  oneGet(h, result);
  assert.equal(result.detail, "EXTRA_BODY_CONTENT");
  assert.equal(result.outcome, "MEDIUM_PERSISTENCE_MISMATCH");
});

test("altered paragraph identifies the first different block", async () => {
  const blocks = canonical.body.blocks.map((block) => ({ ...block }));
  blocks[19].text += " altered";
  const h = harness(response(200, html(blocks)));
  const result = await h.send();
  oneGet(h, result);
  assert.equal(result.detail, "ALTERED_BODY");
  assert.equal(result.lastMatchingCanonicalBlock.number, 19);
  assert.equal(result.nextExpectedCanonicalBlock.number, 20);
});

test("wrong title cannot verify an otherwise exact body", async () => {
  const h = harness(response(200, html(canonical.body.blocks, "Wrong title")));
  const result = await h.send();
  oneGet(h, result);
  assert.equal(result.detail, "WRONG_TITLE");
  assert.equal(result.titleExact, false);
  assert.equal(result.outcome, "MEDIUM_PERSISTENCE_MISMATCH");
});

for (const [name, value, detail] of [
  ["malformed HTML", response(200, html().replace("</html>", "")), "MALFORMED_HTML"],
  ["redirect", response(302, "", "text/html", "opaqueredirect"), "REDIRECT_OBSERVED"],
  ["403", response(403), "HTTP_403"],
  ["404", response(404), "HTTP_404"],
  ["5xx", response(503), "HTTP_5XX"]
]) {
  test(name, async () => {
    const h = harness(value);
    const result = await h.send();
    oneGet(h, result);
    assert.equal(result.outcome, "MEDIUM_PERSISTENCE_UNKNOWN");
    assert.equal(result.serverPersistenceState, "MEDIUM_PERSISTENCE_UNKNOWN");
    assert.equal(result.detail, detail);
  });
}

test("message parameters cannot supply a URL, method, body, or draft ID", async () => {
  for (const extra of [{ url: "https://evil.test" }, { method: "POST" },
    { body: "payload" }, { draftId: "other" }]) {
    const h = harness();
    const result = await h.send({ type: "VERIFY_PRODUCTION_PERSISTENCE", ...extra });
    assert.equal(result.outcome, "UNSUPPORTED_REQUEST");
    assert.equal(h.calls.length, 0);
  }
});

test("network failure does not retry or expose exception text", async () => {
  const h = harness(new Error("Cookie: secret"));
  const result = await h.send();
  oneGet(h, result);
  assert.equal(result.detail, "NETWORK_ERROR");
  assert.equal(result.serverPersistenceState, "MEDIUM_PERSISTENCE_UNKNOWN");
  assert.ok(!JSON.stringify(result).includes("secret"));
});

test("popup renders report as inert text", async () => {
  const popup = fs.readFileSync(path.join(DIR, "popup.js"), "utf8");
  const elements = new Map(["run", "result"].map((id) =>
    [id, { disabled: false, textContent: "", addEventListener(_name, fn) { this.click = fn; } }]));
  const context = { document: { getElementById: (id) => elements.get(id) },
    chrome: { runtime: { sendMessage: async () => ({ text: "<script>inert</script>" }) } } };
  vm.runInNewContext(popup, context);
  await elements.get("run").click();
  assert.equal(elements.get("run").disabled, true);
  assert.match(elements.get("result").textContent, /<script>inert<\/script>/);
});

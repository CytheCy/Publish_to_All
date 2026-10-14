"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const { webcrypto } = require("node:crypto");

const source = fs.readFileSync(path.join(__dirname,
  "../extension-dev/medium-read-probe/background.js"), "utf8");
const popupSource = fs.readFileSync(path.join(__dirname,
  "../extension-dev/medium-read-probe/popup.js"), "utf8");
const A = "a123456789ab";
const B = "b123456789ab";
const evidence = {
  A: { id: A, title: "DISCOVERY TEST A 74291", markers: [
    "MEDIUM_DISCOVERY_A_START_74291", "MEDIUM_DISCOVERY_A_MIDDLE_74291",
    "MEDIUM_DISCOVERY_A_END_74291"] },
  B: { id: B, title: "DISCOVERY TEST B 39518", markers: [
    "MEDIUM_DISCOVERY_B_START_39518", "MEDIUM_DISCOVERY_B_MIDDLE_39518",
    "MEDIUM_DISCOVERY_B_END_39518"] }
};

function html({ id = A, heading = evidence.A.title, included = evidence.A.markers } = {}) {
  return `<html><head><title>${heading}</title><script>"${id}"</script></head>` +
    `<body>${included.map((marker) => `<p>${marker}</p>`).join("")}</body></html>`;
}

function response(status = 200, body = html(), type = "text/html", extra = {}) {
  return { status, type: extra.responseType || "basic",
    headers: { get: (name) => name.toLowerCase() === "content-type" ? type :
      name.toLowerCase() === "location" ? extra.location || null : null },
    text: async () => body };
}

function harness({ a = A, b = B, responseValue = response(), fetchError = false } = {}) {
  const calls = [];
  let listener;
  const code = source.replace(/(A: ")[a-f0-9]{12}("[,\n])/, `$1${a}$2`)
    .replace(/(B: ")[a-f0-9]{12}("[,\n])/, `$1${b}$2`);
  const sandbox = {
    URL, TextEncoder, crypto: webcrypto,
    fetch: async (...args) => { calls.push(args); if (fetchError) throw Error("secret");
      return responseValue; },
    chrome: { runtime: { onMessage: { addListener: (fn) => { listener = fn; } } } }
  };
  vm.runInNewContext(code, sandbox, { filename: "background.js" });
  return { calls, sandbox, async send(message = { type: "RUN_DRAFT_A" }) {
    return new Promise((resolve) => listener(message, {}, resolve));
  } };
}

function assertOneGet(h, report, draft = "A") {
  assert.equal(h.calls.length, 1);
  assert.equal(h.calls[0][0], `https://medium.com/p/${evidence[draft].id}/edit`);
  assert.equal(h.calls[0][1].method, "GET");
  assert.equal(h.calls[0][1].credentials, "include");
  assert.equal(h.calls[0][1].redirect, "manual");
  assert.equal(Object.hasOwn(h.calls[0][1], "body"), false);
  assert.equal(Object.hasOwn(h.calls[0][1], "headers"), false);
  assert.equal(report.outboundRequestsInitiatedByProbe, 1);
  assert.equal(report.methods.GET, 1);
  for (const method of ["POST", "PUT", "PATCH", "DELETE"])
    assert.equal(report.methods[method], 0);
}

test("fixed, cookie-managed GET yields bounded evidence", async () => {
  const h = harness();
  const report = await h.send();
  assertOneGet(h, report);
  assert.equal(report.outcome, "THROWAWAY_DRAFT_READ_EVIDENCE_FOUND");
  assert.equal(report.browserManagedAuthenticatedRequestAttempted, true);
  assert.equal(report.httpStatus, 200);
  assert.equal(report.contentType, "text/html");
  assert.equal(report.completeHtml, true);
  assert.equal(report.draftIdPresent, true);
  assert.equal(report.title, evidence.A.title);
  assert.match(report.responseFingerprint, /^sha256:[0-9a-f]{64}$/);
  assert.match(report.titleFingerprint, /^sha256:[0-9a-f]{64}$/);
  assert.equal(report.responseByteLength, Buffer.byteLength(html()));
  assert.equal(report.bodyMarkerCount, 3);
  assert.equal(report.startMarkerPresent, true);
  assert.equal(report.middleMarkerPresent, true);
  assert.equal(report.endMarkerPresent, true);
  assert.ok(!JSON.stringify(report).includes("<html>"));
  assert.ok(!JSON.stringify(report).includes(evidence.A.markers[0]));
});

test("Draft B action makes one fixed GET and checks B evidence", async () => {
  const body = html({ id: B, heading: evidence.B.title, included: evidence.B.markers });
  const h = harness({ responseValue: response(200, body) });
  const report = await h.send({ type: "RUN_DRAFT_B" });
  assertOneGet(h, report, "B");
  assert.equal(report.outcome, "THROWAWAY_DRAFT_READ_EVIDENCE_FOUND");
  assert.equal(report.browserManagedAuthenticatedRequestAttempted, true);
  assert.equal(report.httpStatus, 200);
  assert.equal(report.completeHtml, true);
  assert.equal(report.draftIdPresent, true);
  assert.equal(report.title, evidence.B.title);
  assert.equal(report.bodyMarkerCount, 3);
  assert.equal(report.startMarkerPresent, true);
  assert.equal(report.middleMarkerPresent, true);
  assert.equal(report.endMarkerPresent, true);
  assert.ok(!JSON.stringify(report).includes(evidence.B.markers[0]));
  assert.ok(!JSON.stringify(report).includes("<html>"));
});

test("production ID is rejected even if placed in either allowlist slot", async () => {
  for (const config of [{ a: "fd6c1824c4de" }, { b: "fd6c1824c4de" },
    { a: "fd6c1824c4de?malformed=1" }]) {
    const h = harness(config);
    for (const type of ["RUN_DRAFT_A", "RUN_DRAFT_B"]) {
      const report = await h.send({ type });
      assert.equal(report.outcome, "PRODUCTION_DRAFT_FORBIDDEN");
      assert.equal(report.outboundRequestsInitiatedByProbe, 0);
      assert.equal(h.calls.length, 0);
    }
  }
});

test("unconfigured, malformed, and duplicate allowlists initiate nothing", async () => {
  for (const config of [{ a: "REPLACE_WITH_DRAFT_A_ID" },
    { b: "REPLACE_WITH_DRAFT_B_ID" }, { a: B, b: B },
    { a: "https://evil.test/" }, { a: "a123456789ab?x=1" },
    { a: "http://medium.com" }, { a: "a123456789ab/other" }]) {
    const h = harness(config);
    for (const type of ["RUN_DRAFT_A", "RUN_DRAFT_B"]) {
      const report = await h.send({ type });
      assert.equal(report.outcome, "THROWAWAY_ALLOWLIST_NOT_CONFIGURED");
      assert.equal(h.calls.length, 0);
    }
  }
});

test("both actions reject method, URL, body, and draft ID parameters", async () => {
  for (const type of ["RUN_DRAFT_A", "RUN_DRAFT_B"]) {
    for (const extra of [{ method: "POST" }, { url: "https://evil.test" },
      { body: "secret" }, { draftId: B }]) {
      const h = harness();
      const report = await h.send({ type, ...extra });
      assert.equal(report.outcome, "UNSUPPORTED_REQUEST");
      assert.equal(h.calls.length, 0);
    }
  }
  const h = harness();
  assert.equal((await h.send({ type: "RUN_DRAFT_C" })).outcome, "UNSUPPORTED_REQUEST");
  assert.equal(h.calls.length, 0);
});

test("manual redirect is observed and never followed", async () => {
  for (const r of [response(302, "", "text/html", { location: "/m/signin?token=secret" }),
    response(0, "", "", { responseType: "opaqueredirect" })]) {
    const h = harness({ responseValue: r });
    const report = await h.send();
    assertOneGet(h, report);
    assert.equal(report.outcome, "AUTHENTICATED_READ_NOT_ESTABLISHED");
    assert.equal(report.detail, "REDIRECT_OBSERVED");
    assert.equal(report.status, r.status);
    assert.ok(!JSON.stringify(report).includes("secret"));
  }
});

test("401, 403, 404, 5xx and non-HTML fail closed", async () => {
  for (const r of [response(401), response(403), response(404), response(503),
    response(200, html(), "application/json")]) {
    const h = harness({ responseValue: r });
    const report = await h.send();
    assertOneGet(h, report);
    assert.equal(report.outcome, "AUTHENTICATED_READ_NOT_ESTABLISHED");
    assert.equal(report.bodyMarkerCount, undefined);
  }
});

test("malformed, missing, wrong, and partial draft evidence fails closed", async () => {
  for (const body of [html().replace("</html>", ""), html({ id: "" }), html({ id: B }),
    html({ heading: "wrong title" }), html({ included: evidence.A.markers.slice(0, 2) })]) {
    const h = harness({ responseValue: response(200, body) });
    const report = await h.send();
    assertOneGet(h, report);
    assert.equal(report.outcome, "AUTHENTICATED_READ_NOT_ESTABLISHED");
    assert.ok(report.bodyMarkerCount >= 0 && report.bodyMarkerCount <= 3);
  }
  const partial = await harness({ responseValue: response(200,
    html({ included: evidence.A.markers.slice(0, 2) })) }).send();
  assert.equal(partial.bodyMarkerCount, 2);
  assert.equal(partial.endMarkerPresent, false);
});

test("Draft B rejects A evidence and incomplete B evidence", async () => {
  for (const body of [html(),
    html({ id: B, heading: evidence.A.title, included: evidence.B.markers }),
    html({ id: B, heading: evidence.B.title, included: evidence.A.markers }),
    html({ id: B, heading: evidence.B.title,
      included: evidence.B.markers.slice(0, 2) })]) {
    const h = harness({ responseValue: response(200, body) });
    const report = await h.send({ type: "RUN_DRAFT_B" });
    assertOneGet(h, report, "B");
    assert.equal(report.outcome, "AUTHENTICATED_READ_NOT_ESTABLISHED");
  }
});

test("response and credential-looking text never enter report", async () => {
  const secrets = "Cookie: SID_VALUE Set-Cookie: UID_VALUE XSRF_VALUE " +
    "CF_CLEARANCE_VALUE Authorization: bearer-secret";
  const h = harness({ responseValue: response(200, html() + `<script>${secrets}</script>`) });
  const report = await h.send();
  assertOneGet(h, report);
  const output = JSON.stringify(report);
  for (const term of ["Cookie:", "Set-Cookie:", "SID_VALUE", "UID_VALUE",
    "XSRF_VALUE", "CF_CLEARANCE_VALUE", "Authorization:", "bearer-secret",
    "<script>", "<html>"]) assert.ok(!output.includes(term), term);
});

test("Draft B redirect fails closed without following or exposing query text", async () => {
  const h = harness({ responseValue: response(302, "", "text/html", {
    location: "/m/signin?token=secret"
  }) });
  const report = await h.send({ type: "RUN_DRAFT_B" });
  assertOneGet(h, report, "B");
  assert.equal(report.outcome, "AUTHENTICATED_READ_NOT_ESTABLISHED");
  assert.equal(report.detail, "REDIRECT_OBSERVED");
  assert.ok(!JSON.stringify(report).includes("secret"));
});

test("redirect fields stay bounded", async () => {
  const h = harness({ responseValue: response(302, "", "text/html", {
    location: "/" + "x".repeat(5000) + "?token=secret"
  }) });
  const report = await h.send({ type: "RUN_DRAFT_B" });
  assertOneGet(h, report, "B");
  assert.equal(report.outcome, "AUTHENTICATED_READ_NOT_ESTABLISHED");
  assert.ok(report.destinationPath.length <= 200);
  assert.ok(!JSON.stringify(report).includes("secret"));
});

test("popup offers separate A and B actions and renders reports as inert text", async () => {
  const elements = new Map(["run-a", "run-b", "result"].map((id) =>
    [id, { disabled: false, textContent: "", addEventListener(_name, fn) { this.click = fn; } }]));
  const messages = [];
  const sandbox = { document: { getElementById: (id) => elements.get(id) },
    chrome: { runtime: { sendMessage: async (message) => {
      messages.push(message);
      return { outcome: "ok", text: "<script>inert</script>" };
    } } } };
  vm.runInNewContext(popupSource, sandbox, { filename: "popup.js" });
  await elements.get("run-b").click();
  assert.deepEqual(JSON.parse(JSON.stringify(messages)), [{ type: "RUN_DRAFT_B" }]);
  assert.equal(elements.get("run-a").disabled, true);
  assert.equal(elements.get("run-b").disabled, true);
  assert.match(elements.get("result").textContent, /<script>inert<\/script>/);
});

test("network error initiates exactly one request and does not retry", async () => {
  const h = harness({ fetchError: true });
  const report = await h.send();
  assertOneGet(h, report);
  assert.equal(report.outcome, "AUTHENTICATED_READ_NOT_ESTABLISHED");
});

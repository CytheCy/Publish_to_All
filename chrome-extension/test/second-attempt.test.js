"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const second = require("../second-attempt.js");
const first = require("../import-session.js");

const FINGERPRINT = "2a5f676c8743eda141101e793cc4ce988ba6f029d0805c0498271bd072937349";
const SOURCE_URL = "https://cyporter.substack.com/p/saturation-of-artificial-intelligence-f8d";
const URL = "https://medium.com/p/import";
const TOKEN = "fixture-document-token-1234";

function readyInput() {
  const editor = {};
  const action = { contains(node) { return node === this.child; }, child: {} };
  return { report: { page: URL, importInterface: {
    heading: "See your story on Medium", headingVerified: true, matchingHeadingCount: 1,
    matchingSourceEditorCount: 1, matchingImportActionCount: 1,
    importAction: { visible: true, enabled: true },
    sourceUrlInput: { editorState: "EXACT_SOURCE_URL_PRESENT",
      entireContentOneUrl: true,
      existingUrl: { matchesIntendedSourceExactly: true,
        safeUrlFingerprint: FINGERPRINT, normalizedUrlLength: SOURCE_URL.length } }
  } }, raw: { sourceGuard: true, source: { exactMatch: true,
    fingerprintMatch: true, normalizedLength: SOURCE_URL.length },
    actionGuard: true, action: {
      candidateCount: 1, identityMatchesInspection: true,
      visible: true, enabled: true, connected: true },
    targets: { editor, action } }, reconciliation: {
    validation: { detected: false }, importPage: { draftEditLink: false,
      seeYourStory: false, confirmation: "Not observed", error: "Not observed" }
  }, fill: { fillSynchronizationStatus: "VERIFIED", exactUrlPresentAfterward: true,
    fingerprintMatch: true }, attempt1: first.KNOWN_HISTORY,
  location: { href: URL }, record: second.empty(), documentToken: TOKEN };
}

function storage() {
  const values = new Map();
  return { values, async get(key) { return { [key]: values.get(key) }; },
    async set(patch) { for (const [key, value] of Object.entries(patch))
      values.set(key, value); } };
}

test("attempt 1 history identifies the old programmatic activation and empty Drafts", () => {
  assert.equal(first.KNOWN_HISTORY.attemptNumber, 1);
  assert.equal(first.KNOWN_HISTORY.clickAttempts, 1);
  assert.equal(first.KNOWN_HISTORY.activationMethod, "PROGRAMMATIC_HTMLELEMENT_CLICK");
  assert.equal(first.KNOWN_HISTORY.reconciliation, "NO_DRAFT_FOUND");
  assert.equal(first.knownAttempt().lifecycleState, "IMPORT_RESULT_AMBIGUOUS");
});

test("attempt 2 readiness needs verified Fill, reconciliation, source, and button", () => {
  const base = readyInput();
  assert.equal(second.eligibility(base).state, "SECOND_ATTEMPT_READY");
  const cases = [
    ["FILL_SYNCHRONIZATION_NOT_VERIFIED", input =>
      { input.fill.fillSynchronizationStatus = "UNVERIFIED"; }],
    ["ATTEMPT_1_RECONCILIATION_REQUIRED", input =>
      { input.attempt1 = { ...input.attempt1, reconciliation: "AMBIGUOUS" }; }],
    ["SOURCE_IDENTITY_MISMATCH", input =>
      { input.report.importInterface.sourceUrlInput.existingUrl.safeUrlFingerprint = "changed"; }],
    ["SOURCE_IDENTITY_MISMATCH", input => { input.raw.source.fingerprintMatch = false; }],
    ["NATIVE_IMPORT_IDENTITY_MISMATCH", input =>
      { input.raw.action.identityMatchesInspection = false; }],
    ["NATIVE_IMPORT_IDENTITY_MISMATCH", input =>
      { input.raw.action.candidateCount = 2; }],
    ["NATIVE_IMPORT_IDENTITY_MISMATCH", input =>
      { input.raw.action.visible = false; }],
    ["NATIVE_IMPORT_IDENTITY_MISMATCH", input =>
      { input.report.importInterface.importAction.enabled = false; }],
    ["EXISTING_RESULT_OR_VALIDATION", input =>
      { input.reconciliation.validation.detected = true; }],
    ["EXISTING_RESULT_OR_VALIDATION", input =>
      { input.reconciliation.importPage.draftEditLink = true; }],
    ["IMPORT_PAGE_MISMATCH", input =>
      { input.location.href = "https://medium.com/me/stories"; }]
  ];
  for (const [reason, change] of cases) {
    const input = readyInput();
    change(input);
    assert.equal(second.eligibility(input).reason, reason);
  }
});

test("reservation is durable before click and does not count as a click", async () => {
  const disk = storage();
  const input = readyInput();
  const readiness = second.eligibility(input);
  const armed = await second.reserve({ storage: disk, record: input.record,
    readiness, documentToken: TOKEN, now: 123456 });
  assert.equal(disk.values.get(second.KEY), armed);
  assert.equal(armed.state, "SECOND_ATTEMPT_ARMED");
  assert.equal(armed.authorizedAttemptNumber, 2);
  assert.equal(armed.attemptNumber, 2);
  assert.equal(armed.observedNativeImportClicks, 0);
  assert.equal(armed.nativeClickObserved, false);
  assert.equal(armed.sourceFingerprint, FINGERPRINT);
  assert.equal(armed.fillSynchronization, "VERIFIED");
  assert.equal(armed.nativeImportIdentity, "verified");
  assert.equal(armed.result, "PENDING");
  assert.equal(armed.attempt1Reconciliation, "NO_DRAFT_FOUND");
  assert.equal(JSON.stringify(armed).includes(SOURCE_URL), false);
  assert.equal(second.eligibility({ ...input, record: armed }).state,
    "SECOND_ATTEMPT_NOT_READY");
});

test("only one trusted click on the pinned button consumes attempt 2", async () => {
  const disk = storage();
  const input = readyInput();
  const armed = await second.reserve({ storage: disk, record: input.record,
    readiness: second.eligibility(input), documentToken: TOKEN, now: 123456 });
  const action = input.raw.targets.action;
  const clicked = second.classifyClick({ event: { isTrusted: true, target: action.child },
    action, freshAction: action, record: armed, documentToken: TOKEN,
    sourceStillValid: true, fillStillVerified: true });
  assert.equal(clicked.authorized, true);
  const consumed = await second.recordClick({ storage: disk, record: armed,
    classification: clicked });
  assert.equal(disk.values.get(second.KEY), consumed);
  assert.equal(consumed.state, "SECOND_ATTEMPT_USER_CLICK_OBSERVED");
  assert.equal(consumed.observedNativeImportClicks, 1);
  assert.equal(consumed.nativeClickObserved, true);
  const duplicate = second.classifyClick({ event: { isTrusted: true, target: action },
    action, freshAction: action, record: consumed, documentToken: TOKEN,
    sourceStillValid: true, fillStillVerified: true });
  assert.equal(duplicate.authorized, false);
  assert.equal(duplicate.reason, "NOT_ARMED_OR_ALREADY_CONSUMED");
  assert.equal(await second.recordClick({ storage: disk, record: consumed,
    classification: duplicate }), null);
  assert.equal(disk.values.get(second.KEY).observedNativeImportClicks, 1);
  const ambiguous = await second.recordResult({ storage: disk, record: consumed,
    verified: false });
  assert.equal(ambiguous.state, "SECOND_ATTEMPT_RESULT_AMBIGUOUS");
  assert.equal(second.eligibility({ ...input, record: ambiguous }).state,
    "SECOND_ATTEMPT_NOT_READY");
  assert.equal(await second.recordResult({ storage: disk, record: ambiguous,
    verified: true }), null);
});

test("unarmed, untrusted, stale, and changed-source clicks are never authorized", async () => {
  const input = readyInput();
  const action = input.raw.targets.action;
  const disk = storage();
  const armed = await second.reserve({ storage: disk, record: input.record,
    readiness: second.eligibility(input), documentToken: TOKEN, now: 123456 });
  const options = { event: { isTrusted: true, target: action }, action,
    freshAction: action, record: armed, documentToken: TOKEN,
    sourceStillValid: true, fillStillVerified: true };
  const cases = [
    [{ ...options, record: second.empty() }, "NOT_ARMED_OR_ALREADY_CONSUMED"],
    [{ ...options, documentToken: "new-document-token-5678" },
      "NOT_ARMED_OR_ALREADY_CONSUMED"],
    [{ ...options, event: { isTrusted: false, target: action } }, "UNTRUSTED_CLICK"],
    [{ ...options, freshAction: {} }, "NATIVE_IMPORT_IDENTITY_CHANGED"],
    [{ ...options, sourceStillValid: false }, "SOURCE_CHANGED"],
    [{ ...options, fillStillVerified: false }, "FILL_NOT_VERIFIED"]
  ];
  for (const [caseInput, reason] of cases) {
    const result = second.classifyClick(caseInput);
    assert.equal(result.authorized, false);
    assert.equal(result.reason, reason);
    assert.equal(await second.recordClick({ storage: disk, record: armed,
      classification: result }), null);
  }
  assert.equal(disk.values.get(second.KEY).observedNativeImportClicks, 0);
});

test("background retires reservation writes even with valid prior history", async () => {
  const local = storage();
  const session = storage();
  let listener;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../background.js"), "utf8"), {
    MediumSecondAttempt: second, MediumImportSession: first, URL: global.URL,
    importScripts(...files) { assert.deepEqual(files,
      ["import-session.js", "second-attempt.js", "medium-retirement.js"]); },
    chrome: { storage: { local, session }, runtime: { onMessage: {
      addListener(fn) { listener = fn; } } } }
  });
  const send = (message, senderUrl = URL) => new Promise(resolve => {
    assert.equal(listener(message, { url: senderUrl }, resolve), true);
  });
  const input = readyInput();
  const armed = await second.reserve({ storage: storage(), record: input.record,
    readiness: second.eligibility(input), documentToken: TOKEN, now: 123456 });
  assert.equal((await send({ type: "SET_MEDIUM_SECOND_ATTEMPT",
    record: armed })).reason, "MEDIUM_URL_IMPORT_RETIRED");
  assert.equal((await send({ type: "GET_MEDIUM_SECOND_ATTEMPT" })).record.state,
    "SECOND_ATTEMPT_NOT_AUTHORIZED");
  const invalid = { ...first.knownAttempt(), clickAttempts: 0 };
  await session.set({ [first.KEY]: invalid });
  assert.equal((await send({ type: "SET_MEDIUM_SECOND_ATTEMPT",
    record: armed })).ok, false);
  await session.set({ [first.KEY]: first.knownAttempt() });
  assert.equal((await send({ type: "SET_MEDIUM_SECOND_ATTEMPT",
    record: armed }, "https://medium.com/me/stories")).ok, false);
  assert.equal((await send({ type: "SET_MEDIUM_SECOND_ATTEMPT",
    record: armed })).reason, "MEDIUM_URL_IMPORT_RETIRED");
  assert.equal(session.values.has(second.KEY), false);
  assert.equal(local.values.has(second.KEY), false);
  assert.equal((await send({ type: "SET_MEDIUM_SECOND_ATTEMPT",
    record: armed })).ok, false);
  assert.deepEqual(session.values.get(first.KEY), first.knownAttempt());
});

test("retired background never attempts a reservation write", async () => {
  const disk = storage();
  await disk.set({ [first.KEY]: first.knownAttempt() });
  const session = { async get(key) { return disk.get(key); },
    async set(values) {
      if (Object.hasOwn(values, second.KEY)) throw Error("disk failed");
      return disk.set(values);
    } };
  const local = storage();
  let listener;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../background.js"), "utf8"), {
    MediumSecondAttempt: second, MediumImportSession: first, URL: global.URL,
    importScripts() {}, chrome: { storage: { local, session }, runtime: { onMessage: {
      addListener(fn) { listener = fn; } } } }
  });
  const input = readyInput();
  const armed = await second.reserve({ storage: storage(), record: input.record,
    readiness: second.eligibility(input), documentToken: TOKEN, now: 123456 });
  const response = await new Promise(resolve => listener({
    type: "SET_MEDIUM_SECOND_ATTEMPT", record: armed }, { url: URL }, resolve));
  assert.equal(response.ok, false);
  assert.equal(response.reason, "MEDIUM_URL_IMPORT_RETIRED");
  assert.equal(disk.values.has(second.KEY), false);
});

test("read-only worker inspection finds a prior local ARMED record and prevents another reservation", async () => {
  const session = storage();
  const local = storage();
  await session.set({ [first.KEY]: first.knownAttempt() });
  const input = readyInput();
  const armed = await second.reserve({ storage: storage(), record: input.record,
    readiness: second.eligibility(input), documentToken: TOKEN, now: 123456 });
  await local.set({ [second.KEY]: armed });
  let listener;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../background.js"), "utf8"), {
    MediumSecondAttempt: second, MediumImportSession: first, URL: global.URL,
    importScripts() {}, chrome: { storage: { local, session }, runtime: {
      id: "fixture-extension", onMessage: { addListener(fn) { listener = fn; } } } }
  });
  const send = (message, sender = { url: URL }) => new Promise(resolve => {
    assert.equal(listener(message, sender, resolve), true);
  });
  const popupSender = { id: "fixture-extension",
    url: "chrome-extension://fixture-extension/popup.html" };
  const inspected = await send({ type: "INSPECT_MEDIUM_RESERVATION" }, popupSender);
  assert.equal(inspected.ok, true);
  assert.equal(inspected.exists, true);
  assert.equal(inspected.record.state, "SECOND_ATTEMPT_ARMED");
  assert.equal(inspected.source, "chrome.storage.local (prior build)");
  assert.equal(session.values.has(second.KEY), false);
  const rejected = await send({ type: "SET_MEDIUM_SECOND_ATTEMPT",
    record: armed, transactionId: "fixture-1" });
  assert.equal(rejected.ok, false);
  assert.equal(rejected.reason, "MEDIUM_URL_IMPORT_RETIRED");
  assert.equal(session.values.has(second.KEY), false);
});

test("retired background cannot write even if storage would accept a reservation", async () => {
  const disk = storage();
  await disk.set({ [first.KEY]: first.knownAttempt() });
  const session = { async get(key) {
    if (key === second.KEY) return { [key]: undefined };
    return disk.get(key);
  }, async set(values) { return disk.set(values); } };
  let listener;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../background.js"), "utf8"), {
    MediumSecondAttempt: second, MediumImportSession: first, URL: global.URL,
    importScripts() {}, chrome: { storage: { local: storage(), session },
      runtime: { onMessage: { addListener(fn) { listener = fn; } } } }
  });
  const input = readyInput();
  const armed = await second.reserve({ storage: storage(), record: input.record,
    readiness: second.eligibility(input), documentToken: TOKEN, now: 123456 });
  const response = await new Promise(resolve => {
    assert.equal(listener({ type: "SET_MEDIUM_SECOND_ATTEMPT",
      record: armed, transactionId: "fixture-2" }, { url: URL }, resolve), true);
  });
  assert.equal(response.ok, false);
  assert.equal(response.reason, "MEDIUM_URL_IMPORT_RETIRED");
  assert.equal(disk.values.has(second.KEY), false);
});

test("extension preparation path has no native click, submit, navigation, or Publish capability", () => {
  const controls = fs.readFileSync(path.join(__dirname, "../medium-controls.js"), "utf8");
  const content = fs.readFileSync(path.join(__dirname, "../content.js"), "utf8");
  const background = fs.readFileSync(path.join(__dirname, "../background.js"), "utf8");
  const preparation = controls.slice(controls.indexOf('importButton.addEventListener("click"'),
    controls.indexOf("// Capture only."));
  assert.doesNotMatch(preparation, /\.click\s*\(|\.dispatchEvent\s*\(|\bMouseEvent\b|\bsubmit\s*\(|\.requestSubmit\s*\(|location\s*=/);
  assert.doesNotMatch(controls + content + background,
    /(?:targets\.action|armedNativeTarget|candidate)\.click\s*\(|\.dispatchEvent\s*\(\s*new\s+MouseEvent|\.requestSubmit\s*\(/);
  assert.doesNotMatch(background, /ENABLE_SECOND_ATTEMPT_AUTHORIZATION/);
  assert.doesNotMatch(controls + content + background,
    /(?:publishButton|publishAction|publishTarget)\.click\s*\(/i);
});

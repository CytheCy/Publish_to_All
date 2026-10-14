"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const second = require("../second-attempt.js");

const source = fs.readFileSync(path.join(__dirname, "../content.js"), "utf8");
const URL = "https://medium.com/p/import";

async function fixture(onSet) {
  let session;
  let inspectMessage;
  let titleFillCalls = 0;
  let titleReadinessCalls = 0;
  const messages = [];
  const runtime = {
    onMessage: { addListener(listener) { inspectMessage = listener; } },
    sendMessage(message, callback) {
      messages.push(message.type);
      if (message.type === "INSPECT_MEDIUM_RESERVATION") {
        callback({ ok: true, exists: false, record: second.empty(),
          source: "chrome.storage.session", diagnostic: {
            transactionId: "Not observed", backgroundResponse: "NOT_RECEIVED" } });
      } else if (message.type === "SET_MEDIUM_SECOND_ATTEMPT") {
        onSet(message, callback, runtime);
      } else callback({ ok: true, record: {} });
    }
  };
  vm.runInNewContext(source, {
    chrome: { runtime }, location: { href: URL }, document: {},
    MediumSecondAttempt: second,
    MediumImportSession: {
      KEY: "attempt-1",
      KNOWN_HISTORY: {},
      KNOWN_RECONCILIATION: {},
      async load() { return { available: true, record: {} }; },
      makeSession() { return { importClicks: 1, importState: "IMPORT_RESULT_AMBIGUOUS",
        async persist() { return true; } }; }
    },
    MediumPageControls: { mount(_doc, _location, _inspector, value) {
      session = value;
      return { getImportInspection() { return null; } };
    } },
    MediumInspector: {}, MediumEditorInspector: {},
    PublishToAllStory: { title: "Fixture title", fingerprint: "fixture" },
    MediumTitleFill: {
      eligibility() { titleReadinessCalls++; return {
        ready: false, reason: "BLANK_EDITOR_NOT_VERIFIED" }; },
      async fill() { titleFillCalls++; return { titleFillAttempted: true }; }
    }
  });
  await new Promise(resolve => setImmediate(resolve));
  assert.ok(session);
  session.prepareTransaction = { transactionId: "fixture-3",
    reservationRequestSent: false, contentResponseReceived: false };
  return { session, messages, inspectMessage,
    titleCalls: () => ({ fill: titleFillCalls, readiness: titleReadinessCalls }) };
}

test("content title click path checks fresh readiness before any fill", async () => {
  const current = await fixture(() => {});
  const response = await new Promise(resolve => current.inspectMessage(
    { type: "FILL_VERIFIED_MEDIUM_TITLE" }, null, resolve));
  assert.equal(response.ok, false);
  assert.equal(response.error, "BLANK_EDITOR_NOT_VERIFIED");
  assert.ok(current.titleCalls().readiness >= 1);
  assert.equal(current.titleCalls().fill, 0);
});

test("content bridge sends the transaction and awaits a confirmed worker response", async () => {
  const record = { state: "SECOND_ATTEMPT_ARMED" };
  const { session, messages } = await fixture((message, callback) => {
    assert.equal(message.transactionId, "fixture-3");
    assert.deepEqual(message.record, record);
    callback({ ok: true, record, diagnostic: { backgroundResponse: "SUCCESS" } });
  });
  await session.secondAttemptStore.set({ [second.KEY]: record });
  assert.equal(messages.includes("SET_MEDIUM_SECOND_ATTEMPT"), true);
  assert.equal(session.prepareTransaction.reservationRequestSent, true);
  assert.equal(session.prepareTransaction.contentResponseReceived, true);
  assert.equal(session.prepareTransaction.backgroundResponse, "SUCCESS");
});

test("content bridge surfaces runtime.lastError and worker rejection", async () => {
  const lost = await fixture((_message, callback, runtime) => {
    runtime.lastError = { message: "port closed" };
    callback(undefined);
    delete runtime.lastError;
  });
  await assert.rejects(
    lost.session.secondAttemptStore.set({ [second.KEY]: {} }),
    /RUNTIME_LAST_ERROR/);
  assert.equal(lost.session.prepareTransaction.reservationRequestSent, true);
  assert.equal(lost.session.prepareTransaction.contentResponseReceived, false);

  const rejected = await fixture((_message, callback) =>
    callback({ ok: false, reason: "WORKER_OR_STORAGE_ERROR",
      diagnostic: { backgroundResponse: "ERROR" } }));
  await assert.rejects(
    rejected.session.secondAttemptStore.set({ [second.KEY]: {} }),
    /WORKER_OR_STORAGE_ERROR/);
  assert.equal(rejected.session.prepareTransaction.contentResponseReceived, true);
  assert.equal(rejected.session.prepareTransaction.backgroundResponse, "ERROR");
});

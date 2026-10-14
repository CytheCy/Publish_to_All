"use strict";

importScripts("import-session.js", "second-attempt.js", "medium-retirement.js");

const KEY = MediumImportSession.KEY;
const SECOND_KEY = MediumSecondAttempt.KEY;
const TRANSACTION_KEY = "publish-to-all:medium-prepare-diagnostics:v1";
let pending = Promise.resolve();

function serialize(task) {
  const result = pending.then(task);
  pending = result.catch(() => {});
  return result;
}

const emptyDiagnostic = () => ({ transactionId: "Not observed",
  backgroundRequestReceived: false, attempt1HistoryCheck: "NOT_RUN",
  existingAttempt2Check: "NOT_RUN", storageWriteAttempted: false,
  storageWriteSucceeded: false, storageReadBackSucceeded: false,
  backgroundResponse: "NOT_RECEIVED", rejectionReason: "None" });

async function recordDiagnostic(diagnostic) {
  await chrome.storage.session.set({ [TRANSACTION_KEY]: diagnostic });
}

async function readSecond() {
  const sessionRecord = (await chrome.storage.session.get(SECOND_KEY))[SECOND_KEY];
  // An older build used local storage. Its record still blocks another reservation.
  const legacyRecord = chrome.storage.local ?
    (await chrome.storage.local.get(SECOND_KEY))[SECOND_KEY] : undefined;
  if (sessionRecord !== undefined && !MediumSecondAttempt.valid(sessionRecord))
    throw Error("INVALID_SECOND_ATTEMPT_SESSION_RECORD");
  if (legacyRecord !== undefined && !MediumSecondAttempt.valid(legacyRecord))
    throw Error("INVALID_SECOND_ATTEMPT_LEGACY_RECORD");
  if (sessionRecord && legacyRecord &&
      JSON.stringify(sessionRecord) !== JSON.stringify(legacyRecord))
    throw Error("CONFLICTING_SECOND_ATTEMPT_RECORDS");
  return { record: sessionRecord || legacyRecord || MediumSecondAttempt.empty(),
    exists: Boolean(sessionRecord || legacyRecord),
    source: sessionRecord ? "chrome.storage.session" : legacyRecord ?
      "chrome.storage.local (prior build)" : "chrome.storage.session" };
}

function safeReason(error) {
  const code = error?.message;
  return ["INVALID_SECOND_ATTEMPT_SESSION_RECORD",
    "INVALID_SECOND_ATTEMPT_LEGACY_RECORD", "CONFLICTING_SECOND_ATTEMPT_RECORDS"]
    .includes(code) ? code : "WORKER_OR_STORAGE_ERROR";
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  let mediumSender = false;
  try { mediumSender = new URL(sender?.url).origin === "https://medium.com"; }
  catch { /* Reject an unrecognized sender. */ }
  const extensionSender = sender?.id === chrome.runtime.id &&
    sender?.url?.startsWith(`chrome-extension://${chrome.runtime.id}/`);
  if (!mediumSender && !extensionSender) return false;

  const reply = task => {
    serialize(task).then(respond, error => respond({ ok: false,
      reason: safeReason(error), backgroundResponse: "ERROR" }));
    // An async sendResponse requires a live MV3 message channel.
    return true;
  };

  if (message?.type === "INSPECT_MEDIUM_RESERVATION" ||
      message?.type === "GET_MEDIUM_SECOND_ATTEMPT") {
    return reply(async () => {
      const state = await readSecond();
      const diagnostic = (await chrome.storage.session.get(TRANSACTION_KEY))[TRANSACTION_KEY];
      return { ok: true, ...state, diagnostic: diagnostic || emptyDiagnostic() };
    });
  }
  if (!mediumSender) return false;
  if (message?.type === "GET_MEDIUM_IMPORT_SESSION") {
    return reply(async () => {
      const saved = (await chrome.storage.session.get(KEY))[KEY];
      return { ok: true,
        record: MediumImportSession.valid(saved) ? saved : MediumImportSession.knownAttempt(),
        source: MediumImportSession.valid(saved) ? "chrome.storage.session" :
          "Known real attempt fallback", reason: "None" };
    });
  }
  if (message?.type === "SET_MEDIUM_SECOND_ATTEMPT") {
    return reply(async () => {
      // Terminal story policy. The legacy transition code below is retained as
      // diagnostic history and is unreachable for every request.
      return { ok: false, reason: "MEDIUM_URL_IMPORT_RETIRED" };
      const diagnostic = { ...emptyDiagnostic(),
        transactionId: /^[a-z0-9-]{1,24}$/i.test(message.transactionId || "") ?
          message.transactionId : "Invalid", backgroundRequestReceived: true };
      const reject = async reason => {
        diagnostic.backgroundResponse = "REJECTED";
        diagnostic.rejectionReason = reason;
        await recordDiagnostic(diagnostic);
        return { ok: false, reason, diagnostic };
      };
      try {
        await recordDiagnostic(diagnostic);
        const next = message.record;
        if (!MediumSecondAttempt.valid(next)) return await reject("INVALID_NEXT_RECORD");
        const current = await readSecond();
        diagnostic.existingAttempt2Check = current.exists ? "FAIL" : "PASS";
        await recordDiagnostic(diagnostic);
        const arm = !current.exists && next.state === "SECOND_ATTEMPT_ARMED" &&
          sender.url === "https://medium.com/p/import" &&
          next.observedNativeImportClicks === 0 && next.result === "PENDING" &&
          next.nativeImportIdentity === "verified";
        const observe = current.record.state === "SECOND_ATTEMPT_ARMED" &&
          next.state === "SECOND_ATTEMPT_USER_CLICK_OBSERVED" &&
          current.record.documentToken === next.documentToken &&
          current.record.authorizationTimestamp === next.authorizationTimestamp &&
          next.observedNativeImportClicks === 1;
        const result = current.record.state === "SECOND_ATTEMPT_USER_CLICK_OBSERVED" &&
          ["SECOND_ATTEMPT_RESULT_VERIFIED", "SECOND_ATTEMPT_RESULT_AMBIGUOUS"]
            .includes(next.state) &&
          current.record.documentToken === next.documentToken &&
          current.record.authorizationTimestamp === next.authorizationTimestamp &&
          next.observedNativeImportClicks === 1 &&
          JSON.stringify(current.record.clickEvidence) === JSON.stringify(next.clickEvidence);
        if (!arm && !observe && !result)
          return await reject(current.exists ? "EXISTING_ATTEMPT_2_RECORD" :
            "TRANSITION_REJECTED");
        if (arm) {
          const persisted = (await chrome.storage.session.get(KEY))[KEY];
          diagnostic.attempt1HistoryCheck = MediumImportSession.valid(persisted) &&
            persisted.clickAttempts === 1 &&
            persisted.lifecycleState === "IMPORT_RESULT_AMBIGUOUS" &&
            persisted.reconciliationStatus === "NO_DRAFT_FOUND" &&
            persisted.activationMethod === "PROGRAMMATIC_HTMLELEMENT_CLICK" ?
              "PASS" : "FAIL";
          await recordDiagnostic(diagnostic);
          if (diagnostic.attempt1HistoryCheck !== "PASS")
            return await reject("ATTEMPT_1_PERSISTED_HISTORY_MISMATCH");
        }
        diagnostic.storageWriteAttempted = true;
        await recordDiagnostic(diagnostic);
        await chrome.storage.session.set({ [SECOND_KEY]: next });
        diagnostic.storageWriteSucceeded = true;
        await recordDiagnostic(diagnostic);
        const confirmed = (await chrome.storage.session.get(SECOND_KEY))[SECOND_KEY];
        diagnostic.storageReadBackSucceeded =
          JSON.stringify(confirmed) === JSON.stringify(next);
        if (!diagnostic.storageReadBackSucceeded)
          return await reject("SECOND_ATTEMPT_READ_BACK_MISMATCH");
        diagnostic.backgroundResponse = "SUCCESS";
        await recordDiagnostic(diagnostic);
        return { ok: true, record: confirmed, diagnostic };
      } catch (error) {
        diagnostic.backgroundResponse = "ERROR";
        diagnostic.rejectionReason = safeReason(error);
        try { await recordDiagnostic(diagnostic); } catch { /* Storage unavailable. */ }
        return { ok: false, reason: diagnostic.rejectionReason, diagnostic };
      }
    });
  }
  if (message?.type === "SET_MEDIUM_IMPORT_SESSION") {
    return reply(async () => {
      return { ok: false, reason: "MEDIUM_URL_IMPORT_RETIRED" };
      const next = message.record;
      if (!MediumImportSession.valid(next)) return { ok: false, reason: "INVALID_ATTEMPT_1_RECORD" };
      const current = await MediumImportSession.load(chrome.storage.session);
      if (!current.available) return { ok: false, reason: "ATTEMPT_1_STORAGE_UNAVAILABLE" };
      const before = current.record.lifecycleState;
      const after = next.lifecycleState;
      const allowed = before === after ||
        (before === "IMPORT_CLICK_INTENT" &&
          ["IMPORT_CLICKED", "IMPORT_RESULT_AMBIGUOUS"].includes(after)) ||
        (before === "IMPORT_CLICKED" &&
          ["IMPORT_RESULT_VERIFIED", "IMPORT_RESULT_AMBIGUOUS"].includes(after));
      if (!allowed || next.clickAttempts !== 1)
        return { ok: false, reason: "ATTEMPT_1_TRANSITION_REJECTED" };
      await chrome.storage.session.set({ [KEY]: next });
      return { ok: true };
    });
  }
  return false;
});

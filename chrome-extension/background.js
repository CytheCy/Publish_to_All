"use strict";

importScripts("import-session.js", "second-attempt.js");

const KEY = MediumImportSession.KEY;
const SECOND_KEY = MediumSecondAttempt.KEY;
// A future, separately reviewed change must explicitly enable reservation.
const ENABLE_SECOND_ATTEMPT_AUTHORIZATION = false;
let pending = Promise.resolve();

function serialize(task) {
  const result = pending.then(task);
  pending = result.catch(() => {});
  return result;
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  let mediumSender = false;
  try { mediumSender = new URL(sender?.url).origin === "https://medium.com"; }
  catch { /* Reject an unrecognized sender. */ }
  if (!mediumSender) return;
  if (message?.type === "GET_MEDIUM_IMPORT_SESSION") {
    serialize(async () => {
      const loaded = await MediumImportSession.load(chrome.storage.session);
      respond({ ok: loaded.available, record: loaded.record });
    }).catch(() => respond({ ok: false }));
    return true;
  }
  if (message?.type === "GET_MEDIUM_SECOND_ATTEMPT") {
    serialize(async () => {
      const saved = (await chrome.storage.local.get(SECOND_KEY))[SECOND_KEY];
      if (saved !== undefined && !MediumSecondAttempt.valid(saved)) {
        respond({ ok: false, reason: "INVALID_SECOND_ATTEMPT_RECORD" });
        return;
      }
      respond({ ok: true, record: saved || MediumSecondAttempt.empty() });
    }).catch(() => respond({ ok: false }));
    return true;
  }
  if (message?.type === "SET_MEDIUM_SECOND_ATTEMPT") {
    serialize(async () => {
      const next = message.record;
      if (!MediumSecondAttempt.valid(next)) { respond({ ok: false }); return; }
      const saved = (await chrome.storage.local.get(SECOND_KEY))[SECOND_KEY];
      if (saved !== undefined && !MediumSecondAttempt.valid(saved)) {
        respond({ ok: false }); return;
      }
      const current = MediumSecondAttempt.valid(saved) ? saved : MediumSecondAttempt.empty();
      const arm = current.state === "SECOND_ATTEMPT_NOT_AUTHORIZED" &&
        next.state === "SECOND_ATTEMPT_ARMED" && ENABLE_SECOND_ATTEMPT_AUTHORIZATION;
      const observe = current.state === "SECOND_ATTEMPT_ARMED" &&
        next.state === "SECOND_ATTEMPT_USER_CLICK_OBSERVED" &&
        current.documentToken === next.documentToken &&
        current.authorizationTimestamp === next.authorizationTimestamp &&
        next.observedNativeImportClicks === 1;
      const result = current.state === "SECOND_ATTEMPT_USER_CLICK_OBSERVED" &&
        ["SECOND_ATTEMPT_RESULT_VERIFIED", "SECOND_ATTEMPT_RESULT_AMBIGUOUS"]
          .includes(next.state) &&
        current.documentToken === next.documentToken &&
        current.authorizationTimestamp === next.authorizationTimestamp &&
        next.observedNativeImportClicks === 1 &&
        JSON.stringify(current.clickEvidence) === JSON.stringify(next.clickEvidence);
      if (!arm && !observe && !result) { respond({ ok: false }); return; }
      await chrome.storage.local.set({ [SECOND_KEY]: next });
      respond({ ok: true });
    }).catch(() => respond({ ok: false }));
    return true;
  }
  if (message?.type === "SET_MEDIUM_IMPORT_SESSION") {
    serialize(async () => {
      const next = message.record;
      if (!MediumImportSession.valid(next)) { respond({ ok: false }); return; }
      const current = await MediumImportSession.load(chrome.storage.session);
      if (!current.available) { respond({ ok: false }); return; }
      const before = current.record.lifecycleState;
      const after = next.lifecycleState;
      const allowed = before === after ||
        (before === "IMPORT_CLICK_INTENT" &&
          ["IMPORT_CLICKED", "IMPORT_RESULT_AMBIGUOUS"].includes(after)) ||
        (before === "IMPORT_CLICKED" &&
          ["IMPORT_RESULT_VERIFIED", "IMPORT_RESULT_AMBIGUOUS"].includes(after));
      if (!allowed || next.clickAttempts !== 1) { respond({ ok: false }); return; }
      await chrome.storage.session.set({ [KEY]: next });
      respond({ ok: true });
    }).catch(() => respond({ ok: false }));
    return true;
  }
});

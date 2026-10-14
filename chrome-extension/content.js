(function () {
"use strict";

globalThis.__publishToAllMediumContent?.dispose?.();
let disposed = false;
let mediumControlsHost = null;
let titleControlsHost = null;
let bodyControlsHost = null;
let recoveryControlsHost = null;
let mediumFillSession = null;
let secondAttemptRecord = MediumSecondAttempt.empty();
let titleFillResult = null;
let titleFillUsed = false;
let titleFillBusy = false;
let bodyFillResult = null;
let bodyFillUsed = false;
let bodyFillBusy = false;
let recoveryResult = null;
let recoveryUsed = false;
let recoveryBusy = false;
let recoveryVerified = false;
let recoveryStorageLoaded = false;
let publishActionsObserved = 0;
const PUBLISH_OBSERVED_KEY = "publishToAllMediumPublishObserved_fd6c1824c4de";
if (location.pathname === "/p/fd6c1824c4de/edit") {
  try {
    if (globalThis.sessionStorage?.getItem(PUBLISH_OBSERVED_KEY) === "1")
      publishActionsObserved = 1;
  } catch { /* The current-page click observer still applies. */ }
}
const onPublishClick = event => {
  const button = event.target?.closest?.("button, [role='button']");
  if (button && /^Publish(?: story)?$/i.test(button.textContent?.trim() || "")) {
    publishActionsObserved++;
    if (location.pathname === "/p/fd6c1824c4de/edit") {
      try { globalThis.sessionStorage?.setItem(PUBLISH_OBSERVED_KEY, "1"); }
      catch { /* The current-page count still blocks recovery. */ }
    }
  }
};
document.addEventListener?.("click", onPublishClick, true);
const storyTitle = typeof PublishToAllStory === "undefined" ? null : PublishToAllStory;
const storyBody = typeof PublishToAllBody === "undefined" ? null : PublishToAllBody;
const TITLE_EVIDENCE_KEY = "mediumTitleFillEvidenceV1";
const BODY_ATTEMPT_KEY = "mediumBodyFillAttemptV1";
const RECOVERY_KEY = "mediumBodyRecoveryAttemptV1";
async function loadRecoveryProgress() {
  if (!chrome.storage?.local?.get || !storyBody) return;
  try {
    const record = (await chrome.storage.local.get(RECOVERY_KEY))?.[RECOVERY_KEY];
    if (record?.draftId !== "fd6c1824c4de" ||
        record?.fingerprint !== storyBody.fingerprint) return;
    recoveryUsed = true;
    recoveryVerified = record.workflow === "MEDIUM_BODY_RECOVERY_VERIFIED";
    if (!recoveryResult) recoveryResult = record.result || null;
  } catch { /* Fresh DOM guards still apply. */ }
  finally {
    recoveryStorageLoaded = true;
    recoveryControlsHost?.refreshRecoveryPanel?.();
  }
}
async function storeRecovery(result) {
  if (!chrome.storage?.local?.set || !storyBody)
    throw Error("RECOVERY_STORAGE_UNAVAILABLE");
  await chrome.storage.local.set({ [RECOVERY_KEY]: {
    draftId: "fd6c1824c4de", fingerprint: storyBody.fingerprint,
    workflow: result.workflow, result: { ...result }
  } });
}
async function loadBodyAttempt() {
  if (!chrome.storage?.local?.get || !storyBody) return;
  try {
    const record = (await chrome.storage.local.get(BODY_ATTEMPT_KEY))?.[BODY_ATTEMPT_KEY];
    if (record?.draftId === "fd6c1824c4de" &&
        record?.fingerprint === storyBody.fingerprint &&
        location.pathname === "/p/fd6c1824c4de/edit") bodyFillUsed = true;
  } catch { /* Existing authored content still blocks a fresh fill. */ }
}
async function reserveBodyAttempt() {
  if (!chrome.storage?.local?.set || !storyBody)
    throw Error("BODY_ATTEMPT_STORAGE_UNAVAILABLE");
  await chrome.storage.local.set({ [BODY_ATTEMPT_KEY]: {
    draftId: "fd6c1824c4de", fingerprint: storyBody.fingerprint } });
}
async function storedTitleEvidence(inspection) {
  if (!inspection?.titleVerifiedInPersistedDraft || !chrome.storage?.local?.get) return null;
  try {
    const record = (await chrome.storage.local.get(TITLE_EVIDENCE_KEY))?.[TITLE_EVIDENCE_KEY];
    return record?.draftId === inspection.draftId &&
      record?.fingerprint === storyTitle?.fingerprint &&
      record?.result?.workflow === "MEDIUM_TITLE_VERIFIED" ? record.result : null;
  } catch { return null; }
}
async function saveTitleEvidence(result) {
  if (result?.workflow !== "MEDIUM_TITLE_VERIFIED" || !chrome.storage?.local?.set) return;
  const inspection = MediumEditorInspector.inspect(document, location, storyTitle);
  if (!inspection.draftContinuationSafe) return;
  try {
    await chrome.storage.local.set({ [TITLE_EVIDENCE_KEY]: {
      draftId: inspection.draftId, fingerprint: storyTitle.fingerprint, result } });
  } catch { /* Inspection remains available when extension storage is unavailable. */ }
}
function titleReadiness() {
  if (typeof MediumTitleFill === "undefined" ||
      typeof MediumEditorInspector === "undefined")
    return { ready: false, reason: "TITLE_INSPECTOR_UNAVAILABLE",
      workflow: "MEDIUM_TITLE_NOT_FILLED" };
  if (titleFillBusy) return { ready: false, reason: "TITLE_FILL_IN_PROGRESS",
    workflow: "MEDIUM_TITLE_NOT_FILLED" };
  return MediumTitleFill.eligibility(document, MediumEditorInspector, location,
    storyTitle, titleFillUsed);
}
async function performTitleFill() {
  const fresh = titleReadiness();
  if (!fresh.ready) return { ok: false, error: fresh.reason };
  titleFillBusy = true;
  titleControlsHost?.refreshTitlePanel?.();
  try {
    // fill() resolves the editor, title and body again before every mutation.
    const result = await MediumTitleFill.fill(document, MediumEditorInspector,
      location, storyTitle, titleFillUsed);
    if (result.titleFillAttempted) titleFillUsed = true;
    titleFillResult = result;
    await saveTitleEvidence(result);
    return { ok: true, titleFillResult: result };
  } catch {
    titleFillUsed = true;
    return { ok: false, error: "TITLE_FILL_AMBIGUOUS" };
  } finally {
    titleFillBusy = false;
    titleControlsHost?.refreshTitlePanel?.();
  }
}
function bodyReadiness() {
  if (typeof MediumBodyFill === "undefined" ||
      typeof MediumEditorInspector === "undefined")
    return { ready: false, reason: "BODY_INSPECTOR_UNAVAILABLE",
      workflow: "MEDIUM_BODY_NOT_FILLED" };
  if (bodyFillBusy) return { ready: false, reason: "BODY_FILL_IN_PROGRESS",
    workflow: "MEDIUM_BODY_NOT_FILLED" };
  return MediumBodyFill.eligibility(document, MediumEditorInspector, location,
    storyTitle, storyBody, bodyFillUsed, publishActionsObserved > 0);
}
async function performBodyFill() {
  await loadBodyAttempt();
  const fresh = bodyReadiness();
  if (!fresh.ready) return { ok: false, error: fresh.reason };
  bodyFillBusy = true;
  bodyControlsHost?.refreshBodyPanel?.();
  try {
    await reserveBodyAttempt();
    // Reserve before editing so a failed postcheck or reload cannot reopen fill.
    const previouslyUsed = bodyFillUsed;
    bodyFillUsed = true;
    // fill() independently revalidates the route, title, body and source immediately.
    const result = await MediumBodyFill.fill(document, MediumEditorInspector,
      location, storyTitle, storyBody, previouslyUsed, undefined,
      () => publishActionsObserved > 0);
    if (result.bodyFillAttempted) bodyFillUsed = true;
    bodyFillResult = result;
    return { ok: true, bodyFillResult: result };
  } catch {
    return { ok: false, error: "MEDIUM_BODY_FILL_AMBIGUOUS" };
  } finally {
    bodyFillBusy = false;
    bodyControlsHost?.refreshBodyPanel?.();
  }
}
function recoveryReadiness() {
  if (!recoveryStorageLoaded)
    return { ready: false, reason: "RECOVERY_STORAGE_NOT_LOADED",
      failedGuard: "storageLoaded",
      workflow: "MEDIUM_BODY_RECOVERY_NOT_READY" };
  if (typeof MediumBodyRecovery === "undefined" ||
      typeof MediumEditorInspector === "undefined")
    return { ready: false, reason: "RECOVERY_INSPECTOR_UNAVAILABLE",
      failedGuard: "inspectorAvailable",
      workflow: "MEDIUM_BODY_RECOVERY_NOT_READY" };
  return MediumBodyRecovery.eligibility(document, MediumEditorInspector, location,
    storyTitle, storyBody, { used: recoveryUsed, busy: recoveryBusy,
      verified: recoveryVerified, publishObserved: publishActionsObserved > 0 });
}
async function performRecovery() {
  await loadRecoveryProgress();
  const fresh = recoveryReadiness();
  if (!fresh.ready) return { ok: false, error: fresh.reason };
  recoveryBusy = true;
  recoveryControlsHost?.refreshRecoveryPanel?.();
  try {
    await storeRecovery({ workflow: "MEDIUM_BODY_RECOVERY_RUNNING",
      lastVerifiedCanonicalBlock: 0, nextExpectedCanonicalBlock: 1 });
    recoveryUsed = true;
    const result = await MediumBodyRecovery.resume(document, MediumEditorInspector,
      location, storyTitle, storyBody, {
        used: false, publishObserved: () => publishActionsObserved > 0,
        onProgress: async progress => { await storeRecovery(progress); }
      });
    recoveryResult = result;
    recoveryVerified = result.workflow === "MEDIUM_BODY_RECOVERY_VERIFIED";
    await storeRecovery(result);
    return { ok: true, recoveryResult: result };
  } catch {
    recoveryResult = { workflow: "MEDIUM_BODY_RECOVERY_AMBIGUOUS",
      bodyRecoveryFailure: "RECOVERY_STORAGE_OR_HANDLER_FAILED" };
    return { ok: false, error: recoveryResult.bodyRecoveryFailure };
  } finally {
    recoveryBusy = false;
    recoveryControlsHost?.refreshRecoveryPanel?.();
  }
}
function sendWorker(message) {
  return new Promise((resolve, reject) => {
    try {
      const pendingResponse = chrome.runtime.sendMessage(message, response => {
        if (chrome.runtime.lastError) {
          reject(Error("RUNTIME_LAST_ERROR"));
          return;
        }
        if (response === undefined) {
          reject(Error("WORKER_RESPONSE_NOT_RECEIVED"));
          return;
        }
        resolve(response);
      });
      if (pendingResponse?.then) pendingResponse.then(resolve, reject);
    } catch {
      reject(Error("WORKER_MESSAGE_ERROR"));
    }
  });
}
const onStoriesPage = () => /^https:\/\/medium\.com\/me\/stories(?:\/(?:drafts|public))?(?:[?#]|$)/
  .test(location.href);
const attemptStore = {
  async get(key) {
    const response = await chrome.runtime.sendMessage({ type: "GET_MEDIUM_IMPORT_SESSION" });
    if (!response?.ok) throw Error("Import attempt storage unavailable");
    return { [key]: response.record };
  },
  async set(values) {
    const response = await chrome.runtime.sendMessage({
      type: "SET_MEDIUM_IMPORT_SESSION", record: values[MediumImportSession.KEY]
    });
    if (!response?.ok) throw Error("Import attempt storage write refused");
  }
};
const secondAttemptStore = {
  async get() {
    const response = await sendWorker({ type: "INSPECT_MEDIUM_RESERVATION" });
    if (!response?.ok) throw Error(response?.reason || "RESERVATION_INSPECTION_FAILED");
    return response;
  },
  async set(values) {
    const transaction = mediumFillSession?.prepareTransaction;
    if (transaction) transaction.reservationRequestSent = true;
    const response = await sendWorker({ type: "SET_MEDIUM_SECOND_ATTEMPT",
      record: values[MediumSecondAttempt.KEY],
      transactionId: transaction?.transactionId || "not-prepare" });
    if (transaction) {
      transaction.contentResponseReceived = true;
      transaction.background = response?.diagnostic || null;
      transaction.backgroundResponse = response?.ok ? "SUCCESS" :
        response?.diagnostic?.backgroundResponse || "REJECTED";
      transaction.rejectionReason = response?.reason || "None";
    }
    if (!response?.ok) throw Error(response?.reason || "RESERVATION_REJECTED");
    if (JSON.stringify(response.record) !==
        JSON.stringify(values[MediumSecondAttempt.KEY]))
      throw Error("WORKER_RESPONSE_RECORD_MISMATCH");
  }
};

const inspectMessage = (message, _sender, sendResponse) => {
  if (message?.type === "RESUME_VERIFIED_MEDIUM_BODY") {
    (async () => {
      const response = await performRecovery();
      sendResponse({ ...response, recoveryEligibility: recoveryReadiness(),
        recoveryControlDiagnostics: recoveryControlsHost?.getRecoveryDiagnostics?.() || null });
    })();
    return true;
  }
  if (message?.type === "FILL_VERIFIED_MEDIUM_BODY") {
    (async () => {
      const response = await performBodyFill();
      sendResponse({ ...response, bodyEligibility: bodyReadiness(),
        bodyControlDiagnostics: bodyControlsHost?.getBodyDiagnostics?.() || null });
    })();
    return true;
  }
  if (message?.type === "FILL_VERIFIED_MEDIUM_TITLE") {
    (async () => {
      const response = await performTitleFill();
      sendResponse({ ...response, titleEligibility: titleReadiness(),
        titleControlDiagnostics: titleControlsHost?.getTitleDiagnostics?.() || null });
    })();
    return true;
  }
  if (message?.type !== "INSPECT_MEDIUM_PAGE") return;
  (async () => {
  try {
    await loadBodyAttempt();
    await loadRecoveryProgress();
    if (mediumFillSession) {
      try {
        const authoritative = await secondAttemptStore.get();
        mediumFillSession.secondAttempt = authoritative.record;
        mediumFillSession.secondAttemptPersisted = authoritative.exists;
        mediumFillSession.secondAttemptStorageSource = authoritative.source;
        mediumFillSession.backgroundPrepareDiagnostic = authoritative.diagnostic;
        mediumFillSession.secondAttemptStorageAvailable = true;
      } catch (error) {
        mediumFillSession.secondAttemptStorageAvailable = false;
        mediumFillSession.prepareBlocked = true;
        mediumFillSession.prepareLoadFailure = error.message;
      }
    }
    const storiesInspection = onStoriesPage() ?
      await MediumInspector.waitForStoriesHydration(document, location) : null;
    if (disposed) return;
    const inspection = mediumControlsHost?.getImportInspection?.();
    const report = inspection?.report || MediumInspector.inspectDocument(document, location);
    const reconciliation = MediumInspector.inspectReconciliation(document, location,
      storiesInspection);
    const editorInspection = typeof MediumEditorInspector !== "undefined" ?
      MediumEditorInspector.inspect(document, location, storyTitle) : null;
    const bodyReconciliation = editorInspection && storyBody &&
      typeof MediumEditorInspector.reconcileBody === "function" ?
      MediumEditorInspector.reconcileBody(document, editorInspection, storyBody,
        { publishObserved: publishActionsObserved > 0 }) : null;
    if (bodyReconciliation?.partialBodyDetected)
      editorInspection.workflowState = "MEDIUM_BODY_PARTIAL";
    else if (bodyReconciliation?.currentBodyExactCanonicalMatch)
      editorInspection.workflowState = "MEDIUM_BODY_VERIFIED";
    const reportedTitleFillResult = titleFillResult ||
      await storedTitleEvidence(editorInspection);
    const titleEligibility = titleReadiness();
    const titleControlDiagnostics = titleControlsHost?.getTitleDiagnostics?.() || null;
    const bodyEligibility = bodyReadiness();
    const bodyControlDiagnostics = bodyControlsHost?.getBodyDiagnostics?.() || null;
    const recoveryEligibility = recoveryReadiness();
    const recoveryControlDiagnostics =
      recoveryControlsHost?.getRecoveryDiagnostics?.() || null;
    sendResponse({ ok: true, report, reconciliation, editorInspection,
      bodyReconciliation, bodyAttemptRecorded: bodyFillUsed,
      titleEligibility, titleControlDiagnostics, bodyEligibility,
      bodyControlDiagnostics, bodyFillResult, recoveryEligibility,
      recoveryControlDiagnostics, recoveryResult, expectedBody: storyBody ? {
        source: storyBody.source, characterCount: storyBody.characterCount,
        normalizedCharacterCount: storyBody.normalizedCharacterCount,
        fingerprint: storyBody.fingerprint, paragraphCount: storyBody.paragraphCount,
        headingCount: storyBody.headingCount, features: storyBody.features,
        unsupported: storyBody.unsupported } : null,
      titleFillResult: reportedTitleFillResult,
      expectedTitle: storyTitle?.title || null,
      retirement: typeof MediumRetirement !== "undefined" ? MediumRetirement : null,
      fills: mediumFillSession?.fills || 0,
      fillAttemptAvailable: false,
      lastFillResult: mediumFillSession?.lastFillResult || null,
      lastReconciliation: MediumImportSession.KNOWN_RECONCILIATION,
      importState: mediumFillSession?.importState || "IMPORT_RESULT_AMBIGUOUS",
      importClickAttempted: true,
      importDiagnostics: inspection?.diagnostics || null,
      restoredImportSession: mediumFillSession?.restoredImportSession || null,
      persistedLifecycle: mediumFillSession?.restoredImportSession?.state ||
        "IMPORT_RESULT_AMBIGUOUS",
      persistedClickAttempts: mediumFillSession?.restoredImportSession?.clickAttempts ?? 1,
      attempt1History: MediumImportSession.KNOWN_HISTORY,
      secondAttempt: mediumFillSession?.secondAttempt || secondAttemptRecord,
      prepareTransaction: mediumFillSession?.prepareTransaction || null });
  } catch {
    sendResponse({ ok: false, error: "Medium page inspection could not be completed safely." });
  }
  })();
  return true;
};
chrome.runtime.onMessage.addListener(inspectMessage);
globalThis.__publishToAllMediumContent = {
  dispose() {
    disposed = true;
    mediumControlsHost?.disposeImportPanel?.();
    titleControlsHost?.disposeTitlePanel?.();
    titleControlsHost?.remove?.();
    bodyControlsHost?.disposeBodyPanel?.();
    bodyControlsHost?.remove?.();
    recoveryControlsHost?.disposeRecoveryPanel?.();
    recoveryControlsHost?.remove?.();
    document.removeEventListener?.("click", onPublishClick, true);
    chrome.runtime.onMessage.removeListener?.(inspectMessage);
  }
};
titleControlsHost = typeof MediumTitleControls !== "undefined" ?
  MediumTitleControls.mount(document, location, titleReadiness, performTitleFill) : null;
bodyControlsHost = typeof MediumTitleControls !== "undefined" ?
  MediumTitleControls.mountBody(document, location, bodyReadiness, performBodyFill) : null;
recoveryControlsHost = typeof MediumTitleControls !== "undefined" ?
  MediumTitleControls.mountRecovery(document, location, recoveryReadiness,
    performRecovery) : null;
void loadRecoveryProgress();

(async () => {
  // Read the legacy record through the worker. Missing storage is represented by
  // the known history; inspection never migrates or rewrites either attempt.
  let loaded;
  try {
    const response = await sendWorker({ type: "GET_MEDIUM_IMPORT_SESSION" });
    loaded = { record: response.record || MediumImportSession.knownAttempt(),
      available: false, source: response.source || "Known real attempt fallback" };
  } catch {
    loaded = { record: MediumImportSession.knownAttempt(), available: false,
      source: "Known real attempt fallback" };
  }
  if (disposed) return;
  mediumFillSession = MediumImportSession.makeSession(loaded, attemptStore, location);
  mediumFillSession.urlImportRetired = true;
  mediumFillSession.secondAttemptStorageAvailable = false;
  try {
    const second = await secondAttemptStore.get();
    if (second?.ok && MediumSecondAttempt.valid(second.record)) {
      secondAttemptRecord = second.record;
      mediumFillSession.secondAttemptStorageAvailable = true;
      mediumFillSession.secondAttemptPersisted = second.exists;
      mediumFillSession.secondAttemptStorageSource = second.source;
      mediumFillSession.backgroundPrepareDiagnostic = second.diagnostic;
      mediumFillSession.prepareBlocked = second.diagnostic?.transactionId !== "Not observed" &&
        second.diagnostic?.backgroundResponse !== "SUCCESS";
    }
  } catch (error) {
    mediumFillSession.prepareBlocked = true;
    mediumFillSession.prepareLoadFailure = error.message;
  }
  mediumFillSession.secondAttempt = secondAttemptRecord;
  mediumFillSession.secondAttemptStore = secondAttemptStore;
  if (disposed) return;
  mediumControlsHost = !/^https:\/\/medium\.com\/p\/import(?:[?#]|$)/.test(location.href) ? null :
    typeof MediumPageControls !== "undefined" ?
    MediumPageControls.mount(document, location, MediumInspector, mediumFillSession) : null;
})();
})();

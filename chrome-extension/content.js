(function () {
"use strict";

globalThis.__publishToAllMediumContent?.dispose?.();
let disposed = false;
let mediumControlsHost = null;
let mediumFillSession = null;
let secondAttemptRecord = MediumSecondAttempt.empty();
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
  async set(values) {
    const response = await chrome.runtime.sendMessage({ type: "SET_MEDIUM_SECOND_ATTEMPT",
      record: values[MediumSecondAttempt.KEY] });
    if (!response?.ok) throw Error("Second attempt storage write refused");
  }
};

const inspectMessage = (message, _sender, sendResponse) => {
  if (message?.type !== "INSPECT_MEDIUM_PAGE") return;
  (async () => {
  try {
    const storiesInspection = onStoriesPage() ?
      await MediumInspector.waitForStoriesHydration(document, location) : null;
    if (disposed) return;
    const inspection = mediumControlsHost?.getImportInspection?.();
    const report = inspection?.report || MediumInspector.inspectDocument(document, location);
    const reconciliation = MediumInspector.inspectReconciliation(document, location,
      storiesInspection);
    sendResponse({ ok: true, report, reconciliation,
      fills: mediumFillSession?.fills || 0,
      fillAttemptAvailable: mediumFillSession?.fills === 0,
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
      secondAttempt: mediumFillSession?.secondAttempt || secondAttemptRecord });
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
    chrome.runtime.onMessage.removeListener?.(inspectMessage);
  }
};

(async () => {
  const loaded = await MediumImportSession.load(attemptStore);
  if (disposed) return;
  mediumFillSession = MediumImportSession.makeSession(loaded, attemptStore, location);
  mediumFillSession.secondAttemptStorageAvailable = false;
  try {
    const second = await chrome.runtime.sendMessage({ type: "GET_MEDIUM_SECOND_ATTEMPT" });
    if (second?.ok && MediumSecondAttempt.valid(second.record)) {
      secondAttemptRecord = second.record;
      mediumFillSession.secondAttemptStorageAvailable = true;
    }
  } catch { /* Keep the unarmed fallback. */ }
  mediumFillSession.secondAttempt = secondAttemptRecord;
  mediumFillSession.secondAttemptStore = secondAttemptStore;
  if (loaded.available && mediumFillSession.importClicks === 1 &&
      /^https:\/\/medium\.com\/p\/import(?:[?#]|$)/.test(location.href)) {
    // Keep the Import-page diagnostic current without writing during Stories inspection.
    await mediumFillSession.persist();
  }
  if (disposed) return;
  mediumControlsHost = onStoriesPage() ? null :
    typeof MediumPageControls !== "undefined" ?
    MediumPageControls.mount(document, location, MediumInspector, mediumFillSession) : null;
})();
})();

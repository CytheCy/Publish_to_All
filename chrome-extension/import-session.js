(function (root) {
  "use strict";

  const KEY = "publish-to-all:medium-import-attempt:v2";
  const FINGERPRINT = "2a5f676c8743eda141101e793cc4ce988ba6f029d0805c0498271bd072937349";
  const HOSTNAME = "cyporter.substack.com";
  // Reconciled on the live Drafts page. This is historical read-only evidence,
  // separate from the consumed Import attempt and its lifecycle.
  const KNOWN_RECONCILIATION = Object.freeze({ status: "NO_DRAFT_FOUND",
    knownImportAttempts: 1, retryAllowed: false });
  const KNOWN_HISTORY = Object.freeze({ attemptNumber: 1,
    clickAttempts: 1,
    activationMethod: "PROGRAMMATIC_HTMLELEMENT_CLICK",
    reconciliation: "NO_DRAFT_FOUND", lifecycleState: "IMPORT_RESULT_AMBIGUOUS" });
  const STATES = new Set(["IMPORT_CLICK_INTENT", "IMPORT_CLICKED",
    "IMPORT_RESULT_VERIFIED", "IMPORT_RESULT_AMBIGUOUS"]);

  // A real click preceded this version. Chrome clears storage.session on extension
  // reload, so a missing record must never turn this known attempt into a retry.
  // A future Import requires a deliberate code change after reconciliation.
  function knownAttempt() {
    return { sourceFingerprint: FINGERPRINT, sourceHostname: HOSTNAME,
      clickAttempts: 1, lifecycleState: "IMPORT_RESULT_AMBIGUOUS",
      activationMethod: "PROGRAMMATIC_HTMLELEMENT_CLICK",
      reconciliationStatus: "NO_DRAFT_FOUND",
      clickIntentTimestamp: null, startingMediumPath: "/p/import",
      currentMediumPath: "/p/import" };
  }

  function valid(value) {
    return value && typeof value === "object" &&
      value.sourceFingerprint === FINGERPRINT && value.sourceHostname === HOSTNAME &&
      value.clickAttempts === 1 && STATES.has(value.lifecycleState) &&
      value.activationMethod === "PROGRAMMATIC_HTMLELEMENT_CLICK" &&
      value.reconciliationStatus === "NO_DRAFT_FOUND" &&
      (value.clickIntentTimestamp === null ||
        (Number.isSafeInteger(value.clickIntentTimestamp) && value.clickIntentTimestamp > 0)) &&
      value.startingMediumPath === "/p/import" &&
      typeof value.currentMediumPath === "string" &&
      /^\/(?:[a-z0-9@/_-]*)$/i.test(value.currentMediumPath);
  }

  async function load(storage) {
    if (!storage?.get || !storage?.set) {
      return { record: knownAttempt(), available: false, source: "Safety fallback: storage unavailable" };
    }
    try {
      const result = await storage.get(KEY);
      const saved = result?.[KEY];
      if (valid(saved)) return { record: saved, available: true, source: "chrome.storage.session" };
      const record = knownAttempt();
      await storage.set({ [KEY]: record });
      return { record, available: true, source: "Known real attempt safety migration" };
    } catch {
      return { record: knownAttempt(), available: false, source: "Safety fallback: storage error" };
    }
  }

  function makeSession(loaded, storage, location) {
    const record = loaded.record;
    const session = {
      fills: 0, importState: record.lifecycleState, importClicks: record.clickAttempts,
      importStartedAt: record.clickIntentTimestamp || 0, importResultKind: "NONE",
      canPersistImport: loaded.available, restorationBlocked: !loaded.available,
      sourceFingerprint: FINGERPRINT, sourceHostname: HOSTNAME,
      startingMediumPath: record.startingMediumPath,
      restoredImportSession: { state: record.lifecycleState,
        clickAttempts: record.clickAttempts, source: loaded.source },
      async persist() {
        if (!this.canPersistImport) return false;
        let path = "/p/import";
        try { const url = new URL(location.href);
          if (url.origin === "https://medium.com" && /^\/[a-z0-9@/_-]*$/i.test(url.pathname))
            path = url.pathname;
        } catch { /* Keep the known path. */ }
        const next = { sourceFingerprint: FINGERPRINT, sourceHostname: HOSTNAME,
          clickAttempts: this.importClicks, lifecycleState: this.importState,
          activationMethod: "PROGRAMMATIC_HTMLELEMENT_CLICK",
          reconciliationStatus: "NO_DRAFT_FOUND",
          clickIntentTimestamp: this.importStartedAt || null,
          startingMediumPath: this.startingMediumPath, currentMediumPath: path };
        if (!valid(next)) return false;
        try {
          await storage.set({ [KEY]: next });
          this.restoredImportSession = { state: next.lifecycleState,
            clickAttempts: next.clickAttempts, source: "chrome.storage.session" };
          return true;
        }
        catch { return false; }
      }
    };
    return session;
  }

  const api = { KEY, KNOWN_RECONCILIATION, KNOWN_HISTORY,
    knownAttempt, valid, load, makeSession };
  root.MediumImportSession = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(globalThis);

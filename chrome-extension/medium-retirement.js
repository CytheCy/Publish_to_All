(function (root) {
  "use strict";

  // This is the reconciled history for this one source story. It is deliberately
  // separate from the legacy attempt records: attempt 2's storage still says
  // ARMED / 0 observed, while the physical click and empty Drafts reconciliation
  // were observed outside that failed bookkeeping transition.
  const SOURCE_URL = "https://cyporter.substack.com/p/saturation-of-artificial-intelligence-f8d";
  const history = Object.freeze({
    state: "MEDIUM_URL_IMPORT_RETIRED",
    sourceUrl: SOURCE_URL,
    urlImportRetired: true,
    retryAllowed: false,
    knownRealImportAttempts: 2,
    attempt1: Object.freeze({ activation: "PROGRAMMATIC_NATIVE_BUTTON",
      clickTrusted: false, result: "NO_DRAFT_FOUND",
      evidence: "Legacy attempt-1 record and Drafts reconciliation" }),
    attempt2: Object.freeze({ activation: "TRUSTED_MANUAL_NATIVE_BUTTON",
      nativeImportClickObserved: true, clickTrusted: true,
      buttonIdentityValid: true, result: "NO_DRAFT_FOUND",
      draftsSelected: true, draftListConclusive: true, draftListEmpty: true,
      visibleDraftCandidates: 0, exactTitleMatches: 0,
      evidence: "Observed trusted physical click and fresh empty Drafts reconciliation; not legacy reservation storage" }),
    legacyAttempt2: Object.freeze({ authorization: "ARMED", observedClicks: 0,
      result: "PENDING", meaning: "Stored legacy reservation only" }),
    editorWorkflow: "MEDIUM_EDITOR_NOT_INSPECTED",
    canonicalTarget: SOURCE_URL
  });

  root.MediumRetirement = history;
  if (typeof module === "object" && module.exports) module.exports = history;
})(globalThis);

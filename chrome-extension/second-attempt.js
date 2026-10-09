(function (root) {
  "use strict";

  const KEY = "publish-to-all:medium-second-attempt:v1";
  const FINGERPRINT = "2a5f676c8743eda141101e793cc4ce988ba6f029d0805c0498271bd072937349";
  const HOSTNAME = "cyporter.substack.com";
  const IMPORT_URL = "https://medium.com/p/import";
  const empty = () => ({ state: "SECOND_ATTEMPT_NOT_AUTHORIZED",
    authorizedAttemptNumber: null, observedNativeImportClicks: 0,
    sourceFingerprint: null, sourceHostname: null, fillSynchronization: null,
    attempt1Reconciliation: "NO_DRAFT_FOUND", authorizationTimestamp: null,
    documentToken: null, nativeClickObserved: false, clickEvidence: null });

  function valid(record) {
    if (!record || typeof record !== "object" ||
        !["SECOND_ATTEMPT_NOT_AUTHORIZED", "SECOND_ATTEMPT_ARMED",
          "SECOND_ATTEMPT_USER_CLICK_OBSERVED", "SECOND_ATTEMPT_RESULT_VERIFIED",
          "SECOND_ATTEMPT_RESULT_AMBIGUOUS"].includes(record.state) ||
        record.attempt1Reconciliation !== "NO_DRAFT_FOUND" ||
        !Number.isSafeInteger(record.observedNativeImportClicks) ||
        record.observedNativeImportClicks < 0 || record.observedNativeImportClicks > 1) return false;
    if (record.state === "SECOND_ATTEMPT_NOT_AUTHORIZED")
      return record.authorizedAttemptNumber === null &&
        record.observedNativeImportClicks === 0 && !record.nativeClickObserved;
    return record.authorizedAttemptNumber === 2 &&
      record.sourceFingerprint === FINGERPRINT && record.sourceHostname === HOSTNAME &&
      record.fillSynchronization === "VERIFIED" &&
      Number.isSafeInteger(record.authorizationTimestamp) &&
      record.authorizationTimestamp > 0 && typeof record.documentToken === "string" &&
      record.documentToken.length >= 16 &&
      (record.state === "SECOND_ATTEMPT_ARMED" ?
        record.observedNativeImportClicks === 0 && !record.nativeClickObserved :
        record.observedNativeImportClicks === 1 && record.nativeClickObserved &&
          record.clickEvidence?.isTrusted === true &&
          record.clickEvidence?.buttonIdentityValid === true &&
          record.clickEvidence?.authorizationPresent === true);
  }

  function eligibility({ report, raw, reconciliation, fill, attempt1, location,
    record, documentToken }) {
    const fail = reason => ({ state: "SECOND_ATTEMPT_NOT_READY", reason });
    if (attempt1?.attemptNumber !== 1 || attempt1?.clickAttempts !== 1 ||
        attempt1?.lifecycleState !== "IMPORT_RESULT_AMBIGUOUS" ||
        attempt1?.activationMethod !== "PROGRAMMATIC_HTMLELEMENT_CLICK")
      return fail("ATTEMPT_1_HISTORY_MISMATCH");
    if (attempt1?.reconciliation !== "NO_DRAFT_FOUND")
      return fail("ATTEMPT_1_RECONCILIATION_REQUIRED");
    if (location?.href !== IMPORT_URL || report?.page !== IMPORT_URL)
      return fail("IMPORT_PAGE_MISMATCH");
    if (report.importInterface?.headingVerified !== true ||
        report.importInterface?.matchingHeadingCount !== 1 ||
        report.importInterface?.matchingSourceEditorCount !== 1)
      return fail("IMPORT_PAGE_IDENTITY_MISMATCH");
    if (fill?.fillSynchronizationStatus !== "VERIFIED" ||
        fill?.exactUrlPresentAfterward !== true || fill?.fingerprintMatch !== true)
      return fail("FILL_SYNCHRONIZATION_NOT_VERIFIED");
    if (report.importInterface?.sourceUrlInput?.editorState !==
        "EXACT_SOURCE_URL_PRESENT" ||
        report.importInterface?.sourceUrlInput?.existingUrl?.matchesIntendedSourceExactly !== true ||
        report.importInterface?.sourceUrlInput?.existingUrl?.safeUrlFingerprint !== FINGERPRINT ||
        raw?.sourceGuard !== true || raw?.source?.exactMatch !== true ||
        raw?.source?.fingerprintMatch !== true || !raw?.targets?.editor)
      return fail("SOURCE_IDENTITY_MISMATCH");
    if (report.importInterface?.matchingImportActionCount !== 1 ||
        raw?.action?.candidateCount !== 1 || raw?.actionGuard !== true ||
        raw?.action?.identityMatchesInspection !== true || !raw?.targets?.action)
      return fail("NATIVE_IMPORT_IDENTITY_MISMATCH");
    if (reconciliation?.validation?.detected ||
        reconciliation?.importPage?.draftEditLink ||
        reconciliation?.importPage?.seeYourStory ||
        reconciliation?.importPage?.confirmation !== "Not observed" ||
        reconciliation?.importPage?.error !== "Not observed")
      return fail("EXISTING_RESULT_OR_VALIDATION");
    if (record?.state !== "SECOND_ATTEMPT_NOT_AUTHORIZED" ||
        record?.observedNativeImportClicks !== 0)
      return fail("SECOND_ATTEMPT_ALREADY_RESERVED_OR_CONSUMED");
    if (typeof documentToken !== "string" || documentToken.length < 16)
      return fail("DOCUMENT_IDENTITY_UNAVAILABLE");
    return { state: "SECOND_ATTEMPT_READY", reason: "None",
      targets: raw.targets };
  }

  async function reserve({ storage, record, readiness, documentToken, now = Date.now() }) {
    if (!valid(record) || readiness?.state !== "SECOND_ATTEMPT_READY" ||
        record.state !== "SECOND_ATTEMPT_NOT_AUTHORIZED" ||
        !Number.isSafeInteger(now) || now <= 0) return null;
    const next = { state: "SECOND_ATTEMPT_ARMED", authorizedAttemptNumber: 2,
      observedNativeImportClicks: 0, sourceFingerprint: FINGERPRINT,
      sourceHostname: HOSTNAME, fillSynchronization: "VERIFIED",
      attempt1Reconciliation: "NO_DRAFT_FOUND", authorizationTimestamp: now,
      documentToken, nativeClickObserved: false, clickEvidence: null };
    if (!valid(next)) return null;
    await storage.set({ [KEY]: next });
    return next;
  }

  function classifyClick({ event, action, freshAction, record, documentToken,
    sourceStillValid, fillStillVerified }) {
    const buttonIdentityValid = Boolean(action && freshAction === action &&
      (event?.target === action || action.contains?.(event?.target)));
    const authorizationPresent = record?.state === "SECOND_ATTEMPT_ARMED" &&
      record?.documentToken === documentToken &&
      record?.observedNativeImportClicks === 0;
    const evidence = { nativeImportClickObserved: true,
      isTrusted: event?.isTrusted === true, buttonIdentityValid,
      authorizationPresent };
    const authorized = evidence.isTrusted && buttonIdentityValid &&
      authorizationPresent && sourceStillValid === true &&
      fillStillVerified === true;
    return { evidence, authorized, reason: authorized ? "None" :
      !authorizationPresent ? "NOT_ARMED_OR_ALREADY_CONSUMED" :
        !evidence.isTrusted ? "UNTRUSTED_CLICK" :
          !buttonIdentityValid ? "NATIVE_IMPORT_IDENTITY_CHANGED" :
            !sourceStillValid ? "SOURCE_CHANGED" : "FILL_NOT_VERIFIED" };
  }

  async function recordClick({ storage, record, classification }) {
    if (!classification?.authorized || !valid(record) ||
        record.state !== "SECOND_ATTEMPT_ARMED") return null;
    const next = { ...record, state: "SECOND_ATTEMPT_USER_CLICK_OBSERVED",
      observedNativeImportClicks: 1, nativeClickObserved: true,
      clickEvidence: classification.evidence };
    await storage.set({ [KEY]: next });
    return next;
  }

  async function recordResult({ storage, record, verified }) {
    if (!valid(record) || record.state !== "SECOND_ATTEMPT_USER_CLICK_OBSERVED" ||
        typeof verified !== "boolean") return null;
    const next = { ...record, state: verified ?
      "SECOND_ATTEMPT_RESULT_VERIFIED" : "SECOND_ATTEMPT_RESULT_AMBIGUOUS" };
    await storage.set({ [KEY]: next });
    return next;
  }

  const api = { KEY, empty, valid, eligibility, reserve, classifyClick,
    recordClick, recordResult };
  root.MediumSecondAttempt = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(globalThis);

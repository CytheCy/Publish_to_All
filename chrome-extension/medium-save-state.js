(function (root) {
  "use strict";
  const STATES = Object.freeze({ UNKNOWN: "MEDIUM_SAVE_UNKNOWN",
    PENDING: "MEDIUM_SAVE_PENDING", ERROR: "MEDIUM_SAVE_ERROR_VISIBLE",
    INDICATED: "MEDIUM_SAVE_INDICATED",
    PERSISTENCE_VERIFIED: "MEDIUM_PERSISTENCE_VERIFIED" });
  const clean = value => String(value || "").replace(/\s+/g, " ").trim();
  const short = (value, limit = 160) => clean(value).slice(0, limit);
  const attr = (node, name) => node?.getAttribute?.(name) || "";
  const metadata = node => [attr(node, "role"), attr(node, "aria-label"),
    attr(node, "data-testid"), typeof node?.className === "string" ? node.className : ""
  ].join(" ").toLowerCase();
  const visible = (node, doc) => {
    if (!node || !node.isConnected || node.hidden) return false;
    for (let current = node; current; current = current.parentElement) {
      if (current.hidden || attr(current, "aria-hidden") === "true") return false;
      const style = doc.defaultView?.getComputedStyle?.(current);
      if (style && (style.display === "none" || style.visibility === "hidden" ||
          style.visibility === "collapse")) return false;
    }
    return !node.getClientRects || node.getClientRects().length > 0;
  };
  const errorWords = /cannot save|can’t save|can't save|could not save|unable to save|save (?:failed|error)|saving (?:failed|error)|something is wrong.*save/i;
  const indicatorWords = /^(?:saved|saving(?:…|\.\.\.)?|draft saved|all changes saved)$/i;
  function describe(node, doc) {
    const raw = clean(node.innerText || node.textContent);
    const ancestor = [node.parentElement, node.parentElement?.parentElement,
      node.parentElement?.parentElement?.parentElement]
      .find(item => /alert|status|error|warning|toast|notification|save/i.test(metadata(item)));
    return { tag: node.tagName?.toLowerCase() || "", role: attr(node, "role"),
      classes: short(typeof node.className === "string" ? node.className : "", 120),
      dataTestId: short(attr(node, "data-testid"), 80), visible: visible(node, doc),
      normalizedTextLength: raw.length, safeText: short(raw),
      nearestRelevantAncestor: ancestor ? { tag: ancestor.tagName?.toLowerCase() || "",
        role: short(attr(ancestor, "role"), 40),
        classes: short(typeof ancestor.className === "string" ? ancestor.className : "", 120),
        dataTestId: short(attr(ancestor, "data-testid"), 80) } : null };
  }
  function inspect(doc, reconciliation) {
    const nodes = Array.from(doc?.querySelectorAll?.(
      "[role='alert'], [role='status'], [aria-live], [aria-label], [data-testid], [data-test-id], [class*='error'], [class*='Error'], [class*='warning'], [class*='Warning'], [class*='toast'], [class*='Toast'], [style*='red']") || []);
    let warningCount = 0, indicator = null;
    const warnings = [];
    for (const node of nodes) {
      if (node.closest?.("[contenteditable='true'], [contenteditable='plaintext-only']") ||
          !visible(node, doc)) continue;
      const raw = clean(node.innerText || node.textContent);
      if (!raw || raw.length > 600) continue;
      const hint = metadata(node);
      const semantic = /alert|assertive|error|warning|toast|notification/i.test(hint);
      const warning = errorWords.test(raw) || semantic &&
        /sav(?:e|ing)|story|draft/i.test(raw) && /wrong|fail|error|unable|cannot|can't|can’t/i.test(raw);
      if (warning) {
        warningCount++;
        if (warnings.length < 5) warnings.push(describe(node, doc));
      } else if (!indicator && indicatorWords.test(raw) &&
          /status|polite|save|draft/i.test(hint)) indicator = describe(node, doc);
    }
    const domBodyVerified = Boolean(reconciliation?.currentBodyExactCanonicalMatch);
    const state = warningCount ? STATES.ERROR :
      indicator ? /^saving/i.test(indicator.safeText) ? STATES.PENDING :
        STATES.INDICATED : STATES.UNKNOWN;
    return { mediumSaveState: state, mediumSaveErrorVisible: warningCount > 0,
      mediumSaveErrorText: warnings[0]?.safeText || "Not observed",
      mediumSaveWarningCandidateCount: warningCount,
      mediumSaveWarningCandidates: warnings,
      mediumSaveIndicatorVisible: Boolean(indicator),
      mediumSaveIndicatorText: indicator?.safeText || "Not observed",
      domBodyVerified, persistenceVerified: false,
      persistenceVerificationBasis: "No independent read of server-stored draft body",
      safeToLeaveReloadPage: warningCount || domBodyVerified ? "No" : "UNKNOWN",
      overallMediumDraftState: domBodyVerified ?
        "BODY_DOM_VERIFIED_SAVE_UNVERIFIED" : "BODY_DOM_UNVERIFIED_SAVE_UNVERIFIED",
      currentTabProtection: domBodyVerified ?
        "CURRENT TAB CONTAINS DOM-VERIFIED BODY WITH UNVERIFIED SAVE STATE\nDO NOT REFRESH\nDO NOT CLOSE\nDO NOT NAVIGATE\nDO NOT RETRY BODY RECOVERY" : null };
  }
  function observe(doc) {
    const records = [];
    const limit = 80;
    let lastWarning = null, lastIndicator = null;
    const record = item => {
      records.push({ ...item, timestampMs: Date.now() });
      if (records.length > limit) records.splice(0, records.length - limit);
    };
    const snapshot = () => {
      const state = inspect(doc);
      if (lastWarning !== null && lastWarning !== state.mediumSaveErrorVisible) {
        record({ type: state.mediumSaveErrorVisible ? "save-warning-appeared" :
          "save-warning-disappeared" });
      }
      lastWarning = state.mediumSaveErrorVisible;
      const nextIndicator = state.mediumSaveIndicatorText;
      if (lastIndicator !== null && lastIndicator !== nextIndicator) {
        record({ type: "save-indicator-changed", text: nextIndicator });
      }
      lastIndicator = nextIndicator;
    };
    const onEvent = event => {
      const target = event.target;
      if (!target?.closest?.("[contenteditable='true'], [contenteditable='plaintext-only']"))
        return;
      record({ type: event.type, inputType: short(event.inputType || "", 40),
        isTrusted: Boolean(event.isTrusted) });
    };
    for (const type of ["beforeinput", "input", "change"])
      doc.addEventListener?.(type, onEvent, true);
    const Observer = doc.defaultView?.MutationObserver;
    const observer = Observer ? new Observer(changes => {
      let editorChanges = 0;
      for (const change of changes) if (change.target?.closest?.(
        "[contenteditable='true'], [contenteditable='plaintext-only']")) editorChanges++;
      if (editorChanges) record({ type: "editor-dom-mutation", count: editorChanges });
      snapshot();
    }) : null;
    if (observer && doc.documentElement) observer.observe(doc.documentElement,
      { subtree: true, childList: true, characterData: true,
        attributes: true, attributeFilter: ["class", "style", "hidden", "aria-hidden"] });
    snapshot();
    return { read: () => records.slice(), dispose() {
      observer?.disconnect();
      for (const type of ["beforeinput", "input", "change"])
        doc.removeEventListener?.(type, onEvent, true);
    } };
  }
  const api = { STATES, inspect, observe };
  root.MediumSaveState = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(globalThis);

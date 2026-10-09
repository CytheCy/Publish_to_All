(function (root) {
  "use strict";

  const HOST_ID = "publish-to-all-medium-controls";
  const IMPORT_URL = "https://medium.com/p/import";
  const HEADING = "See your story on Medium";
  const SOURCE_FINGERPRINT =
    "2a5f676c8743eda141101e793cc4ce988ba6f029d0805c0498271bd072937349";
  const RESULT_WAIT_MS = 30000;
  const ENABLE_PREPARE_SECOND_ATTEMPT = false;
  const secondAttempt = root.MediumSecondAttempt ||
    (typeof require === "function" ? require("./second-attempt.js") : null);

  function onImportPage(location) {
    try {
      const url = new URL(location.href);
      return url.origin === "https://medium.com" && url.pathname === "/p/import";
    } catch {
      return false;
    }
  }

  function eligibility(report, session, location) {
    const view = report?.importInterface;
    if (!onImportPage(location) || report?.page !== IMPORT_URL) {
      return { ready: false, reason: "Wrong page" };
    }
    if (session.fills !== 0) return { ready: false, reason: "Fill attempt already used" };
    if (!view?.headingVerified || view.heading !== HEADING) {
      return { ready: false, reason: "Import heading not verified" };
    }
    if (view.sourceStatus !== "READY" || !view.found || !view.fillAllowed ||
        view.fillDisabledReason !== "None" ||
        view.sourceUrlInput?.editorState !== "MEDIUM_DEFAULT_EXAMPLE_URL" ||
        view.sourceUrlInput?.observedDefaultFingerprintMatches !== true ||
        view.sourceUrlInput?.fillSafe !== true ||
        view.importAction?.enabled !== true) {
      return { ready: false, reason: view.fillDisabledReason &&
        view.fillDisabledReason !== "None" ? view.fillDisabledReason :
        "Verified default source and Import action required" };
    }
    return { ready: true, reason: "None" };
  }

  function importEligibility(report, session, location, navigatingAway) {
    const view = report?.importInterface;
    const input = view?.sourceUrlInput;
    const url = input?.existingUrl;
    const action = view?.importAction;
    if (![undefined, "IMPORT_NOT_STARTED", "IMPORT_READY", "IMPORT_FAILED_BEFORE_CLICK"]
      .includes(session.importState) ||
        (session.importClicks || 0) !== 0) {
      return { ready: false, reason: "Import attempt already used" };
    }
    if (navigatingAway || location.href !== IMPORT_URL || report?.page !== IMPORT_URL) {
      return { ready: false, reason: "Import page is changing or has changed" };
    }
    if (session.restorationBlocked) {
      return { ready: false, reason: "Stored Import attempt state is invalid" };
    }
    if (session.canPersistImport === false) {
      return { ready: false, reason: "Import attempt state cannot be saved for navigation" };
    }
    if (view?.matchingHeadingCount !== 1 || view.heading !== HEADING ||
        view.headingVerified !== true) {
      return { ready: false, reason: "Import heading is not unique and verified" };
    }
    if (view.matchingSourceEditorCount !== 1) {
      return { ready: false, reason: "SOURCE_EDITOR_COUNT_MISMATCH" };
    }
    if (input?.editorState !== "EXACT_SOURCE_URL_PRESENT" ||
        input.entireContentOneUrl !== true) {
      return { ready: false, reason: "SOURCE_EDITOR_STATE_MISMATCH: source editor differs" };
    }
    if (url?.matchesIntendedSourceExactly !== true) {
      return { ready: false, reason: "SOURCE_RAW_NORMALIZATION_MISMATCH" };
    }
    if (url.safeUrlFingerprint !== SOURCE_FINGERPRINT) {
      return { ready: false, reason: "SOURCE_RAW_FINGERPRINT_MISMATCH" };
    }
    if (
        url.scheme !== "https" || url.hostname !== "cyporter.substack.com" ||
        url.pathShape !== "/p/[segment]" || url.queryPresent || url.fragmentPresent ||
        url.portPresent || url.credentialsPresent) {
      return { ready: false, reason: "SOURCE_URL_SHAPE_MISMATCH" };
    }
    if (view.matchingImportActionCount !== 1) return { ready: false,
      reason: "IMPORT_ACTION_COUNT_MISMATCH" };
    if (!view.found || action?.tag !== "button") return { ready: false,
      reason: "IMPORT_ACTION_TAG_MISMATCH" };
    if (action.role !== "button") return { ready: false,
      reason: "IMPORT_ACTION_ROLE_MISMATCH" };
    if (action.type !== "submit" || action.typeAttributeValid === false) return { ready: false,
      reason: "IMPORT_ACTION_TYPE_MISMATCH" };
    if (action.visibleText !== "Import") return { ready: false,
      reason: "IMPORT_ACTION_TEXT_MISMATCH" };
    if (action.accessibleName !== "Import") return { ready: false,
      reason: "IMPORT_ACTION_ACCESSIBLE_NAME_MISMATCH" };
    if (action.visible === false) return { ready: false,
      reason: "IMPORT_ACTION_NOT_VISIBLE" };
    if (action.enabled !== true) return { ready: false,
      reason: "IMPORT_ACTION_DISABLED" };
    if (action.connected === false) return { ready: false,
      reason: "IMPORT_ACTION_STALE" };
    return { ready: true, reason: "None" };
  }

  // Both rendering and activation use this decision. The report checks the
  // page signature; the live checks pin it to the raw DOM and reject a result
  // that was already present before any extension-owned Import activation.
  function canOfferVerifiedImport(report, session, location, inspector, doc,
      navigatingAway = false) {
    const raw = inspector && doc ? inspector.inspectImportRaw(doc, location, report) : null;
    const guard = importEligibility(report, session, location, navigatingAway);
    if (!guard.ready) return { allowed: false,
      reason: /^(SOURCE_|IMPORT_ACTION_)/.test(guard.reason) && raw?.reason !== "None" ?
        raw.reason : guard.reason, targets: null, raw };
    if (!inspector || !doc) return { allowed: false,
      reason: "Live Import targets cannot be checked", targets: null, raw };
    if (!raw.targets) return { allowed: false,
      reason: raw.reason, targets: null, raw };
    if (inspector.inspectImportResult(doc, location).verified ||
        inspector.inspectReconciliation(doc, location).importPage.draftEditLink) return {
      allowed: false, reason: "Draft result was already present before Import", targets: null, raw
    };
    return { allowed: true, reason: "None", targets: raw.targets, raw };
  }

  function secondAttemptReadiness(report, session, location, inspector, doc,
      record, documentToken) {
    if (session.secondAttemptStorageAvailable === false) return {
      state: "SECOND_ATTEMPT_NOT_READY", reason: "SECOND_ATTEMPT_STORAGE_UNAVAILABLE" };
    if (!inspector || !doc || !report) return {
      state: "SECOND_ATTEMPT_NOT_READY", reason: "PAGE_INSPECTION_UNAVAILABLE" };
    const raw = inspector.inspectImportRaw(doc, location, report);
    const reconciliation = inspector.inspectReconciliation(doc, location);
    if (inspector.inspectImportResult(doc, location).verified) return {
      state: "SECOND_ATTEMPT_NOT_READY", reason: "EXISTING_DRAFT_RESULT" };
    return secondAttempt.eligibility({ report, raw, reconciliation,
      fill: session.lastFillResult,
      attempt1: { ...(root.MediumImportSession?.KNOWN_HISTORY || {
        attemptNumber: 1,
        activationMethod: "PROGRAMMATIC_HTMLELEMENT_CLICK",
        reconciliation: "NO_DRAFT_FOUND" }), clickAttempts: session.importClicks,
        lifecycleState: session.importState },
      location, record, documentToken });
  }

  function importButtonDomState(doc, shadow, host, button) {
    const attribute = name => button.getAttribute?.(name) ?? null;
    const view = doc.defaultView;
    const style = view?.getComputedStyle?.(button);
    const parentStyle = button.parentElement ?
      view?.getComputedStyle?.(button.parentElement) : null;
    const hostStyle = view?.getComputedStyle?.(host);
    const disabledCssClass = [button, button.parentElement].filter(Boolean).some(node =>
      node.classList?.contains?.("disabled") ||
      /(?:^|\s)disabled(?:\s|$)/.test(node.className || ""));
    const rect = button.getBoundingClientRect?.();
    const center = rect && Number.isFinite(rect.left) && Number.isFinite(rect.top) ?
      { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 } : null;
    const shadowHit = center ? shadow.elementFromPoint?.(center.x, center.y) : null;
    const documentHit = center ? doc.elementFromPoint?.(center.x, center.y) : null;
    const hitButton = shadowHit === button || Boolean(button.contains?.(shadowHit));
    let hitTest = "UNAVAILABLE";
    if (documentHit && documentHit !== host && documentHit !== button &&
        !shadow.contains?.(documentHit)) {
      hitTest = `blocked by ${documentHit.tagName || "unknown element"}`;
    } else if (shadowHit) hitTest = hitButton ? "BUTTON" :
      `blocked by ${shadowHit.tagName || "unknown element"}`;
    else if (documentHit === button) hitTest = "BUTTON";
    else if (center && !documentHit &&
        (shadow.elementFromPoint || doc.elementFromPoint)) {
      hitTest = "blocked by viewport or hidden element";
    }
    const ancestors = [button, button.parentElement, host].filter(Boolean);
    const blockingAncestor = ancestors.find(node =>
      node.hidden || node.getAttribute?.("aria-disabled") === "true" ||
      view?.getComputedStyle?.(node)?.pointerEvents === "none");
    const blockingReason = button.disabled ? "disabled property is true" :
      attribute("disabled") !== null ? "disabled attribute is present" :
        attribute("aria-disabled") === "true" ? "aria-disabled is true" :
          button.hidden ? "button is hidden" :
            style?.pointerEvents === "none" ? "button pointer-events is none" :
              blockingAncestor ? "button or parent blocks interaction" :
                rect && (!rect.width || !rect.height) ? "button has no visible bounding box" :
                  disabledCssClass ? "disabled CSS class is present" :
                  hitTest !== "BUTTON" && hitTest !== "UNAVAILABLE" ? hitTest :
                    hitTest === "UNAVAILABLE" || !style ?
                      "button interaction could not be verified" : "None";
    return {
      disabledProperty: Boolean(button.disabled),
      disabledAttribute: attribute("disabled") !== null,
      ariaDisabled: attribute("aria-disabled") ?? "None",
      pointerEvents: style?.pointerEvents ?? "Unavailable",
      cursor: style?.cursor ?? "Unavailable",
      parentPointerEvents: parentStyle?.pointerEvents ?? "Unavailable",
      hostPointerEvents: hostStyle?.pointerEvents ?? "Unavailable",
      hostDisplay: hostStyle?.display ?? "Unavailable",
      hostZIndex: hostStyle?.zIndex ?? "Unavailable",
      disabledCssClass,
      boundingBox: rect ? `${Math.round(rect.left)}, ${Math.round(rect.top)}, ` +
        `${Math.round(rect.width)} x ${Math.round(rect.height)}` : "Unavailable",
      hitTest,
      blockingReason,
      interactability: blockingReason === "None" ? "ENABLED" : "BLOCKED"
    };
  }

  function mount(doc, location, inspector, session) {
    if (!session.importState) session.importState = "IMPORT_NOT_STARTED";
    if (/^https:\/\/medium\.com\/me\/stories(?:\/(?:drafts|public))?(?:[?#]|$)/
      .test(location.href)) return null;
    if ((!onImportPage(location) && !(session.importState || "").startsWith("IMPORT_CLICK") &&
        session.importState !== "IMPORT_RESULT_AMBIGUOUS" &&
        session.importState !== "IMPORT_RESULT_VERIFIED") || !doc.body || !doc.createElement) return null;
    const existing = doc.getElementById(HOST_ID);
    if (existing?.importPanelSession === session) return existing;
    existing?.disposeImportPanel?.();
    existing?.remove?.();

    const host = doc.createElement("div");
    host.id = HOST_ID;
    const shadow = host.attachShadow({ mode: "open" });
    const style = doc.createElement("style");
    style.textContent = `
      :host { all: initial; position: fixed; left: 24px; bottom: 24px;
        z-index: 2147483647; font: 13px/1.45 system-ui, sans-serif;
        color-scheme: light; pointer-events: auto; }
      .panel { box-sizing: border-box; width: 286px; max-width: calc(100vw - 48px);
        padding: 14px; border: 1px solid #9cbbdb; border-radius: 10px;
        background: #f5f9ff; color: #172b46; box-shadow: 0 4px 18px #172b462e; }
      h2 { margin: 0 0 8px; font: 700 15px/1.3 system-ui, sans-serif; }
      p { margin: 4px 0; }
      button { box-sizing: border-box; width: 100%; margin-top: 10px; padding: 9px;
        border: 0; border-radius: 6px; background: #244b78; color: white;
        font: 600 13px system-ui, sans-serif; cursor: pointer; pointer-events: auto; }
      button:disabled { opacity: .55; cursor: not-allowed; }
      .import-button { background: #8b3b21; border: 2px solid #f0b597; }
      .detail { margin-top: 8px; overflow-wrap: anywhere; }
    `;
    const panel = doc.createElement("section");
    panel.className = "panel";
    panel.setAttribute("aria-label", "Publish to All Medium controls");
    const title = doc.createElement("h2");
    title.textContent = "Publish to All";
    const message = doc.createElement("p");
    message.setAttribute("role", "status");
    const details = doc.createElement("div");
    details.className = "detail";
    const button = doc.createElement("button");
    button.type = "button";
    button.textContent = "Fill verified Substack URL";
    button.disabled = true;
    const importButton = doc.createElement("button");
    importButton.type = "button";
    importButton.className = "import-button";
    importButton.textContent = "Prepare one manual Import attempt";
    importButton.disabled = true;
    panel.append(title, message, details, button, importButton);
    shadow.append(style, panel);
    doc.body.append(host);
    host.importPanelSession = session;

    let lastAction = "NONE";
    let actionReason = "None";
    let exactVerified = false;
    let busy = false;
    let observer;
    let scheduled = false;
    let navigatingAway = false;
    let resultTimer;
    let lastImportDiagnostics;
    let lastImportReport;
    const documentToken = root.crypto?.randomUUID?.() ||
      `${Date.now()}-${Math.random()}-${Math.random()}`;
    session.secondAttempt ||= secondAttempt.empty();
    let secondReadiness = { state: "SECOND_ATTEMPT_NOT_READY",
      reason: "PAGE_INSPECTION_UNAVAILABLE" };
    let nativeClickEvidence = null;
    let nativeClickClassification = null;
    let armedNativeTarget = null;

    function setImportState(state) {
      session.importState = state;
      const saved = ["IMPORT_CLICK_INTENT", "IMPORT_CLICKED",
        "IMPORT_RESULT_VERIFIED", "IMPORT_RESULT_AMBIGUOUS"].includes(state) ?
        (session.persist?.() ?? true) : true;
      if (["IMPORT_RESULT_VERIFIED", "IMPORT_RESULT_AMBIGUOUS"].includes(state)) {
        observer?.disconnect();
        if (resultTimer) clearTimeout(resultTimer);
      }
      return saved;
    }

    function line(label, value) {
      const row = doc.createElement("p");
      row.textContent = `${label}: ${value}`;
      details.append(row);
    }

    function keepMediumImportVisible() {
      if (!host.style || !host.getBoundingClientRect) return;
      const target = Array.from(doc.querySelectorAll("button")).find(element =>
        element.textContent?.trim() === "Import" && element.getBoundingClientRect);
      if (!target) return;
      const overlaps = () => {
        const panelRect = host.getBoundingClientRect();
        const importRect = target.getBoundingClientRect();
        return panelRect.left < importRect.right && panelRect.right > importRect.left &&
          panelRect.top < importRect.bottom && panelRect.bottom > importRect.top;
      };
      if (!overlaps()) return;
      for (const [left, right, top, bottom] of [
        ["24px", "auto", "24px", "auto"],
        ["auto", "24px", "24px", "auto"],
        ["auto", "24px", "auto", "24px"]
      ]) {
        Object.assign(host.style, { left, right, top, bottom });
        if (!overlaps()) return;
      }
      host.style.display = "none";
    }

    function refresh() {
      scheduled = false;
      if (!onImportPage(location) && session.importState === "IMPORT_NOT_STARTED") {
        observer?.disconnect();
        host.remove();
        return;
      }
      let guard;
      let importGuard;
      let report;
      try {
        if (onImportPage(location)) {
          report = inspector.inspectDocument(doc, location);
          guard = eligibility(report, session, location);
          importGuard = canOfferVerifiedImport(report, session, location,
            inspector, doc, navigatingAway);
          secondReadiness = secondAttemptReadiness(report, session, location,
            inspector, doc, session.secondAttempt, documentToken);
        } else {
          guard = { ready: false, reason: "Import page has changed" };
          importGuard = { allowed: false, reason: guard.reason };
        }
        if (["IMPORT_CLICK_INTENT", "IMPORT_CLICKED"].includes(session.importState)) {
          const result = inspector.inspectImportResult(doc, location);
          if (result.verified) {
            session.importResultKind = result.kind;
            setImportState("IMPORT_RESULT_VERIFIED");
          } else if (Date.now() - (session.importStartedAt || 0) >= RESULT_WAIT_MS) {
            setImportState("IMPORT_RESULT_AMBIGUOUS");
          }
        }
        if (importGuard.allowed && session.importState === "IMPORT_NOT_STARTED") {
          setImportState("IMPORT_READY");
        } else if (!importGuard.allowed && session.importState === "IMPORT_READY") {
          setImportState("IMPORT_NOT_STARTED");
        }
      } catch {
        guard = { ready: false, reason: "Page inspection failed" };
        importGuard = { allowed: false, reason: "Page inspection failed" };
      }
      lastImportReport = report;
      button.disabled = busy || !guard.ready || lastAction === "FILLED";
      // Authorization is intentionally unavailable in this development build.
      importButton.disabled = !ENABLE_PREPARE_SECOND_ATTEMPT || busy ||
        secondReadiness.state !== "SECOND_ATTEMPT_READY";
      if (importButton.disabled) importButton.setAttribute("aria-disabled", "true");
      else importButton.removeAttribute("aria-disabled");
      button.hidden = !guard.ready;
      importButton.hidden = !importGuard.allowed && session.importState === "IMPORT_NOT_STARTED" &&
        report?.importInterface?.sourceUrlInput?.editorState !== "EXACT_SOURCE_URL_PRESENT";
      message.textContent = session.importState === "IMPORT_RESULT_VERIFIED" ?
        "Medium draft identified. Stopped." :
        session.importState === "IMPORT_RESULT_AMBIGUOUS" ?
          "Import result is ambiguous. Stopped; no retry." :
          session.importState === "IMPORT_CLICKED" ?
            "Medium Import activated once. Waiting for draft editor; no retry." :
          session.importState === "IMPORT_CLICK_INTENT" ?
            "Import click was attempted. Result pending; no retry." :
          importGuard.allowed ? "Verified source ready for explicit Import. Import has NOT been clicked." :
          lastAction === "FILLED" ?
            "Source URL verified. Import has NOT been clicked." :
        guard.ready ? "Ready to insert verified source URL" : "Medium Import: BLOCKED";
      details.replaceChildren();
      line("Page guard", guard.ready || importGuard.allowed ? "READY" : "BLOCKED");
      line("Source state", report?.importInterface?.sourceUrlInput?.editorState || "UNAVAILABLE");
      line("Fill attempt", session.fills === 0 ? "AVAILABLE" : "USED");
      if (session.lastFillResult) {
        line("Fill DOM result", session.lastFillResult.fillDomResult);
        line("Fill fingerprint match", session.lastFillResult.fingerprintMatch ? "Yes" : "No");
        line("Browser-generated input observed",
          session.lastFillResult.browserInputObserved ? "Yes" : "No");
        line("Synthetic input fallback used",
          session.lastFillResult.syntheticInputFallbackUsed ? "Yes" : "No");
        line("Application input signal delivered",
          session.lastFillResult.applicationInputSignalDelivered ? "Yes" : "No");
        line("Fill synchronization status", session.lastFillResult.fillSynchronizationStatus);
      }
      line("Last action", lastAction);
      line("Verified source", importGuard.allowed || ["IMPORT_CLICK_INTENT", "IMPORT_CLICKED",
        "IMPORT_RESULT_VERIFIED", "IMPORT_RESULT_AMBIGUOUS"].includes(session.importState) ?
        "YES" : "NO");
      line("Import state", session.importState === "IMPORT_READY" ? "READY" :
        session.importState === "IMPORT_NOT_STARTED" ? "NOT_STARTED" : session.importState);
      if (session.importState === "IMPORT_RESULT_VERIFIED") {
        line("Result", session.importResultKind === "DRAFT_EDITOR" ?
          "Medium draft editor verified; stopped" :
          session.importResultKind === "DRAFT_LIST" ?
            "Medium Stories draft verified; stopped" :
            "Medium draft result verified; stopped");
      } else if (session.importState === "IMPORT_RESULT_AMBIGUOUS") {
        line("Result", "Ambiguous; no retry");
      } else if (["IMPORT_CLICK_INTENT", "IMPORT_CLICKED"].includes(session.importState)) {
        line("Import click attempt", "USED");
      } else if (lastAction === "FILLED") {
        line("Exact source verified", exactVerified ? "YES" : "NO");
        line("Medium Import clicked", "NO");
      } else if (lastAction !== "NONE") {
        line("Reason", actionReason);
      } else if (importGuard.allowed) {
        line("Source", "Verified Substack publication");
      } else if (!guard.ready) {
        line("Reason", guard.reason);
      } else {
        line("Source", "Verified Substack publication");
      }
      if (!importGuard.allowed && report?.importInterface?.sourceUrlInput?.editorState ===
          "EXACT_SOURCE_URL_PRESENT" && session.importState === "IMPORT_NOT_STARTED") {
        line("Import guard reason", importGuard.reason);
      }
      keepMediumImportVisible();
      const dom = importButtonDomState(doc, shadow, host, importButton);
      const attemptAvailable = (session.importClicks || 0) === 0 &&
        session.canPersistImport !== false && !session.restorationBlocked &&
        !["IMPORT_CLICK_INTENT", "IMPORT_CLICKED", "IMPORT_RESULT_VERIFIED",
          "IMPORT_RESULT_AMBIGUOUS"].includes(session.importState);
      const disabledReason = importGuard.allowed ?
        busy ? "Import action is in progress" : dom.blockingReason : importGuard.reason;
      lastImportDiagnostics = {
        lifecycleState: session.importState,
        attemptAvailable, clickAttempts: session.importClicks || 0,
        guardAllowed: importGuard.allowed,
        raw: importGuard.raw || null,
        firstFailureReason: importGuard.reason,
        disabledReason,
        control: importButton.disabled ? "DISABLED" : "ENABLED",
        logicalEligibility: importGuard.allowed ? "ENABLED" : "DISABLED",
        ...dom,
        restored: session.restoredImportSession || null
      };
      line("Import lifecycle state", lastImportDiagnostics.lifecycleState);
      line("Import attempt available", attemptAvailable ? "Yes" : "No");
      line("Import click attempts", lastImportDiagnostics.clickAttempts);
      line("Import guard allowed", importGuard.allowed ? "Yes" : "No");
      line("Import disabled reason", disabledReason);
      if (lastImportDiagnostics.raw) {
        const raw = lastImportDiagnostics.raw;
        const yn = value => value ? "Yes" : "No";
        line("Import raw-source guard", raw.sourceGuard ? "PASS" : "FAIL");
        line("Import raw-action guard", raw.actionGuard ? "PASS" : "FAIL");
        line("Import source normalized equality", yn(raw.source.normalizedEquality));
        line("Import source fingerprint equality", yn(raw.source.fingerprintEquality));
        line("Raw source exact match", yn(raw.source.exactMatch));
        line("Raw source fingerprint match", yn(raw.source.fingerprintMatch));
        line("Raw source normalized length", raw.source.normalizedLength ?? "Not observed");
        line("Raw source DOM representation", raw.source.representation ?? "Not observed");
        line("Inspector normalized length", raw.source.inspectorNormalizedLength ?? "Not observed");
        line("Resolver normalized length", raw.source.resolverNormalizedLength ?? "Not observed");
        line("Normalized values equal", yn(raw.source.normalizedValuesEqual));
        line("Inspector fingerprint", raw.source.inspectorFingerprint ?? "Not observed");
        line("Resolver fingerprint", raw.source.resolverFingerprint ?? "Not observed");
        line("Import action raw match", yn(raw.action.rawMatch));
        line("Native Import candidate count", raw.action.candidateCount);
        line("Import tag", raw.action.tag ?? "Not observed");
        line("Import type", raw.action.type ?? "Not observed");
        line("Import type attribute present", yn(raw.action.typeAttributePresent));
        line("Import type attribute value", raw.action.typeAttributePresent ?
          raw.action.typeAttributeValue : "Absent");
        line("Import type attribute valid", yn(raw.action.typeAttributeValid));
        line("Import effective DOM type", raw.action.effectiveDomType ?? "Not observed");
        line("Import effective type match", yn(raw.action.effectiveTypeMatch));
        line("Import visible text exact", yn(raw.action.visibleTextExact));
        line("Import accessible name exact", yn(raw.action.accessibleNameExact));
        line("Import visible", yn(raw.action.visible));
        line("Import enabled", yn(raw.action.enabled));
        line("Import connected to DOM", yn(raw.action.connected));
        line("Inspector and resolver source identity", yn(raw.source.identityMatchesInspection));
        line("Inspector and resolver Import identity", yn(raw.action.identityMatchesInspection));
        line("Native Import identity", raw.actionGuard ? "PASS" : "FAIL");
      }
      line("Overall Import guard", importGuard.allowed ? "PASS" : "FAIL");
      if (!importGuard.allowed) line("First Import guard failure", importGuard.reason);
      line("Extension Import control", lastImportDiagnostics.control);
      line("Import logical eligibility", lastImportDiagnostics.logicalEligibility);
      line("Import actual interactability", dom.interactability);
      if (importGuard.allowed && dom.interactability !== "ENABLED") {
        line("Import UI/logic mismatch", dom.blockingReason);
      }
      line("Hit-test", dom.hitTest);
      line("Import button disabled property", dom.disabledProperty ? "true" : "false");
      line("Import button disabled attribute", dom.disabledAttribute ? "present" : "absent");
      line("Import button aria-disabled", dom.ariaDisabled);
      line("Import button pointer-events", dom.pointerEvents);
      line("Import button cursor", dom.cursor);
      line("Import panel pointer-events", dom.parentPointerEvents);
      line("Import host pointer-events", dom.hostPointerEvents);
      line("Import host display", dom.hostDisplay);
      line("Import host z-index", dom.hostZIndex);
      line("Import disabled CSS class", dom.disabledCssClass ? "present" : "absent");
      line("Import button bounding box", dom.boundingBox);
      if (lastImportDiagnostics.restored) {
        line("Restored session state", lastImportDiagnostics.restored.state);
        line("Restored session click attempts", lastImportDiagnostics.restored.clickAttempts);
        line("Restored session source", lastImportDiagnostics.restored.source);
      }
      const second = session.secondAttempt;
      Object.assign(lastImportDiagnostics, {
        attempt1ActivationMechanism: "HTMLElement.click on native Import button",
        attempt1Reconciliation: "NO_DRAFT_FOUND",
        currentFillSynchronization: session.lastFillResult?.fillSynchronizationStatus ||
          "UNVERIFIED",
        programmaticImportClickTrusted: false,
        secondAttemptEligibility: secondReadiness.state === "SECOND_ATTEMPT_READY" ?
          "READY" : "NOT_READY",
        secondAttemptReason: secondReadiness.reason,
        secondAttemptAuthorization: second.state === "SECOND_ATTEMPT_ARMED" ?
          "ARMED" : "NOT_AUTHORIZED",
        authorizedAttemptNumber: second.authorizedAttemptNumber,
        observedNativeImportClicks: second.observedNativeImportClicks,
        nativeClickEvidence, nativeClickClassification,
        automaticNativeImportActivation: "DISABLED"
      });
      line("Attempt 1 activation mechanism", lastImportDiagnostics.attempt1ActivationMechanism);
      line("Attempt 1 reconciliation", "NO_DRAFT_FOUND");
      line("Current Fill synchronization", lastImportDiagnostics.currentFillSynchronization);
      line("Programmatic Import click trusted in Chrome fixture", "No");
      line("Second attempt eligibility", lastImportDiagnostics.secondAttemptEligibility);
      line("Second attempt eligibility reason", secondReadiness.reason);
      line("Second attempt authorization", lastImportDiagnostics.secondAttemptAuthorization);
      line("Authorized attempt number", second.authorizedAttemptNumber ?? "None");
      line("Observed native Import clicks", second.observedNativeImportClicks);
      line("Native Import click observed", nativeClickEvidence ? "Yes" : "No");
      line("Native Import click isTrusted", nativeClickEvidence ?
        nativeClickEvidence.isTrusted ? "Yes" : "No" : "Not observed");
      line("Native Import button identity still valid", nativeClickEvidence ?
        nativeClickEvidence.buttonIdentityValid ? "Yes" : "No" : "Not observed");
      line("Native Import attempt authorization present", nativeClickEvidence ?
        nativeClickEvidence.authorizationPresent ? "Yes" : "No" : "Not observed");
      line("Automatic native Import activation", "DISABLED");
    }

    function scheduleRefresh() {
      if (scheduled || busy) return;
      scheduled = true;
      queueMicrotask(refresh);
    }

    button.addEventListener("click", event => {
      event.preventDefault();
      event.stopPropagation();
      if (busy || button.disabled || session.fills !== 0) return;
      busy = true;
      button.disabled = true;
      try {
        // A displayed READY state is never authorization for a later click.
        const fresh = inspector.inspectDocument(doc, location);
        const guard = eligibility(fresh, session, location);
        if (!guard.ready) {
          lastAction = "REFUSED";
          actionReason = guard.reason;
        } else {
          const fill = inspector.fillVerifiedSourceUrl(doc, location, session);
          session.lastFillResult = fill;
          const after = inspector.inspectDocument(doc, location);
          exactVerified = after.importInterface.sourceUrlInput?.editorState ===
            "EXACT_SOURCE_URL_PRESENT" &&
            after.importInterface.sourceUrlInput.existingUrl?.matchesIntendedSourceExactly === true;
          const verified = fill.status === "URL_FILLED" && fill.fillSucceeded === true &&
            fill.fillSynchronizationStatus === "VERIFIED" &&
            fill.defaultExampleReplaced === true && fill.exactUrlPresentAfterward === true &&
            fill.importButtonStillExists === true && fill.pageRemainsImport === true &&
            fill.importClicked === false && fill.formSubmitted === false &&
            fill.navigationOccurred === false && onImportPage(location) &&
            after.importInterface.importAction?.enabled === true && exactVerified;
          lastAction = verified ? "FILLED" : fill.urlFillAttempted ? "FAILED" : "REFUSED";
          actionReason = verified ? "None" : fill.status;
        }
      } catch {
        lastAction = session.fills > 0 ? "FAILED" : "REFUSED";
        actionReason = "Page action failed safely";
      } finally {
        busy = false;
        refresh();
      }
    });

    importButton.addEventListener("click", async event => {
      event.preventDefault();
      event.stopPropagation();
      if (!ENABLE_PREPARE_SECOND_ATTEMPT || importButton.disabled || busy) return;
      busy = true;
      importButton.disabled = true;
      try {
        const report = inspector.inspectDocument(doc, location);
        const ready = secondAttemptReadiness(report, session, location,
          inspector, doc, session.secondAttempt, documentToken);
        if (ready.state !== "SECOND_ATTEMPT_READY") return;
        const reserved = await secondAttempt.reserve({ storage: session.secondAttemptStore,
          record: session.secondAttempt, readiness: ready, documentToken });
        if (reserved) {
          session.secondAttempt = reserved;
          armedNativeTarget = ready.targets.action;
        }
      } catch { /* A failed reservation never authorizes a click. */ }
      finally { busy = false; refresh(); }
    });

    // Capture only. Never prevent, dispatch, replay, or initiate a Medium click.
    const observeNativeClick = event => {
      if (!onImportPage(location)) return;
      let report;
      let raw;
      try {
        report = inspector.inspectDocument(doc, location);
        raw = inspector.inspectImportRaw(doc, location, report);
      } catch { return; }
      const candidate = raw?.targets?.action || armedNativeTarget;
      if (!candidate || !(event.target === candidate ||
          candidate.contains?.(event.target))) return;
      const readiness = secondAttemptReadiness(report, session, location,
        inspector, doc, secondAttempt.empty(), documentToken);
      const classification = secondAttempt.classifyClick({ event,
        action: armedNativeTarget || candidate,
        freshAction: raw.actionGuard ? candidate : null,
        record: session.secondAttempt, documentToken,
        sourceStillValid: readiness.state === "SECOND_ATTEMPT_READY",
        fillStillVerified: session.lastFillResult?.fillSynchronizationStatus === "VERIFIED" });
      nativeClickEvidence = classification.evidence;
      nativeClickClassification = classification.reason;
      if (classification.authorized) {
        // Close the in-document gate synchronously before any storage round trip.
        const armed = session.secondAttempt;
        session.secondAttempt = { ...armed,
          state: "SECOND_ATTEMPT_USER_CLICK_OBSERVED",
          observedNativeImportClicks: 1, nativeClickObserved: true,
          clickEvidence: classification.evidence };
        secondAttempt.recordClick({ storage: session.secondAttemptStore,
          record: armed, classification }).catch(() => {
          // The reservation remains on disk and its document token cannot be
          // reused after navigation; the click result stays ambiguous.
        });
      }
      setTimeout(refresh, 0);
    };
    doc.addEventListener?.("click", observeNativeClick, true);

    refresh();
    host.getImportDiagnostics = () => { refresh(); return lastImportDiagnostics; };
    host.getImportInspection = () => {
      refresh();
      return { report: lastImportReport, diagnostics: lastImportDiagnostics };
    };
    host.disposeImportPanel = () => {
      doc.removeEventListener?.("click", observeNativeClick, true);
      observer?.disconnect();
      if (resultTimer) clearTimeout(resultTimer);
    };
    if (["IMPORT_RESULT_VERIFIED", "IMPORT_RESULT_AMBIGUOUS"].includes(session.importState)) {
      return host;
    }
    if (["IMPORT_CLICK_INTENT", "IMPORT_CLICKED"].includes(session.importState)) {
      resultTimer = setTimeout(refresh,
        Math.max(0, RESULT_WAIT_MS - (Date.now() - (session.importStartedAt || 0))));
      resultTimer?.unref?.();
    }
    if (typeof MutationObserver === "function") {
      observer = new MutationObserver(records => {
        if (records?.length && records.every(record => record.target === host)) return;
        scheduleRefresh();
      });
      observer.observe(doc.documentElement, { subtree: true, childList: true,
        characterData: true, attributes: true });
    }
    doc.defaultView?.addEventListener?.("popstate", scheduleRefresh);
    doc.defaultView?.addEventListener?.("pagehide", () => { navigatingAway = true; });
    doc.defaultView?.addEventListener?.("beforeunload", () => { navigatingAway = true; });
    return host;
  }

  const api = { mount, eligibility, importEligibility, canOfferVerifiedImport,
    importButtonDomState, onImportPage };
  root.MediumPageControls = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(globalThis);

"use strict";

const button = document.getElementById("inspect");
const reservationButton = document.getElementById("inspect-reservation");
const titleButton = document.getElementById("fill-medium-title");
const bodyButton = document.getElementById("fill-medium-body");
const recoveryButton = document.getElementById("resume-medium-body");
const output = document.getElementById("report");
function setTitleEnabled(enabled) {
  if (!titleButton) return;
  titleButton.disabled = !enabled;
  if (enabled) titleButton.removeAttribute?.("disabled");
  else titleButton.setAttribute?.("disabled", "");
  titleButton.setAttribute?.("aria-disabled", enabled ? "false" : "true");
}
function setBodyEnabled(enabled) {
  if (!bodyButton) return;
  bodyButton.disabled = !enabled;
  if (enabled) bodyButton.removeAttribute?.("disabled");
  else bodyButton.setAttribute?.("disabled", "");
  bodyButton.setAttribute?.("aria-disabled", enabled ? "false" : "true");
}
function setRecoveryEnabled(enabled) {
  if (!recoveryButton) return;
  recoveryButton.disabled = !enabled;
  if (enabled) recoveryButton.removeAttribute?.("disabled");
  else recoveryButton.setAttribute?.("disabled", "");
  recoveryButton.setAttribute?.("aria-disabled", enabled ? "false" : "true");
}
function popupTitleControlState() {
  if (!titleButton) return { interactability: "BLOCKED", reason: "CONTROL_MISSING" };
  const style = document.defaultView?.getComputedStyle?.(titleButton);
  const rect = titleButton.getBoundingClientRect?.();
  const center = rect && rect.width && rect.height ?
    { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 } : null;
  const hit = center ? document.elementFromPoint?.(center.x, center.y) : null;
  const hitTest = hit === titleButton || titleButton.contains?.(hit) ? "BUTTON" :
    hit ? `BLOCKED_BY_${hit.tagName || "UNKNOWN"}` :
      center && document.elementFromPoint ? "OUTSIDE_VIEWPORT" : "UNAVAILABLE";
  const reason = titleButton.disabled ? "DISABLED_PROPERTY" :
    titleButton.hasAttribute?.("disabled") ? "DISABLED_ATTRIBUTE" :
      titleButton.getAttribute?.("aria-disabled") === "true" ? "ARIA_DISABLED" :
        style?.pointerEvents === "none" ? "POINTER_EVENTS_NONE" :
          hitTest !== "BUTTON" && hitTest !== "UNAVAILABLE" ? hitTest : "None";
  return { interactability: reason === "None" ? "ENABLED" : "BLOCKED", reason,
    disabledProperty: Boolean(titleButton.disabled),
    disabledAttribute: Boolean(titleButton.hasAttribute?.("disabled")),
    ariaDisabled: titleButton.getAttribute?.("aria-disabled") ?? "Absent",
    hitTest };
}

function inspectReservation() {
  return new Promise((resolve, reject) => {
    if (!chrome.runtime?.sendMessage) {
      reject(Error("RESERVATION_WORKER_UNAVAILABLE"));
      return;
    }
    try {
      chrome.runtime.sendMessage({ type: "INSPECT_MEDIUM_RESERVATION" }, response => {
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
    } catch {
      reject(Error("WORKER_MESSAGE_ERROR"));
    }
  });
}

function yesNo(value) {
  return value ? "Yes" : "No";
}

function printable(value) {
  return value === null || value === undefined || value === "" ? "Not observed" : String(value);
}

function authorization(record) {
  return record?.state === "SECOND_ATTEMPT_ARMED" ? "ARMED" :
    record?.state === "SECOND_ATTEMPT_NOT_AUTHORIZED" ? "NOT_AUTHORIZED" :
      record?.state?.startsWith("SECOND_ATTEMPT_") ? "CONSUMED" : "Not observed";
}

function formatReservation(response) {
  if (!response?.ok) return `Attempt-2 reservation inspection: ERROR
Reservation inspection failure: ${printable(response?.reason || "WORKER_RESPONSE_NOT_RECEIVED")}
Second attempt authorization: NOT_AUTHORIZED`;
  const record = response.record;
  const diagnostic = response.diagnostic || {};
  return [
    `Attempt-2 reservation exists: ${yesNo(response.exists)}`,
    `Authorization: ${authorization(record)}`,
    `Authorized attempt: ${printable(record?.authorizedAttemptNumber)}`,
    `Observed clicks: ${printable(record?.observedNativeImportClicks)}`,
    `Reservation result: ${printable(record?.result)}`,
    `Storage source: ${printable(response.source)}`,
    `Background request received: ${yesNo(diagnostic.backgroundRequestReceived)}`,
    `Background attempt-1 history check: ${printable(diagnostic.attempt1HistoryCheck)}`,
    `Background existing-attempt-2 check: ${printable(diagnostic.existingAttempt2Check)}`,
    `Reservation storage write attempted: ${yesNo(diagnostic.storageWriteAttempted)}`,
    `Reservation storage write succeeded: ${yesNo(diagnostic.storageWriteSucceeded)}`,
    `Reservation read-back succeeded: ${yesNo(diagnostic.storageReadBackSucceeded)}`,
    `Background response: ${printable(diagnostic.backgroundResponse)}`,
    `Background rejection reason: ${printable(diagnostic.rejectionReason)}`
  ].join("\n");
}

function formatPreparation(status) {
  const diagnostic = status?.importDiagnostics || {};
  const transaction = diagnostic.prepareTransaction || status?.prepareTransaction || {};
  return [
    `Prepare click received: ${yesNo(transaction.clickReceived)}`,
    `Prepare transaction ID: ${printable(transaction.transactionId)}`,
    `Fresh readiness check: ${printable(transaction.freshReadinessCheck)}`,
    `Fresh readiness failure: ${printable(transaction.freshReadinessFailure)}`,
    `Reservation request sent: ${yesNo(transaction.reservationRequestSent)}`,
    `Content script response received: ${yesNo(transaction.contentResponseReceived)}`,
    `Prepare control actual disabled: ${printable(diagnostic.prepareControlActualDisabled)}`,
    `Prepare control hit test: ${printable(diagnostic.prepareControlHitTest)}`,
    `Prepare control in current Shadow DOM: ${printable(diagnostic.prepareControlInCurrentShadow)}`,
    `Prepare pointer received: ${yesNo(diagnostic.preparePointerReceived)}`,
    `Prepare click listener attached: ${yesNo(diagnostic.prepareClickListenerAttached)}`,
    `Prepare click listener generation: ${printable(diagnostic.prepareClickListenerGeneration)}`,
    `Prepare handler invocation count: ${printable(diagnostic.prepareHandlerInvocationCount)}`,
    `Attempt-2 persisted record present: ${yesNo(diagnostic.attempt2PersistedRecordPresent)}`,
    `Persisted authorization: ${authorization(status?.reservation?.record)}`,
    `Persisted authorized attempt number: ${printable(status?.reservation?.record?.authorizedAttemptNumber)}`,
    `Persisted observed native clicks: ${printable(status?.reservation?.record?.observedNativeImportClicks)}`,
    `UI armed transition completed: ${yesNo(diagnostic.prepareUiArmedTransitionCompleted)}`
  ].join("\n");
}

function fields(item) {
  const names = [
    ["tag", "Tag"], ["role", "Role"], ["type", "Type"],
    ["visibleText", "Visible text"], ["accessibleName", "Accessible name"],
    ["placeholder", "Placeholder"], ["ariaLabel", "aria-label"],
    ["ariaLabelledby", "aria-labelledby"], ["name", "Name"], ["id", "ID"],
    ["dataTestId", "data-testid"], ["contenteditable", "contenteditable"],
    ["ariaMultiline", "aria-multiline"], ["dataPlaceholder", "data-placeholder"],
    ["editorClasses", "Editor classes"], ["childElementCount", "Child element count"],
    ["childStructure", "Child structure"],
    ["normalizedVisibleText", "Normalized visible text"], ["editorState", "Editor state"],
    ["href", "href"],
    ["nearbyText", "Nearby text"]
  ];
  const parts = names.filter(([key]) => item[key] !== undefined && item[key] !== null && item[key] !== "")
    .map(([key, label]) => `${label}: ${item[key]}`);
  if (item.currentValueEmpty !== undefined) parts.push(`Empty: ${yesNo(item.currentValueEmpty)}`);
  if (item.enabled !== undefined) parts.push(`Enabled: ${yesNo(item.enabled)}`);
  else if (item.disabled !== undefined) parts.push(`Disabled: ${yesNo(item.disabled)}`);
  return parts.join(" | ");
}

function numbered(lines, heading, items) {
  lines.push("", `${heading}:`);
  if (!items.length) lines.push("  None observed");
  else items.forEach((item, index) => lines.push(`  ${index + 1}. ${fields(item)}`));
}

function editorCandidate(item) {
  if (!item) return "None selected";
  const detail = [
    `index=${item.index}`, `tag=${printable(item.tag)}`, `role=${printable(item.role)}`,
    `contenteditable=${printable(item.contenteditable)}`,
    `aria-label=${printable(item.ariaLabel)}`,
    `aria-placeholder=${printable(item.ariaPlaceholder)}`,
    `placeholder=${printable(item.placeholder)}`,
    `data-placeholder=${printable(item.dataPlaceholder)}`,
    `data-testid=${printable(item.dataTestId)}`, `id=${printable(item.id)}`,
    `classes=${(item.classes || []).join(" ") || "None"}`,
    `parent=${JSON.stringify(item.parent || null)}`,
    `nearest editor-like ancestor=${JSON.stringify(item.nearestEditorLikeAncestor || null)}`,
    `ancestry=${JSON.stringify(item.ancestry || [])}`,
    `children=${printable(item.childStructure)}`,
    `child element count=${item.childElementCount}`,
    `descendant contenteditable count=${item.descendantContenteditableCount}`,
    `normalized innerText length=${item.normalizedInnerTextLength}`,
    `normalized textContent length=${item.normalizedTextContentLength}`,
    `normalized text length=${item.normalizedTextLength}`,
    `br-only=${yesNo(item.brOnly)}`, `empty paragraph=${yesNo(item.emptyParagraph)}`,
    `empty section=${yesNo(item.emptySection)}`,
    `zero-width characters=${yesNo(item.zeroWidthCharacters)}`,
    `non-breaking spaces=${yesNo(item.nonBreakingSpaces)}`,
    `placeholder-like descendants=${item.placeholderLikeDescendants}`,
    `aria-hidden descendants=${item.ariaHiddenDescendants}`,
    `contenteditable=false descendants=${item.noneditableDescendants}`,
    `::before=${JSON.stringify(item.before || null)}`,
    `::after=${JSON.stringify(item.after || null)}`,
    `editable descendant pseudo-elements=${JSON.stringify(item.immediateEditableDescendantPseudos || [])}`,
    `tabindex=${printable(item.tabindex)}`, `spellcheck=${printable(item.spellcheck)}`,
    `autocapitalize=${printable(item.autocapitalize)}`,
    `inputmode=${printable(item.inputmode)}`,
    `aria-multiline=${printable(item.ariaMultiline)}`,
    `state=${item.currentState}`, `text evidence=${JSON.stringify(item.textEvidence || {})}`,
    `title score=${item.titleScore ?? 0}`,
    `title evidence=${JSON.stringify(item.titleEvidence || [])}`,
    `body score=${item.bodyScore ?? 0}`,
    `body evidence=${JSON.stringify(item.bodyEvidence || [])}`
  ];
  return detail.join("; ");
}

function formatEditorInspection(editor) {
  const lines = [
    `Medium story editor detected: ${yesNo(editor.detected)}`,
    `Medium editor route recognized: ${yesNo(editor.editorRouteRecognized)}`,
    `Editor route kind: ${printable(editor.editorRouteKind)}`,
    `Draft ID: ${printable(editor.draftId)}`,
    `Draft editor route: ${yesNo(editor.draftEditorRoute)}`,
    `Draft ID/path: ${printable(editor.draftIdentity)}`,
    `Draft identity established: ${yesNo(editor.draftIdentityEstablished)}`,
    `Draft route established by Medium: ${yesNo(editor.draftRouteEstablishedByMedium)}`,
    `Editor structural evidence present: ${yesNo(editor.editorStructuralEvidencePresent)}`,
    `Editor URL shape: ${printable(editor.editorUrlShape)}`,
    `Draft identity: ${printable(editor.draftIdentity)}`,
    `Editor architecture: ${printable(editor.editorArchitecture || "UNKNOWN")}`,
    `Composite editor root detected: ${yesNo(editor.compositeEditorRootDetected)}`,
    `Composite editor candidate index: ${printable(editor.compositeEditorCandidateIndex)}`,
    `Composite editor evidence: ${(editor.compositeEditorEvidence || []).join("; ") || "None"}`,
    `Primary composite editor: ${editorCandidate(editor.primaryCompositeEditor)}`,
    `Composite editor root: ${editor.primaryCompositeEditor ?
      `${editor.primaryCompositeEditor.tag}${editor.primaryCompositeEditor.id ?
        `#${editor.primaryCompositeEditor.id}` : ""}` : "Not observed"}`,
    `Auxiliary contenteditables excluded: ${JSON.stringify(editor.auxiliaryContenteditablesExcluded || [])}`,
    `Default-value prompt count: ${(editor.defaultValuePrompts || []).length}`,
    ...(editor.defaultValuePrompts || []).map((prompt, index) =>
      `Default prompt ${index + 1}: ${JSON.stringify(prompt)}`),
    `Title subregion candidate count: ${editor.titleSubregionCandidateCount ?? 0}`,
    `Body subregion candidate count: ${editor.bodySubregionCandidateCount ?? 0}`,
    `Selected title subregion: ${JSON.stringify(editor.selectedTitleSubregion || null)}`,
    `Selected body subregion: ${JSON.stringify(editor.selectedBodySubregion || null)}`,
    `Resolved body subregion: ${editor.selectedBodySubregion ?
      `${editor.selectedBodySubregion.tag}[data-testid="${
        editor.selectedBodySubregion.data?.["data-testid"] || ""}"]` : "Not observed"}`,
    `Title subregion state: ${printable(editor.titleSubregionState || "UNKNOWN")}`,
    `Body subregion state: ${printable(editor.bodySubregionState || "UNKNOWN")}`,
    `Title exact match: ${yesNo(editor.titleExactMatch)}`,
    `Title fingerprint: ${printable(editor.titleFingerprint)}`,
    `Title fingerprint match: ${yesNo(editor.titleFingerprintMatch)}`,
    `Title verified in persisted draft: ${yesNo(editor.titleVerifiedInPersistedDraft)}`,
    `Body still blank: ${yesNo(editor.bodyStillBlank)}`,
    `Draft continuation safe: ${yesNo(editor.draftContinuationSafe)}`,
    `User-authored content detected: ${printable(editor.userAuthoredContentDetected || "Unknown")}`,
    `Title editor candidate count: ${editor.titleEditorCandidateCount}`,
    `Composite root body-editor score candidate count: ${editor.bodyEditorCandidateCount}`,
    `Selected title editor: ${editorCandidate(editor.selectedTitleEditor)}`,
    `Composite root body-editor score selection: ${editorCandidate(editor.selectedBodyEditor)}`,
    `Title current state: ${editor.titleCurrentState}`,
    `Body current state: ${editor.bodyCurrentState}`,
    `Publish control count: ${editor.publishControlCount}`,
    `Publish control: ${editor.publishControl ?
      `${editor.publishControl.name}; visible=${yesNo(editor.publishControl.visible)}; enabled=${yesNo(editor.publishControl.enabled)}` : "Not observed"}`,
    `Draft/save indicator: ${editor.draftSaveIndicator}`,
    `Editor container: ${editor.editorContainer}`,
    `Nearest common editor root: ${JSON.stringify(editor.commonEditorRoot || null)}`,
    `Editable relationship: ${printable(editor.editableRelationship)}`,
    `DOM order: ${printable(editor.domOrder)}`,
    `Title/body order: ${printable(editor.titleBodyOrder)}`,
    `Editor appears blank: ${editor.editorAppearsBlank}`,
    `Editor classification: ${editor.classification}`,
    `Editor workflow state: ${editor.workflowState}`,
    `Story/settings control count: ${editor.settingsControls.length}`,
    ...editor.settingsControls.map((control, index) =>
      `Story/settings control ${index + 1}: ${control.name}; visible=${yesNo(control.visible)}; enabled=${yesNo(control.enabled)}; data=${JSON.stringify(control.data || {})}`),
    "Composite editor structural blocks:",
    ...(editor.compositeStructuralBlocks || []).map((block, index) =>
      `  ${index + 1}. ${JSON.stringify(block)}`),
    "Editable candidate structure:"
  ];
  if (!editor.editableCandidates.length) lines.push("  None observed");
  else editor.editableCandidates.forEach((item, index) =>
    lines.push(`  ${index + 1}. ${editorCandidate(item)}`));
  return lines.join("\n");
}

function formatTitleDiagnostics(status) {
  const fill = status.titleFillResult || {};
  const eligibility = status.titleEligibility || {};
  const verifiedDraft = status.editorInspection?.titleVerifiedInPersistedDraft;
  const historical = Boolean(fill.workflow);
  const observed = value => historical ? yesNo(value) : "Not observed";
  const control = status.titleControlDiagnostics || {};
  const popupControl = popupTitleControlState();
  return [
    `Medium title workflow: ${printable(verifiedDraft ? "MEDIUM_TITLE_VERIFIED" :
      fill.workflow || eligibility.workflow || "MEDIUM_TITLE_NOT_FILLED")}`,
    `Historical title-fill telemetry available: ${yesNo(historical)}`,
    `Expected title: ${printable(status.expectedTitle || globalThis.PublishToAllStory?.title)}`,
    `Expected title fingerprint: ${printable(globalThis.PublishToAllStory?.fingerprint)}`,
    `Title fill eligibility: ${eligibility.ready ? "READY" : "NOT_READY"}`,
    `Title fill logical eligibility: ${eligibility.ready ? "READY" : "NOT_READY"}`,
    `Title fill actual interactability: ${printable(control.interactability)}`,
    `Popup title control actual interactability: ${popupControl.interactability}`,
    `Popup title control blocking reason: ${popupControl.reason}`,
    `Popup title control disabled property: ${popupControl.disabledProperty}`,
    `Popup title control disabled attribute: ${popupControl.disabledAttribute}`,
    `Popup title control aria-disabled: ${popupControl.ariaDisabled}`,
    `Popup title control hit-test: ${popupControl.hitTest}`,
    `Title fill UI safety reason: ${printable(control.uiSafetyReason)}`,
    `Title fill control present: ${yesNo(control.present)}`,
    `Title fill control actual disabled property: ${printable(control.disabledProperty)}`,
    `Title fill control disabled attribute: ${printable(control.disabledAttribute)}`,
    `Title fill control aria-disabled: ${printable(control.ariaDisabled)}`,
    `Title fill control pointer-events: ${printable(control.pointerEvents)}`,
    `Title fill control cursor: ${printable(control.cursor)}`,
    `Title fill control visibility: ${printable(control.visibility)}`,
    `Title fill control display: ${printable(control.display)}`,
    `Title fill control opacity: ${printable(control.opacity)}`,
    `Title fill control bounding box: ${printable(control.boundingBox)}`,
    `Title fill control hit-test: ${printable(control.hitTest)}`,
    `Title panel pointer-events: ${printable(control.panelPointerEvents)}`,
    `Title panel z-index: ${printable(control.panelZIndex)}`,
    `Title host pointer-events: ${printable(control.hostPointerEvents)}`,
    `Title host display: ${printable(control.hostDisplay)}`,
    `Title host z-index: ${printable(control.hostZIndex)}`,
    `Title disabled fieldset ancestor: ${yesNo(control.disabledFieldset)}`,
    `Title inert ancestor: ${yesNo(control.inert)}`,
    `Title aria-disabled ancestor: ${yesNo(control.ariaAncestor)}`,
    `Title zero-opacity ancestor: ${yesNo(control.zeroOpacity)}`,
    `Title current Shadow DOM host unique: ${yesNo(control.currentHostUnique)}`,
    `Title control generation: ${printable(control.currentControlGeneration)}`,
    `Title click listener attached: ${yesNo(control.listenerAttached)}`,
    `Title click listener count: ${printable(control.listenerCount)}`,
    `Title click listener generation: ${printable(control.listenerGeneration)}`,
    `Title handler invocation count: ${printable(control.handlerInvocationCount)}`,
    `Title pointer event received: ${yesNo(control.pointerEventReceived)}`,
    `Title click event received: ${yesNo(control.clickEventReceived)}`,
    `Title fill failure: ${printable(verifiedDraft ? "None" :
      fill.titleFillFailure && fill.titleFillFailure !== "None" ?
        fill.titleFillFailure : eligibility.ready ? "None" : eligibility.reason)}`,
    `Title fill attempted: ${observed(fill.titleFillAttempted)}`,
    `Title exact match: ${yesNo(verifiedDraft || fill.titleExactMatch)}`,
    `Title normalized length: ${printable(fill.titleNormalizedLength)}`,
    `Title fingerprint: ${printable(status.editorInspection?.titleFingerprint || fill.titleFingerprint)}`,
    `Title fingerprint match: ${yesNo(verifiedDraft || fill.titleFingerprintMatch)}`,
    `Default title prompt removed: ${historical ? yesNo(fill.defaultTitlePromptRemoved) :
      verifiedDraft ? "Yes" : "Not observed"}`,
    `Title browser-generated input observed: ${observed(fill.titleBrowserGeneratedInputObserved)}`,
    `Title synthetic input fallback used: ${observed(fill.titleSyntheticInputFallbackUsed)}`,
    `Title application input delivered: ${observed(fill.titleApplicationInputSignalDelivered)}`,
    `Trusted title input events: ${JSON.stringify(fill.trustedTitleInputEvents || [])}`,
    `Title inputType: ${printable(fill.titleInputType)}`,
    `Title data-testid remains: ${historical ? yesNo(fill.titleDataTestIdRemains) :
      yesNo(status.editorInspection?.selectedTitleSubregion?.data?.["data-testid"] ===
        "editorTitleParagraph")}`,
    `Title node rebuilt: ${yesNo(fill.titleNodeRebuilt)}`,
    `Body unchanged after title fill: ${observed(fill.bodyUnchangedAfterTitleFill)}`,
    `Body subregion still present: ${yesNo(fill.bodySubregionStillPresent ||
      status.editorInspection?.selectedBodySubregion)}`,
    `Body state: ${printable(status.bodyFillResult?.workflow === "MEDIUM_BODY_VERIFIED" ?
      "USER_CONTENT_PRESENT" : status.editorInspection?.bodySubregionState || fill.bodyState)}`,
    `Body default prompt still present: ${yesNo(
      status.bodyFillResult?.workflow === "MEDIUM_BODY_VERIFIED" ? false :
        verifiedDraft && status.editorInspection?.bodySubregionState === "SCAFFOLDING_ONLY" ||
        fill.bodyDefaultPromptStillPresent)}`,
    `Body user-authored content: ${yesNo(
      status.bodyFillResult?.bodyUserAuthoredContent || fill.bodyUserAuthoredContent ||
      status.bodyReconciliation?.partialBodyDetected ||
      status.editorInspection?.bodySubregionState === "USER_CONTENT_PRESENT")}`,
    `Autosave evidence: ${printable(fill.autosaveIndicatorObserved)}`,
    `Draft identity after title fill: ${printable(fill.draftIdentityAfterTitleFill ||
      status.editorInspection?.draftIdentity)}`,
    `Navigation occurred: ${observed(fill.navigationOccurred)}`,
    `Publish capability: No`
  ].join("\n");
}
function formatBodyDiagnostics(status) {
  const source = status.expectedBody || {};
  const eligibility = status.bodyEligibility || {};
  const control = status.bodyControlDiagnostics || {};
  const fill = status.bodyFillResult || {};
  const recovery = status.recoveryResult || {};
  const recoveryEligibility = status.recoveryEligibility || {};
  const recoveryControl = status.recoveryControlDiagnostics || {};
  const editor = status.editorInspection || {};
  const partial = status.bodyReconciliation || {};
  const observedBody = value => status.bodyReconciliation ? yesNo(value) : "Not observed";
  const workflow = partial.partialBodyDetected ? "MEDIUM_BODY_PARTIAL" :
    partial.currentBodyExactCanonicalMatch ? "MEDIUM_BODY_VERIFIED" :
      (fill.workflow || (status.bodyAttemptRecorded ? "MEDIUM_BODY_FILL_AMBIGUOUS" :
        eligibility.workflow || "MEDIUM_BODY_NOT_FILLED"));
  const readiness = eligibility.diagnostics || {};
  const guard = value => value === "NOT_EVALUATED" ? "NOT_EVALUATED" :
    value === undefined ? "Not observed" : value ? "PASS" : "FAIL";
  const selected = readiness.selectedBodySubregion;
  const selectedLabel = selected ? `${selected.tag}[data-testid="${
    selected.data?.["data-testid"] || ""}"]` : "Not observed";
  return [
    `Medium body workflow: ${workflow}`,
    `Expected body source: ${printable(source.source)}`,
    `Expected body character count: ${printable(source.characterCount)}`,
    `Expected body normalized character count: ${printable(source.normalizedCharacterCount)}`,
    `Expected normalized body length: ${printable(source.normalizedCharacterCount)}`,
    `Expected body fingerprint: ${printable(source.fingerprint)}`,
    `Expected body paragraph count: ${printable(source.paragraphCount)}`,
    `Expected body heading count: ${printable(source.headingCount)}`,
    `Markdown features: ${JSON.stringify(source.features || {})}`,
    `Unsupported Markdown structures: ${(source.unsupported || []).join(", ") || "None"}`,
    `Body fill eligibility: ${eligibility.ready ? "READY" : "NOT_READY"}`,
    `Normal body fill eligibility: ${eligibility.ready ? "READY" : "NOT_READY"}`,
    `Body fill reason: ${printable(eligibility.reason)}`,
    `Body recovery workflow: ${printable(recovery.workflow || recoveryEligibility.workflow)}`,
    `Body recovery eligibility: ${recoveryEligibility.ready ? "READY" : "NOT_READY"}`,
    `Body recovery reason: ${printable(recoveryEligibility.reason)}`,
    `Recovery failed guard: ${printable(recoveryEligibility.failedGuard)}`,
    `Recovery expected title available: ${yesNo(
      recoveryEligibility.diagnostics?.expectedTitleAvailable)}`,
    `Recovery expected title: ${printable(recoveryEligibility.diagnostics?.expectedTitle)}`,
    `Recovery expected title fingerprint: ${printable(
      recoveryEligibility.diagnostics?.expectedTitleFingerprint)}`,
    `Recovery live title: ${printable(recoveryEligibility.diagnostics?.liveTitle)}`,
    `Recovery live title fingerprint: ${printable(
      recoveryEligibility.diagnostics?.liveTitleFingerprint)}`,
    `Recovery canonical body source available: ${yesNo(
      recoveryEligibility.diagnostics?.canonicalBodySourceAvailable)}`,
    `Recovery canonical body blocks available: ${yesNo(
      recoveryEligibility.diagnostics?.canonicalBodyBlocksAvailable)}`,
    `Recovery expected full body fingerprint: ${printable(
      recoveryEligibility.diagnostics?.expectedFullBodyFingerprint)}`,
    `Recovery expected draft ID: ${printable(recoveryEligibility.diagnostics?.expectedDraftId)}`,
    `Recovery current draft ID: ${printable(recoveryEligibility.diagnostics?.currentDraftId)}`,
    `Recovery partial reconciliation result: ${printable(
      recoveryEligibility.diagnostics?.partialReconciliationResult)}`,
    `Recovery body resolver: ${printable(
      recoveryEligibility.diagnostics?.recoveryBodyResolver)}`,
    `Recovery body candidate count: ${printable(
      recoveryEligibility.diagnostics?.recoveryBodyCandidateCount)}`,
    `Recovery body selected node: ${printable(
      recoveryEligibility.diagnostics?.recoveryBodySelectedNode?.tag)}[data-testid="${printable(
      recoveryEligibility.diagnostics?.recoveryBodySelectedNode?.data?.["data-testid"])}"]`,
    `Recovery body shares inspector node: ${yesNo(
      recoveryEligibility.diagnostics?.recoveryBodySharesInspectorNode)}`,
    `Recovery unexpected-content candidate count: ${printable(
      recoveryEligibility.diagnostics?.unexpectedContentCandidateCount)}`,
    `Recovery unexpected-content candidates: ${
      (recoveryEligibility.diagnostics?.unexpectedContentCandidates || []).length ?
        (recoveryEligibility.diagnostics.unexpectedContentCandidates || [])
          .map((item, index) => `\n${index + 1}. tag=${item.tag}` +
            `\n   selector/path=${item.path}` +
            `\n   normalized text length=${item.normalizedTextLength}` +
            `\n   normalized text fingerprint=${item.normalizedTextFingerprint}` +
            `\n   safe short text=${item.safeShortText}` +
            `\n   reason considered unexpected=${item.reason}`).join("") : "None"}`,
    `Recovery unexpected-content candidates truncated: ${yesNo(
      recoveryEligibility.diagnostics?.unexpectedContentCandidatesTruncated)}`,
    `Recovery unexpected-content internal classification bug: ${yesNo(
      recoveryEligibility.diagnostics?.unexpectedContentClassificationBug)}`,
    `Recovery editor identity checks: ${JSON.stringify(
      recoveryEligibility.diagnostics?.editorIdentityChecks || {})}`,
    `Recovery route guard: ${guard(recoveryEligibility.guards?.route)}`,
    `Recovery draft ID guard: ${guard(recoveryEligibility.guards?.draftId)}`,
    `Recovery title text guard: ${guard(recoveryEligibility.guards?.titleText)}`,
    `Recovery title fingerprint guard: ${guard(recoveryEligibility.guards?.titleFingerprint)}`,
    `Recovery persisted title verification guard: ${guard(
      recoveryEligibility.guards?.titleVerification)}`,
    `Recovery source loaded guard: ${guard(recoveryEligibility.guards?.sourceLoaded)}`,
    `Recovery expected body fingerprint guard: ${guard(
      recoveryEligibility.guards?.expectedBodyFingerprint)}`,
    `Recovery canonical blocks loaded guard: ${guard(
      recoveryEligibility.guards?.canonicalBlocks)}`,
    `Recovery partial-state guard: ${guard(recoveryEligibility.guards?.partialState)}`,
    `Recovery partial fingerprint guard: ${guard(
      recoveryEligibility.guards?.partialFingerprint)}`,
    `Recovery block-count guard: ${guard(recoveryEligibility.guards?.blockCount)}`,
    `Recovery formatting-repair guard: ${guard(recoveryEligibility.guards?.formattingRepair)}`,
    `Recovery Publish-state guard: ${guard(recoveryEligibility.guards?.publishState)}`,
    `Recovery editor structure guard: ${guard(recoveryEligibility.guards?.editorStructure)}`,
    `Recovery body subregion guard: ${guard(recoveryEligibility.guards?.bodySubregion)}`,
    `Recovery unexpected content guard: ${guard(recoveryEligibility.guards?.unexpectedContent)}`,
    `Recovery final result: ${recoveryEligibility.ready ? "READY" : "NOT_READY"}`,
    `Recovery start block: ${printable(recoveryEligibility.recoveryStartBlock)}`,
    `Recovery next canonical block: ${printable(recoveryEligibility.recoveryNextCanonicalBlock ||
      recovery.nextExpectedCanonicalBlock)}`,
    `Block 1 requires formatting repair: ${yesNo(
      recoveryEligibility.blockOneRequiresFormattingRepair)}`,
    `Body recovery control present: ${yesNo(recoveryControl.present)}`,
    `Body recovery control interactability: ${printable(recoveryControl.interactability)}`,
    `Body recovery control disabled property: ${printable(recoveryControl.disabledProperty)}`,
    `Body recovery click listener attached: ${yesNo(recoveryControl.listenerAttached)}`,
    `Body recovery failure: ${printable(recovery.bodyRecoveryFailure)}`,
    `Last verified canonical block: ${printable(recovery.lastVerifiedCanonicalBlock)}`,
    `Next expected canonical block: ${printable(recovery.nextExpectedCanonicalBlock)}`,
    `Post-edit stabilization attempts: ${printable(recovery.postEditStabilizationAttempts)}`,
    `Stable DOM observed: ${yesNo(recovery.stableDOMObserved)}`,
    `Mutation repeated: ${yesNo(recovery.mutationRepeated)}`,
    `Block 1 text: ${printable(recovery.blockOneText)}`,
    `Block 1 fingerprint: ${printable(recovery.blockOneFingerprint)}`,
    `Block 1 canonical text: ${yesNo(recovery.blockOneCanonicalText)}`,
    `Block 1 canonical formatting: ${yesNo(recovery.blockOneCanonicalFormatting)}`,
    `Title unchanged: ${yesNo(recovery.titleUnchanged)}`,
    `Completed canonical blocks: ${JSON.stringify(recovery.completedBlocks || [])}`,
    `Body readiness route guard: ${guard(readiness.routeGuard)}`,
    `Body readiness draft identity guard: ${guard(readiness.draftIdentityGuard)}`,
    `Body readiness composite root guard: ${guard(readiness.compositeRootGuard)}`,
    `Body readiness title guard: ${guard(readiness.titleGuard)}`,
    `Body readiness body-subregion candidate count: ${readiness.bodySubregionCandidateCount ?? "Not observed"}`,
    `Body readiness selected body subregion: ${selectedLabel}`,
    `Body readiness body state: ${printable(readiness.bodyState)}`,
    `Body readiness authored-body check: ${guard(readiness.authoredBodyCheck)}`,
    `Body readiness continuation-safe guard: ${guard(readiness.continuationSafeGuard)}`,
    `Body readiness unsupported-Markdown guard: ${guard(readiness.unsupportedMarkdownGuard)}`,
    `Body readiness final result: ${readiness.finalResult || "NOT_READY"}`,
    `Body fill control present: ${yesNo(control.present)}`,
    `Body fill control actual interactability: ${printable(control.interactability)}`,
    `Body fill control disabled property: ${printable(control.disabledProperty)}`,
    `Body fill control hit-test: ${printable(control.hitTest)}`,
    `Body click listener attached: ${yesNo(control.listenerAttached)}`,
    `Body fill attempted: ${yesNo(fill.bodyFillAttempted)}`,
    `Body fill failure: ${printable(fill.bodyFillFailure)}`,
    `Partial body detected: ${observedBody(partial.partialBodyDetected)}`,
    `Body reconciliation available: ${yesNo(Boolean(status.bodyReconciliation))}`,
    `Current body normalized length: ${partial.currentBodyNormalizedLength ?? "Not observed"}`,
    `Current body fingerprint: ${printable(partial.currentBodyFingerprint)}`,
    `Current body block count: ${partial.currentBodyBlockCount ?? "Not observed"}`,
    `Current body DOM structure: ${JSON.stringify(partial.currentBodyDOMStructure || [])}`,
    `Current body safe text: ${printable(partial.currentBodySafeText)}`,
    `Current body matches Markdown heading markup: ${observedBody(
      partial.currentBodyMatchesMarkdownHeadingMarkup)}`,
    `Current body exact canonical match: ${observedBody(partial.currentBodyExactCanonicalMatch)}`,
    `Current body canonical-prefix match: ${observedBody(partial.currentBodyCanonicalPrefixMatch)}`,
    `Current body canonical text prefix: ${observedBody(partial.currentBodyCanonicalTextPrefix)}`,
    `Current body canonical formatting complete: ${observedBody(
      partial.currentBodyCanonicalFormattingComplete)}`,
    `Formatting discrepancy: ${printable(partial.formattingDiscrepancy)}`,
    `Canonical prefix matched through block: ${partial.canonicalPrefixMatchedThroughBlock ?? "Not observed"}`,
    `Canonical prefix matched character count: ${partial.canonicalPrefixMatchedCharacterCount ?? "Not observed"}`,
    `Expected next canonical block: ${JSON.stringify(partial.expectedNextCanonicalBlock || null)}`,
    `Partial body recovery classification: ${printable(partial.partialBodyRecoveryClassification)}`,
    `Body browser-generated input observed: ${yesNo(fill.bodyBrowserGeneratedInputObserved)}`,
    `Body synthetic input fallback used: ${yesNo(fill.bodySyntheticInputFallbackUsed)}`,
    `Body application input delivered: ${yesNo(fill.bodyApplicationInputDelivered)}`,
    `Trusted body input event count: ${fill.trustedBodyInputEventCount ?? 0}`,
    `Body inputTypes: ${(fill.bodyInputTypes || []).join(", ") || "None"}`,
    `Body default prompt removed: ${yesNo(fill.bodyDefaultPromptRemoved)}`,
    `Body user-authored content: ${yesNo(fill.bodyUserAuthoredContent ||
      partial.partialBodyDetected ||
      editor.bodySubregionState === "USER_CONTENT_PRESENT")}`,
    `Body exact normalized match: ${yesNo(fill.bodyExactNormalizedMatch ||
      recovery.bodyExactNormalizedMatch)}`,
    `Body fingerprint match: ${yesNo(fill.bodyFingerprintMatch ||
      recovery.bodyFingerprintMatch)}`,
    `Body paragraph count match: ${yesNo(fill.bodyParagraphCountMatch ||
      recovery.bodyParagraphCountMatch)}`,
    `Body heading count match: ${yesNo(recovery.bodyHeadingCountMatch)}`,
    `Title unchanged after body fill: ${yesNo(fill.titleUnchangedAfterBodyFill)}`,
    `Title exact match: ${yesNo(fill.titleExactMatch || editor.titleExactMatch)}`,
    `Title fingerprint match: ${yesNo(fill.titleFingerprintMatch || editor.titleFingerprintMatch)}`,
    `Draft identity unchanged: ${yesNo(fill.draftIdentityUnchanged ||
      editor.draftId === "fd6c1824c4de")}`,
    `Navigation unexpected: ${yesNo(fill.navigationUnexpected)}`,
    `Publish action occurred: ${yesNo(fill.publishActionOccurred ||
      recovery.publishActionOccurred)}`,
    `Autosave evidence after body fill: ${printable(fill.autosaveIndicatorObserved)}`,
    `Publish capability: No`
  ].join("\n");
}

function formatRetirement(status) {
  const history = status.retirement || {};
  const reservation = status.reservation || {};
  return [
    `URL Import retired: ${yesNo(history.urlImportRetired !== false)}`,
    `URL Import retry allowed: No`,
    `Known real Import attempts: ${history.knownRealImportAttempts ?? 2}`,
    `Attempt 1 activation: ${history.attempt1?.activation || "PROGRAMMATIC_NATIVE_BUTTON"}`,
    `Attempt 1 reconciliation: ${history.attempt1?.result || "NO_DRAFT_FOUND"}`,
    `Attempt 2 activation: ${history.attempt2?.activation || "TRUSTED_MANUAL_NATIVE_BUTTON"}`,
    `Attempt 2 reconciliation: ${history.attempt2?.result || "NO_DRAFT_FOUND"}`,
    `Reconciled native Import click observed: ${yesNo(history.attempt2?.nativeImportClickObserved)}`,
    `Reconciled native Import click trusted: ${yesNo(history.attempt2?.clickTrusted)}`,
    `Reconciled native Import identity valid: ${yesNo(history.attempt2?.buttonIdentityValid)}`,
    `Reconciled Drafts list conclusive and empty: ${yesNo(history.attempt2?.draftListConclusive && history.attempt2?.draftListEmpty)}`,
    `Legacy stored authorization: ${authorization(reservation.record || status.secondAttempt)}`,
    `Legacy stored observed clicks: ${printable((reservation.record || status.secondAttempt)?.observedNativeImportClicks)}`,
    `Legacy stored result: ${printable((reservation.record || status.secondAttempt)?.result)}`,
    `Reconciled attempt-2 evidence: observed trusted physical click; fresh empty Drafts list`,
    `Canonical target for future workflow: ${history.canonicalTarget || "https://cyporter.substack.com/p/saturation-of-artificial-intelligence-f8d"}`,
    `Publish capability: No`
  ].join("\n");
}

function formatReport(report, status = {}) {
  if (status.reconciliation?.pageKind === "STORIES")
    return formatStoriesReport(report, status);
  const lines = [
    `Content script ran: ${yesNo(report.contentScriptRan)}`,
    `Page: ${report.page}`,
    `Title: ${printable(report.title)}`,
    `Authentication: ${report.authentication.classification}`,
    `Decisive evidence: ${report.authentication.decisiveEvidence.join(", ") || "None"}`,
    `Observed account/navigation cues: ${report.authentication.accountCues.join(", ") || "None"}`,
    `Rendered signed-out controls: ${report.authentication.signedOutControls.join(", ") || "None"}`,
    "",
    `Stories control found: ${yesNo(report.stories.found)}`
  ];
  if (report.stories.found) {
    lines.push(`  Element type: ${printable(report.stories.elementType)}`,
      `  Role: ${printable(report.stories.role)}`,
      `  Accessible name: ${printable(report.stories.accessibleName)}`,
      `  Visible text: ${printable(report.stories.visibleText)}`,
      `  href: ${printable(report.stories.href)}`,
      `  data-testid: ${printable(report.stories.dataTestId)}`);
  }
  const view = report.importInterface;
  lines.push("", `Import interface found: ${yesNo(view.found)}`,
    `Import page detected: ${yesNo(view.found)}`,
    `Heading/title signature verified: ${yesNo(view.headingVerified)}`,
    `Fill preconditions met: ${yesNo(view.fillAllowed)}`,
    `Source editor status: ${printable(view.sourceStatus)}`,
    `Heading: ${printable(view.heading)}`,
    `Primary title: ${printable(view.primaryTitle?.text)}`,
    `Primary title element type: ${printable(view.primaryTitle?.elementType)}`,
    `Primary title role: ${printable(view.primaryTitle?.role)}`);
  lines.push(`Matching heading count: ${printable(view.matchingHeadingCount)}`,
    `Matching source editor count: ${printable(view.matchingSourceEditorCount)}`,
    `Matching Import action count: ${printable(view.matchingImportActionCount)}`);
  numbered(lines, "Visible input candidates", view.inputCandidates || []);
  numbered(lines, "Visible action candidates", view.actionCandidates || []);
  lines.push("", "Selected source URL input:",
    view.sourceUrlInput ? `  ${fields(view.sourceUrlInput)}` : "  None selected",
    `Source editor state: ${printable(view.sourceUrlInput?.editorState)}`,
    `Medium default example recognized: ${yesNo(view.sourceUrlInput?.mediumDefaultExampleRecognized)}`,
    `Default hostname: ${printable(view.sourceUrlInput?.defaultHostname)}`,
    `Normalized text length: ${printable(view.sourceUrlInput?.normalizedTextLength)}`,
    `Looks like absolute URL: ${yesNo(view.sourceUrlInput?.looksLikeAbsoluteUrl)}`,
    `Begins with http/https: ${yesNo(view.sourceUrlInput?.beginsWithHttp)}`,
    `Entire editor content is one URL: ${yesNo(view.sourceUrlInput?.entireContentOneUrl)}`,
    `Word count: ${printable(view.sourceUrlInput?.wordCount)}`,
    `Contains whitespace-separated prose: ${yesNo(view.sourceUrlInput?.containsWhitespaceSeparatedProse)}`,
    `Instruction-like: ${yesNo(view.sourceUrlInput?.instructionLike)}`,
    `Instruction/placeholder evidence: ${printable(view.sourceUrlInput?.placeholderEvidence)}`,
    `Fill-safe: ${yesNo(view.sourceUrlInput?.fillSafe && view.fillAllowed)}`,
    `Fill reason: ${printable(view.sourceUrlInput?.fillReason)}`,
    `Fill disabled reason: ${printable(view.fillDisabledReason)}`);
  const existingUrl = view.sourceUrlInput?.existingUrl;
  lines.push("", `URL present: ${yesNo(Boolean(existingUrl))}`);
  if (existingUrl) {
    lines.push(`URL length: ${existingUrl.normalizedUrlLength}`,
      `Scheme: ${existingUrl.scheme}`,
      `Hostname: ${existingUrl.hostname}`,
      `Port present: ${yesNo(existingUrl.portPresent)}`,
      `Path segment count: ${existingUrl.pathSegmentCount}`,
      `Path character length: ${existingUrl.pathCharacterLength}`,
      `Safe path shape: ${existingUrl.pathShape}`,
      `Query present: ${yesNo(existingUrl.queryPresent)}`,
      `Fragment present: ${yesNo(existingUrl.fragmentPresent)}`,
      `Username/password in URL: ${yesNo(existingUrl.credentialsPresent)}`,
      `Total normalized URL length: ${existingUrl.normalizedUrlLength}`,
      `Matches intended source exactly: ${yesNo(existingUrl.matchesIntendedSourceExactly)}`,
      `Same hostname as intended source: ${yesNo(existingUrl.sameHostnameAsIntendedSource)}`,
      `Obvious example-domain URL: ${yesNo(existingUrl.obviousExampleDomainUrl)}`,
      `Safe URL fingerprint: ${existingUrl.safeUrlFingerprint}`);
    if (view.sourceUrlInput?.mediumDefaultExampleRecognized) {
      lines.push(`Observed default fingerprint matches: ${yesNo(
        view.sourceUrlInput.observedDefaultFingerprintMatches)}`);
    }
  }
  lines.push("", "Selected Import action:",
    view.importAction ? `  ${fields(view.importAction)}` : "  None selected",
    `Back/Cancel/Close controls: ${view.backCancelClose.map(item => item.accessibleName).join(", ") || "None"}`);
  const other = (view.inventory || []).filter(item =>
    !["input", "textarea"].includes(item.tag) && item.role !== "textbox" && item.role !== "button");
  numbered(lines, "Other safe controls", other);
  numbered(lines, "Visible DOM inventory (maximum 50)", view.inventory || []);
  const state = status.importDiagnostics;
  const second = status.secondAttempt || {};
  lines.push("", `Import lifecycle state: ${printable(state?.lifecycleState || status.importState)}`,
    "Medium Import Reconciliation:",
    `Attempt 1 stored click attempts: 1`,
    `Last reconciliation: ${printable(status.lastReconciliation?.status)}`,
    `Persisted lifecycle: ${printable(status.persistedLifecycle ||
      status.restoredImportSession?.state)}`,
    `Persisted click attempts: ${printable(status.persistedClickAttempts ??
      status.restoredImportSession?.clickAttempts)}`,
    `Attempt 1 activation mechanism: ${printable(state?.attempt1ActivationMechanism ||
      "HTMLElement.click on native Import button")}`,
    "Attempt 1 reconciliation: NO_DRAFT_FOUND",
    `Current Fill synchronization: ${printable(state?.currentFillSynchronization ||
      status.lastFillResult?.fillSynchronizationStatus || "UNVERIFIED")}`,
    "Programmatic Import click trusted in Chrome fixture: No",
    `Second attempt eligibility: ${state?.secondAttemptEligibility || "NOT_READY"}`,
    `Second attempt readiness failure: ${printable(state?.secondAttemptReason || "None")}`,
    `Second attempt preparation control: ${state?.secondAttemptPreparationControl || "DISABLED"}`,
    `Second attempt authorization: ${status.reservation?.ok ?
      authorization(status.reservation.record) :
      state?.secondAttemptAuthorization || "NOT_AUTHORIZED"}`,
    `Authorized attempt number: ${printable(status.reservation?.ok ?
      status.reservation.record?.authorizedAttemptNumber :
      state?.authorizedAttemptNumber ?? second.authorizedAttemptNumber)}`,
    `Observed native Import clicks: ${printable(status.reservation?.ok ?
      status.reservation.record?.observedNativeImportClicks :
      state?.observedNativeImportClicks ?? second.observedNativeImportClicks ?? 0)}`,
    `Native Import click observed: ${yesNo(state?.nativeClickEvidence?.nativeImportClickObserved)}`,
    `Native Import click isTrusted: ${state?.nativeClickEvidence ?
      yesNo(state.nativeClickEvidence.isTrusted) : "Not observed"}`,
    `Native Import button identity still valid: ${state?.nativeClickEvidence ?
      yesNo(state.nativeClickEvidence.buttonIdentityValid) : "Not observed"}`,
    `Native Import attempt authorization present: ${state?.nativeClickEvidence ?
      yesNo(state.nativeClickEvidence.authorizationPresent) : "Not observed"}`,
    "Automatic native Import activation: DISABLED",
    `Reconciliation status: ${printable(status.reconciliation?.status || "NOT_RUN")}`,
    `Retry allowed: No`,
    `Validation/error message detected: ${yesNo(status.reconciliation?.validation?.detected)}`,
    `Text: ${printable(status.reconciliation?.validation?.text)}`,
    `Current-page validation message: ${printable(status.reconciliation?.validation?.text)}`,
    `See your story link: ${yesNo(status.reconciliation?.importPage?.seeYourStory)}`,
    `Draft/edit link visible: ${yesNo(status.reconciliation?.importPage?.draftEditLink)}`,
    `Import confirmation: ${printable(status.reconciliation?.importPage?.confirmation)}`,
    `Visible Import error: ${printable(status.reconciliation?.importPage?.error)}`,
    `Matching draft candidates: ${status.reconciliation?.stories?.matchingCandidates > 1 ?
      ">1" : printable(status.reconciliation?.stories?.matchingCandidates ?? 0)}`,
    `Exact title candidate: ${yesNo(status.reconciliation?.stories?.exactTitleCandidate)}`,
    `Candidate state: ${printable(status.reconciliation?.stories?.candidateState)}`,
    `Candidate href shape: ${printable(status.reconciliation?.stories?.candidateHrefShape)}`,
    `Draft list confirmed: ${yesNo(status.reconciliation?.stories?.draftListConfirmed)}`,
    `Draft editor exact title: ${yesNo(status.reconciliation?.editor?.exactTitle)}`,
    `Draft editor body UI: ${yesNo(status.reconciliation?.editor?.editableBody)}`,
    `Publish control present, untouched: ${yesNo(status.reconciliation?.editor?.publishControl)}`,
    `Import attempt available: ${yesNo(state?.attemptAvailable)}`,
    `Import click attempts: ${printable(state?.clickAttempts)}`,
    `Import guard allowed: ${yesNo(state?.guardAllowed)}`,
    `Import disabled reason: ${printable(state?.disabledReason ||
      "Extension Import control is unavailable on this page")}`,
    `Import raw-source guard: ${state?.raw?.sourceGuard ? "PASS" : "FAIL"}`,
    `Import raw-action guard: ${state?.raw?.actionGuard ? "PASS" : "FAIL"}`,
    `Import source normalized equality: ${yesNo(state?.raw?.source?.normalizedEquality)}`,
    `Import source fingerprint equality: ${yesNo(state?.raw?.source?.fingerprintEquality)}`,
    `Raw source exact match: ${yesNo(state?.raw?.source?.exactMatch)}`,
    `Raw source fingerprint match: ${yesNo(state?.raw?.source?.fingerprintMatch)}`,
    `Raw source normalized length: ${printable(state?.raw?.source?.normalizedLength)}`,
    `Raw source DOM representation: ${printable(state?.raw?.source?.representation)}`,
    `Inspector normalized length: ${printable(state?.raw?.source?.inspectorNormalizedLength)}`,
    `Resolver normalized length: ${printable(state?.raw?.source?.resolverNormalizedLength)}`,
    `Normalized values equal: ${yesNo(state?.raw?.source?.normalizedValuesEqual)}`,
    `Inspector fingerprint: ${printable(state?.raw?.source?.inspectorFingerprint)}`,
    `Resolver fingerprint: ${printable(state?.raw?.source?.resolverFingerprint)}`,
    `Import action raw match: ${yesNo(state?.raw?.action?.rawMatch)}`,
    `Native Import candidate count: ${printable(state?.raw?.action?.candidateCount)}`,
    `Import tag: ${printable(state?.raw?.action?.tag)}`,
    `Import type: ${printable(state?.raw?.action?.type)}`,
    `Import type attribute present: ${yesNo(state?.raw?.action?.typeAttributePresent)}`,
    `Import type attribute value: ${state?.raw?.action?.typeAttributePresent ?
      printable(state.raw.action.typeAttributeValue) : "Absent"}`,
    `Import type attribute valid: ${yesNo(state?.raw?.action?.typeAttributeValid)}`,
    `Import effective DOM type: ${printable(state?.raw?.action?.effectiveDomType)}`,
    `Import effective type match: ${yesNo(state?.raw?.action?.effectiveTypeMatch)}`,
    `Import visible text exact: ${yesNo(state?.raw?.action?.visibleTextExact)}`,
    `Import accessible name exact: ${yesNo(state?.raw?.action?.accessibleNameExact)}`,
    `Import visible: ${yesNo(state?.raw?.action?.visible)}`,
    `Import enabled: ${yesNo(state?.raw?.action?.enabled)}`,
    `Import connected to DOM: ${yesNo(state?.raw?.action?.connected)}`,
    `Inspector and resolver source identity: ${yesNo(state?.raw?.source?.identityMatchesInspection)}`,
    `Inspector and resolver Import identity: ${yesNo(state?.raw?.action?.identityMatchesInspection)}`,
    `Native Import identity: ${state?.raw?.actionGuard ? "PASS" : "FAIL"}`,
    `Overall Import guard: ${state?.guardAllowed ? "PASS" : "FAIL"}`,
    `First Import guard failure: ${printable(state?.firstFailureReason || state?.disabledReason)}`,
    `Extension Import control: ${state?.control || "DISABLED"}`,
    `Import logical eligibility: ${state?.logicalEligibility || "DISABLED"}`,
    `Import actual interactability: ${state?.interactability || "UNAVAILABLE"}`,
    `Hit-test: ${state?.hitTest || "UNAVAILABLE"}`,
    `Import button disabled property: ${printable(state?.disabledProperty)}`,
    `Import button disabled attribute: ${printable(state?.disabledAttribute)}`,
    `Import button aria-disabled: ${printable(state?.ariaDisabled)}`,
    `Import button pointer-events: ${printable(state?.pointerEvents)}`,
    `Import button cursor: ${printable(state?.cursor)}`,
    `Import panel pointer-events: ${printable(state?.parentPointerEvents)}`,
    `Import host pointer-events: ${printable(state?.hostPointerEvents)}`,
    `Import host display: ${printable(state?.hostDisplay)}`,
    `Import host z-index: ${printable(state?.hostZIndex)}`,
    `Import disabled CSS class: ${state?.disabledCssClass ? "present" : "absent"}`,
    `Import button bounding box: ${printable(state?.boundingBox)}`,
    `Restored session state: ${printable(status.restoredImportSession?.state)}`,
    `Restored session click attempts: ${printable(status.restoredImportSession?.clickAttempts)}`,
    `Restored session source: ${printable(status.restoredImportSession?.source)}`);
  return lines.join("\n") + (status.lastFillResult ?
    formatFillResult(status.lastFillResult) : "");
}

function formatStoriesReport(report, status) {
  const stories = status.reconciliation.stories;
  const draft = stories.draftControl || {};
  const lines = [
    `Content script ran: ${yesNo(report.contentScriptRan)}`,
    `Page: ${report.page}`,
    `Stories page recognized: ${yesNo(stories.pageRecognized)}`,
    `Stories UI hydrated: ${yesNo(stories.uiHydrated)}`,
    `Hydration wait elapsed: ${printable(stories.hydrationWaitElapsedMs)} ms`,
    `Drafts control found: ${yesNo(draft.found)}`,
    `Tag: ${printable(draft.tag)}`,
    `Role: ${printable(draft.role)}`,
    `Text: ${printable(draft.text)}`,
    `Selected/active: ${yesNo(draft.selected)}`,
    `href/path: ${printable(draft.hrefShape)}`,
    `Selected tab: ${printable(stories.selectedTab)}`,
    `Tab state: ${printable(stories.tabState)}`,
    `Selected tab evidence: ${(stories.selectedEvidence || []).join(", ") || "None"}`,
    `Drafts selected evidence: ${(stories.draftsSelectedEvidence || []).join(", ") || "None"}`,
    `Tablist found: ${yesNo(stories.tablistFound)}`,
    `Visible tabpanel count: ${printable(stories.visibleTabpanelCount ?? 0)}`,
    `Hidden tabpanel count: ${printable(stories.hiddenTabpanelCount ?? 0)}`,
    `Visible tabpanel: ${(stories.visibleTabpanels || []).length ?
      stories.visibleTabpanels.map(panel => `${panel.tag}#${printable(panel.id)} ` +
        `aria-labelledby=${printable(panel.ariaLabelledby)} ` +
        `aria-controls=${printable(panel.ariaControls)} ` +
        `children=${panel.childElementCount} text=${printable(panel.snippet)}`).join("; ") : "None observed"}`,
    `Stories content kind: ${printable(stories.contentKind)}`,
    `Stories content region: ${printable(stories.contentRegion)}`,
    `Stories root container: ${JSON.stringify(stories.storiesRootContainer || null)}`,
    `Tablist container: ${JSON.stringify(stories.tablistContainer || null)}`,
    `Post-tab content region identified: ${yesNo(stories.postTabContentRegionIdentified)}`,
    `Post-tab content evidence: ${printable(stories.postTabContentEvidence)}`,
    `Post-tab content container: ${JSON.stringify(stories.postTabContentContainer || null)}`,
    `Content container tag: ${printable(stories.postTabContentContainer?.tag)}`,
    `Content container role: ${printable(stories.postTabContentContainer?.role)}`,
    `Content container classes: ${(stories.postTabContentContainer?.classes || []).join(" ") || "None"}`,
    `Content child count: ${printable(stories.postTabContentContainer?.childCount)}`,
    `Visible child count: ${printable(stories.visibleContentChildCount)}`,
    `Content region conclusively rendered: ${yesNo(stories.contentRegionRendered)}`,
    `Loading evidence: ${yesNo(stories.loadingEvidence)}`,
    `Loading details: ${(stories.loadingDetails || []).join("; ") || "None"}`,
    `Error evidence: ${yesNo(stories.errorEvidence)}`,
    `Error details: ${(stories.errorDetails || []).join("; ") || "None"}`,
    `Empty-state block: ${yesNo(stories.promoBlock?.found)}`,
    `Empty-state heading: ${printable(stories.promoBlock?.heading)}`,
    `Empty-state nearby body text: ${(stories.promoBlock?.nearbyBodyText || []).join("; ") || "None"}`,
    `Empty-state controls: ${JSON.stringify(stories.promoBlock?.controls || [])}`,
    `Empty-state container child count: ${printable(stories.promoBlock?.childCount)}`,
    `Empty-state story-entry siblings: ${printable(stories.promoBlock?.storyEntrySiblings)}`,
    `Empty-state loading siblings: ${printable(stories.promoBlock?.loadingSiblings)}`,
    `Empty-state dedicated: ${yesNo(stories.promoBlock?.dedicated)}`,
    `Empty-state evidence: ${(stories.emptyStateEvidence || []).join("; ") || "None"}`,
    `Draft list confirmed: ${yesNo(stories.draftListConfirmed)}`,
    `Draft list conclusively loaded: ${yesNo(stories.listConclusivelyLoaded)}`,
    `Draft list empty state: ${yesNo(stories.draftListEmptyState)}`,
    `Draft entries visible: ${yesNo(stories.draftEntriesVisible)}`,
    `List incomplete/loading: ${yesNo(stories.listIncomplete)}`,
    `Visible draft candidate count: ${printable(stories.visibleDraftCandidateCount)}`,
    `Exact-title matches: ${printable(stories.exactTitleMatches)}`,
    `Reconciliation status: ${status.reconciliation.status}`,
    "Attempt 1 stored click attempts: 1",
    `Last reconciliation: ${printable(status.lastReconciliation?.status)}`,
    `Persisted lifecycle: ${printable(status.persistedLifecycle ||
      status.restoredImportSession?.state)}`,
    `Persisted click attempts: ${printable(status.persistedClickAttempts ??
      status.restoredImportSession?.clickAttempts)}`,
    "Attempt 1 activation mechanism: HTMLElement.click on native Import button",
    "Attempt 1 reconciliation: NO_DRAFT_FOUND",
    `Current Fill synchronization: ${printable(status.lastFillResult?.fillSynchronizationStatus ||
      "UNVERIFIED")}`,
    "Programmatic Import click trusted in Chrome fixture: No",
    "Second attempt eligibility: NOT_READY",
    "Second attempt readiness failure: IMPORT_PAGE_MISMATCH",
    "Second attempt preparation control: DISABLED",
    `Second attempt authorization: ${status.secondAttempt?.state ===
      "SECOND_ATTEMPT_ARMED" ? "ARMED" : status.secondAttempt?.state ===
      "SECOND_ATTEMPT_USER_CLICK_OBSERVED" ||
      ["SECOND_ATTEMPT_RESULT_VERIFIED", "SECOND_ATTEMPT_RESULT_AMBIGUOUS"]
        .includes(status.secondAttempt?.state) ? "CONSUMED" : "NOT_AUTHORIZED"}`,
    `Authorized attempt number: ${printable(status.secondAttempt?.authorizedAttemptNumber)}`,
    `Observed native Import clicks: ${status.secondAttempt?.observedNativeImportClicks ?? 0}`,
    "Automatic native Import activation: DISABLED",
    "Retry allowed: No",
    "Automatic tab navigation: No",
    "Automatic draft opening: No",
    "",
    "Visible tab diagnostics:",
    ...(stories.tabDiagnostics || []).map((item, index) =>
      `  ${index + 1}. Text: ${printable(item.visibleText)} | Accessible name: ` +
      `${printable(item.accessibleName)} | aria-selected: ${printable(item.ariaSelected)} | ` +
      `aria-current: ${printable(item.ariaCurrent)} | aria-controls: ` +
      `${printable(item.ariaControls)} | tabindex: ${printable(item.tabIndex)} | ` +
      `data-testid: ${printable(item.dataTestId)} | Classes: ` +
      `${item.classes?.join(" ") || "None"} | ID: ${printable(item.id)} | ` +
      `href/path: ${printable(item.hrefShape)} | Parent: ` +
      `${printable(item.parentTag)} role=${printable(item.parentRole)} ` +
      `classes=${item.parentClasses?.join(" ") || "None"} | ` +
      `State classes: ${item.stateClasses?.join(" ") || "None"} | ` +
      `State attributes: ${item.stateAttributes?.join(" ") || "None"} | ` +
      `Ancestor state: ${JSON.stringify(item.ancestorState || [])} | ` +
      `Style: font-weight=${printable(item.style?.fontWeight)}, ` +
      `text-decoration-line=${printable(item.style?.textDecorationLine)}, ` +
      `border-bottom-style=${printable(item.style?.borderBottomStyle)}, ` +
      `border-bottom-width=${printable(item.style?.borderBottomWidth)}, ` +
      `opacity=${printable(item.style?.opacity)}`),
    ...(!(stories.tabDiagnostics || []).length ? ["  None observed"] : []),
    `Unique visual differences: ${(stories.visualEvidence || []).join("; ") || "None observed"}`,
    "",
    "Nearby Stories text:",
    ...(stories.contentSnippets || []).map((item, index) =>
      `  ${index + 1}. ${item.tag} role=${printable(item.role)} ` +
      `parent=${printable(item.parentTag)} role=${printable(item.parentRole)}: ` +
      `${printable(item.text)}`),
    ...(!(stories.contentSnippets || []).length ? ["  None observed"] : []),
    "",
    "Post-tab content inventory (maximum 60):"
  ];
  if (!stories.inventory?.length) lines.push("  None observed");
  else stories.inventory.forEach((item, index) => lines.push(
    `  ${index + 1}. Tag: ${item.tag} | Role: ${printable(item.role)} | ` +
    `Visible text: ${printable(item.visibleText)} | Accessible name: ` +
    `${printable(item.accessibleName)} | href/path: ${printable(item.hrefShape)} | ` +
      `Selected/active: ${yesNo(item.selected)}`));
  lines.push("", "Story card structures (maximum 25):");
  if (!stories.storyCardStructures?.length) lines.push("  None observed");
  else stories.storyCardStructures.forEach((item, index) => lines.push(
    `  ${index + 1}. ${item.tag} role=${printable(item.role)} ` +
    `classes=${(item.classes || []).join(" ") || "None"} ` +
    `headings=${item.headingCount} links=${item.linkCount} ` +
    `times=${item.timeCount} buttons=${item.buttonCount} text=${printable(item.text)}`));
  lines.push("", "Visible story entries (maximum 40):");
  if (!stories.entries?.length) lines.push("  None observed");
  else stories.entries.forEach((item, index) => lines.push(
    `  ${index + 1}. Title: ${item.title} | State: ${item.state} | ` +
    `href/path: ${item.hrefShape} | Editor/draft route: ${yesNo(item.editUrlExists)} | ` +
    `Visible timestamp: ${printable(item.visibleTimestamp)}`));
  return lines.join("\n");
}

function formatFillResult(fill) {
  const counts = fill.fillEventCounts || {};
  return ["", `Fill result: ${printable(fill.status)}`,
    `Fill attempted: ${yesNo(fill.urlFillAttempted)}`,
    `Default example replaced: ${yesNo(fill.defaultExampleReplaced)}`,
    `Fill succeeded: ${yesNo(fill.fillSucceeded)}`,
    `URL fill attempted: ${yesNo(fill.urlFillAttempted)}`,
    `Exact intended source present: ${yesNo(fill.exactUrlPresentAfterward)}`,
    `Exact URL present afterward: ${yesNo(fill.exactUrlPresentAfterward)}`,
    `Fill DOM result: ${printable(fill.fillDomResult || "FAILED")}`,
    `Fill fingerprint match: ${yesNo(fill.fingerprintMatch)}`,
    `Browser-generated input observed: ${yesNo(fill.browserInputObserved)}`,
    `Synthetic input fallback used: ${yesNo(fill.syntheticInputFallbackUsed)}`,
    `Application input signal delivered: ${yesNo(fill.applicationInputSignalDelivered)}`,
    `Fill synchronization status: ${printable(fill.fillSynchronizationStatus || "UNVERIFIED")}`,
    `beforeinput events observed: ${counts.beforeinput ?? 0}`,
    `input events observed: ${counts.input ?? 0}`,
    `change events observed: ${counts.change ?? 0}`,
    `trusted input events observed: ${counts.trustedInput ?? 0}`,
    `synthetic input events observed: ${counts.syntheticInput ?? 0}`,
    ...((fill.fillEvents || []).map((event, index) =>
      `Editing event ${index + 1}: type=${event.type}; ` +
      `target is selected source editor=${yesNo(event.targetIsSelectedEditor)}; ` +
      `bubbles=${yesNo(event.bubbles)}; cancelable=${yesNo(event.cancelable)}; ` +
      `composed=${yesNo(event.composed)}; isTrusted=${yesNo(event.isTrusted)}; ` +
      `inputType=${printable(event.inputType)}; data length=${printable(event.dataLength)}`)),
    `Import clicked: ${yesNo(fill.importClicked)}`,
    `Form submitted: ${yesNo(fill.formSubmitted)}`,
    `Navigation occurred: ${yesNo(fill.navigationOccurred)}`,
    `Import button still exists: ${yesNo(fill.importButtonStillExists)}`,
    `Current page remains /p/import: ${yesNo(fill.pageRemainsImport)}`].join("\n");
}

async function inspectCurrentPage() {
  button.disabled = true;
  setTitleEnabled(false);
  setBodyEnabled(false);
  setRecoveryEnabled(false);
  output.textContent = "Inspecting the current tab…";
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) {
      output.textContent = "No active tab is available.";
      return;
    }
    const response = await chrome.tabs.sendMessage(tab.id, { type: "INSPECT_MEDIUM_PAGE" });
    let reservation;
    try { reservation = await inspectReservation(); }
    catch (error) { reservation = { ok: false, reason: error.message }; }
    if (response?.ok && response.report) {
      const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
      const sameTab = activeTab?.id === tab.id;
      setTitleEnabled(Boolean(response.titleEligibility?.ready && sameTab));
      setBodyEnabled(Boolean(response.bodyEligibility?.ready && sameTab));
      setRecoveryEnabled(Boolean(response.recoveryEligibility?.ready && sameTab));
      output.textContent = formatTitleDiagnostics(response) + "\n\n" +
        formatBodyDiagnostics(response) + "\n\n" +
        (response.editorInspection &&
          !["IMPORT", "STORIES"].includes(response.reconciliation?.pageKind) ?
        formatEditorInspection(response.editorInspection) :
        formatReport(response.report, { ...response, reservation }) + "\n\n" +
          formatPreparation({ ...response, reservation })) + "\n\n" +
        formatRetirement({ ...response, reservation }) + "\n\n" +
        formatReservation(reservation) +
        (activeTab?.id === tab.id ? "" : "\n\nInspected tab is no longer active.");
    } else {
      output.textContent = "Page inspection could not be completed safely.";
    }
  } catch {
    output.textContent = "No Medium content script responded. Open or refresh a medium.com tab and try again.";
  } finally {
    button.disabled = false;
  }
}

button.addEventListener("click", inspectCurrentPage);

titleButton?.addEventListener?.("click", async () => {
  setTitleEnabled(false);
  output.textContent = "Filling the verified Medium title…";
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) {
      output.textContent = "No active tab is available.";
      return;
    }
    const response = await chrome.tabs.sendMessage(tab.id,
      { type: "FILL_VERIFIED_MEDIUM_TITLE" });
    if (!response?.ok) {
      output.textContent = `Title fill stopped: ${printable(response?.error)}`;
      return;
    }
    await inspectCurrentPage();
  } catch {
    output.textContent = "Title fill could not be completed safely. Inspect the page before any further action.";
  }
});

bodyButton?.addEventListener?.("click", async () => {
  setBodyEnabled(false);
  output.textContent = "Filling the verified Medium body…";
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) { output.textContent = "No active tab is available."; return; }
    const response = await chrome.tabs.sendMessage(tab.id,
      { type: "FILL_VERIFIED_MEDIUM_BODY" });
    if (!response?.ok) {
      output.textContent = `Body fill stopped: ${printable(response?.error)}`;
      return;
    }
    await inspectCurrentPage();
  } catch {
    output.textContent = "Body fill could not be completed safely. Inspect the page before any further action.";
  }
});

recoveryButton?.addEventListener?.("click", async () => {
  setRecoveryEnabled(false);
  output.textContent = "Resuming the verified Medium body…";
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) { output.textContent = "No active tab is available."; return; }
    const response = await chrome.tabs.sendMessage(tab.id,
      { type: "RESUME_VERIFIED_MEDIUM_BODY" });
    if (!response?.ok) {
      output.textContent = `Body recovery stopped: ${printable(response?.error)}`;
      return;
    }
    await inspectCurrentPage();
  } catch {
    output.textContent = "Body recovery could not be completed safely. Inspect the page before any further action.";
  }
});

reservationButton?.addEventListener?.("click", async () => {
  reservationButton.disabled = true;
  try {
    output.textContent = formatReservation(await inspectReservation());
  } catch (error) {
    output.textContent = formatReservation({ ok: false, reason: error.message });
  } finally {
    reservationButton.disabled = false;
  }
});

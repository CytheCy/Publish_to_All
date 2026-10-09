"use strict";

const button = document.getElementById("inspect");
const output = document.getElementById("report");

function yesNo(value) {
  return value ? "Yes" : "No";
}

function printable(value) {
  return value === null || value === undefined || value === "" ? "Not observed" : String(value);
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
    `Known Import attempts: 1`,
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
    `Second attempt eligibility reason: ${printable(state?.secondAttemptReason)}`,
    `Second attempt authorization: ${state?.secondAttemptAuthorization || "NOT_AUTHORIZED"}`,
    `Authorized attempt number: ${printable(state?.authorizedAttemptNumber ??
      second.authorizedAttemptNumber)}`,
    `Observed native Import clicks: ${printable(state?.observedNativeImportClicks ??
      second.observedNativeImportClicks ?? 0)}`,
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
    "Known Import attempts: 1",
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
    `Second attempt authorization: ${status.secondAttempt?.state ===
      "SECOND_ATTEMPT_ARMED" ? "ARMED" : "NOT_AUTHORIZED"}`,
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

button.addEventListener("click", async () => {
  button.disabled = true;
  output.textContent = "Inspecting the current tab…";
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) {
      output.textContent = "No active tab is available.";
      return;
    }
    const response = await chrome.tabs.sendMessage(tab.id, { type: "INSPECT_MEDIUM_PAGE" });
    if (response?.ok && response.report) {
      const [activeTab] = await chrome.tabs.query({ active: true, currentWindow: true });
      output.textContent = formatReport(response.report, response) +
        (activeTab?.id === tab.id ? "" : "\n\nInspected tab is no longer active.");
    } else {
      output.textContent = "Page inspection could not be completed safely.";
    }
  } catch {
    output.textContent = "No Medium content script responded. Open or refresh a medium.com tab and try again.";
  } finally {
    button.disabled = false;
  }
});

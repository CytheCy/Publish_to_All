"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const retirement = require("../medium-retirement.js");

const popupSource = fs.readFileSync(path.join(__dirname, "../popup.js"), "utf8");
const popupHtml = fs.readFileSync(path.join(__dirname, "../popup.html"), "utf8");

function report() {
  return {
    contentScriptRan: true, page: "https://medium.com/p/import", title: "Medium",
    authentication: { classification: "UNKNOWN", decisiveEvidence: [], accountCues: [],
      signedOutControls: [] },
    stories: { found: false },
    importInterface: {
      found: true, headingVerified: true, fillAllowed: true, sourceStatus: "READY",
      fillDisabledReason: "None", heading: "See your story on Medium",
      inputCandidates: [], actionCandidates: [],
      sourceUrlInput: { fillSafe: true, editorState: "MEDIUM_DEFAULT_EXAMPLE_URL" },
      importAction: { enabled: true }, backCancelClose: [], inventory: []
    }
  };
}

function popupFixture(response = { ok: true, report: report() }) {
  const inspect = { disabled: false, addEventListener(_type, fn) { this.action = fn; } };
  const reservationControl = { disabled: false,
    addEventListener(_type, fn) { this.action = fn; } };
  const titleControl = { disabled: true, attributes: new Map([["disabled", ""]]),
    setAttribute(name, value) { this.attributes.set(name, value); },
    removeAttribute(name) { this.attributes.delete(name); },
    getAttribute(name) { return this.attributes.get(name) ?? null; },
    hasAttribute(name) { return this.attributes.has(name); },
    addEventListener(_type, fn) { this.action = fn; } };
  const bodyControl = { ...titleControl, attributes: new Map([["disabled", ""]]),
    addEventListener(_type, fn) { this.action = fn; } };
  const recoveryControl = { ...titleControl, attributes: new Map([["disabled", ""]]),
    addEventListener(_type, fn) { this.action = fn; } };
  const output = { textContent: "" };
  const sent = [];
  const chrome = { tabs: {
    query: async () => [{ id: 17, url: "https://medium.com/new-story" }],
    sendMessage: async (_id, message) => { sent.push(message.type); return response; }
  }, runtime: { sendMessage(_message, callback) { callback({ ok: true,
    exists: false, record: { state: "SECOND_ATTEMPT_NOT_AUTHORIZED",
      authorizedAttemptNumber: null, observedNativeImportClicks: 0 },
    source: "chrome.storage.session" }); } } };
  vm.runInNewContext(popupSource, {
    document: { getElementById: id => ({ inspect, report: output,
      "inspect-reservation": reservationControl,
      "fill-medium-title": titleControl, "fill-medium-body": bodyControl,
      "resume-medium-body": recoveryControl })[id] }, chrome
  });
  return { inspect, reservationControl, titleControl, bodyControl,
    recoveryControl, output, sent, chrome };
}

test("popup exposes only the exact recovery path after inspection", async () => {
  const fixture = popupFixture({ ok: true, report: report(),
    reconciliation: { pageKind: "IMPORT" },
    editorInspection: { titleVerifiedInPersistedDraft: true,
      titleExactMatch: true, titleFingerprintMatch: true,
      titleFingerprint: "fnv1a32:5e81665d", draftId: "fd6c1824c4de",
      bodySubregionState: "UNKNOWN" },
    bodyEligibility: { ready: false, reason: "BODY_ALREADY_AUTHORED" },
    recoveryEligibility: { ready: true, reason: "None",
      workflow: "MEDIUM_BODY_RECOVERY_READY", recoveryStartBlock: 1,
      recoveryNextCanonicalBlock: 2, blockOneRequiresFormattingRepair: true,
      diagnostics: { unexpectedContentCandidateCount: 0,
        unexpectedContentCandidates: [], unexpectedContentClassificationBug: false },
      guards: Object.fromEntries(["route", "draftId", "titleText", "titleFingerprint",
        "titleVerification",
        "sourceLoaded", "expectedBodyFingerprint", "canonicalBlocks", "partialState",
        "partialFingerprint", "blockCount", "formattingRepair", "publishState",
        "editorStructure", "bodySubregion", "unexpectedContent"].map(name => [name, true])) },
    recoveryControlDiagnostics: { present: true, interactability: "ENABLED",
      disabledProperty: false, listenerAttached: true },
    bodyReconciliation: { partialBodyDetected: true, currentBodySafeText: "01",
      currentBodyFingerprint: "fnv1a32:20ed70ba",
      partialBodyRecoveryClassification: "EXACT_CANONICAL_PREFIX" } });
  await fixture.inspect.action();
  assert.equal(fixture.bodyControl.disabled, true);
  assert.equal(fixture.recoveryControl.disabled, false);
  assert.match(fixture.output.textContent, /Body recovery eligibility: READY/);
  assert.match(fixture.output.textContent, /Body recovery reason: None/);
  assert.match(fixture.output.textContent, /Recovery title fingerprint guard: PASS/);
  assert.match(fixture.output.textContent, /Recovery canonical blocks loaded guard: PASS/);
  assert.match(fixture.output.textContent, /Recovery Publish-state guard: PASS/);
  assert.match(fixture.output.textContent, /Recovery final result: READY/);
  assert.match(fixture.output.textContent,
    /Recovery unexpected-content candidate count: 0/);
  assert.match(fixture.output.textContent,
    /Recovery unexpected content guard: PASS/);
  assert.match(fixture.output.textContent, /Recovery start block: 1/);
  assert.match(fixture.output.textContent, /Recovery next canonical block: 2/);
  assert.match(fixture.output.textContent, /Block 1 requires formatting repair: Yes/);
  assert.match(fixture.output.textContent, /Body recovery control interactability: ENABLED/);
  assert.match(fixture.output.textContent, /Normal body fill eligibility: NOT_READY/);
  assert.match(fixture.output.textContent, /Publish capability: No/);
  assert.deepEqual(fixture.sent, ["INSPECT_MEDIUM_PAGE"]);
});

test("popup names bounded unexpected authored content", async () => {
  const fixture = popupFixture({ ok: true, report: report(),
    recoveryEligibility: { ready: false, reason: "UNEXPECTED_BODY_CONTENT",
      failedGuard: "unexpectedContent", guards: { unexpectedContent: false },
      diagnostics: { unexpectedContentCandidateCount: 1,
        unexpectedContentCandidates: [{ tag: "p", path: "div#editor_7 > p",
          normalizedTextLength: 15, normalizedTextFingerprint: "fnv1a32:12345678",
          safeShortText: "unexpected text",
          reason: "ADDITIONAL_AUTHORED_STORY_BLOCK" }],
        unexpectedContentClassificationBug: false } } });
  await fixture.inspect.action();
  assert.match(fixture.output.textContent,
    /Recovery unexpected-content candidate count: 1/);
  assert.match(fixture.output.textContent,
    /tag=p\n   selector\/path=div#editor_7 > p/);
  assert.match(fixture.output.textContent,
    /reason considered unexpected=ADDITIONAL_AUTHORED_STORY_BLOCK/);
  assert.match(fixture.output.textContent,
    /Recovery unexpected content guard: FAIL/);
  assert.deepEqual(fixture.sent, ["INSPECT_MEDIUM_PAGE"]);
});

test("popup reports an internal classification bug when no node explains a failure", async () => {
  const fixture = popupFixture({ ok: true, report: report(),
    recoveryEligibility: { ready: false, reason: "UNEXPECTED_BODY_CONTENT",
      failedGuard: "unexpectedContent", guards: { unexpectedContent: false },
      diagnostics: { unexpectedContentCandidateCount: 0,
        unexpectedContentCandidates: [], unexpectedContentClassificationBug: true } } });
  await fixture.inspect.action();
  assert.match(fixture.output.textContent,
    /Recovery unexpected-content candidate count: 0/);
  assert.match(fixture.output.textContent,
    /Recovery unexpected-content internal classification bug: Yes/);
});

test("popup distinguishes body-dependent guards that were not evaluated", async () => {
  const fixture = popupFixture({ ok: true, report: report(),
    recoveryEligibility: { ready: false, reason: "BODY_SUBREGION_AMBIGUOUS",
      failedGuard: "bodySubregion", guards: { bodySubregion: false,
        partialFingerprint: "NOT_EVALUATED", blockCount: "NOT_EVALUATED",
        formattingRepair: "NOT_EVALUATED" } } });
  await fixture.inspect.action();
  assert.match(fixture.output.textContent, /Recovery body subregion guard: FAIL/);
  assert.match(fixture.output.textContent,
    /Recovery partial fingerprint guard: NOT_EVALUATED/);
  assert.match(fixture.output.textContent, /Recovery block-count guard: NOT_EVALUATED/);
  assert.match(fixture.output.textContent,
    /Recovery formatting-repair guard: NOT_EVALUATED/);
});

test("body popup reports canonical readiness and disables after a verified fill", async () => {
  let filled = false;
  const status = () => ({ ok: true, report: report(),
    editorInspection: { detected: false, editorRouteRecognized: false,
      editorStructuralEvidencePresent: false, editorUrlShape: "/p/[story-id]/edit",
      draftIdentity: "Medium story path /p/fd6c1824c4de/edit",
      editorArchitecture: "COMPOSITE_SINGLE_CONTENTEDITABLE",
      compositeEditorRootDetected: true, compositeEditorEvidence: [],
      auxiliaryContenteditablesExcluded: [], defaultValuePrompts: [],
      titleSubregionCandidateCount: 1, bodySubregionCandidateCount: 1,
      titleSubregionState: "USER_CONTENT_PRESENT",
      bodySubregionState: filled ? "USER_CONTENT_PRESENT" : "SCAFFOLDING_ONLY",
      titleEditorCandidateCount: 0, bodyEditorCandidateCount: 0,
      titleCurrentState: "UNKNOWN", bodyCurrentState: "UNKNOWN",
      publishControlCount: 1, publishControl: { name: "Publish", visible: true },
      draftSaveIndicator: "Saved", settingsControls: [], editableCandidates: [],
      classification: "NONEMPTY_DRAFT", workflowState: "MEDIUM_TITLE_VERIFIED_BODY_BLANK" },
    reconciliation: { pageKind: "EDITOR" },
    bodyEligibility: { ready: !filled, reason: filled ? "BODY_ALREADY_FILLED_BY_WORKFLOW" :
      "None", workflow: filled ? "MEDIUM_BODY_NOT_FILLED" : "MEDIUM_BODY_FILL_READY",
    diagnostics: { routeGuard: true, draftIdentityGuard: true,
      compositeRootGuard: true, titleGuard: true, bodySubregionCandidateCount: 1,
      selectedBodySubregion: { tag: "p", data: { "data-testid": "editorParagraphText" } },
      bodyState: filled ? "USER_CONTENT_PRESENT" : "SCAFFOLDING_ONLY",
      authoredBodyCheck: !filled, continuationSafeGuard: !filled,
      unsupportedMarkdownGuard: true, finalResult: filled ? "NOT_READY" : "READY" } },
    bodyControlDiagnostics: { present: true, interactability: filled ? "BLOCKED" :
      "ENABLED", disabledProperty: filled, hitTest: "BUTTON", listenerAttached: true },
    expectedBody: { source: "In/01_01_2040.md", fingerprint: "fnv1a32:342d11eb",
      paragraphCount: 18, unsupported: [] },
    bodyFillResult: filled ? { workflow: "MEDIUM_BODY_VERIFIED", bodyFillAttempted: true,
      bodyFingerprintMatch: true, titleUnchangedAfterBodyFill: true } : null });
  const fixture = popupFixture(status());
  fixture.chrome.tabs.sendMessage = async (_id, message) => {
    fixture.sent.push(message.type);
    if (message.type === "FILL_VERIFIED_MEDIUM_BODY") {
      filled = true;
      return { ok: true, bodyFillResult: status().bodyFillResult };
    }
    return status();
  };
  await fixture.inspect.action();
  assert.equal(fixture.bodyControl.disabled, false);
  assert.match(fixture.output.textContent, /Body fill eligibility: READY/);
  assert.match(fixture.output.textContent, /Body readiness route guard: PASS/);
  assert.match(fixture.output.textContent, /Body readiness body-subregion candidate count: 1/);
  assert.match(fixture.output.textContent,
    /Body readiness selected body subregion: p\[data-testid="editorParagraphText"\]/);
  assert.match(fixture.output.textContent, /Body readiness final result: READY/);
  assert.match(fixture.output.textContent, /Expected body fingerprint: fnv1a32:342d11eb/);
  await fixture.bodyControl.action();
  assert.equal(fixture.bodyControl.disabled, true);
  assert.match(fixture.output.textContent, /Medium body workflow: MEDIUM_BODY_VERIFIED/);
  assert.deepEqual(fixture.sent,
    ["INSPECT_MEDIUM_PAGE", "FILL_VERIFIED_MEDIUM_BODY", "INSPECT_MEDIUM_PAGE"]);
});

test("partial draft inspection keeps title verified and body fill blocked", async () => {
  const fixture = popupFixture({ ok: true, report: report(),
    reconciliation: { pageKind: "IMPORT" },
    editorInspection: { titleVerifiedInPersistedDraft: true,
      titleExactMatch: true, titleFingerprintMatch: true,
      titleFingerprint: "fnv1a32:5e81665d", draftId: "fd6c1824c4de",
      bodySubregionState: "UNKNOWN" },
    titleEligibility: { ready: false, workflow: "MEDIUM_TITLE_NOT_FILLED" },
    bodyEligibility: { ready: false, reason: "BODY_ALREADY_FILLED_BY_WORKFLOW",
      workflow: "MEDIUM_BODY_NOT_FILLED" },
    bodyFillResult: { workflow: "MEDIUM_BODY_FILL_AMBIGUOUS",
      bodyFillAttempted: true, bodyFillFailure: "BLOCK_POSTCHECK_FAILED",
      titleExactMatch: true, titleFingerprintMatch: true },
    bodyReconciliation: { partialBodyDetected: true,
      currentBodyNormalizedLength: 2, currentBodyFingerprint: "fnv1a32:20ed70ba",
      currentBodyBlockCount: 1, currentBodyDOMStructure: [{ tag: "p",
        dataTestId: "editorParagraphText", children: ["font"], normalizedLength: 2 }],
      currentBodySafeText: "01", currentBodyExactCanonicalMatch: false,
      currentBodyCanonicalPrefixMatch: true,
      canonicalPrefixMatchedThroughBlock: 1,
      canonicalPrefixMatchedCharacterCount: 2,
      expectedNextCanonicalBlock: { number: 2, kind: "paragraph" },
      partialBodyRecoveryClassification: "EXACT_CANONICAL_PREFIX" } });
  await fixture.inspect.action();
  assert.equal(fixture.bodyControl.disabled, true);
  assert.deepEqual(fixture.sent, ["INSPECT_MEDIUM_PAGE"]);
  assert.match(fixture.output.textContent, /Medium title workflow: MEDIUM_TITLE_VERIFIED/);
  assert.match(fixture.output.textContent, /Medium body workflow: MEDIUM_BODY_PARTIAL/);
  assert.match(fixture.output.textContent, /Body fill eligibility: NOT_READY/);
  assert.match(fixture.output.textContent, /Current body normalized length: 2/);
  assert.match(fixture.output.textContent, /Current body canonical-prefix match: Yes/);
  assert.match(fixture.output.textContent, /Draft identity unchanged: Yes/);
  assert.match(fixture.output.textContent, /Publish action occurred: No/);
});

test("persisted partial body needs no historical fill telemetry", async () => {
  const fixture = popupFixture({ ok: true, report: report(),
    reconciliation: { pageKind: "EDITOR" },
    editorInspection: { titleVerifiedInPersistedDraft: true,
      titleExactMatch: true, titleFingerprintMatch: true,
      titleFingerprint: "fnv1a32:5e81665d", draftId: "fd6c1824c4de",
      bodySubregionState: "UNKNOWN", settingsControls: [], editableCandidates: [],
      compositeEditorEvidence: [], auxiliaryContenteditablesExcluded: [],
      defaultValuePrompts: [], compositeStructuralBlocks: [] },
    bodyEligibility: { ready: false, reason: "BODY_ALREADY_AUTHORED" },
    bodyReconciliation: { partialBodyDetected: true, currentBodySafeText: "01",
      currentBodyNormalizedLength: 2, currentBodyFingerprint: "fnv1a32:20ed70ba",
      currentBodyCanonicalTextPrefix: true, currentBodyCanonicalPrefixMatch: true,
      currentBodyCanonicalFormattingComplete: false,
      formattingDiscrepancy: "Expected heading block; observed paragraph/font wrapper",
      partialBodyRecoveryClassification: "EXACT_CANONICAL_PREFIX" } });
  await fixture.inspect.action();
  assert.equal(fixture.bodyControl.disabled, true);
  assert.deepEqual(fixture.sent, ["INSPECT_MEDIUM_PAGE"]);
  assert.match(fixture.output.textContent, /Medium title workflow: MEDIUM_TITLE_VERIFIED/);
  assert.match(fixture.output.textContent, /Medium body workflow: MEDIUM_BODY_PARTIAL/);
  assert.match(fixture.output.textContent, /Current body safe text: 01/);
  assert.match(fixture.output.textContent, /Body user-authored content: Yes/);
  assert.match(fixture.output.textContent, /Current body canonical text prefix: Yes/);
  assert.match(fixture.output.textContent,
    /Formatting discrepancy: Expected heading block; observed paragraph\/font wrapper/);
});

test("missing reconciliation is reported as unavailable instead of a negative result", async () => {
  const fixture = popupFixture({ ok: true, report: report(),
    reconciliation: { pageKind: "IMPORT" },
    bodyEligibility: { ready: false, reason: "BODY_INSPECTOR_UNAVAILABLE" } });
  await fixture.inspect.action();
  assert.match(fixture.output.textContent, /Partial body detected: Not observed/);
  assert.match(fixture.output.textContent, /Body reconciliation available: No/);
  assert.match(fixture.output.textContent, /Current body canonical-prefix match: Not observed/);
});

test("a recorded prompt-removal attempt does not revert to unfilled", async () => {
  const fixture = popupFixture({ ok: true, report: report(),
    reconciliation: { pageKind: "IMPORT" },
    bodyAttemptRecorded: true,
    bodyEligibility: { ready: false, reason: "BODY_ALREADY_FILLED_BY_WORKFLOW" },
    bodyReconciliation: { partialBodyDetected: false,
      currentBodyNormalizedLength: 0, currentBodyExactCanonicalMatch: false } });
  await fixture.inspect.action();
  assert.equal(fixture.bodyControl.disabled, true);
  assert.match(fixture.output.textContent,
    /Medium body workflow: MEDIUM_BODY_FILL_AMBIGUOUS/);
  assert.match(fixture.output.textContent, /Body fill eligibility: NOT_READY/);
  assert.deepEqual(fixture.sent, ["INSPECT_MEDIUM_PAGE"]);
});

test("popup preserves read-only inspection and has no Fill button", async () => {
  const fixture = popupFixture();
  await fixture.inspect.action();
  assert.deepEqual(fixture.sent, ["INSPECT_MEDIUM_PAGE"]);
  assert.match(fixture.output.textContent, /Page: https:\/\/medium.com\/p\/import/);
  assert.match(fixture.output.textContent, /Source editor state: MEDIUM_DEFAULT_EXAMPLE_URL/);
  assert.doesNotMatch(popupHtml, /id="fill"/);
  assert.doesNotMatch(popupSource, /FILL_VERIFIED_SUBSTACK_URL/);
});

test("verified Medium title control enables only when ready and disables after fill", async () => {
  let filled = false;
  const status = () => ({ ok: true, report: report(),
    editorInspection: { detected: false, editorRouteRecognized: false,
      editorStructuralEvidencePresent: false, editorUrlShape: "/new-story",
      draftIdentity: "New story editor", editorArchitecture: "COMPOSITE_SINGLE_CONTENTEDITABLE",
      compositeEditorRootDetected: true, compositeEditorEvidence: [],
      auxiliaryContenteditablesExcluded: [], defaultValuePrompts: [],
      titleSubregionCandidateCount: 1, bodySubregionCandidateCount: 1,
      titleSubregionState: "SCAFFOLDING_ONLY", bodySubregionState: "SCAFFOLDING_ONLY",
      titleEditorCandidateCount: 0, bodyEditorCandidateCount: 0,
      titleCurrentState: "UNKNOWN", bodyCurrentState: "UNKNOWN",
      publishControlCount: 1, publishControl: { name: "Publish", visible: true },
      draftSaveIndicator: "Not observed", settingsControls: [], editableCandidates: [],
      classification: "BLANK_DRAFT_EDITOR_VERIFIED", workflowState: "BLANK_DRAFT_EDITOR_VERIFIED" },
    reconciliation: { pageKind: "EDITOR" },
    titleEligibility: { ready: !filled, workflow: filled ?
      "MEDIUM_TITLE_NOT_FILLED" : "MEDIUM_TITLE_FILL_READY" },
    titleFillResult: filled ? { workflow: "MEDIUM_TITLE_VERIFIED", titleFillAttempted: true,
      titleExactMatch: true, titleApplicationInputSignalDelivered: true,
      bodyUnchangedAfterTitleFill: true } : null });
  const fixture = popupFixture(status());
  fixture.chrome.tabs.sendMessage = async (_id, message) => {
    fixture.sent.push(message.type);
    if (message.type === "FILL_VERIFIED_MEDIUM_TITLE") {
      filled = true;
      return { ok: true, titleFillResult: status().titleFillResult };
    }
    return status();
  };
  await fixture.inspect.action();
  assert.equal(fixture.titleControl.disabled, false);
  assert.equal(fixture.titleControl.hasAttribute("disabled"), false);
  assert.equal(fixture.titleControl.getAttribute("aria-disabled"), "false");
  assert.match(fixture.output.textContent, /Popup title control actual interactability: ENABLED/);
  await fixture.titleControl.action();
  assert.equal(fixture.titleControl.disabled, true);
  assert.equal(fixture.titleControl.hasAttribute("disabled"), true);
  assert.equal(fixture.titleControl.getAttribute("aria-disabled"), "true");
  assert.match(fixture.output.textContent, /Medium title workflow: MEDIUM_TITLE_VERIFIED/);
  assert.match(fixture.output.textContent, /Publish capability: No/);
  assert.deepEqual(fixture.sent,
    ["INSPECT_MEDIUM_PAGE", "FILL_VERIFIED_MEDIUM_TITLE", "INSPECT_MEDIUM_PAGE"]);
  assert.match(popupHtml, /id="fill-medium-title"/);
  assert.doesNotMatch(popupHtml, /body-fill|canonical|id="publish"/i);
});

test("popup keeps the title control disabled when shared readiness is NOT_READY", async () => {
  const fixture = popupFixture({ ok: true, report: report(),
    titleEligibility: { ready: false, reason: "BLANK_EDITOR_NOT_VERIFIED" } });
  await fixture.inspect.action();
  assert.equal(fixture.titleControl.disabled, true);
  assert.equal(fixture.titleControl.hasAttribute("disabled"), true);
  assert.equal(fixture.titleControl.getAttribute("aria-disabled"), "true");
  assert.match(fixture.output.textContent, /Title fill logical eligibility: NOT_READY/);
  assert.match(fixture.output.textContent, /Popup title control actual interactability: BLOCKED/);
});

test("popup reports read-only normal editor structure and distinct Import history", async () => {
  const editorInspection = {
    detected: true, editorRouteRecognized: true,
    editorStructuralEvidencePresent: true, editorUrlShape: "/p/[story-id]/edit",
    draftIdentity: "Medium story path /p/abcdef123456/edit",
    titleEditorCandidateCount: 1, bodyEditorCandidateCount: 1,
    selectedTitleEditor: { tag: "h1", role: "heading", contenteditable: "true",
      data: { "data-placeholder": "Title" }, childStructure: "None",
      normalizedTextLength: 0, currentState: "EMPTY" },
    selectedBodyEditor: { tag: "div", role: "textbox", contenteditable: "true",
      data: { "data-placeholder": "Tell your story" }, childStructure: "p",
      normalizedTextLength: 0, currentState: "EMPTY" },
    titleCurrentState: "EMPTY", bodyCurrentState: "EMPTY",
    publishControlCount: 1, publishControl: { name: "Publish", visible: true, enabled: true },
    draftSaveIndicator: "Saved", editorContainer: "Editor/story ancestor observed",
    commonEditorRoot: { tag: "section", classes: ["story-editor"] },
    editableRelationship: "Separate title and body editors",
    domOrder: "Candidate 1 before candidate 2", titleBodyOrder: "Title before body",
    editorAppearsBlank: "Yes", classification: "BLANK_DRAFT_EDITOR_VERIFIED",
    workflowState: "BLANK_DRAFT_EDITOR_VERIFIED",
    editorArchitecture: "SEPARATE_EDITABLES", compositeEditorRootDetected: false,
    compositeEditorCandidateIndex: null, compositeEditorEvidence: [],
    primaryCompositeEditor: null, auxiliaryContenteditablesExcluded: [],
    defaultValuePrompts: [], compositeStructuralBlocks: [],
    titleSubregionCandidateCount: 0, bodySubregionCandidateCount: 0,
    titleSubregionState: "UNKNOWN", bodySubregionState: "UNKNOWN",
    userAuthoredContentDetected: "No",
    settingsControls: [{ name: "More settings", visible: true, enabled: true, data: {} }],
    editableCandidates: []
  };
  const fixture = popupFixture({ ok: true, report: report(), editorInspection,
    reconciliation: { pageKind: "OTHER" }, retirement });
  fixture.chrome.runtime.sendMessage = (_message, callback) => callback({ ok: true,
    exists: true, source: "chrome.storage.session", record: {
      state: "SECOND_ATTEMPT_ARMED", authorizedAttemptNumber: 2,
      observedNativeImportClicks: 0, result: "PENDING" } });
  await fixture.inspect.action();
  for (const expected of ["Medium story editor detected: Yes",
    "Medium editor route recognized: Yes", "Editor structural evidence present: Yes",
    "Editor URL shape: /p/[story-id]/edit", "Title editor candidate count: 1",
    "Editor architecture: SEPARATE_EDITABLES", "Default-value prompt count: 0",
    "Composite root body-editor score candidate count: 1", "Editor appears blank: Yes",
    "Editor classification: BLANK_DRAFT_EDITOR_VERIFIED",
    "Publish control: Publish; visible=Yes; enabled=Yes",
    "Story/settings control count: 1", "URL Import retired: Yes",
    "Known real Import attempts: 2", "Attempt 2 reconciliation: NO_DRAFT_FOUND",
    "Legacy stored authorization: ARMED", "Legacy stored observed clicks: 0",
    "Legacy stored result: PENDING", "Publish capability: No"]) {
    assert.ok(fixture.output.textContent.includes(expected), expected);
  }
  assert.deepEqual(fixture.sent, ["INSPECT_MEDIUM_PAGE"]);
});

test("popup reports a failed inspection", async () => {
  const fixture = popupFixture({ ok: false });
  await fixture.inspect.action();
  assert.match(fixture.output.textContent, /could not be completed safely/);
  assert.equal(fixture.inspect.disabled, false);
});

test("popup prints composite root, prompts, subregions, and auxiliary exclusion", async () => {
  const editorInspection = {
    detected: true, editorRouteRecognized: true, editorStructuralEvidencePresent: true,
    editorUrlShape: "/new-story", draftIdentity: "New story editor",
    editorArchitecture: "COMPOSITE_SINGLE_CONTENTEDITABLE",
    compositeEditorRootDetected: true, compositeEditorCandidateIndex: 1,
    compositeEditorEvidence: ["/new-story route", "article.postArticle ancestor"],
    primaryCompositeEditor: { index: 1, tag: "div", role: "textbox",
      contenteditable: "true", id: "editor_7", classes: ["postArticle-content"] },
    auxiliaryContenteditablesExcluded: [{ index: 2,
      classification: "AUXILIARY_CONTENTEDITABLE", reason: "Empty body-level editable" }],
    defaultValuePrompts: [{ index: 1, text: "Title", length: 5, parent: { tag: "h1" } },
      { index: 2, text: "Tell your story.", length: 16, parent: { tag: "p" } }],
    compositeStructuralBlocks: [{ tag: "h1" }, { tag: "p" }],
    titleSubregionCandidateCount: 1, bodySubregionCandidateCount: 1,
    selectedTitleSubregion: { tag: "h1" }, selectedBodySubregion: { tag: "p" },
    titleSubregionState: "SCAFFOLDING_ONLY", bodySubregionState: "SCAFFOLDING_ONLY",
    userAuthoredContentDetected: "No", titleEditorCandidateCount: 0,
    bodyEditorCandidateCount: 0, titleCurrentState: "UNKNOWN", bodyCurrentState: "UNKNOWN",
    publishControlCount: 1, publishControl: { name: "Publish", visible: true, enabled: true },
    editorAppearsBlank: "Yes", classification: "BLANK_DRAFT_EDITOR_VERIFIED",
    workflowState: "BLANK_DRAFT_EDITOR_VERIFIED", settingsControls: [],
    editableCandidates: []
  };
  const fixture = popupFixture({ ok: true, report: report(), editorInspection,
    reconciliation: { pageKind: "OTHER" }, retirement });
  await fixture.inspect.action();
  for (const expected of ["Editor architecture: COMPOSITE_SINGLE_CONTENTEDITABLE",
    "Composite editor root detected: Yes", "Composite editor candidate index: 1",
    "AUXILIARY_CONTENTEDITABLE", "Default-value prompt count: 2",
    "Default prompt 1:", "Default prompt 2:",
    "Title subregion candidate count: 1", "Body subregion candidate count: 1",
    "Title subregion state: SCAFFOLDING_ONLY",
    "Body subregion state: SCAFFOLDING_ONLY",
    "User-authored content detected: No",
    "Editor classification: BLANK_DRAFT_EDITOR_VERIFIED"]) {
    assert.ok(fixture.output.textContent.includes(expected), expected);
  }
  assert.deepEqual(fixture.sent, ["INSPECT_MEDIUM_PAGE"]);
});

test("popup reports verified post-title draft from read-only inspection", async () => {
  const editorInspection = {
    detected: true, editorRouteRecognized: true, editorRouteKind: "DRAFT_EDIT",
    draftId: "fd6c1824c4de", draftEditorRoute: true,
    draftIdentity: "Medium story path /p/fd6c1824c4de/edit",
    draftIdentityEstablished: true, draftRouteEstablishedByMedium: true,
    editorStructuralEvidencePresent: true, editorUrlShape: "/p/[story-id]/edit",
    editorArchitecture: "COMPOSITE_SINGLE_CONTENTEDITABLE",
    compositeEditorRootDetected: true, compositeEditorEvidence: [],
    auxiliaryContenteditablesExcluded: [], defaultValuePrompts: [],
    titleSubregionCandidateCount: 1, bodySubregionCandidateCount: 1,
    titleSubregionState: "USER_CONTENT_PRESENT", bodySubregionState: "SCAFFOLDING_ONLY",
    titleExactMatch: true, titleFingerprint: "fnv1a32:5e81665d",
    titleFingerprintMatch: true, titleVerifiedInPersistedDraft: true,
    bodyStillBlank: true, draftContinuationSafe: true,
    titleEditorCandidateCount: 0, bodyEditorCandidateCount: 0,
    titleCurrentState: "UNKNOWN", bodyCurrentState: "UNKNOWN",
    publishControlCount: 1, publishControl: { name: "Publish", visible: true },
    draftSaveIndicator: "Not observed", settingsControls: [], editableCandidates: [],
    classification: "NONEMPTY_DRAFT", workflowState: "MEDIUM_TITLE_VERIFIED_BODY_BLANK"
  };
  const fixture = popupFixture({ ok: true, report: report(), editorInspection,
    titleEligibility: { ready: false, reason: "NOT_NEW_STORY" },
    reconciliation: { pageKind: "EDITOR" }, retirement });
  await fixture.inspect.action();
  for (const expected of ["Medium title workflow: MEDIUM_TITLE_VERIFIED",
    "Historical title-fill telemetry available: No",
    "Title browser-generated input observed: Not observed",
    "Editor route kind: DRAFT_EDIT", "Draft identity established: Yes",
    "Editor architecture: COMPOSITE_SINGLE_CONTENTEDITABLE",
    "Title exact match: Yes", "Title fingerprint match: Yes",
    "Body subregion state: SCAFFOLDING_ONLY", "Body still blank: Yes",
    "Draft continuation safe: Yes", "Editor classification: NONEMPTY_DRAFT"]) {
    assert.ok(fixture.output.textContent.includes(expected), expected);
  }
  assert.equal(fixture.titleControl.disabled, true);
  assert.deepEqual(fixture.sent, ["INSPECT_MEDIUM_PAGE"]);
});

test("popup prints the Stories hydration, Drafts state, and read-only entries", async () => {
  const fixture = popupFixture({ ok: true, report: report(), reconciliation: {
    pageKind: "STORIES", status: "VERIFIED_DRAFT_CANDIDATE", stories: {
      pageRecognized: true, uiHydrated: true, hydrationWaitElapsedMs: 420,
      draftControl: { found: true, tag: "button", role: "tab", text: "Drafts",
        selected: true, hrefShape: null }, selectedTab: "Drafts",
      tabState: "DRAFTS_SELECTED", draftsSelectedEvidence: ["aria-selected=true"],
      selectedEvidence: ["aria-selected=true"], tablistFound: true,
      visibleTabpanelCount: 1, hiddenTabpanelCount: 2,
      visibleTabpanels: [{ tag: "section", id: "draft-panel",
        ariaLabelledby: "draft-tab", ariaControls: null,
        childElementCount: 1, snippet: "Draft story" }],
      contentKind: "Visible story entries", contentRegion: "Visible Drafts tabpanel",
      storiesRootContainer: { tag: "main", role: "", classes: [], childCount: 2 },
      tablistContainer: { tag: "div", role: "tablist", classes: [], childCount: 4 },
      postTabContentRegionIdentified: true,
      postTabContentEvidence: "Post-tab branch in bounded Stories root",
      postTabContentContainer: { tag: "section", role: "", classes: ["drafts"], childCount: 1 },
      visibleContentChildCount: 1, contentRegionRendered: true,
      loadingEvidence: false, errorEvidence: false,
      promoBlock: { found: false, heading: null, nearbyBodyText: [], controls: [],
        childCount: 0, storyEntrySiblings: 0, loadingSiblings: 0, dedicated: false },
      emptyStateEvidence: [], storyCardStructures: [],
      contentSnippets: [{ tag: "h2", role: "heading", text: "Draft stories",
        parentTag: "section", parentRole: "tabpanel" }],
      visualEvidence: ["Drafts: font-weight=700; others=400"],
      tabDiagnostics: [{ visibleText: "Drafts", accessibleName: "Drafts",
        ariaSelected: "true", ariaCurrent: null, ariaControls: "draft-panel",
        tabIndex: "0", dataTestId: "stories-drafts", classes: ["selected"],
        id: "draft-tab", hrefShape: "https://medium.com/me/stories",
        parentTag: "div", parentRole: "tablist", parentClasses: [],
        stateClasses: ["selected"], stateAttributes: ["aria-selected=true"],
        ancestorState: [], style: { fontWeight: "700", textDecorationLine: "none",
          borderBottomStyle: "solid", borderBottomWidth: "2px", opacity: "1" } }],
      draftListConfirmed: true, listConclusivelyLoaded: false, listIncomplete: false,
      draftListEmptyState: false, draftEntriesVisible: true,
      visibleDraftCandidateCount: 1, exactTitleMatches: "1", matchingCandidates: 1,
      inventory: [{ tag: "button", role: "tab", visibleText: "Drafts",
        accessibleName: "Drafts", selected: true }],
      entries: [{ title: "Saturation of Artificial Intelligence", state: "Draft",
        hrefShape: "/p/[12-hex-id]/edit", editUrlExists: true,
        visibleTimestamp: "Oct 7, 2026" }]
    }
  }, importState: "IMPORT_RESULT_AMBIGUOUS", persistedClickAttempts: 1 });
  await fixture.inspect.action();
  for (const expected of ["Stories page recognized: Yes", "Stories UI hydrated: Yes",
    "Hydration wait elapsed: 420 ms", "Drafts control found: Yes",
    "Selected/active: Yes", "Draft list confirmed: Yes", "Exact-title matches: 1",
    "Tab state: DRAFTS_SELECTED", "Drafts selected evidence: aria-selected=true",
    "Visible tabpanel count: 1", "Hidden tabpanel count: 2",
    "Post-tab content region identified: Yes", "Content container tag: section",
    "Content region conclusively rendered: Yes", "Loading evidence: No",
    "Error evidence: No", "Empty-state evidence: None",
    "Draft list conclusively loaded: No", "Draft list empty state: No",
    "Draft entries visible: Yes", "font-weight=700", "Draft stories",
    "Reconciliation status: VERIFIED_DRAFT_CANDIDATE", "Retry allowed: No",
    "Automatic tab navigation: No", "Automatic draft opening: No",
    "Saturation of Artificial Intelligence"]) {
    assert.ok(fixture.output.textContent.includes(expected), expected);
  }
});

test("popup exposes the same Import readiness and DOM state as the on-page control", async () => {
  const fixture = popupFixture({ ok: true, report: report(), importState: "IMPORT_READY",
    restoredImportSession: { state: "IMPORT_NOT_STARTED", clickAttempts: 0,
      source: "sessionStorage key publish-to-all:medium-import-attempt:v1" },
    importDiagnostics: {
      lifecycleState: "IMPORT_READY", attemptAvailable: true, clickAttempts: 0,
      guardAllowed: true, disabledReason: "None", control: "ENABLED",
      firstFailureReason: "None",
      raw: { sourceGuard: true, actionGuard: true,
        source: { exactMatch: true, fingerprintMatch: true, normalizedLength: 77,
          representation: "contenteditable paragraph", inspectorNormalizedLength: 77,
          resolverNormalizedLength: 77, normalizedValuesEqual: true,
          inspectorFingerprint: "fingerprint", resolverFingerprint: "fingerprint",
          normalizedEquality: true, fingerprintEquality: true },
        action: { rawMatch: true, candidateCount: 1, tag: "button", type: "submit",
          typeAttributePresent: false, typeAttributeValue: null,
          effectiveDomType: "submit", effectiveTypeMatch: true, typeAttributeValid: true,
          visibleTextExact: true, accessibleNameExact: true, visible: true,
          enabled: true, connected: true } },
      logicalEligibility: "ENABLED", interactability: "ENABLED", hitTest: "BUTTON",
      disabledProperty: false, disabledAttribute: false, ariaDisabled: "None",
      pointerEvents: "auto", cursor: "pointer", boundingBox: "24, 24, 120 x 40"
    } });
  await fixture.inspect.action();
  for (const expected of ["Import lifecycle state: IMPORT_READY",
    "Import attempt available: Yes", "Import click attempts: 0",
    "Import guard allowed: Yes", "Import disabled reason: None",
    "Extension Import control: ENABLED", "Import actual interactability: ENABLED",
    "Hit-test: BUTTON", "Restored session state: IMPORT_NOT_STARTED",
    "Restored session click attempts: 0", "Import raw-source guard: PASS",
    "Import raw-action guard: PASS", "Native Import candidate count: 1",
    "Import type attribute present: No", "Import type attribute value: Absent",
    "Import type attribute valid: Yes",
    "Import effective DOM type: submit", "Import effective type match: Yes",
    "Native Import identity: PASS", "Overall Import guard: PASS",
    "Inspector fingerprint: fingerprint", "Resolver fingerprint: fingerprint"]) {
    assert.ok(fixture.output.textContent.includes(expected), expected);
  }
});

test("popup reports attempt history and the disabled manual workflow", async () => {
  const fixture = popupFixture({ ok: true, report: report(),
    lastFillResult: { fillSynchronizationStatus: "VERIFIED" },
    secondAttempt: { state: "SECOND_ATTEMPT_NOT_AUTHORIZED",
      authorizedAttemptNumber: null, observedNativeImportClicks: 0 },
    importDiagnostics: { secondAttemptEligibility: "READY",
      secondAttemptReason: "None", secondAttemptPreparationControl: "ENABLED",
      secondAttemptAuthorization: "NOT_AUTHORIZED",
      currentFillSynchronization: "VERIFIED", observedNativeImportClicks: 0,
      automaticNativeImportActivation: "DISABLED" } });
  await fixture.inspect.action();
  for (const expected of ["Attempt 1 activation mechanism: HTMLElement.click on native Import button",
    "Attempt 1 reconciliation: NO_DRAFT_FOUND", "Current Fill synchronization: VERIFIED",
    "Programmatic Import click trusted in Chrome fixture: No",
    "Second attempt eligibility: READY", "Second attempt readiness failure: None",
    "Second attempt preparation control: ENABLED",
    "Second attempt authorization: NOT_AUTHORIZED",
    "Observed native Import clicks: 0", "Automatic native Import activation: DISABLED"]) {
    assert.ok(fixture.output.textContent.includes(expected), expected);
  }
});

test("popup reads the canonical worker record even when page state stayed unarmed", async () => {
  const fixture = popupFixture({ ok: true, report: report(),
    secondAttempt: { state: "SECOND_ATTEMPT_NOT_AUTHORIZED" },
    importDiagnostics: { secondAttemptAuthorization: "NOT_AUTHORIZED" } });
  fixture.chrome.runtime.sendMessage = (message, callback) => {
    assert.equal(message.type, "INSPECT_MEDIUM_RESERVATION");
    callback({ ok: true, exists: true, record: {
      state: "SECOND_ATTEMPT_ARMED", authorizedAttemptNumber: 2,
      observedNativeImportClicks: 0, result: "PENDING" },
    source: "chrome.storage.session", diagnostic: {
      backgroundRequestReceived: true, attempt1HistoryCheck: "PASS",
      existingAttempt2Check: "PASS", storageWriteAttempted: true,
      storageWriteSucceeded: true, storageReadBackSucceeded: true,
      backgroundResponse: "SUCCESS", rejectionReason: "None" } });
  };
  await fixture.inspect.action();
  assert.match(fixture.output.textContent, /Second attempt authorization: ARMED/);
  assert.match(fixture.output.textContent, /Attempt-2 reservation exists: Yes/);
  assert.match(fixture.output.textContent, /Storage source: chrome.storage.session/);
  assert.match(fixture.output.textContent, /Reservation read-back succeeded: Yes/);
  await fixture.reservationControl.action();
  assert.match(fixture.output.textContent, /Authorization: ARMED/);
  assert.deepEqual(fixture.sent, ["INSPECT_MEDIUM_PAGE"]);
});

test("popup surfaces runtime.lastError during read-only reservation inspection", async () => {
  const fixture = popupFixture();
  fixture.chrome.runtime.sendMessage = (_message, callback) => {
    fixture.chrome.runtime.lastError = { message: "port closed" };
    callback(undefined);
    delete fixture.chrome.runtime.lastError;
  };
  await fixture.reservationControl.action();
  assert.match(fixture.output.textContent, /Reservation inspection failure: RUNTIME_LAST_ERROR/);
  assert.match(fixture.output.textContent, /Second attempt authorization: NOT_AUTHORIZED/);
});

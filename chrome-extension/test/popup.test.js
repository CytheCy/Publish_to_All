"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

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
  const output = { textContent: "" };
  const sent = [];
  const chrome = { tabs: {
    query: async () => [{ id: 17 }],
    sendMessage: async (_id, message) => { sent.push(message.type); return response; }
  } };
  vm.runInNewContext(popupSource, {
    document: { getElementById: id => ({ inspect, report: output })[id] }, chrome
  });
  return { inspect, output, sent };
}

test("popup preserves read-only inspection and has no Fill button", async () => {
  const fixture = popupFixture();
  await fixture.inspect.action();
  assert.deepEqual(fixture.sent, ["INSPECT_MEDIUM_PAGE"]);
  assert.match(fixture.output.textContent, /Page: https:\/\/medium.com\/p\/import/);
  assert.match(fixture.output.textContent, /Source editor state: MEDIUM_DEFAULT_EXAMPLE_URL/);
  assert.doesNotMatch(popupHtml, /id="fill"/);
  assert.doesNotMatch(popupSource, /FILL_VERIFIED_SUBSTACK_URL/);
});

test("popup reports a failed inspection", async () => {
  const fixture = popupFixture({ ok: false });
  await fixture.inspect.action();
  assert.match(fixture.output.textContent, /could not be completed safely/);
  assert.equal(fixture.inspect.disabled, false);
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
      secondAttemptReason: "None", secondAttemptAuthorization: "NOT_AUTHORIZED",
      currentFillSynchronization: "VERIFIED", observedNativeImportClicks: 0,
      automaticNativeImportActivation: "DISABLED" } });
  await fixture.inspect.action();
  for (const expected of ["Attempt 1 activation mechanism: HTMLElement.click on native Import button",
    "Attempt 1 reconciliation: NO_DRAFT_FOUND", "Current Fill synchronization: VERIFIED",
    "Programmatic Import click trusted in Chrome fixture: No",
    "Second attempt eligibility: READY", "Second attempt authorization: NOT_AUTHORIZED",
    "Observed native Import clicks: 0", "Automatic native Import activation: DISABLED"]) {
    assert.ok(fixture.output.textContent.includes(expected), expected);
  }
});

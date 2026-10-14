"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");
const editor = require("../medium-editor-inspector.js");
const retired = require("../medium-retirement.js");
const STORY = { title: "Saturation of Artificial Intelligence",
  fingerprint: "fnv1a32:5e81665d" };

function element(tag, attrs = {}, text = "") {
  const node = { tagName: tag.toUpperCase(), className: attrs.class || "",
    children: [], childNodes: [], parentElement: null,
    disabled: false, clicks: 0, getAttribute(name) { return attrs[name] || null; },
    getClientRects() { return [{}]; },
    querySelectorAll(selector) {
      const all = [];
      const walk = current => {
        for (const child of current.children) { all.push(child); walk(child); }
      };
      walk(this);
      if (selector === "*") return all;
      if (selector.includes("img")) return all.filter(child =>
        ["img", "video", "iframe", "figure", "canvas", "table"].includes(
          child.tagName.toLowerCase()));
      return [];
    },
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
    click() { this.clicks += 1; }, setAttribute() { throw Error("mutation attempted"); } };
  Object.defineProperty(node, "textContent", { get() {
    return this.childNodes.map(child => child.nodeType === 3 ? child.nodeValue :
      child.textContent).join("");
  } });
  Object.defineProperty(node, "innerText", { get() { return this.textContent; } });
  if (text) node.childNodes.push({ nodeType: 3, nodeValue: text });
  return node;
}

function append(parent, child) {
  child.parentElement = parent;
  parent.children.push(child);
  parent.childNodes.push(child);
  return child;
}

function fixture({ titles = [""], bodies = [""], publish = true,
  path = "/p/abcdef123456/edit", saved = true, editorContainer = true } = {}) {
  const container = element("section", { class: editorContainer ? "story-editor" : "" });
  const titleNodes = titles.map(value => element("h1", {
    contenteditable: "true", "data-placeholder": "Title" }, value));
  const bodyNodes = bodies.map(value => element("div", {
    contenteditable: "true", "data-placeholder": "Tell your story" }, value));
  const editable = [...titleNodes, ...bodyNodes];
  for (const node of editable) append(container, node);
  const publishNode = element("button", {}, "Publish");
  const settingsNode = element("button", { "aria-label": "More settings" });
  const savedNode = element("span", { role: "status" }, "Saved");
  const controls = publish ? [publishNode, settingsNode] : [settingsNode];
  const doc = { defaultView: { getComputedStyle: (node, pseudo) => ({ display: "block",
    visibility: "visible", content: pseudo ? node.pseudo?.[pseudo] || "none" : "none" }) },
    querySelectorAll(selector) {
    if (selector.includes("contenteditable")) return editable;
    if (selector.startsWith("button")) return controls;
    if (selector.startsWith("[role='status']")) return saved ? [savedNode] : [];
    throw Error(`Unexpected selector: ${selector}`);
  } };
  return { inspect: () => editor.inspect(doc, { href: `https://medium.com${path}` }),
    get result() { return this.inspect(); }, editable, controls, publishNode, settingsNode,
    container };
}

function compositeFixture({ titlePrompt = "Title", bodyPrompt = "Tell your story.",
  markedTitle = true, markedBody = true, auxiliary = true, extraTitle = false,
  titleExtraText = "", bodyExtraText = "", elsewhereText = "",
  path = "/new-story", titleTag = "h1", titleTestId = "", bodyTestId = "",
  titleWrapper = "span", missingBody = false, story = STORY } = {}) {
  const body = element("body");
  const article = append(body, element("article", { class: "postArticle postArticle--full" }));
  const root = append(article, element("div", { role: "textbox", contenteditable: "true",
    id: "editor_7", class: "postArticle-content js-postField js-notesSource editable" }));
  const section = append(root, element("section"));
  const title = append(section, element(titleTag, { "data-testid": titleTestId },
    titleWrapper === "text" ? titlePrompt : ""));
  const prose = element("p", { "data-testid": bodyTestId });
  if (!missingBody) append(section, prose);
  const titleSpan = titleWrapper === "text" ? null : append(title, element(titleWrapper, {
    class: markedTitle ? "defaultValue defaultValue--root" : "arbitrary"
  }, titlePrompt));
  const bodySpan = append(prose, element("span", {
    class: markedBody ? "defaultValue defaultValue--root" : "arbitrary"
  }, bodyPrompt));
  if (titleExtraText) append(title, element("span", {}, titleExtraText));
  if (bodyExtraText) append(prose, element("span", {}, bodyExtraText));
  if (elsewhereText) append(section, element("span", {}, elsewhereText));
  if (extraTitle) append(append(section, element("h2")), element("span", {
    class: "defaultValue defaultValue--root" }, "Title"));
  const aux = auxiliary ? append(body, element("div", {
    contenteditable: "true", tabindex: "-1" })) : null;
  const publish = append(body, element("button", {}, "Publish"));
  const doc = { defaultView: { getComputedStyle: () => ({ display: "block",
    visibility: "visible", content: "none" }) },
  getSelection() { throw Error("selection attempted"); },
  querySelectorAll(selector) {
    const all = [body, ...body.querySelectorAll("*")];
    if (selector.includes("contenteditable")) return all.filter(node =>
      node.getAttribute("contenteditable") === "true");
    if (selector.startsWith("button")) return [publish];
    if (selector.startsWith("[role='status']")) return [];
    throw Error(`Unexpected selector: ${selector}`);
  } };
  for (const node of [root, section, title, prose, titleSpan, bodySpan, aux].filter(Boolean)) {
    node.focus = () => { throw Error("focus attempted"); };
    node.dispatchEvent = () => { throw Error("input event attempted"); };
  }
  return { get result() { return editor.inspect(doc,
    { href: `https://medium.com${path}` }, story); },
  root, section, title, prose, aux, publish, titleSpan, bodySpan };
}

test("retired history separates reconciled attempts from legacy reservation", () => {
  assert.equal(retired.state, "MEDIUM_URL_IMPORT_RETIRED");
  assert.equal(retired.urlImportRetired, true);
  assert.equal(retired.retryAllowed, false);
  assert.equal(retired.knownRealImportAttempts, 2);
  assert.deepEqual([retired.attempt1.activation, retired.attempt1.result],
    ["PROGRAMMATIC_NATIVE_BUTTON", "NO_DRAFT_FOUND"]);
  assert.deepEqual([retired.attempt2.activation, retired.attempt2.result],
    ["TRUSTED_MANUAL_NATIVE_BUTTON", "NO_DRAFT_FOUND"]);
  assert.equal(retired.attempt2.clickTrusted, true);
  assert.equal(retired.attempt2.buttonIdentityValid, true);
  assert.equal(retired.attempt2.draftListConclusive, true);
  assert.equal(retired.attempt2.visibleDraftCandidates, 0);
  assert.deepEqual([retired.legacyAttempt2.authorization,
    retired.legacyAttempt2.observedClicks, retired.legacyAttempt2.result],
  ["ARMED", 0, "PENDING"]);
});

test("normal blank editor has one unique title and body, Publish and settings untouched", () => {
  const page = fixture();
  assert.equal(page.result.detected, true);
  assert.equal(page.result.classification, "BLANK_DRAFT_EDITOR_VERIFIED");
  assert.equal(page.result.workflowState, "BLANK_DRAFT_EDITOR_VERIFIED");
  assert.equal(page.result.titleEditorCandidateCount, 1);
  assert.equal(page.result.bodyEditorCandidateCount, 1);
  assert.equal(page.result.titleCurrentState, "EMPTY");
  assert.equal(page.result.bodyCurrentState, "EMPTY");
  assert.equal(page.result.editorAppearsBlank, "Yes");
  assert.equal(page.result.publishControlCount, 1);
  assert.equal(page.result.settingsControls.length, 1);
  assert.equal(page.result.draftSaveIndicator, "Saved");
  assert.equal(page.publishNode.clicks, 0);
  assert.equal(page.settingsNode.clicks, 0);
  assert.ok(page.editable.every(node => node.clicks === 0));
});

test("multiple title or body candidates remain ambiguous", () => {
  const titles = fixture({ titles: ["", ""] }).result;
  assert.equal(titles.titleEditorCandidateCount, 2);
  assert.equal(titles.classification, "EDITOR_STRUCTURE_AMBIGUOUS");
  const bodies = fixture({ bodies: ["", ""] }).result;
  assert.equal(bodies.classification, "EDITOR_STRUCTURE_AMBIGUOUS");
});

test("nonempty title or body blocks blank-draft verification", () => {
  for (const options of [{ titles: ["Existing title"] },
    { bodies: ["Existing body"] }]) {
    const result = fixture(options).result;
    assert.equal(result.classification, "NONEMPTY_DRAFT");
    assert.equal(result.editorAppearsBlank, "No");
  }
});

test("route and structure without Publish are ambiguous; route alone is not an editor", () => {
  assert.equal(fixture({ publish: false }).result.classification,
    "EDITOR_STRUCTURE_AMBIGUOUS");
  assert.equal(editor.inspect({ querySelectorAll() { return []; } },
    { href: "https://medium.com/p/abcdef123456/edit" }).classification, "NOT_MEDIUM_EDITOR");
  assert.equal(fixture({ path: "/unexpected-editor-route" }).result.classification,
    "BLANK_DRAFT_EDITOR_VERIFIED");
  assert.equal(fixture({ saved: false, editorContainer: false }).result.classification,
    "BLANK_DRAFT_EDITOR_VERIFIED");
});

test("new-story route and two editables are recognized without pretending route proves identity", () => {
  const page = fixture({ path: "/new-story", saved: false });
  const result = page.result;
  assert.equal(result.editorRouteRecognized, true);
  assert.equal(result.editorStructuralEvidencePresent, true);
  assert.equal(result.publishControlCount, 1);
  assert.equal(result.publishControl.visible, true);
  assert.equal(result.publishControl.enabled, true);
  assert.equal(result.commonEditorRoot.tag, "section");
  assert.equal(result.domOrder, "Candidate 1 before candidate 2");
  assert.equal(result.classification, "BLANK_DRAFT_EDITOR_VERIFIED");
  assert.equal(page.publishNode.clicks, 0);
});

test("marked DOM title and body prompts are scaffolding", () => {
  const page = fixture({ path: "/new-story" });
  append(page.editable[0], element("span", { class: "placeholder" }, "Title"));
  append(page.editable[1], element("span", { class: "placeholder" }, "Tell your story"));
  const result = page.result;
  assert.equal(result.classification, "BLANK_DRAFT_EDITOR_VERIFIED");
  assert.equal(result.titleCurrentState, "SCAFFOLDING_ONLY");
  assert.equal(result.bodyCurrentState, "SCAFFOLDING_ONLY");
  assert.equal(result.editableCandidates[0].textEvidence.safeUiText, "Title");
  assert.equal(result.editableCandidates[1].textEvidence.safeUiText, "Tell your story");
});

test("text matching a placeholder attribute alone does not prove scaffolding", () => {
  const page = fixture({ path: "/new-story", titles: ["Title"] });
  const result = page.result;
  assert.equal(result.titleCurrentState, "UNKNOWN");
  assert.equal(result.editableCandidates[0].textEvidence.matchesPlaceholderAttribute, true);
  assert.equal(result.classification, "EDITOR_STRUCTURE_AMBIGUOUS");
});

test("CSS prompt, zero-width text, and br-only body remain blank", () => {
  const page = fixture({ path: "/new-story", titles: ["\u200b"], bodies: [""] });
  page.editable[0].pseudo = { "::before": '"Title"' };
  append(page.editable[1], element("br"));
  const result = page.result;
  assert.equal(result.classification, "BLANK_DRAFT_EDITOR_VERIFIED");
  assert.equal(result.editableCandidates[0].before.generated, true);
  assert.equal(result.editableCandidates[0].before.content.safePrompt, "Title");
  assert.equal(result.editableCandidates[0].zeroWidthCharacters, true);
  assert.equal(result.editableCandidates[1].brOnly, true);
  assert.equal(result.bodyCurrentState, "EMPTY");
});

test("CSS-generated prompts can identify blank title and body without DOM prompt text", () => {
  const page = fixture({ path: "/new-story" });
  for (const node of page.editable) {
    node.tagName = "DIV";
    node.getAttribute = name => name === "contenteditable" ? "true" : null;
  }
  page.editable[0].pseudo = { "::before": '"Title"' };
  page.editable[1].pseudo = { "::before": '"Tell your story"' };
  const result = page.result;
  assert.equal(result.classification, "BLANK_DRAFT_EDITOR_VERIFIED");
  assert.equal(result.editableCandidates[0].before.content.safePrompt, "Title");
  assert.equal(result.editableCandidates[1].before.content.safePrompt, "Tell your story");
  assert.equal(result.editableCandidates[0].normalizedTextContentLength, 0);
});

test("nested noneditable prompt is excluded and section structure is reported", () => {
  const page = fixture({ path: "/new-story" });
  const section = append(page.editable[1], element("section"));
  append(section, element("span", { contenteditable: "false", class: "placeholder" },
    "Tell your story"));
  const result = page.result;
  const body = result.editableCandidates[1];
  assert.equal(result.classification, "BLANK_DRAFT_EDITOR_VERIFIED");
  assert.equal(result.bodyCurrentState, "SCAFFOLDING_ONLY");
  assert.equal(body.childStructure, "section");
  assert.equal(body.noneditableDescendants, 1);
  assert.equal(body.placeholderLikeDescendants, 1);
  assert.equal(body.textEvidence.childSource[0].kind, "nested noneditable UI");
});

test("unexplained short DOM text on new-story is unknown, with fingerprint only", () => {
  const page = fixture({ path: "/new-story", titles: ["Unexplained text value"] });
  const result = page.result;
  assert.equal(result.classification, "EDITOR_STRUCTURE_AMBIGUOUS");
  assert.equal(result.titleCurrentState, "UNKNOWN");
  assert.equal(result.editableCandidates[0].normalizedTextLength, 22);
  assert.equal(result.editableCandidates[0].textEvidence.safeUiText, null);
  assert.match(result.editableCandidates[0].textEvidence.fingerprint, /^fnv1a32:/);
});

test("two unlabelled editable divs under editor root remain ambiguous", () => {
  const page = fixture({ path: "/new-story", saved: false });
  for (const node of page.editable) {
    node.tagName = "DIV";
    node.getAttribute = name => name === "contenteditable" ? "true" : null;
  }
  append(page.editable[0], element("section"));
  const result = page.result;
  assert.equal(result.editorRouteRecognized, true);
  assert.equal(result.editorStructuralEvidencePresent, true);
  assert.equal(result.titleEditorCandidateCount, 0);
  assert.equal(result.bodyEditorCandidateCount, 0);
  assert.equal(result.classification, "EDITOR_STRUCTURE_AMBIGUOUS");
});

test("nested contenteditables are reported and never verified as separate title and body", () => {
  const page = fixture({ path: "/new-story", saved: false });
  const outer = page.editable[0];
  const inner = page.editable[1];
  page.container.children = [outer];
  page.container.childNodes = [outer];
  append(outer, inner);
  const result = page.result;
  assert.equal(result.editableRelationship, "Nested contenteditables");
  assert.equal(result.editableCandidates[0].descendantContenteditableCount, 1);
  assert.equal(result.classification, "EDITOR_STRUCTURE_AMBIGUOUS");
});

test("unmarked nested noneditable text cannot prove a blank editor", () => {
  const page = fixture({ path: "/new-story" });
  append(page.editable[1], element("span", { contenteditable: "false" }, "Unknown UI"));
  const result = page.result;
  assert.equal(result.bodyCurrentState, "UNKNOWN");
  assert.equal(result.classification, "EDITOR_STRUCTURE_AMBIGUOUS");
  assert.equal(result.editableCandidates[1].textEvidence.safeUiText, null);
});

test("single Medium composite editor resolves marked title and body prompts safely", () => {
  const page = compositeFixture();
  const result = page.result;
  assert.equal(result.editorArchitecture, "COMPOSITE_SINGLE_CONTENTEDITABLE");
  assert.equal(result.compositeEditorRootDetected, true);
  assert.equal(result.compositeEditorCandidateIndex, 1);
  assert.equal(result.primaryCompositeEditor.id, "editor_7");
  assert.equal(result.auxiliaryContenteditablesExcluded.length, 1);
  assert.equal(result.auxiliaryContenteditablesExcluded[0].classification,
    "AUXILIARY_CONTENTEDITABLE");
  assert.deepEqual(result.defaultValuePrompts.map(item => item.text),
    ["Title", "Tell your story."]);
  assert.deepEqual(result.defaultValuePrompts.map(item => item.length), [5, 16]);
  assert.deepEqual(result.defaultValuePrompts.map(item => item.parent.tag), ["h1", "p"]);
  assert.equal(result.titleSubregionCandidateCount, 1);
  assert.equal(result.bodySubregionCandidateCount, 1);
  assert.equal(result.selectedTitleSubregion.tag, "h1");
  assert.equal(result.selectedBodySubregion.tag, "p");
  const resolved = editor.resolveBodySubregion(result);
  assert.equal(resolved.root, page.root);
  assert.equal(resolved.body, page.prose);
  assert.equal(resolved.selected, result.selectedBodySubregion);
  assert.equal(Object.keys(result).includes("resolvedBodySubregion"), false);
  assert.equal(result.titleSubregionState, "SCAFFOLDING_ONLY");
  assert.equal(result.bodySubregionState, "SCAFFOLDING_ONLY");
  assert.equal(result.userAuthoredContentDetected, "No");
  assert.equal(result.editorAppearsBlank, "Yes");
  assert.equal(result.classification, "BLANK_DRAFT_EDITOR_VERIFIED");
  assert.equal(page.publish.clicks, 0);
  assert.equal(page.root.clicks, 0);
});

test("mixed authored text in either composite block or elsewhere prevents blank verification", () => {
  for (const options of [{ titleExtraText: "Authored heading" },
    { bodyExtraText: "Authored prose" }, { elsewhereText: "Other story text" }]) {
    const result = compositeFixture(options).result;
    assert.equal(result.userAuthoredContentDetected, "Yes");
    assert.equal(result.editorAppearsBlank, "No");
    assert.equal(result.classification, "NONEMPTY_DRAFT");
  }
});

test("matching text in an arbitrary span is not Medium scaffolding", () => {
  const result = compositeFixture({ markedTitle: false }).result;
  assert.equal(result.defaultValuePrompts.length, 1);
  assert.equal(result.titleSubregionState, "UNKNOWN");
  assert.notEqual(result.classification, "BLANK_DRAFT_EDITOR_VERIFIED");
});

test("ambiguous composite descendants are not verified", () => {
  const result = compositeFixture({ extraTitle: true }).result;
  assert.equal(result.titleSubregionCandidateCount, 2);
  assert.equal(result.classification, "EDITOR_STRUCTURE_AMBIGUOUS");
});

test("ordered generic blocks with distinct Medium prompts resolve as title and body", () => {
  const page = compositeFixture();
  page.title.tagName = "DIV";
  page.prose.tagName = "DIV";
  const result = page.result;
  assert.equal(result.selectedTitleSubregion.tag, "div");
  assert.equal(result.selectedBodySubregion.tag, "div");
  assert.equal(result.classification, "BLANK_DRAFT_EDITOR_VERIFIED");
});

test("an extra default-value prompt outside selected subregions prevents blank verification", () => {
  const page = compositeFixture();
  append(page.section, element("span", { class: "defaultValue defaultValue--root" },
    "Other UI prompt"));
  assert.equal(page.result.classification, "EDITOR_STRUCTURE_AMBIGUOUS");
});

test("draft edit route identifies an authored title and remaining body prompt", () => {
  for (const titleWrapper of ["font", "span", "text"]) {
    const page = compositeFixture({ path: "/p/fd6c1824c4de/edit",
      titlePrompt: STORY.title, markedTitle: false, titleTag: "h3",
      titleTestId: "editorTitleParagraph", bodyTestId: "editorParagraphText",
      bodyPrompt: "Tell your story…", titleWrapper });
    const result = page.result;
    assert.equal(result.editorRouteRecognized, true);
    assert.equal(result.editorRouteKind, "DRAFT_EDIT");
    assert.equal(result.draftId, "fd6c1824c4de");
    assert.equal(result.draftIdentityEstablished, true);
    assert.equal(result.draftRouteEstablishedByMedium, true);
    assert.equal(result.editorArchitecture, "COMPOSITE_SINGLE_CONTENTEDITABLE");
    assert.equal(result.defaultValuePrompts.length, 1);
    assert.equal(result.titleSubregionCandidateCount, 1);
    assert.equal(result.bodySubregionCandidateCount, 1);
    assert.equal(result.selectedTitleSubregion.data["data-testid"],
      "editorTitleParagraph");
    assert.equal(result.selectedBodySubregion.data["data-testid"],
      "editorParagraphText");
    assert.equal(result.titleExactMatch, true);
    assert.equal(result.titleFingerprint, STORY.fingerprint);
    assert.equal(result.titleFingerprintMatch, true);
    assert.equal(result.bodySubregionState, "SCAFFOLDING_ONLY");
    assert.equal(result.bodyStillBlank, true);
    assert.equal(result.titleVerifiedInPersistedDraft, true);
    assert.equal(result.draftContinuationSafe, true);
    assert.equal(result.classification, "NONEMPTY_DRAFT");
    assert.equal(result.workflowState, "MEDIUM_TITLE_VERIFIED_BODY_BLANK");
    assert.equal(page.publish.clicks, 0);
    assert.equal(page.root.clicks, 0);
    assert.equal(page.prose.textContent, "Tell your story…");
  }
});

test("draft continuation rejects wrong title, wrong fingerprint, and authored body", () => {
  const base = { path: "/p/fd6c1824c4de/edit", titleTag: "h3",
    titleTestId: "editorTitleParagraph", bodyTestId: "editorParagraphText",
    bodyPrompt: "Tell your story…", markedTitle: false };
  for (const variant of [
    { titlePrompt: "A different title" },
    { titlePrompt: STORY.title, story: { ...STORY, fingerprint: "fnv1a32:00000000" } },
    { titlePrompt: STORY.title, bodyExtraText: "Actual body text" },
    { titlePrompt: STORY.title, elsewhereText: "Unexpected story text" },
    { titlePrompt: STORY.title, missingBody: true },
    { titlePrompt: STORY.title, extraTitle: true }
  ]) {
    const result = compositeFixture({ ...base, ...variant }).result;
    assert.equal(result.draftContinuationSafe, false, JSON.stringify(variant));
    assert.notEqual(result.workflowState, "MEDIUM_TITLE_VERIFIED_BODY_BLANK");
  }
  const missing = compositeFixture({ ...base, titlePrompt: STORY.title,
    missingBody: true }).result;
  assert.equal(missing.bodySubregionCandidateCount, 0);
  assert.equal(missing.classification, "EDITOR_STRUCTURE_AMBIGUOUS");
  const multiple = compositeFixture({ ...base, titlePrompt: STORY.title,
    extraTitle: true }).result;
  assert.equal(multiple.titleSubregionCandidateCount, 2);
  assert.equal(multiple.classification, "EDITOR_STRUCTURE_AMBIGUOUS");
});

test("draft route matching stays restricted to twelve hexadecimal characters", () => {
  for (const path of ["/p/not-a-draft/edit", "/p/fd6c1824c4d/edit",
    "/p/fd6c1824c4de/publish", "/P/fd6c1824c4de/edit",
    "/p/fd6c1824c4de/EDIT"]) {
    const result = compositeFixture({ path }).result;
    assert.equal(result.editorRouteKind, "UNKNOWN");
    assert.equal(result.editorRouteRecognized, false);
    assert.equal(result.compositeEditorRootDetected, false);
  }
});

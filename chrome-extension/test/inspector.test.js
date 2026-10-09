"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const { createHash } = require("node:crypto");
const inspector = require("../inspector.js");
const mediumControls = require("../medium-controls.js");
const importSession = require("../import-session.js");
const secondAttempt = require("../second-attempt.js");

const HOME = "https://medium.com/";

function control(name, overrides = {}) {
  return {
    tag: "a", role: "link", accessibleName: name, visibleText: name,
    href: null, dataTestId: "", inNavigation: true,
    inDialog: false, ariaHaspopup: "", ariaExpanded: "", ...overrides
  };
}

function snapshot(overrides = {}) {
  return { pageUrl: HOME, title: "Medium", controls: [], headings: [], inputs: [], ...overrides };
}

test("owner-only profile control establishes authentication", () => {
  const result = inspector.analyzeSnapshot(snapshot({
    pageUrl: "https://medium.com/@test.owner?token=private#fragment",
    controls: [control("Edit profile", { tag: "button", role: "button" })]
  }));
  assert.equal(result.authentication.classification, "AUTHENTICATED");
  assert.deepEqual(result.authentication.decisiveEvidence, ["Edit profile on a profile page"]);
  assert.equal(result.page, "https://medium.com/@[profile redacted]");
  assert.equal(JSON.stringify(result).includes("test.owner"), false);
  assert.equal(JSON.stringify(result).includes("private"), false);
});

test("account menu, Stories, and Notifications establish signed-in UI evidence", () => {
  const result = inspector.analyzeSnapshot(snapshot({ controls: [
    control("User options menu", { tag: "button", role: "button", ariaHaspopup: "menu" }),
    control("Stories", { href: "https://medium.com/me/stories" }),
    control("Notifications", { tag: "button", role: "button" })
  ] }));
  assert.equal(result.authentication.classification, "AUTHENTICATED");
  assert.deepEqual(result.authentication.decisiveEvidence,
    ["User options menu", "Stories", "Notifications"]);
  assert.deepEqual(result.authentication.accountCues,
    ["User options menu", "Stories", "Notifications"]);
});

test("Stories and Write without an account control remain unknown", () => {
  const result = inspector.analyzeSnapshot(snapshot({ controls: [
    control("Stories", { href: "https://medium.com/me/stories" }), control("Write")
  ] }));
  assert.equal(result.authentication.classification, "UNKNOWN");
});

test("Sign out with Stories establishes authenticated evidence", () => {
  const result = inspector.analyzeSnapshot(snapshot({ controls: [
    control("Sign out"), control("Stories", { href: "https://medium.com/me/stories" })
  ] }));
  assert.equal(result.authentication.classification, "AUTHENTICATED");
  assert.deepEqual(result.authentication.decisiveEvidence, ["Sign out", "Stories"]);
});

test("signed-out controls establish unauthenticated state", () => {
  const result = inspector.analyzeSnapshot(snapshot({ controls: [
    control("Sign in"), control("Get started", { tag: "button", role: "button" })
  ] }));
  assert.equal(result.authentication.classification, "NOT_AUTHENTICATED");
  assert.deepEqual(result.authentication.signedOutControls, ["Sign in", "Get started"]);
});

test("conflicting owner and signed-out controls stay unknown", () => {
  const result = inspector.analyzeSnapshot(snapshot({
    pageUrl: "https://medium.com/@test.owner",
    controls: [control("Edit profile"), control("Sign in")]
  }));
  assert.equal(result.authentication.classification, "UNKNOWN");
  assert.deepEqual(result.authentication.decisiveEvidence, []);
});

test("signed-out controls conflict with account navigation evidence", () => {
  const result = inspector.analyzeSnapshot(snapshot({ controls: [
    control("Sign in"), control("Stories", { href: "https://medium.com/me/stories" })
  ] }));
  assert.equal(result.authentication.classification, "UNKNOWN");
});

test("an article-body Sign in link alone does not prove signed-out state", () => {
  const result = inspector.analyzeSnapshot(snapshot({ controls: [control("Sign in", {
    inNavigation: false, href: "https://medium.com/@author/story"
  })] }));
  assert.equal(result.authentication.classification, "UNKNOWN");
});

test("Stories is found with its semantic metadata and no navigation", () => {
  const result = inspector.analyzeSnapshot(snapshot({ controls: [control("Stories", {
    tag: "a", role: "link", visibleText: "Stories", href: "https://medium.com/me/stories",
    dataTestId: "nav-stories"
  })] }));
  assert.deepEqual(result.stories, {
    found: true, elementType: "a", role: "link", accessibleName: "Stories",
    visibleText: "Stories", href: "https://medium.com/me/stories", dataTestId: "nav-stories"
  });
  assert.equal(result.authentication.classification, "UNKNOWN");
});

test("an article link named Stories is not treated as navigation", () => {
  const result = inspector.analyzeSnapshot(snapshot({ controls: [control("Stories", {
    inNavigation: false, href: "https://medium.com/@someone"
  })] }));
  assert.equal(result.stories.found, false);
});

test("a visible Stories destination in navigation is found even when icon-only", () => {
  const result = inspector.analyzeSnapshot(snapshot({ controls: [control("", {
    visibleText: "", inNavigation: true, href: "https://medium.com/me/stories"
  })] }));
  assert.equal(result.stories.found, true);
  assert.equal(result.stories.accessibleName, "");
  assert.equal(result.authentication.classification, "UNKNOWN");
});

test("a Stories destination inside a menu overlay is found", () => {
  const result = inspector.analyzeSnapshot(snapshot({ controls: [control("Stories", {
    inNavigation: false, href: "https://medium.com/me/stories"
  })] }));
  assert.equal(result.stories.found, true);
});

test("Import interface reports the visible field and button without source value", () => {
  const result = inspector.analyzeSnapshot(snapshot({
    pageUrl: "https://medium.com/p/import",
    headings: [{ accessibleName: "See your story on Medium", visibleText: "See your story on Medium" }],
    inputs: [{ visible: true, tag: "input", type: "url", accessibleName: "Story URL",
      placeholder: "Paste a link", dataTestId: "story-url", currentValueEmpty: false }],
    controls: [control("Import", { tag: "button", role: "button", type: "submit",
      enabled: true, dataTestId: "import-action" }), control("Cancel")]
  }));
  assert.equal(result.importInterface.found, true);
  assert.equal(result.importInterface.sourceUrlInput.currentValueEmpty, false);
  assert.equal(result.importInterface.sourceUrlInput.accessibleName, "Story URL");
  assert.deepEqual(result.importInterface.importAction, {
    tag: "button", role: "button", accessibleName: "Import", visibleText: "Import",
    type: "submit", enabled: true, dataTestId: "import-action"
  });
  assert.deepEqual(result.importInterface.backCancelClose,
    [{ role: "link", accessibleName: "Cancel" }]);
});

test("ambiguous Import fields and missing action do not assert an interface", () => {
  const base = snapshot({
    headings: [{ accessibleName: "Import a story", visibleText: "Import a story" }],
    inputs: [{ visible: true, tag: "input", type: "url", accessibleName: "Story URL",
      placeholder: "", dataTestId: "", currentValueEmpty: true }]
  });
  assert.equal(inspector.analyzeSnapshot(base).importInterface.found, false);
  base.controls = [control("Import", { tag: "button", role: "button" })];
  base.inputs.push({ ...base.inputs[0] });
  assert.equal(inspector.analyzeSnapshot(base).importInterface.found, false);
});

test("URL and title sanitization removes query, fragments, and credentials", () => {
  assert.equal(inspector.safeUrl("https://medium.com/me/stories?token=private#fragment"),
    "https://medium.com/me/stories");
  assert.equal(inspector.safeUrl("https://user:password@medium.com/me"),
    "[unrecognized URL redacted]");
  assert.equal(inspector.safeUrl("https://other.example/me"), "[unrecognized URL redacted]");
  assert.equal(inspector.safeText("user@example.com access_token=short-private"),
    "[email redacted] [secret redacted]");
  assert.equal(inspector.safeText("private.example/story?token=short-private"),
    "[URL redacted]");
});

class Element {
  constructor(tag, attributes = {}, text = "", options = {}) {
    this.tagName = tag.toUpperCase();
    this.attributes = attributes;
    this.innerText = text;
    this.textContent = text;
    this.nodeType = 1;
    this.parentElement = null;
    this.hidden = options.hidden || false;
    this.inNavigation = options.inNavigation || false;
    this.disabled = options.disabled || false;
    this._value = options.value || "";
    this.labels = options.labels || [];
    this.children = options.children || [];
    this.childElementCount = this.children.length;
  }
  get value() { return this._value; }
  get type() {
    const attribute = this.getAttribute("type");
    if (this.tagName === "BUTTON") {
      const value = attribute?.toLowerCase();
      return ["submit", "button", "reset"].includes(value) ? value : "submit";
    }
    return attribute || (this.tagName === "INPUT" ? "text" : "");
  }
  set value(_value) { throw Error("Inspection changed a form value"); }
  getAttribute(key) { return Object.hasOwn(this.attributes, key) ? this.attributes[key] : null; }
  hasAttribute(key) { return this.getAttribute(key) !== null; }
  getClientRects() { return [{}]; }
  contains(node) {
    for (let current = node; current; current = current.parentElement)
      if (current === this) return true;
    return false;
  }
  closest(selector) {
    if (selector.includes("nav") && this.inNavigation) return {};
    if (selector === "[role='tablist']") {
      for (let node = this; node; node = node.parentElement)
        if (node.getAttribute("role") === "tablist") return node;
    }
    return null;
  }
  querySelector() { return null; }
  click() { throw Error("Inspection clicked a control"); }
  focus() { throw Error("Inspection focused a control"); }
  dispatchEvent() { throw Error("Inspection dispatched an event"); }
  submit() { throw Error("Inspection submitted a form"); }
}

function fixtureDocument(controls, headings, inputs, others = []) {
  const elements = [...new Set([...controls, ...headings, ...inputs, ...others])];
  function matches(element, part) {
    if (/^[a-z][\w-]*$/.test(part)) return element.tagName.toLowerCase() === part;
    const attribute = part.match(/^\[([\w-]+)(?:=['"]([^'"]+)['"])?\]$/);
    if (attribute) return attribute[2] ? element.getAttribute(attribute[1]) === attribute[2] :
      element.hasAttribute(attribute[1]);
    const typedInput = part.match(/^input\[type=['"]([^'"]+)['"]\]$/);
    return Boolean(typedInput && element.tagName === "INPUT" &&
      element.getAttribute("type") === typedInput[1]);
  }
  return {
    title: "Import a story | Medium",
    defaultView: { getComputedStyle: node => ({ display: "block", visibility: "visible",
      opacity: "1", ...node.computedStyle }) },
    getElementById: id => elements.find(element => element.getAttribute("id") === id) || null,
    querySelectorAll(selector) {
      const parts = selector.split(",").map(part => part.trim());
      return elements.filter(element => parts.some(part => matches(element, part)));
    }
  };
}

test("DOM inspection reads URL field emptiness but never reads unrelated form values", () => {
  const stories = new Element("a", { href: "/me/stories", "data-testid": "stories-link" }, "Stories",
    { inNavigation: true });
  const action = new Element("button", { type: "submit", "data-testid": "import-button" }, "Import");
  const heading = new Element("h1", {}, "See your story on Medium");
  const source = new Element("input", { type: "url", "aria-label": "Story URL",
    placeholder: "Paste a link" }, "", { value: "private-source-url" });
  const password = new Element("input", { type: "password" });
  Object.defineProperty(password, "value", { get() { throw Error("Password value was read"); } });
  const hidden = new Element("input", { type: "url" }, "", { hidden: true });
  Object.defineProperty(hidden, "value", { get() { throw Error("Hidden value was read"); } });
  const doc = fixtureDocument([stories, action], [heading], [source, password, hidden]);
  const result = inspector.inspectDocument(doc, { href: "https://medium.com/p/import?token=private" });
  assert.equal(result.stories.found, true);
  assert.equal(result.importInterface.found, true);
  assert.equal(result.importInterface.sourceUrlInput.currentValueEmpty, false);
  assert.equal(JSON.stringify(result).includes("private-source-url"), false);
  assert.equal(JSON.stringify(result).includes("token=private"), false);
  assert.equal(source.value, "private-source-url");
});

test("outside the Import page, no input values are read", () => {
  const source = new Element("input", { type: "url", "aria-label": "Story URL" });
  Object.defineProperty(source, "value", { get() { throw Error("Unexpected value read"); } });
  const doc = fixtureDocument([], [], [source]);
  const result = inspector.inspectDocument(doc, { href: HOME });
  assert.equal(result.importInterface.found, false);
});

function inspectImport({ inputs = [], actions = [],
  headings = [new Element("h1", {}, "See your story on Medium")], others = [], title } = {}) {
  const doc = fixtureDocument(actions, headings, inputs, others);
  if (title) doc.title = title;
  return inspector.inspectDocument(doc, { href: "https://medium.com/p/import?token=private" });
}

test("standard input and button identify Import with a visible primary title", () => {
  const input = new Element("input", { type: "url", placeholder: "Paste story URL" });
  const action = new Element("button", {}, "Import");
  const title = new Element("div", {}, "See your story on Medium");
  const result = inspectImport({ inputs: [input], actions: [action], others: [title], headings: [],
    title: "Import your story" });
  assert.equal(result.importInterface.found, true);
  assert.equal(result.importInterface.heading, null);
  assert.deepEqual(result.importInterface.primaryTitle,
    { text: "See your story on Medium", elementType: "div", role: "" });
  assert.equal(result.importInterface.sourceUrlInput.currentValueEmpty, true);
});

test("an aria-labelled region can supply the visible primary title", () => {
  const region = new Element("section", { role: "region", "aria-label": "See your story on Medium" });
  const result = inspectImport({ others: [region], headings: [] });
  assert.deepEqual(result.importInterface.primaryTitle,
    { text: "See your story on Medium", elementType: "section", role: "region" });
  assert.equal(result.importInterface.found, false);
});

test("a label identifies a text source URL field", () => {
  const label = new Element("label", {}, "URL of the story to import");
  const input = new Element("input", { type: "text", id: "source-url" }, "", { labels: [label] });
  const result = inspectImport({ inputs: [input], actions: [new Element("button", {}, "Import")],
    others: [label] });
  assert.equal(result.importInterface.found, true);
  assert.equal(result.importInterface.sourceUrlInput.accessibleName, "URL of the story to import");
  assert.equal(result.importInterface.inventory.some(item => item.tag === "label"), true);
});

test("placeholder and aria-label independently identify source URL fields", () => {
  for (const attributes of [
    { type: "text", placeholder: "Paste the story link" },
    { type: "text", "aria-label": "Source URL" }
  ]) {
    const result = inspectImport({ inputs: [new Element("input", attributes)],
      actions: [new Element("button", {}, "Import")] });
    assert.equal(result.importInterface.found, true);
  }
});

test("aria-labelledby and nearby instructions identify a text field", () => {
  const instruction = new Element("span", { id: "url-instruction" }, "Paste the URL of the story");
  const input = new Element("input", { type: "text", "aria-labelledby": "url-instruction" });
  const result = inspectImport({ inputs: [input], actions: [new Element("button", {}, "Import")],
    others: [instruction] });
  assert.equal(result.importInterface.found, true);
  assert.equal(result.importInterface.sourceUrlInput.accessibleName, "Paste the URL of the story");
  assert.equal(result.importInterface.inventory.find(item => item.tag === "input").ariaLabelledby,
    "url-instruction");
});

test("nearby instructions identify an otherwise unnamed URL field", () => {
  const wrapper = new Element("div", {}, "Paste the URL of the story you want to import");
  const input = new Element("input", { type: "text" });
  input.parentElement = wrapper;
  const result = inspectImport({ inputs: [input], actions: [new Element("button", {}, "Import")] });
  assert.equal(result.importInterface.found, true);
  assert.match(result.importInterface.sourceUrlInput.nearbyText, /Paste the URL/);
});

test("role=textbox and role=button can identify Import controls", () => {
  const input = new Element("div", { role: "textbox", contenteditable: "true", "aria-label": "Story URL" });
  const action = new Element("div", { role: "button" }, "Import");
  const result = inspectImport({ inputs: [input], actions: [action] });
  assert.equal(result.importInterface.found, true);
  assert.equal(result.importInterface.sourceUrlInput.role, "textbox");
  assert.equal(result.importInterface.importAction.role, "button");
  assert.equal(result.importInterface.importAction.visibleText, "Import");
});

test("visible Import text identifies a button despite a different accessible name", () => {
  const result = inspectImport({ inputs: [new Element("input", { type: "url" })],
    actions: [new Element("button", { "aria-label": "Continue" }, "Import")] });
  assert.equal(result.importInterface.found, true);
  assert.equal(result.importInterface.importAction.visibleText, "Import");
});

test("disabled Import action is reported and remains an action candidate", () => {
  const input = new Element("input", { type: "url" });
  const action = new Element("button", { "aria-disabled": "true" }, "Import");
  const result = inspectImport({ inputs: [input], actions: [action] });
  assert.equal(result.importInterface.found, false);
  assert.equal(result.importInterface.importAction, null);
  assert.equal(result.importInterface.inventory.find(item => item.tag === "button").disabled, true);
});

test("unrelated controls do not displace the source field and Import action", () => {
  const result = inspectImport({
    inputs: [new Element("input", { type: "search", placeholder: "Search Medium" }),
      new Element("input", { type: "text", "aria-label": "Story URL" })],
    actions: [new Element("button", {}, "Cancel"), new Element("button", {}, "Import"),
      new Element("button", {}, "Help")]
  });
  assert.equal(result.importInterface.found, true);
  assert.equal(result.importInterface.inputCandidates.length, 2);
  assert.equal(result.importInterface.actionCandidates.length, 3);
  assert.equal(result.importInterface.sourceUrlInput.accessibleName, "Story URL");
  assert.equal(result.importInterface.importAction.visibleText, "Import");
});

test("ambiguous URL fields stay unconfirmed", () => {
  const result = inspectImport({ inputs: [new Element("input", { type: "url" }),
    new Element("input", { type: "url" })], actions: [new Element("button", {}, "Import")] });
  assert.equal(result.importInterface.found, false);
  assert.equal(result.importInterface.sourceUrlInput, null);
});

test("the page title alone cannot identify the Import interface", () => {
  const result = inspectImport({ title: "Import your story", headings: [] });
  assert.equal(result.importInterface.found, false);
  assert.equal(result.importInterface.primaryTitle, null);
  assert.equal(result.authentication.classification, "UNKNOWN");
});

test("inventory exposes only source hostname while hiding query, passwords, hidden fields, and account chrome", () => {
  const source = new Element("input", { type: "url", name: "story-url" }, "",
    { value: "https://secret.example/path?token=private" });
  const password = new Element("input", { type: "password", name: "password" });
  Object.defineProperty(password, "value", { get() { throw Error("Password value read"); } });
  const hidden = new Element("input", { type: "hidden", name: "csrf-token" });
  Object.defineProperty(hidden, "value", { get() { throw Error("Hidden value read"); } });
  const account = new Element("button", { "aria-label": "Personal Account Name" }, "", { inNavigation: true });
  const result = inspectImport({ inputs: [source, password, hidden],
    actions: [account, new Element("button", {}, "Import")] });
  const report = JSON.stringify(result);
  assert.equal(result.importInterface.found, true);
  assert.equal(result.importInterface.sourceUrlInput.currentValueEmpty, false);
  assert.equal(result.importInterface.sourceUrlInput.existingUrl.hostname, "secret.example");
  for (const secret of ["token=private", "csrf-token", "password",
    "Personal Account Name"]) assert.equal(report.includes(secret), false);
});

test("contenteditable text is reduced to emptiness without nearby value leakage", () => {
  const wrapper = new Element("div", {}, "private.example/p/story");
  const textbox = new Element("div", { role: "textbox", contenteditable: "true", "aria-label": "Story URL" },
    "private.example/p/story");
  textbox.parentElement = wrapper;
  const result = inspectImport({ inputs: [textbox],
    actions: [new Element("button", {}, "Import")] });
  assert.equal(result.importInterface.found, true);
  assert.equal(result.importInterface.sourceUrlInput.currentValueEmpty, false);
  assert.equal(JSON.stringify(result).includes("private.example"), false);
});

test("inventory reports only sanitized href origin and path", () => {
  const known = new Element("a", { href: "/me/stories?token=private#fragment" }, "Stories");
  const unknown = new Element("a", { href: "https://outside.example/private?secret=yes" }, "Help");
  const result = inspectImport({ others: [known, unknown] });
  const links = result.importInterface.inventory.filter(item => item.tag === "a");
  assert.deepEqual(links.map(item => item.href),
    ["https://medium.com/me/stories", "[unrecognized URL redacted]"]);
  assert.equal(JSON.stringify(result).includes("outside.example"), false);
});

test("inventory is capped at 50 visible candidates", () => {
  const others = Array.from({ length: 60 }, (_, index) => new Element("label", {}, `Field ${index}`));
  const result = inspectImport({ others });
  assert.equal(result.importInterface.inventory.length, 50);
});

test("no extension code automatically activates native Import or Publish", () => {
  const passive = ["inspector.js", "content.js", "popup.js"].map(file =>
    fs.readFileSync(path.join(__dirname, "..", file), "utf8")).join("\n");
  const controls = fs.readFileSync(path.join(__dirname, "../medium-controls.js"), "utf8");
  const source = passive + controls;
  assert.doesNotMatch(passive, /\.(?:click|submit|requestSubmit|dispatchEvent)\s*\(/);
  assert.doesNotMatch(controls, /\.click\s*\(/);
  assert.doesNotMatch(controls, /publish(?:Button|Action|Target)\.click\s*\(/i);
  assert.doesNotMatch(source, /\.(?:submit|requestSubmit|dispatchEvent)\s*\(/);
  assert.doesNotMatch(source, /\.value\s*(?:=(?!=)|\+=|-=|\+\+|--)/);
  assert.doesNotMatch(source, /\b(?:fetch|XMLHttpRequest|sendBeacon)\s*\(/);
  assert.doesNotMatch(source, /(?:key|code)\s*:\s*["']Enter["']/);
  assert.doesNotMatch(source, /location\.(?:href|assign|replace)\s*(?:=|\()/);
});

test("content script only responds to inspection messages and hides errors", () => {
  const source = fs.readFileSync(path.join(__dirname, "../content.js"), "utf8");
  let listener;
  const context = {
    chrome: { runtime: { onMessage: { addListener(fn) { listener = fn; } } } },
    MediumInspector: { inspectDocument: () => { throw Error("token=private"); } },
    MediumImportSession: importSession,
    MediumSecondAttempt: secondAttempt,
    document: {}, location: { href: HOME }
  };
  vm.runInNewContext(source, context);
  let response;
  listener({ type: "UNRELATED" }, null, value => { response = value; });
  assert.equal(response, undefined);
  listener({ type: "INSPECT_MEDIUM_PAGE" }, null, value => { response = value; });
  assert.equal(response.ok, false);
  assert.equal(JSON.stringify(response).includes("private"), false);
});

test("manifest is limited to Medium and has no broad permissions", () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(__dirname, "../manifest.json"), "utf8"));
  assert.equal(manifest.manifest_version, 3);
  assert.deepEqual(manifest.content_scripts[0].matches, ["https://medium.com/*"]);
  assert.deepEqual(manifest.content_scripts[0].js, ["medium-fill.js", "inspector.js", "second-attempt.js", "medium-controls.js",
    "import-session.js", "content.js"]);
  assert.deepEqual(manifest.permissions, ["storage"]);
  assert.equal(manifest.host_permissions, undefined);
  assert.deepEqual(manifest.background, { service_worker: "background.js" });
});

test("popup asks only the active content script and displays read-only details", async () => {
  const source = fs.readFileSync(path.join(__dirname, "../popup.js"), "utf8");
  const button = { disabled: false, addEventListener(_event, fn) { this.inspect = fn; } };
  const fillButton = { disabled: true, addEventListener(_event, fn) { this.fill = fn; } };
  const output = { textContent: "" };
  const report = inspector.analyzeSnapshot(snapshot({
    pageUrl: "https://medium.com/p/import",
    controls: [control("Stories", { href: "https://medium.com/me/stories" }),
      control("Import", { tag: "button", role: "button", type: "submit", enabled: true })],
    headings: [{ accessibleName: "See your story on Medium", visibleText: "See your story on Medium" }],
    inputs: [{ visible: true, tag: "input", type: "url", accessibleName: "Story URL",
      placeholder: "Paste a link", dataTestId: "", currentValueEmpty: true }]
  }));
  let message;
  const chrome = { tabs: {
    query: async query => {
      assert.deepEqual({ ...query }, { active: true, currentWindow: true });
      return [{ id: 7 }];
    },
    sendMessage: async (tabId, sent) => {
      assert.equal(tabId, 7);
      message = sent;
      return { ok: true, report, fills: 0 };
    }
  } };
  vm.runInNewContext(source, {
    document: { getElementById: id => id === "inspect" ? button : id === "fill" ? fillButton : output }, chrome
  });
  await button.inspect();
  assert.deepEqual({ ...message }, { type: "INSPECT_MEDIUM_PAGE" });
  assert.equal(button.disabled, false);
  assert.match(output.textContent, /Content script ran: Yes/);
  assert.match(output.textContent, /Stories control found: Yes/);
  assert.match(output.textContent, /Import interface found: Yes/);
  assert.match(output.textContent, /Empty: Yes/);
  assert.match(output.textContent, /Selected Import action:/);
});

test("popup shows the primary title, candidates, selection, and safe inventory", () => {
  const report = inspectImport({
    inputs: [new Element("input", { type: "text", placeholder: "Paste story URL" })],
    actions: [new Element("button", {}, "Import")],
    others: [new Element("span", {}, "See your story on Medium"),
      new Element("a", { href: "/me/stories?token=private" }, "Stories")]
  });
  const source = fs.readFileSync(path.join(__dirname, "../popup.js"), "utf8");
  const rendered = vm.runInNewContext(`${source}\nformatReport(report);`, {
    report, document: { getElementById: () => ({ addEventListener() {} }) }
  });
  for (const expected of ["Import page detected: Yes", "Primary title: See your story on Medium",
    "Visible input candidates:",
    "Visible action candidates:", "Selected source URL input:",
    "Selected Import action:", "Enabled: Yes", "Other safe controls:",
    "Visible DOM inventory (maximum 50):"]) assert.ok(rendered.includes(expected), expected);
  assert.doesNotMatch(rendered, /token=private/);
});

const VERIFIED_URL = "https://cyporter.substack.com/p/saturation-of-artificial-intelligence-f8d";
const MEDIUM_DEFAULT_URL = "http://www.yoursite.org/your-post";
const MEDIUM_DEFAULT_FINGERPRINT =
  "0729da24628af2a65580e38e7931b60e41bcbbdc639b9b3a306fcd8de00f828c";

function popupReport(report) {
  const source = fs.readFileSync(path.join(__dirname, "../popup.js"), "utf8");
  return vm.runInNewContext(`${source}\nformatReport(report);`, {
    report, document: { getElementById: () => ({ addEventListener() {} }) }
  });
}

function liveImportFixture({ url = "https://medium.com/p/import", heading = "See your story on Medium",
  text = "", attributes = {}, extraEditors = [], actions = 1, search = true } = {}) {
  const editor = new Element("div", { role: "textbox", contenteditable: "true", id: "editor_7",
    class: "medium-editor source-textbox", "aria-multiline": "false", ...attributes }, text);
  const editorListeners = new Map();
  editor.eventListeners = editorListeners;
  editor.addEventListener = (type, listener) => {
    if (!editorListeners.has(type)) editorListeners.set(type, new Set());
    editorListeners.get(type).add(listener);
  };
  editor.removeEventListener = (type, listener) => editorListeners.get(type)?.delete(listener);
  editor.dispatchEvent = event => {
    Object.defineProperty(event, "target", { value: event.target || editor,
      configurable: true });
    for (const listener of editorListeners.get(event.type) || []) listener(event);
    return true;
  };
  editor.focus = () => {};
  const buttons = Array.from({ length: actions }, () => new Element("button", { type: "submit" }, "Import"));
  const searchInput = new Element("input", { type: "search", placeholder: "Search Medium" });
  const doc = fixtureDocument(buttons, [new Element("h1", {}, heading)],
    [editor, ...extraEditors, ...(search ? [searchInput] : [])]);
  doc.defaultView.InputEvent = class {
    constructor(type, options) { Object.assign(this, { type, isTrusted: false,
      cancelable: false }, options); }
  };
  const location = { href: url };
  const calls = { insertText: 0, focuses: 0, range: 0 };
  editor.focus = () => { calls.focuses += 1; };
  doc.addEventListener = (name, fn) => {
    assert.ok(["submit", "click"].includes(name));
    doc[`${name}Listener`] = fn;
  };
  doc.removeEventListener = name => {
    assert.ok(["submit", "click"].includes(name));
    delete doc[`${name}Listener`];
  };
  doc.createRange = () => ({ selectNodeContents(node) { assert.equal(node, editor); calls.range += 1; } });
  doc.getSelection = () => ({ removeAllRanges() {}, addRange() {} });
  doc.execCommand = (command, showUI, value) => {
    assert.equal(command, "insertText");
    assert.equal(showUI, false);
    assert.equal(value, VERIFIED_URL);
    calls.insertText += 1;
    editor.textContent = value;
    editor.innerText = value;
    editor.dispatchEvent({ type: "input", target: editor, bubbles: true,
      cancelable: false, composed: true, isTrusted: true,
      inputType: "insertText", data: value });
    return true;
  };
  return { doc, location, editor, buttons, searchInput, calls, session: { fills: 0 } };
}

test("live Import DOM identifies an unnamed dynamic contenteditable editor and excludes search", () => {
  for (const id of ["editor_7", "editor_932"]) {
    const page = liveImportFixture({ attributes: { id, "data-placeholder": "Paste a link" } });
    const report = inspector.inspectDocument(page.doc, page.location);
    assert.equal(report.importInterface.found, true);
    assert.equal(report.importInterface.fillAllowed, true);
    assert.equal(report.importInterface.sourceUrlInput.id, undefined);
    assert.equal(report.importInterface.sourceUrlInput.contenteditable, "true");
    assert.equal(report.importInterface.sourceUrlInput.ariaMultiline, "false");
    assert.equal(report.importInterface.sourceUrlInput.dataPlaceholder, "Paste a link");
    assert.equal(report.importInterface.sourceUrlInput.childElementCount, 0);
    assert.match(report.importInterface.sourceUrlInput.editorClasses, /editor/);
    assert.equal(report.importInterface.inputCandidates.length, 2);
    assert.equal(report.importInterface.sourceStatus, "READY");
  }
});

test("br and zero-width scaffolding are empty; represented placeholder is identified", () => {
  for (const text of ["", "\u200b\ufeff", "\u200b \n"]) {
    const page = liveImportFixture({ text });
    const report = inspector.inspectDocument(page.doc, page.location);
    assert.equal(report.importInterface.sourceUrlInput.currentValueEmpty, true);
    assert.equal(report.importInterface.sourceUrlInput.editorState, "EMPTY_OR_SCAFFOLDING");
  }
  const brPage = liveImportFixture();
  brPage.editor.children = [new Element("br")];
  brPage.editor.childElementCount = 1;
  const brReport = inspector.inspectDocument(brPage.doc, brPage.location);
  assert.equal(brReport.importInterface.sourceUrlInput.currentValueEmpty, true);
  assert.equal(brReport.importInterface.sourceUrlInput.childStructure, "br");
  const page = liveImportFixture({ text: "Paste a link", attributes: { "data-placeholder": "Paste a link" } });
  const report = inspector.inspectDocument(page.doc, page.location);
  assert.equal(report.importInterface.sourceUrlInput.editorState, "PLACEHOLDER_OR_INSTRUCTION");
  assert.equal(report.importInterface.fillAllowed, true);
  assert.equal(report.importInterface.sourceUrlInput.normalizedVisibleText,
    "[text redacted — instructional content]");
  assert.equal(report.importInterface.sourceUrlInput.dataPlaceholder, "[editor text redacted]");
  const nested = liveImportFixture({ text: "Paste a link" });
  nested.editor.children = [new Element("span", { "data-placeholder": "Paste a link" }, "Paste a link")];
  nested.editor.childElementCount = 1;
  assert.equal(inspector.inspectDocument(nested.doc, nested.location)
    .importInterface.sourceUrlInput.editorState, "PLACEHOLDER_OR_INSTRUCTION");
});

test("unrelated named editors and meaningful embedded elements are not fill targets", () => {
  const unrelated = liveImportFixture({ attributes: { "aria-label": "Reply editor" } });
  assert.equal(inspector.inspectDocument(unrelated.doc, unrelated.location)
    .importInterface.sourceStatus, "SOURCE_EDITOR_NOT_FOUND");
  const structured = liveImportFixture();
  structured.editor.children = [new Element("img")];
  structured.editor.childElementCount = 1;
  const report = inspector.inspectDocument(structured.doc, structured.location);
  assert.equal(report.importInterface.sourceUrlInput.editorState, "AMBIGUOUS_CONTENT");
  assert.equal(report.importInterface.sourceStatus, "SOURCE_EDITOR_NOT_EMPTY");
});

test("unverified instructions and a different URL refuse filling without disclosing values", () => {
  for (const [text, state] of [["Paste the story URL here", "AMBIGUOUS_CONTENT"],
    ["https://someone.example/post", "DIFFERENT_URL_PRESENT"]]) {
    const page = liveImportFixture({ text });
    const report = inspector.inspectDocument(page.doc, page.location);
    assert.equal(report.importInterface.sourceUrlInput.editorState, state);
    assert.equal(report.importInterface.sourceStatus, "SOURCE_EDITOR_NOT_EMPTY");
    if (state === "DIFFERENT_URL_PRESENT") assert.equal(JSON.stringify(report).includes(text), false);
    const result = inspector.fillVerifiedSourceUrl(page.doc, page.location, page.session);
    assert.equal(result.status, "SOURCE_EDITOR_NOT_EMPTY");
    assert.equal(result.urlFillAttempted, false);
    assert.equal(page.calls.insertText, 0);
  }
});

function mediumParagraphFixture(text, attributes = {}) {
  const page = liveImportFixture({ text, attributes: {
    class: "textInput textInput--large u-textAlignLeft", ...attributes
  } });
  page.editor.children = [new Element("p", {}, text)];
  page.editor.childElementCount = 1;
  return page;
}

test("Medium paragraph scaffolding is empty and inspection never changes the page", () => {
  const page = mediumParagraphFixture("");
  page.editor.children[0].children = [new Element("br")];
  page.editor.children[0].childElementCount = 1;
  const beforeUrl = page.location.href;
  const report = inspector.inspectDocument(page.doc, page.location);
  const input = report.importInterface.sourceUrlInput;
  assert.equal(input.editorState, "EMPTY_OR_SCAFFOLDING");
  assert.equal(input.normalizedTextLength, 0);
  assert.equal(input.fillSafe, true);
  assert.equal(report.importInterface.fillAllowed, true);
  assert.equal(page.editor.textContent, "");
  assert.equal(page.calls.focuses, 0);
  assert.equal(page.calls.insertText, 0);
  assert.equal(page.location.href, beforeUrl);
  assert.equal(page.doc.submitListener, undefined);
});

test("Medium instruction needs a matching placeholder attribute to be fill-safe", () => {
  const text = "Paste the URL of the story you want to import";
  const page = mediumParagraphFixture(text);
  const report = inspector.inspectDocument(page.doc, page.location);
  const input = report.importInterface.sourceUrlInput;
  assert.equal(input.editorState, "AMBIGUOUS_CONTENT");
  assert.equal(input.normalizedTextLength, text.length);
  assert.equal(input.looksLikeAbsoluteUrl, false);
  assert.equal(input.beginsWithHttp, false);
  assert.equal(input.entireContentOneUrl, false);
  assert.equal(input.wordCount, 10);
  assert.equal(input.containsWhitespaceSeparatedProse, true);
  assert.equal(input.instructionLike, true);
  assert.match(input.placeholderEvidence, /Medium source textbox with a single paragraph; origin unverified/);
  assert.equal(input.fillSafe, false);
  assert.equal(report.importInterface.fillAllowed, false);
  assert.equal(JSON.stringify(report).includes(text), false);

  const unverified = liveImportFixture({ text });
  const uncertain = inspector.inspectDocument(unverified.doc, unverified.location);
  assert.equal(uncertain.importInterface.sourceUrlInput.editorState, "AMBIGUOUS_CONTENT");
  assert.equal(uncertain.importInterface.fillAllowed, false);
  assert.match(uncertain.importInterface.sourceUrlInput.placeholderEvidence, /origin unverified/);

  const attributed = mediumParagraphFixture(text, { "aria-placeholder": text });
  const attributedReport = inspector.inspectDocument(attributed.doc, attributed.location);
  assert.equal(attributedReport.importInterface.sourceUrlInput.editorState,
    "PLACEHOLDER_OR_INSTRUCTION");
  assert.match(attributedReport.importInterface.sourceUrlInput.placeholderEvidence,
    /aria-placeholder attribute/);
  assert.equal(JSON.stringify(attributedReport).includes(text), false);
  for (const attribute of ["data-placeholder", "placeholder"]) {
    const fixture = liveImportFixture({ text, attributes: { [attribute]: text } });
    const observed = inspector.inspectDocument(fixture.doc, fixture.location);
    assert.equal(observed.importInterface.sourceUrlInput.editorState,
      "PLACEHOLDER_OR_INSTRUCTION");
    assert.match(observed.importInterface.sourceUrlInput.placeholderEvidence,
      new RegExp(`${attribute} attribute`));
    assert.equal(JSON.stringify(observed).includes(text), false);
  }
});

test("absolute URLs require one parsed HTTP(S) URL and never appear in diagnostics", () => {
  for (const [text, state, parsed] of [
    [VERIFIED_URL, "EXACT_SOURCE_URL_PRESENT", true],
    ["https://example.com/another-story", "DIFFERENT_URL_PRESENT", true],
    ["https://example.com/another-story?token=private#fragment", "DIFFERENT_URL_PRESENT", true],
    ["https://", "AMBIGUOUS_CONTENT", false],
    ["https:///example.com", "AMBIGUOUS_CONTENT", false],
    ["https://example.com\\bad", "AMBIGUOUS_CONTENT", false],
    ["https://example.com/path extra", "AMBIGUOUS_CONTENT", false],
    ["This prose mentions https but contains no URL", "NON_URL_MEANINGFUL_CONTENT", false],
    ["example.com", "NON_URL_MEANINGFUL_CONTENT", false]
  ]) {
    const page = mediumParagraphFixture(text);
    const report = inspector.inspectDocument(page.doc, page.location);
    const input = report.importInterface.sourceUrlInput;
    assert.equal(input.editorState, state, text);
    assert.equal(input.looksLikeAbsoluteUrl, parsed, text);
    assert.equal(input.entireContentOneUrl, parsed, text);
    assert.equal(input.fillSafe, false, text);
    assert.equal(report.importInterface.fillAllowed, false, text);
    if (text !== "https://") assert.equal(JSON.stringify(report).includes(text), false, text);
    assert.equal(page.calls.insertText, 0);
  }
});

test("HTTP and HTTPS existing URLs expose only safe structure and hostname", () => {
  for (const [text, scheme, host, portPresent] of [
    ["http://example.com:80/p/opaque?token=private#hidden", "http", "example.com", true],
    ["https://www.example.com/p/opaque", "https", "www.example.com", false]
  ]) {
    const report = inspector.inspectDocument(mediumParagraphFixture(text).doc,
      { href: "https://medium.com/p/import" });
    const input = report.importInterface.sourceUrlInput;
    const diagnostic = input.existingUrl;
    assert.equal(input.editorState, "DIFFERENT_URL_PRESENT");
    assert.equal(input.fillSafe, false);
    assert.equal(report.importInterface.fillAllowed, false);
    assert.equal(diagnostic.scheme, scheme);
    assert.equal(diagnostic.hostname, host);
    assert.equal(diagnostic.portPresent, portPresent);
    assert.equal(diagnostic.pathSegmentCount, 2);
    assert.equal(diagnostic.pathCharacterLength, "/p/opaque".length);
    assert.equal(diagnostic.pathShape, "/p/[segment]");
    assert.equal(diagnostic.normalizedUrlLength, text.length);
    assert.equal(diagnostic.obviousExampleDomainUrl, true);
    assert.equal(diagnostic.matchesIntendedSourceExactly, false);
    assert.equal(diagnostic.sameHostnameAsIntendedSource, false);
    assert.equal(diagnostic.safeUrlFingerprint,
      createHash("sha256").update(text).digest("hex"));
    const rendered = popupReport(report);
    for (const expected of [`Scheme: ${scheme}`, `Hostname: ${host}`, "Port present:",
      "Path segment count: 2", "Path character length: 9", "Safe path shape: /p/[segment]",
      "Total normalized URL length:", "Obvious example-domain URL: Yes",
      "Safe URL fingerprint:", "Source editor state: DIFFERENT_URL_PRESENT", "Fill-safe: No"]) {
      assert.ok(rendered.includes(expected), expected);
    }
    assert.equal(rendered.includes(text), false);
    assert.equal(JSON.stringify(report).includes(text), false);
  }
});

test("URL credentials, query, fragment, and opaque path content stay redacted", () => {
  const text = "https://private-user:private-pass@other.test:8443/opaque-one/opaque-two?secret=private-query#private-fragment";
  const page = mediumParagraphFixture(text);
  const report = inspector.inspectDocument(page.doc, page.location);
  const diagnostic = report.importInterface.sourceUrlInput.existingUrl;
  const rendered = popupReport(report);
  assert.equal(diagnostic.hostname, "other.test");
  assert.equal(diagnostic.portPresent, true);
  assert.equal(diagnostic.pathShape, "/[segment]/[segment]");
  assert.equal(diagnostic.queryPresent, true);
  assert.equal(diagnostic.fragmentPresent, true);
  assert.equal(diagnostic.credentialsPresent, true);
  assert.equal(diagnostic.obviousExampleDomainUrl, false);
  for (const expected of ["Query present: Yes", "Fragment present: Yes",
    "Username/password in URL: Yes", "Safe path shape: /[segment]/[segment]"]) {
    assert.ok(rendered.includes(expected), expected);
  }
  for (const secret of [text, "private-user", "private-pass", "opaque-one", "opaque-two",
    "private-query", "private-fragment", "secret="]) {
    assert.equal(rendered.includes(secret), false, secret);
    assert.equal(JSON.stringify(report).includes(secret), false, secret);
  }
});

test("exact intended source comparison is internal and fingerprint is deterministic", () => {
  const same = mediumParagraphFixture(VERIFIED_URL);
  const first = inspector.inspectDocument(same.doc, same.location);
  const second = inspector.inspectDocument(same.doc, same.location);
  const different = inspector.inspectDocument(
    mediumParagraphFixture("https://cyporter.substack.com/p/another-story").doc,
    { href: "https://medium.com/p/import" });
  const a = first.importInterface.sourceUrlInput.existingUrl;
  const b = second.importInterface.sourceUrlInput.existingUrl;
  const c = different.importInterface.sourceUrlInput.existingUrl;
  assert.equal(a.matchesIntendedSourceExactly, true);
  assert.equal(a.sameHostnameAsIntendedSource, true);
  assert.equal(c.matchesIntendedSourceExactly, false);
  assert.equal(c.sameHostnameAsIntendedSource, true);
  assert.equal(a.safeUrlFingerprint, b.safeUrlFingerprint);
  assert.notEqual(a.safeUrlFingerprint, c.safeUrlFingerprint);
  assert.match(popupReport(first), /Matches intended source exactly: Yes/);
  assert.match(popupReport(different), /Matches intended source exactly: No/);
  assert.equal(popupReport(first).includes(VERIFIED_URL), false);
});

test("inspection of an example URL never mutates DOM, clicks Import, submits, or navigates", () => {
  const text = "https://example.com/p/existing";
  const page = mediumParagraphFixture(text);
  const originalHref = page.location.href;
  Object.freeze(page.location);
  page.doc.execCommand = () => { throw Error("Inspection attempted to edit the source"); };
  page.doc.addEventListener = () => { throw Error("Inspection attempted to handle a form"); };
  page.doc.submit = () => { throw Error("Inspection submitted a form"); };
  const report = inspector.inspectDocument(page.doc, page.location);
  assert.equal(report.importInterface.sourceUrlInput.editorState, "DIFFERENT_URL_PRESENT");
  assert.equal(report.importInterface.sourceUrlInput.fillSafe, false);
  assert.equal(report.importInterface.fillAllowed, false);
  assert.equal(page.editor.textContent, text);
  assert.equal(page.editor.innerText, text);
  assert.equal(page.calls.focuses, 0);
  assert.equal(page.calls.range, 0);
  assert.equal(page.calls.insertText, 0);
  assert.equal(page.doc.submitListener, undefined);
  assert.equal(page.location.href, originalHref);
});

test("popup keeps Fill disabled for an example-domain URL", async () => {
  const page = mediumParagraphFixture("https://example.com/p/existing");
  const controls = {};
  for (const id of ["inspect", "fill"]) {
    controls[id] = { disabled: id === "fill", addEventListener(_event, fn) { this.action = fn; } };
  }
  controls.report = { textContent: "" };
  const sent = [];
  const chrome = { tabs: {
    query: async () => [{ id: 17 }],
    sendMessage: async (_id, message) => {
      sent.push(message.type);
      return { ok: true, report: inspector.inspectDocument(page.doc, page.location), fills: 0 };
    }
  } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../popup.js"), "utf8"),
    { document: { getElementById: id => controls[id] }, chrome });
  await controls.inspect.action();
  assert.equal(controls.fill.disabled, true);
  assert.deepEqual(sent, ["INSPECT_MEDIUM_PAGE"]);
  assert.match(controls.report.textContent, /Obvious example-domain URL: Yes/);
  assert.match(controls.report.textContent, /Fill-safe: No/);
});

test("meaningful prose remains protected even in the observed Medium editor", () => {
  const text = "This is something I typed";
  const page = mediumParagraphFixture(text);
  const report = inspector.inspectDocument(page.doc, page.location);
  assert.equal(report.importInterface.sourceUrlInput.editorState, "NON_URL_MEANINGFUL_CONTENT");
  assert.equal(report.importInterface.sourceStatus, "SOURCE_EDITOR_NOT_EMPTY");
  assert.equal(report.importInterface.fillDisabledReason, "NON_URL_MEANINGFUL_CONTENT");
  assert.equal(report.importInterface.fillAllowed, false);
  assert.equal(page.editor.textContent, text);
});

test("normalization removes zero-width characters and surrounding whitespace", () => {
  for (const text of [` \u200b${VERIFIED_URL}\ufeff \n`,
    "\u200b  \n", "  https://example.com/path  \n"]) {
    const page = mediumParagraphFixture(text);
    const input = inspector.inspectDocument(page.doc, page.location).importInterface.sourceUrlInput;
    assert.equal(input.normalizedTextLength,
      text.includes(VERIFIED_URL) ? VERIFIED_URL.length :
        text.includes("example.com") ? "https://example.com/path".length : 0);
    assert.equal(input.editorState, text.includes(VERIFIED_URL) ? "EXACT_SOURCE_URL_PRESENT" :
      text.includes("example.com") ? "DIFFERENT_URL_PRESENT" : "EMPTY_OR_SCAFFOLDING");
  }
});

test("popup reports editor shape and safety without revealing source content", () => {
  const text = "Paste the URL of the story you want to import";
  const page = mediumParagraphFixture(text, { "data-placeholder": text });
  const report = inspector.inspectDocument(page.doc, page.location);
  const source = fs.readFileSync(path.join(__dirname, "../popup.js"), "utf8");
  const rendered = vm.runInNewContext(`${source}\nformatReport(report);`, {
    report, document: { getElementById: () => ({ addEventListener() {} }) }
  });
  for (const expected of ["Source editor state: PLACEHOLDER_OR_INSTRUCTION",
    `Normalized text length: ${text.length}`, "Looks like absolute URL: No",
    "Entire editor content is one URL: No", "Instruction/placeholder evidence:",
    "Fill-safe: Yes", "Fill disabled reason: None"]) assert.ok(rendered.includes(expected), expected);
  assert.equal(rendered.includes(text), false);
});

test("long editor text echoed in an attribute is never sent in diagnostics", () => {
  const text = "This is something I typed ".repeat(8).trim();
  const page = mediumParagraphFixture(text, { "data-placeholder": text, "aria-label": text });
  const report = inspector.inspectDocument(page.doc, page.location);
  const serialized = JSON.stringify(report);
  assert.equal(report.importInterface.sourceUrlInput.editorState, "AMBIGUOUS_CONTENT");
  assert.equal(report.importInterface.fillAllowed, false);
  assert.equal(serialized.includes(text), false);
  assert.equal(serialized.includes("This is something I typed"), false);
  assert.equal(report.importInterface.sourceUrlInput.dataPlaceholder, "[editor text redacted]");
  assert.equal(report.importInterface.sourceUrlInput.accessibleName, "[editor text redacted]");
});

test("a focus-time content change blocks filling before insertion", () => {
  const page = mediumParagraphFixture(MEDIUM_DEFAULT_URL);
  page.editor.focus = () => {
    page.calls.focuses += 1;
    page.editor.textContent = "This is something I typed";
    page.editor.innerText = "This is something I typed";
  };
  const result = inspector.fillVerifiedSourceUrl(page.doc, page.location, page.session);
  assert.equal(result.status, "REFUSE");
  assert.equal(result.urlFillAttempted, false);
  assert.equal(result.importClicked, false);
  assert.equal(result.formSubmitted, false);
  assert.equal(result.navigationOccurred, false);
  assert.equal(page.calls.insertText, 0);
  assert.equal(page.session.fills, 0);
});

test("exact existing URL reports already present and does not mutate", () => {
  for (const text of [VERIFIED_URL, `\u200b${VERIFIED_URL}\ufeff`]) {
    const page = liveImportFixture({ text });
    assert.equal(inspector.inspectDocument(page.doc, page.location).importInterface.sourceStatus,
      "SOURCE_URL_ALREADY_PRESENT");
    const result = inspector.fillVerifiedSourceUrl(page.doc, page.location, page.session);
    assert.equal(result.status, "SOURCE_URL_ALREADY_PRESENT");
    assert.equal(result.urlFillAttempted, false);
    assert.equal(page.calls.insertText, 0);
  }
});

test("ambiguous, missing, disabled, unrelated, and wrong-page controls block filling", () => {
  const second = new Element("div", { role: "textbox", contenteditable: "true", id: "editor_88" });
  const hidden = liveImportFixture();
  hidden.editor.hidden = true;
  const disabled = liveImportFixture({ attributes: { "aria-disabled": "true" } });
  const disabledAction = liveImportFixture();
  disabledAction.buttons[0].disabled = true;
  const cases = [
    [liveImportFixture({ extraEditors: [second] }), "SOURCE_EDITOR_AMBIGUOUS"],
    [hidden, "SOURCE_EDITOR_NOT_FOUND"],
    [disabled, "SOURCE_EDITOR_NOT_FOUND"],
    [liveImportFixture({ attributes: { contenteditable: "false" } }), "SOURCE_EDITOR_NOT_FOUND"],
    [liveImportFixture({ actions: 0 }), "IMPORT_ACTION_NOT_UNIQUE"],
    [liveImportFixture({ actions: 2 }), "IMPORT_ACTION_NOT_UNIQUE"],
    [disabledAction, "IMPORT_ACTION_NOT_UNIQUE"],
    [liveImportFixture({ heading: "Another page" }), "IMPORT_HEADING_NOT_FOUND"],
    [liveImportFixture({ url: "https://medium.com/other" }), "WRONG_PAGE"],
    [liveImportFixture({ url: "https://evil.example/p/import" }), "WRONG_PAGE"]
  ];
  for (const [page, expected] of cases) {
    const result = inspector.fillVerifiedSourceUrl(page.doc, page.location, page.session);
    assert.equal(result.status, expected);
    assert.equal(result.urlFillAttempted, false);
    assert.equal(page.calls.insertText, 0);
    assert.equal(page.session.fills, 0);
  }
});

test("exact URL is inserted once with edit semantics and verified without Import activity", () => {
  const page = mediumParagraphFixture(MEDIUM_DEFAULT_URL);
  const result = inspector.fillVerifiedSourceUrl(page.doc, page.location, page.session);
  assert.equal(result.status, "URL_FILLED");
  assert.equal(result.urlFillAttempted, true);
  assert.equal(result.exactUrlPresentAfterward, true);
  assert.equal(result.importClicked, false);
  assert.equal(result.formSubmitted, false);
  assert.equal(result.navigationOccurred, false);
  assert.equal(result.importButtonStillExists, true);
  assert.equal(result.pageRemainsImport, true);
  assert.equal(page.calls.insertText, 1);
  assert.equal(page.calls.focuses, 1);
  assert.equal(page.calls.range, 1);
  assert.equal(page.session.fills, 1);
  const again = inspector.fillVerifiedSourceUrl(page.doc, page.location, page.session);
  assert.equal(again.status, "FILL_LIMIT_REACHED");
  assert.equal(page.calls.insertText, 1);
});

test("one-fill limit survives a cleared editor and failed verification", () => {
  const page = mediumParagraphFixture(MEDIUM_DEFAULT_URL);
  page.doc.execCommand = () => { page.calls.insertText += 1; return true; };
  const first = inspector.fillVerifiedSourceUrl(page.doc, page.location, page.session);
  assert.equal(first.status, "URL_VERIFICATION_FAILED");
  assert.equal(first.exactUrlPresentAfterward, false);
  const second = inspector.fillVerifiedSourceUrl(page.doc, page.location, page.session);
  assert.equal(second.status, "FILL_LIMIT_REACHED");
  assert.equal(page.calls.insertText, 1);
});

test("unexpected submit or navigation is reported and cannot trigger a retry", () => {
  const page = mediumParagraphFixture(MEDIUM_DEFAULT_URL);
  page.doc.execCommand = () => {
    page.calls.insertText += 1;
    let prevented = false;
    page.doc.submitListener({ preventDefault() { prevented = true; } });
    assert.equal(prevented, true);
    page.location.href = "https://medium.com/p/other";
    return true;
  };
  const result = inspector.fillVerifiedSourceUrl(page.doc, page.location, page.session);
  assert.equal(result.status, "UNEXPECTED_PAGE_CHANGE");
  assert.equal(result.formSubmitted, true);
  assert.equal(result.navigationOccurred, true);
  assert.equal(page.session.fills, 1);
  inspector.fillVerifiedSourceUrl(page.doc, page.location, page.session);
  assert.equal(page.calls.insertText, 1);
});

test("popup inspection never starts a fill attempt", async () => {
  const source = fs.readFileSync(path.join(__dirname, "../popup.js"), "utf8");
  const page = mediumParagraphFixture(MEDIUM_DEFAULT_URL);
  const controls = { inspect: { disabled: false,
    addEventListener(_event, fn) { this.action = fn; } }, report: { textContent: "" } };
  const sent = [];
  const chrome = { tabs: {
    query: async () => [{ id: 17 }],
    sendMessage: async (_id, message) => {
      sent.push(message.type);
      return { ok: true, report: inspector.inspectDocument(page.doc, page.location),
        fills: page.session.fills };
    }
  } };
  vm.runInNewContext(source, { document: { getElementById: id => controls[id] }, chrome });
  await controls.inspect.action();
  assert.deepEqual(sent, ["INSPECT_MEDIUM_PAGE"]);
  assert.equal(page.session.fills, 0);
  assert.match(controls.report.textContent, /Fill-safe: Yes/);
  assert.doesNotMatch(source, /FILL_VERIFIED_SUBSTACK_URL/);
});

test("observed Medium example is recognized with the exact hostname and fingerprint", () => {
  assert.equal(createHash("sha256").update(MEDIUM_DEFAULT_URL).digest("hex"),
    MEDIUM_DEFAULT_FINGERPRINT);
  for (const text of [MEDIUM_DEFAULT_URL, "https://www.yoursite.org/your-post"]) {
    const page = mediumParagraphFixture(text);
    const report = inspector.inspectDocument(page.doc, page.location);
    const input = report.importInterface.sourceUrlInput;
    assert.equal(input.editorState, "MEDIUM_DEFAULT_EXAMPLE_URL");
    assert.equal(input.mediumDefaultExampleRecognized, true);
    assert.equal(input.defaultHostname, "www.yoursite.org");
    assert.equal(input.fillSafe, true);
    assert.equal(input.fillReason, "Verified Medium default example URL");
    assert.equal(report.importInterface.fillAllowed, true);
    assert.equal(input.observedDefaultFingerprintMatches, text === MEDIUM_DEFAULT_URL);
    assert.equal(JSON.stringify(report).includes(text), false);
    const rendered = popupReport(report);
    for (const expected of ["Source editor state: MEDIUM_DEFAULT_EXAMPLE_URL",
      "Medium default example recognized: Yes", "Default hostname: www.yoursite.org",
      "Fill-safe: Yes", "Fill reason: Verified Medium default example URL"]) {
      assert.ok(rendered.includes(expected), expected);
    }
  }
});

test("deceptive hosts and extra URL components remain protected", () => {
  for (const text of [
    "http://yoursite.org.evil.example/your-post",
    "http://other.yoursite.org/your-post",
    "http://www.yoursite.org.evil.example/your-post",
    "http://example.com/your-post",
    "http://www.yoursite.org/your-post?x=1",
    "http://www.yoursite.org/your-post#section",
    "http://user:password@www.yoursite.org/your-post",
    "http://www.yoursite.org:8080/your-post",
    "http://www.yoursite.org/one/two",
    "https://someone.example/post"
  ]) {
    const page = mediumParagraphFixture(text);
    const report = inspector.inspectDocument(page.doc, page.location);
    assert.equal(report.importInterface.sourceUrlInput.editorState, "DIFFERENT_URL_PRESENT", text);
    assert.equal(report.importInterface.sourceUrlInput.fillSafe, false, text);
    assert.equal(report.importInterface.fillAllowed, false, text);
    assert.equal(inspector.fillVerifiedSourceUrl(page.doc, page.location, page.session).urlFillAttempted,
      false, text);
    assert.equal(page.calls.insertText, 0, text);
  }
});

test("wrong page, heading, editor, or Import action cannot recognize the default", () => {
  const cases = [
    mediumParagraphFixture(MEDIUM_DEFAULT_URL),
    mediumParagraphFixture(MEDIUM_DEFAULT_URL),
    mediumParagraphFixture(MEDIUM_DEFAULT_URL),
    mediumParagraphFixture(MEDIUM_DEFAULT_URL),
    mediumParagraphFixture(MEDIUM_DEFAULT_URL)
  ];
  cases[0].location.href = "https://medium.com/other";
  const heading = cases[1].doc.querySelectorAll("h1")[0];
  heading.innerText = "Another heading";
  heading.textContent = "Another heading";
  cases[2].editor.attributes.contenteditable = "false";
  cases[3].buttons[0].disabled = true;
  cases[4] = liveImportFixture({ text: MEDIUM_DEFAULT_URL, actions: 2 });
  for (const page of cases) {
    const report = inspector.inspectDocument(page.doc, page.location);
    assert.notEqual(report.importInterface.sourceUrlInput?.editorState,
      "MEDIUM_DEFAULT_EXAMPLE_URL");
    assert.equal(report.importInterface.fillAllowed, false);
    assert.equal(inspector.fillVerifiedSourceUrl(page.doc, page.location, page.session).urlFillAttempted,
      false);
  }
});

test("a second source editor beyond the displayed inventory prevents default recognition", () => {
  const editor = new Element("div", { role: "textbox", contenteditable: "true" },
    MEDIUM_DEFAULT_URL);
  const second = new Element("div", { role: "textbox", contenteditable: "true" }, "");
  const labels = Array.from({ length: 55 }, (_, index) => new Element("label", {}, `Field ${index}`));
  const doc = fixtureDocument([new Element("button", { type: "submit" }, "Import")],
    [new Element("h1", {}, "See your story on Medium")], [editor], [...labels, second]);
  const report = inspector.inspectDocument(doc, { href: "https://medium.com/p/import" });
  assert.equal(report.importInterface.inventory.length, 50);
  assert.equal(report.importInterface.sourceStatus, "SOURCE_EDITOR_AMBIGUOUS");
  assert.equal(report.importInterface.sourceUrlInput, null);
  assert.equal(report.importInterface.fillAllowed, false);
});

test("one explicit fill replaces the observed example and blocks a second attempt", () => {
  const page = mediumParagraphFixture(MEDIUM_DEFAULT_URL);
  let importClicks = 0;
  page.buttons[0].click = () => { importClicks += 1; };
  page.doc.submit = () => { throw Error("form submit called"); };
  page.doc.requestSubmit = () => { throw Error("form requestSubmit called"); };
  const originalUrl = page.location.href;
  const result = inspector.fillVerifiedSourceUrl(page.doc, page.location, page.session);
  assert.equal(result.status, "URL_FILLED");
  assert.equal(result.urlFillAttempted, true);
  assert.equal(result.defaultExampleReplaced, true);
  assert.equal(result.fillSucceeded, true);
  assert.equal(result.exactUrlPresentAfterward, true);
  assert.equal(page.editor.textContent, VERIFIED_URL);
  assert.equal(page.editor.textContent.includes(MEDIUM_DEFAULT_URL), false);
  assert.equal(page.calls.insertText, 1);
  assert.equal(page.session.fills, 1);
  assert.equal(page.location.href, originalUrl);
  assert.equal(importClicks, 0);
  assert.equal(result.formSubmitted, false);
  assert.equal(result.navigationOccurred, false);
  assert.equal(result.importButtonStillExists, true);
  const report = inspector.inspectDocument(page.doc, page.location);
  assert.equal(report.importInterface.sourceUrlInput.editorState, "EXACT_SOURCE_URL_PRESENT");
  assert.equal(report.importInterface.sourceUrlInput.existingUrl.matchesIntendedSourceExactly, true);
  assert.equal(report.importInterface.sourceUrlInput.existingUrl.sameHostnameAsIntendedSource, true);
  const rendered = popupReport(report) + vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, "../popup.js"), "utf8") + "\nformatFillResult(fill);",
    { fill: result, document: { getElementById: () => ({ addEventListener() {} }) } });
  for (const expected of ["Source editor state: EXACT_SOURCE_URL_PRESENT",
    "Matches intended source exactly: Yes", "Fill attempted: Yes",
    "Default example replaced: Yes", "Fill succeeded: Yes", "Import clicked: No",
    "Form submitted: No", "Navigation occurred: No"]) assert.ok(rendered.includes(expected), expected);
  const second = inspector.fillVerifiedSourceUrl(page.doc, page.location, page.session);
  assert.equal(second.status, "FILL_LIMIT_REACHED");
  assert.equal(second.urlFillAttempted, false);
  assert.equal(page.calls.insertText, 1);
});

test("stale default text or Import controls after focus refuse before mutation", () => {
  for (const change of [
    page => { page.editor.textContent = "http://www.yoursite.org/other-url";
      page.editor.innerText = page.editor.textContent; },
    page => { page.editor.textContent = MEDIUM_DEFAULT_URL + "?x=1";
      page.editor.innerText = page.editor.textContent; },
    page => { page.buttons[0].disabled = true; },
    page => { const heading = page.doc.querySelectorAll("h1")[0];
      heading.innerText = "Another heading"; heading.textContent = "Another heading"; },
    page => { page.location.href = "https://medium.com/other"; }
  ]) {
    const page = mediumParagraphFixture(MEDIUM_DEFAULT_URL);
    page.editor.focus = () => { page.calls.focuses += 1; change(page); };
    const result = inspector.fillVerifiedSourceUrl(page.doc, page.location, page.session);
    assert.equal(result.urlFillAttempted, false);
    assert.equal(result.defaultExampleReplaced, false);
    assert.equal(page.calls.insertText, 0);
    assert.equal(page.session.fills, 0);
    assert.ok(["REFUSE", "UNEXPECTED_PAGE_CHANGE"].includes(result.status));
  }
});

test("failed default replacement consumes the only mutation attempt", () => {
  const page = mediumParagraphFixture(MEDIUM_DEFAULT_URL);
  page.doc.execCommand = () => { page.calls.insertText += 1; return true; };
  const first = inspector.fillVerifiedSourceUrl(page.doc, page.location, page.session);
  assert.equal(first.status, "URL_VERIFICATION_FAILED");
  assert.equal(first.urlFillAttempted, true);
  assert.equal(first.defaultExampleReplaced, false);
  assert.equal(first.fillSucceeded, false);
  assert.equal(first.exactUrlPresentAfterward, false);
  assert.equal(page.session.fills, 1);
  const second = inspector.fillVerifiedSourceUrl(page.doc, page.location, page.session);
  assert.equal(second.status, "FILL_LIMIT_REACHED");
  assert.equal(page.calls.insertText, 1);
});

function popupHarness(respond) {
  const controls = {
    inspect: { disabled: false, addEventListener(_event, fn) { this.action = fn; } },
    report: { textContent: "" }
  };
  const sent = [];
  const chrome = { tabs: {
    query: async () => [{ id: 17 }],
    sendMessage: async (_id, message) => {
      sent.push(message.type);
      return respond(message);
    }
  } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../popup.js"), "utf8"),
    { document: { getElementById: id => controls[id] }, chrome });
  return { controls, sent };
}

test("popup retains inspection for a verified default example without a fill control", async () => {
  const page = mediumParagraphFixture(MEDIUM_DEFAULT_URL);
  const report = inspector.inspectDocument(page.doc, page.location);
  const { controls, sent } = popupHarness(() => ({ ok: true, report }));
  await controls.inspect.action();
  assert.deepEqual(sent, ["INSPECT_MEDIUM_PAGE"]);
  assert.match(controls.report.textContent, /Fill preconditions met: Yes/);
  assert.match(controls.report.textContent, /Source editor state: MEDIUM_DEFAULT_EXAMPLE_URL/);
  assert.equal(page.session.fills, 0);
  assert.doesNotMatch(fs.readFileSync(path.join(__dirname, "../popup.html"), "utf8"),
    /id="fill"/);
});

test("popup reports stale tab without sending a fill request", async () => {
  const page = mediumParagraphFixture(MEDIUM_DEFAULT_URL);
  const controls = { inspect: { disabled: false,
    addEventListener(_event, fn) { this.action = fn; } }, report: { textContent: "" } };
  let activeTabId = 17;
  const chrome = { tabs: {
    query: async () => [{ id: activeTabId }],
    sendMessage: async () => {
      activeTabId = 18;
      return { ok: true, report: inspector.inspectDocument(page.doc, page.location) };
    }
  } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../popup.js"), "utf8"),
    { document: { getElementById: id => controls[id] }, chrome });
  await controls.inspect.action();
  assert.match(controls.report.textContent, /Inspected tab is no longer active/);
  assert.equal(page.session.fills, 0);
});

test("popup displays unsafe source states for diagnosis", async () => {
  const page = mediumParagraphFixture("https://example.com/p/existing");
  const { controls, sent } = popupHarness(() => ({ ok: true,
    report: inspector.inspectDocument(page.doc, page.location) }));
  await controls.inspect.action();
  assert.match(controls.report.textContent, /Source editor state: DIFFERENT_URL_PRESENT/);
  assert.match(controls.report.textContent, /Fill-safe: No/);
  assert.deepEqual(sent, ["INSPECT_MEDIUM_PAGE"]);
});

test("content script inspection preserves the known attempt and rejects fill messages", async () => {
  const page = mediumParagraphFixture(MEDIUM_DEFAULT_URL);
  let listener;
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../content.js"), "utf8"), {
    chrome: { runtime: { onMessage: { addListener(fn) { listener = fn; } } } },
    MediumImportSession: importSession,
    MediumSecondAttempt: secondAttempt,
    MediumInspector: inspector, document: page.doc, location: page.location
  });
  await Promise.resolve();
  const send = type => {
    let response;
    listener({ type }, null, value => { response = value; });
    return response;
  };
  assert.equal(send("INSPECT_MEDIUM_PAGE").fillAttemptAvailable, false);
  assert.equal(send("INSPECT_MEDIUM_PAGE").importState, "IMPORT_RESULT_AMBIGUOUS");
  assert.equal(send("FILL_VERIFIED_SUBSTACK_URL"), undefined);
  assert.equal(page.session.fills, 0);
});

function memoryStorage() {
  const data = new Map();
  return { data, async get(key) { return { [key]: data.get(key) }; },
    async set(values) { for (const [key, value] of Object.entries(values)) data.set(key, value); } };
}

test("known ambiguous attempt survives reinjection, navigation, and extension reload", async () => {
  const storage = memoryStorage();
  const first = await importSession.load(storage);
  assert.equal(first.record.lifecycleState, "IMPORT_RESULT_AMBIGUOUS");
  assert.equal(first.record.clickAttempts, 1);
  const initial = importSession.makeSession(first, storage,
    { href: "https://medium.com/p/import" });
  assert.equal(initial.importClicks, 1);
  assert.equal(initial.importState, "IMPORT_RESULT_AMBIGUOUS");
  const second = importSession.makeSession(await importSession.load(storage), storage,
    { href: "https://medium.com/me/stories" });
  assert.equal(second.importClicks, 1);
  assert.equal(second.importState, "IMPORT_RESULT_AMBIGUOUS");
  const afterReload = importSession.makeSession(await importSession.load(memoryStorage()),
    memoryStorage(), { href: "https://medium.com/me/stories" });
  assert.equal(afterReload.importClicks, 1);
  assert.equal(afterReload.importState, "IMPORT_RESULT_AMBIGUOUS");
  const page = mediumParagraphFixture(VERIFIED_URL);
  page.session = second;
  const ui = onPageFixture(page);
  assert.equal(ui.importButton.disabled, true);
  assert.equal(ui.host.getImportDiagnostics().disabledReason, "Import attempt already used");
});

test("safe session metadata persists click intent and count without source body", async () => {
  const storage = memoryStorage();
  const loaded = await importSession.load(storage);
  const session = importSession.makeSession(loaded, storage,
    { href: "https://medium.com/p/import" });
  session.importState = "IMPORT_CLICK_INTENT";
  session.importClicks = 1;
  session.importStartedAt = 1234;
  assert.equal(await session.persist(), true);
  const saved = storage.data.get(importSession.KEY);
  assert.equal(saved.lifecycleState, "IMPORT_CLICK_INTENT");
  assert.equal(saved.clickAttempts, 1);
  assert.equal(saved.clickIntentTimestamp, 1234);
  assert.equal(saved.startingMediumPath, "/p/import");
  assert.equal(saved.currentMediumPath, "/p/import");
  assert.equal(saved.activationMethod, "PROGRAMMATIC_HTMLELEMENT_CLICK");
  assert.equal(saved.reconciliationStatus, "NO_DRAFT_FOUND");
  assert.equal(Object.keys(saved).length, 9);
  assert.equal(JSON.stringify(saved).includes(VERIFIED_URL), false);
});

class PanelNode {
  constructor(tag) {
    this.tagName = tag.toUpperCase();
    this.children = [];
    this.listeners = {};
    this.attributes = {};
    this._disabled = false;
    this.textContent = "";
  }
  get disabled() { return this._disabled; }
  set disabled(value) {
    this._disabled = Boolean(value);
    if (value) this.attributes.disabled = "";
    else delete this.attributes.disabled;
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; }
  append(...children) {
    for (const child of children) child.parentElement = this;
    this.children.push(...children);
  }
  replaceChildren(...children) {
    for (const child of children) child.parentElement = this;
    this.children = children;
  }
  getBoundingClientRect() {
    return { left: 24, top: 24, width: this.hidden ? 0 : 120,
      height: this.hidden ? 0 : 40 };
  }
  elementFromPoint() { return this.children[1]?.children[4] || null; }
  attachShadow(options) {
    assert.equal(options.mode, "open");
    this.shadowRoot = new PanelNode("shadow");
    return this.shadowRoot;
  }
  addEventListener(type, fn) { this.listeners[type] = fn; }
  remove() {
    this.removed = true;
    if (this.parentElement) this.parentElement.children =
      this.parentElement.children.filter(child => child !== this);
  }
  click() {
    if (this.disabled) return false;
    this.listeners.click({ preventDefault() {}, stopPropagation() {} });
    return true;
  }
}

function onPageFixture(page, pageInspector = inspector) {
  const doc = page.doc;
  const before = doc.querySelectorAll("button, [role='textbox'], h1").slice();
  const originalGetById = doc.getElementById;
  doc.body = new PanelNode("body");
  doc.documentElement = new PanelNode("html");
  doc.createElement = tag => new PanelNode(tag);
  doc.defaultView.getComputedStyle = node => ({ display: node.hidden ? "none" : "block",
    visibility: "visible", opacity: "1", pointerEvents: node.pointerEvents || "auto",
    cursor: node.disabled ? "not-allowed" : "pointer" });
  doc.elementFromPoint = () => doc.body.children[0] || null;
  doc.getElementById = id => doc.body.children.find(node => node.id === id) ||
    originalGetById(id);
  const windowListeners = {};
  doc.defaultView.addEventListener = (type, fn) => { windowListeners[type] = fn; };
  let observer;
  const originalObserver = global.MutationObserver;
  global.MutationObserver = class {
    constructor(callback) { this.callback = callback; observer = this; }
    observe(root, options) { assert.equal(root, doc.documentElement);
      assert.equal(options.subtree, true); }
    disconnect() { this.disconnected = true; }
  };
  let host;
  try {
    host = mediumControls.mount(doc, page.location, pageInspector, page.session);
  } finally {
    global.MutationObserver = originalObserver;
  }
  const panel = host?.shadowRoot.children[1];
  const button = panel?.children[3];
  const importButton = panel?.children[4];
  const lines = () => panel?.children[2].children.map(node => node.textContent).join("\n") || "";
  return { host, panel, button, importButton, lines, observer, before, doc, page,
    windowListeners };
}

test("on-page panel is unique, shadowed, and leaves Medium DOM unchanged", () => {
  const page = mediumParagraphFixture(MEDIUM_DEFAULT_URL);
  const ui = onPageFixture(page);
  assert.equal(ui.host.id, "publish-to-all-medium-controls");
  assert.ok(ui.host.shadowRoot);
  assert.equal(ui.doc.body.children.length, 1);
  assert.equal(ui.panel.children[0].textContent, "Publish to All");
  assert.equal(ui.button.textContent, "Fill verified Substack URL");
  assert.equal(ui.button.type, "button");
  assert.equal(ui.button.disabled, false);
  assert.match(ui.host.shadowRoot.children[0].textContent, /position: fixed/);
  assert.equal(mediumControls.mount(ui.doc, page.location, inspector, page.session), ui.host);
  assert.equal(ui.doc.body.children.length, 1);
  assert.deepEqual(ui.doc.querySelectorAll("button, [role='textbox'], h1"), ui.before);
  assert.equal(page.editor.textContent, MEDIUM_DEFAULT_URL);
  assert.equal(page.session.fills, 0);
  assert.match(ui.lines(), /Page guard: READY/);
  assert.match(ui.lines(), /Source state: MEDIUM_DEFAULT_EXAMPLE_URL/);
  assert.match(ui.lines(), /Fill attempt: AVAILABLE/);
  assert.match(ui.lines(), /Last action: NONE/);
});

test("a new content-script session replaces a stale Shadow DOM panel", () => {
  const page = mediumParagraphFixture(VERIFIED_URL);
  const ui = onPageFixture(page);
  const replacement = { fills: 0, importState: "IMPORT_NOT_STARTED", importClicks: 0,
    canPersistImport: true };
  const host = mediumControls.mount(ui.doc, page.location, inspector, replacement);
  assert.notEqual(host, ui.host);
  assert.equal(ui.host.removed, true);
  assert.equal(ui.doc.body.children.length, 1);
  assert.equal(host.getImportDiagnostics().lifecycleState, "IMPORT_READY");
  assert.equal(replacement.importClicks, 0);
});

test("on-page control appears only on the exact Medium Import path", () => {
  for (const href of ["https://medium.com/", "https://medium.com/p/import/",
    "https://other.example/p/import", "http://medium.com/p/import"]) {
    const page = mediumParagraphFixture(MEDIUM_DEFAULT_URL);
    page.location.href = href;
    const ui = onPageFixture(page);
    assert.equal(ui.host, null, href);
    assert.equal(ui.doc.body.children.length, 0);
  }
});

test("on-page button is disabled for unsafe source, heading, editor, and action states", () => {
  const cases = [
    ["DIFFERENT_URL_PRESENT", () => mediumParagraphFixture("https://example.com/p/existing")],
    ["AMBIGUOUS_CONTENT", () => mediumParagraphFixture("Paste the story URL here")],
    ["NON_URL_MEANINGFUL_CONTENT", () => mediumParagraphFixture("My private draft notes")],
    ["EXACT_SOURCE_URL_PRESENT", () => mediumParagraphFixture(VERIFIED_URL)],
    ["WRONG_HEADING", () => {
      const page = mediumParagraphFixture(MEDIUM_DEFAULT_URL);
      const heading = page.doc.querySelectorAll("h1")[0];
      heading.innerText = "Another heading"; heading.textContent = "Another heading";
      return page;
    }],
    ["AMBIGUOUS_EDITOR", () => liveImportFixture({ text: MEDIUM_DEFAULT_URL,
      extraEditors: [new Element("div", { role: "textbox", contenteditable: "true" }, "")] })],
    ["MISSING_IMPORT", () => liveImportFixture({ text: MEDIUM_DEFAULT_URL, actions: 0 })],
    ["DIFFERENT_DEFAULT", () => mediumParagraphFixture("http://www.yoursite.org/other-post")]
  ];
  for (const [name, makePage] of cases) {
    const page = makePage();
    const ui = onPageFixture(page);
    assert.equal(ui.button.disabled, true, name);
    assert.match(ui.lines(), name === "EXACT_SOURCE_URL_PRESENT" ?
      /Page guard: READY/ : /Page guard: BLOCKED/, name);
    assert.equal(page.session.fills, 0, name);
    assert.equal(ui.button.click(), false, name);
    assert.equal(page.calls.insertText, 0, name);
  }
});

test("on-page click revalidates, inserts once, verifies, and never activates Medium", () => {
  const page = mediumParagraphFixture(MEDIUM_DEFAULT_URL);
  let importClicks = 0;
  page.buttons[0].click = () => { importClicks += 1; };
  page.doc.submit = () => { throw Error("form submit called"); };
  page.doc.requestSubmit = () => { throw Error("requestSubmit called"); };
  const startUrl = page.location.href;
  const ui = onPageFixture(page);
  assert.equal(ui.button.click(), true);
  assert.equal(page.calls.insertText, 1);
  assert.equal(page.editor.textContent, VERIFIED_URL);
  assert.equal(page.editor.textContent.includes(MEDIUM_DEFAULT_URL), false);
  assert.equal(page.session.fills, 1);
  assert.equal(page.location.href, startUrl);
  assert.equal(importClicks, 0);
  assert.equal(ui.button.disabled, true);
  assert.equal(ui.button.click(), false);
  assert.equal(page.calls.insertText, 1);
  assert.match(ui.lines(), /Last action: FILLED/);
  assert.match(ui.lines(), /Exact source verified: YES/);
  assert.match(ui.lines(), /Medium Import clicked: NO/);
  assert.match(ui.panel.children[1].textContent, /Import has NOT been clicked/);
  assert.equal(ui.importButton.disabled, true);
  assert.match(ui.lines(), /Import state: READY/);
});

test("stale page before the on-page click refuses without consuming an attempt", () => {
  const page = mediumParagraphFixture(MEDIUM_DEFAULT_URL);
  const ui = onPageFixture(page);
  assert.equal(ui.button.disabled, false);
  page.editor.textContent = "https://example.com/private";
  page.editor.innerText = page.editor.textContent;
  assert.equal(ui.button.click(), true);
  assert.equal(page.calls.insertText, 0);
  assert.equal(page.session.fills, 0);
  assert.equal(ui.button.disabled, true);
  assert.match(ui.lines(), /Last action: REFUSED/);
  assert.match(ui.lines(), /Source state: DIFFERENT_URL_PRESENT/);
});

test("page mutation refreshes the on-page eligibility", async () => {
  const page = mediumParagraphFixture(MEDIUM_DEFAULT_URL);
  const ui = onPageFixture(page);
  page.editor.textContent = VERIFIED_URL;
  page.editor.innerText = VERIFIED_URL;
  ui.observer.callback();
  await new Promise(resolve => queueMicrotask(resolve));
  assert.equal(ui.button.disabled, true);
  assert.match(ui.lines(), /Source state: EXACT_SOURCE_URL_PRESENT/);
  assert.equal(page.session.fills, 0);
});

test("exact source is diagnosed while manual-attempt preparation stays disabled", () => {
  const exact = onPageFixture(mediumParagraphFixture(VERIFIED_URL));
  assert.equal(exact.button.disabled, true);
  assert.equal(exact.importButton.disabled, true);
  assert.match(exact.lines(), /Verified source: YES/);
  assert.match(exact.lines(), /Import state: READY/);
  assert.match(exact.lines(), /Import lifecycle state: IMPORT_READY/);
  assert.match(exact.lines(), /Import attempt available: Yes/);
  assert.match(exact.lines(), /Import click attempts: 0/);
  assert.match(exact.lines(), /Import guard allowed: Yes/);
  assert.match(exact.lines(), /Import disabled reason: disabled property is true/);
  assert.match(exact.lines(), /Extension Import control: DISABLED/);
  assert.match(exact.lines(), /Import actual interactability: BLOCKED/);
  assert.match(exact.lines(), /Hit-test: BUTTON/);
  assert.match(exact.lines(), /Import button disabled property: true/);
  assert.match(exact.lines(), /Import button disabled attribute: present/);
  assert.match(exact.lines(), /Import button aria-disabled: true/);
  assert.match(exact.lines(), /Import button pointer-events: auto/);
  assert.match(exact.lines(), /Import button cursor: not-allowed/);
  assert.match(exact.lines(), /Import panel pointer-events: auto/);
  assert.match(exact.lines(), /Import host pointer-events: auto/);
  assert.match(exact.lines(), /Import disabled CSS class: absent/);
  assert.match(exact.lines(), /Import button bounding box: 24, 24, 120 x 40/);
  const hiddenCharacter = onPageFixture(mediumParagraphFixture(`\u200b${VERIFIED_URL}`));
  assert.equal(hiddenCharacter.importButton.disabled, true);
  assert.match(hiddenCharacter.lines(), /Import raw-source guard: PASS/);
  assert.match(hiddenCharacter.lines(), /Import source fingerprint equality: Yes/);
  const duplicate = onPageFixture(liveImportFixture({ text: VERIFIED_URL, actions: 2 }));
  assert.equal(duplicate.importButton.disabled, true);
  const disabled = liveImportFixture({ text: VERIFIED_URL });
  disabled.buttons[0].disabled = true;
  assert.equal(onPageFixture(disabled).importButton.disabled, true);
});

test("inspector and fresh resolver share normalization without changing URL identity", () => {
  for (const text of [VERIFIED_URL, `${VERIFIED_URL}\n`, `\u200b${VERIFIED_URL}`,
    `\u2060${VERIFIED_URL}`, `\u00a0${VERIFIED_URL}\u00a0`]) {
    const page = mediumParagraphFixture(text);
    const report = inspector.inspectDocument(page.doc, page.location);
    const raw = inspector.inspectImportRaw(page.doc, page.location, report);
    assert.equal(report.importInterface.sourceUrlInput.editorState,
      "EXACT_SOURCE_URL_PRESENT");
    assert.equal(raw.sourceGuard, true);
    assert.equal(raw.source.normalizedValuesEqual, true);
    assert.equal(raw.source.inspectorFingerprint, raw.source.resolverFingerprint);
    assert.equal(raw.source.resolverFingerprint,
      "2a5f676c8743eda141101e793cc4ce988ba6f029d0805c0498271bd072937349");
    assert.equal(raw.source.normalizedLength, VERIFIED_URL.length);
    assert.equal(raw.source.representation, "contenteditable paragraph");
  }
  const different = mediumParagraphFixture("https://cyporter.substack.com/p/different");
  const report = inspector.inspectDocument(different.doc, different.location);
  const raw = inspector.inspectImportRaw(different.doc, different.location, report);
  assert.equal(raw.sourceGuard, false);
  assert.equal(raw.reason, "SOURCE_RAW_NORMALIZATION_MISMATCH");
  assert.equal(raw.source.fingerprintMatch, false);
  assert.equal(JSON.stringify(raw).includes("/p/different"), false);
  const encoded = mediumParagraphFixture(VERIFIED_URL.replace("saturation", "%73aturation"));
  const encodedRaw = inspector.inspectImportRaw(encoded.doc, encoded.location,
    inspector.inspectDocument(encoded.doc, encoded.location));
  assert.equal(encodedRaw.sourceGuard, false);
  assert.equal(encodedRaw.source.fingerprintMatch, false);
});

test("raw Import diagnostics distinguish each action failure without clicking", () => {
  const cases = [
    ["IMPORT_ACTION_COUNT_MISMATCH", page => {
      const query = page.doc.querySelectorAll.bind(page.doc);
      const duplicate = new Element("button", { type: "submit" }, "Import");
      page.doc.querySelectorAll = selector => selector.includes("button") ?
        [...query(selector), duplicate] : query(selector);
    }],
    ["IMPORT_ACTION_TYPE_MISMATCH", page => { page.buttons[0].attributes.type = "button"; }],
    ["IMPORT_ACTION_TAG_MISMATCH", page => {
      page.buttons[0].tagName = "DIV";
      page.buttons[0].attributes.role = "button";
    }],
    ["IMPORT_ACTION_ROLE_MISMATCH", page => {
      page.buttons[0].attributes.role = "link";
    }],
    ["IMPORT_ACTION_TEXT_MISMATCH", page => {
      page.buttons[0].innerText = "Import story";
      page.buttons[0].textContent = "Import story";
    }],
    ["IMPORT_ACTION_ACCESSIBLE_NAME_MISMATCH", page => {
      page.buttons[0].attributes["aria-label"] = "Import story";
    }],
    ["IMPORT_ACTION_NOT_VISIBLE", page => { page.buttons[0].hidden = true; }],
    ["IMPORT_ACTION_DISABLED", page => { page.buttons[0].disabled = true; }],
    ["IMPORT_ACTION_STALE", page => { page.buttons[0].isConnected = false; }]
  ];
  for (const [reason, mutate] of cases) {
    const page = mediumParagraphFixture(VERIFIED_URL);
    let clicks = 0;
    page.buttons[0].click = () => { clicks += 1; };
    mutate(page);
    const report = inspector.inspectDocument(page.doc, page.location);
    const raw = inspector.inspectImportRaw(page.doc, page.location, report);
    assert.equal(raw.reason, reason);
    assert.equal(raw.actionGuard, false);
    assert.equal(raw.targets, null);
    assert.equal(clicks, 0);
  }
});

test("HTML button effective type is shared by inspector and fresh resolver", () => {
  for (const [attribute, expectedType, ready] of [
    [null, "submit", true], ["submit", "submit", true],
    ["button", "button", false], ["unknown", "submit", false],
    ["", "submit", false], [" submit ", "submit", false]
  ]) {
    const page = mediumParagraphFixture(VERIFIED_URL);
    if (attribute === null) delete page.buttons[0].attributes.type;
    else page.buttons[0].attributes.type = attribute;
    let clicks = 0;
    page.buttons[0].click = () => { clicks += 1; };
    assert.equal(page.buttons[0].getAttribute("type"), attribute);
    assert.equal(page.buttons[0].type, expectedType);
    const report = inspector.inspectDocument(page.doc, page.location);
    const raw = inspector.inspectImportRaw(page.doc, page.location, report);
    const selected = report.importInterface.importAction;
    assert.equal(selected.typeAttributePresent, attribute !== null);
    assert.equal(selected.typeAttributeValue, attribute);
    assert.equal(selected.effectiveDomType, expectedType);
    assert.equal(selected.type, raw.action.effectiveDomType);
    assert.equal(raw.action.typeAttributePresent, attribute !== null);
    assert.equal(raw.action.typeAttributeValue, attribute);
    assert.equal(raw.action.effectiveTypeMatch, expectedType === "submit");
    assert.equal(raw.action.typeAttributeValid,
      attribute === null || ["submit", "button"].includes(attribute));
    assert.equal(raw.actionGuard, ready);
    assert.equal(raw.sourceGuard, true);
    assert.equal(raw.reason, ready ? "None" : "IMPORT_ACTION_TYPE_MISMATCH");
    assert.equal(mediumControls.importEligibility(report, { importClicks: 0 },
      page.location, false).ready, ready);
    assert.equal(clicks, 0);
  }
});

test("omitted type is ready in on-page diagnostics without activation", () => {
  const page = mediumParagraphFixture(VERIFIED_URL);
  delete page.buttons[0].attributes.type;
  let clicks = 0;
  page.buttons[0].click = () => { clicks += 1; };
  const ui = onPageFixture(page);
  const diagnostics = ui.host.getImportDiagnostics();
  assert.equal(diagnostics.guardAllowed, true);
  assert.equal(diagnostics.lifecycleState, "IMPORT_READY");
  assert.equal(diagnostics.clickAttempts, 0);
  assert.equal(diagnostics.control, "DISABLED");
  for (const expected of ["Import raw-source guard: PASS", "Import raw-action guard: PASS",
    "Import type attribute present: No", "Import type attribute value: Absent",
    "Import type attribute valid: Yes",
    "Import effective DOM type: submit", "Import effective type match: Yes",
    "Native Import identity: PASS", "Overall Import guard: PASS"]) {
    assert.ok(ui.lines().includes(expected), expected);
  }
  assert.equal(clicks, 0);
});

test("raw source count and stale source identity have distinct reasons", () => {
  const duplicate = liveImportFixture({ text: VERIFIED_URL, extraEditors: [
    new Element("div", { role: "textbox", contenteditable: "true" }, VERIFIED_URL)
  ] });
  let raw = inspector.inspectImportRaw(duplicate.doc, duplicate.location,
    inspector.inspectDocument(duplicate.doc, duplicate.location));
  assert.equal(raw.reason, "SOURCE_EDITOR_COUNT_MISMATCH");
  const page = mediumParagraphFixture(VERIFIED_URL);
  const report = inspector.inspectDocument(page.doc, page.location);
  const replacement = new Element("div", { ...page.editor.attributes }, VERIFIED_URL);
  const query = page.doc.querySelectorAll.bind(page.doc);
  page.doc.querySelectorAll = selector => query(selector).map(element =>
    element === page.editor ? replacement : element);
  raw = inspector.inspectImportRaw(page.doc, page.location, report);
  assert.equal(raw.reason, "SOURCE_EDITOR_STALE");
  assert.equal(raw.source.identityMatchesInspection, false);
});

test("Import readiness has no combined raw failure reason", () => {
  const controls = fs.readFileSync(path.join(__dirname, "../medium-controls.js"), "utf8");
  assert.doesNotMatch(controls, /Raw source or Import action differs/);
  const page = mediumParagraphFixture(VERIFIED_URL);
  const ui = onPageFixture(page);
  assert.match(ui.lines(), /Import raw-source guard: PASS/);
  assert.match(ui.lines(), /Import raw-action guard: PASS/);
  assert.match(ui.lines(), /Overall Import guard: PASS/);
  assert.equal(page.session.importClicks || 0, 0);
});

test("Shadow DOM controls are excluded and hidden native duplicate blocks Import", () => {
  const page = mediumParagraphFixture(VERIFIED_URL);
  const extensionButton = new Element("button", { type: "submit" }, "Import");
  extensionButton.getRootNode = () => ({ host: { id: "publish-to-all-medium-controls" } });
  const query = page.doc.querySelectorAll.bind(page.doc);
  page.doc.querySelectorAll = selector => selector.includes("button") ?
    [...query(selector), extensionButton] : query(selector);
  let report = inspector.inspectDocument(page.doc, page.location);
  let raw = inspector.inspectImportRaw(page.doc, page.location, report);
  assert.equal(raw.action.candidateCount, 1);
  assert.equal(raw.actionGuard, true);
  assert.equal(report.importInterface.matchingImportActionCount, 1);
  const hidden = new Element("button", { type: "submit" }, "Import", { hidden: true });
  page.doc.querySelectorAll = selector => selector.includes("button") ?
    [...query(selector), extensionButton, hidden] : query(selector);
  report = inspector.inspectDocument(page.doc, page.location);
  raw = inspector.inspectImportRaw(page.doc, page.location, report);
  assert.equal(raw.action.candidateCount, 2);
  assert.equal(raw.reason, "IMPORT_ACTION_COUNT_MISMATCH");
  assert.equal(report.importInterface.matchingImportActionCount, 2);
});

test("repeated Import inspection keeps the ready attempt unused", () => {
  const page = mediumParagraphFixture(VERIFIED_URL);
  const ui = onPageFixture(page);
  for (let index = 0; index < 3; index += 1) {
    const state = ui.host.getImportDiagnostics();
    assert.equal(state.lifecycleState, "IMPORT_READY");
    assert.equal(state.attemptAvailable, true);
    assert.equal(state.clickAttempts, 0);
    assert.equal(state.guardAllowed, true);
  }
  assert.equal(page.session.importClicks || 0, 0);
});

test("Import hit-test blockage is explicit when logical guard allows activation", () => {
  const ui = onPageFixture(mediumParagraphFixture(VERIFIED_URL));
  ui.doc.elementFromPoint = () => new PanelNode("aside");
  const state = ui.host.getImportDiagnostics();
  assert.equal(state.guardAllowed, true);
  assert.equal(state.control, "DISABLED");
  assert.equal(state.interactability, "BLOCKED");
  assert.equal(state.disabledReason, "disabled property is true");
  assert.match(state.hitTest, /blocked by ASIDE/);
});

test("disabled preparation retains aria-disabled and reports pointer blockage", () => {
  const ui = onPageFixture(mediumParagraphFixture(VERIFIED_URL));
  ui.importButton.setAttribute("aria-disabled", "true");
  assert.equal(ui.host.getImportDiagnostics().ariaDisabled, "true");
  ui.importButton.pointerEvents = "none";
  const state = ui.host.getImportDiagnostics();
  assert.equal(state.guardAllowed, true);
  assert.equal(state.interactability, "BLOCKED");
  assert.equal(state.disabledReason, "disabled property is true");
  assert.equal(state.pointerEvents, "none");
});

test("Import readiness requires every independently verified page signature", () => {
  const page = mediumParagraphFixture(VERIFIED_URL);
  const report = inspector.inspectDocument(page.doc, page.location);
  const session = { importState: "IMPORT_NOT_STARTED", importClicks: 0,
    canPersistImport: true };
  assert.equal(mediumControls.importEligibility(report, session, page.location, false).ready,
    true);
  const mutations = [
    view => { view.matchingHeadingCount = 0; view.heading = null;
      view.headingVerified = true; },
    view => { view.matchingHeadingCount = 2; },
    view => { view.matchingSourceEditorCount = 2; },
    view => { view.sourceUrlInput.editorState = "DIFFERENT_URL_PRESENT"; },
    view => { view.sourceUrlInput.existingUrl.safeUrlFingerprint = "wrong"; },
    view => { view.sourceUrlInput.existingUrl.matchesIntendedSourceExactly = false; },
    view => { view.matchingImportActionCount = 2; },
    view => { view.importAction.accessibleName = "Import story"; },
    view => { view.importAction.visibleText = "Import story"; },
    view => { view.importAction.enabled = false; }
  ];
  for (const mutate of mutations) {
    const changed = JSON.parse(JSON.stringify(report));
    mutate(changed.importInterface);
    assert.equal(mediumControls.importEligibility(changed, session, page.location, false).ready,
      false);
  }
  assert.equal(mediumControls.importEligibility(report, session,
    { href: "https://medium.com/p/import?x=1" }, false).ready, false);
  assert.equal(mediumControls.importEligibility(report, session, page.location, true).ready,
    false);
  assert.equal(mediumControls.importEligibility(report,
    { ...session, importClicks: 1 }, page.location, false).ready, false);
});

test("changed source cannot enable manual-attempt preparation", () => {
  const page = mediumParagraphFixture(VERIFIED_URL);
  page.session.importClicks = 1;
  page.session.importState = "IMPORT_RESULT_AMBIGUOUS";
  page.session.lastFillResult = { fillSynchronizationStatus: "VERIFIED",
    exactUrlPresentAfterward: true, fingerprintMatch: true };
  const ui = onPageFixture(page);
  page.editor.textContent = "https://example.com/other";
  page.editor.innerText = page.editor.textContent;
  const diagnostics = ui.host.getImportDiagnostics();
  assert.equal(diagnostics.secondAttemptEligibility, "NOT_READY");
  assert.equal(ui.importButton.click(), false);
  assert.equal(page.session.importClicks, 1);
});

test("replaced native Import action cannot enable manual preparation", () => {
  const page = mediumParagraphFixture(VERIFIED_URL);
  const ui = onPageFixture(page);
  const replacement = new Element("button", { type: "submit" }, "Import");
  const originalQuery = page.doc.querySelectorAll.bind(page.doc);
  page.doc.querySelectorAll = selector => selector.includes("button") ?
    [...originalQuery(selector).filter(node => node !== page.buttons[0]), replacement] :
    originalQuery(selector);
  assert.equal(ui.importButton.disabled, true);
  assert.equal(ui.importButton.click(), false);
  assert.equal(page.session.importClicks || 0, 0);
});

test("navigation and unavailable storage keep preparation disabled", () => {
  const page = mediumParagraphFixture(VERIFIED_URL);
  const ui = onPageFixture(page);
  ui.windowListeners.pagehide();
  assert.equal(ui.importButton.click(), false);
  assert.equal(page.session.importClicks || 0, 0);
  const noStorage = mediumParagraphFixture(VERIFIED_URL);
  noStorage.session.secondAttemptStorageAvailable = false;
  const blocked = onPageFixture(noStorage);
  assert.equal(blocked.importButton.disabled, true);
  assert.equal(blocked.host.getImportDiagnostics().secondAttemptEligibility, "NOT_READY");
});

test("manual preparation never invokes the native Import button", () => {
  const page = mediumParagraphFixture(VERIFIED_URL);
  let attempted = 0;
  page.buttons[0].click = () => { attempted += 1; throw Error("should not run"); };
  page.session.persist = () => { throw Error("should not persist an old attempt"); };
  const ui = onPageFixture(page);
  assert.equal(ui.importButton.click(), false);
  assert.equal(attempted, 0);
  assert.equal(page.session.importClicks || 0, 0);
});

test("attempt 1 history remains consumed after reconciliation", async () => {
  const storage = memoryStorage();
  const first = await importSession.load(storage);
  assert.equal(first.record.clickAttempts, 1);
  assert.equal(first.record.lifecycleState, "IMPORT_RESULT_AMBIGUOUS");
  assert.equal(importSession.KNOWN_HISTORY.activationMethod,
    "PROGRAMMATIC_HTMLELEMENT_CLICK");
  assert.equal(importSession.KNOWN_HISTORY.reconciliation, "NO_DRAFT_FOUND");
  const page = mediumParagraphFixture(VERIFIED_URL);
  page.session = importSession.makeSession(first, storage, page.location);
  const ui = onPageFixture(page);
  assert.equal(ui.importButton.click(), false);
  assert.equal(page.session.importClicks, 1);
});

test("known ambiguous Import attempt allows new-tab Fill while keeping Import locked", () => {
  const page = mediumParagraphFixture(MEDIUM_DEFAULT_URL);
  page.session.importClicks = 1;
  page.session.importState = "IMPORT_RESULT_AMBIGUOUS";
  let importClicks = 0;
  page.buttons[0].click = () => { importClicks += 1; };
  const ui = onPageFixture(page);
  assert.equal(ui.button.disabled, false);
  assert.equal(ui.importButton.disabled, true);
  assert.equal(ui.button.click(), true);
  assert.equal(page.calls.insertText, 1);
  assert.match(ui.lines(), /Fill synchronization status: VERIFIED/);
  assert.equal(ui.importButton.disabled, true);
  assert.equal(ui.importButton.click(), false);
  assert.equal(page.session.importClicks, 1);
  assert.equal(page.session.importState, "IMPORT_RESULT_AMBIGUOUS");
  assert.equal(importClicks, 0);
});

test("verified second-attempt prerequisites report READY without enabling preparation", () => {
  const page = mediumParagraphFixture(VERIFIED_URL);
  page.session.importClicks = 1;
  page.session.importState = "IMPORT_RESULT_AMBIGUOUS";
  page.session.lastFillResult = { fillSynchronizationStatus: "VERIFIED",
    exactUrlPresentAfterward: true, fingerprintMatch: true };
  const ui = onPageFixture(page);
  const diagnostics = ui.host.getImportDiagnostics();
  assert.equal(diagnostics.secondAttemptEligibility, "READY");
  assert.equal(diagnostics.secondAttemptAuthorization, "NOT_AUTHORIZED");
  assert.equal(diagnostics.observedNativeImportClicks, 0);
  assert.equal(diagnostics.automaticNativeImportActivation, "DISABLED");
  assert.equal(ui.importButton.disabled, true);
  assert.equal(ui.importButton.click(), false);
  assert.equal(page.session.importClicks, 1);
});

test("known ambiguous result cannot trigger a second Import", () => {
  const page = mediumParagraphFixture(VERIFIED_URL);
  page.session.importClicks = 1;
  page.session.importState = "IMPORT_RESULT_AMBIGUOUS";
  const ui = onPageFixture(page);
  assert.equal(ui.importButton.click(), false);
  assert.equal(page.session.importState, "IMPORT_RESULT_AMBIGUOUS");
  assert.equal(ui.importButton.disabled, true);
  assert.match(ui.panel.children[1].textContent, /ambiguous.*no retry/i);
});

test("a unique See your story link alone remains ambiguous", () => {
  const link = new Element("a", { href: "/p/abcdef123456/edit" }, "See your story");
  const resultPage = { doc: fixtureDocument([link], [], []),
    location: { href: "https://medium.com/p/import" },
    session: { fills: 0, importClicks: 1, importState: "IMPORT_RESULT_AMBIGUOUS" } };
  const result = onPageFixture(resultPage);
  assert.equal(inspector.inspectReconciliation(resultPage.doc, resultPage.location).status,
    "AMBIGUOUS");
  assert.equal(result.importButton.disabled, true);
  const duplicate = fixtureDocument([link,
    new Element("a", { href: "/p/123456abcdef/edit" }, "See your story")], [], []);
  assert.equal(inspector.inspectImportResult(duplicate, resultPage.location).verified, false);
  assert.equal(inspector.inspectImportResult(fixtureDocument([
    new Element("a", { href: "https://evil.example/p/abcdef123456/edit" }, "See your story")],
  [], []), resultPage.location).verified, false);
});

test("a draft link already visible before Import blocks the click", () => {
  const editor = new Element("div", { role: "textbox", contenteditable: "true" }, VERIFIED_URL);
  const action = new Element("button", { type: "submit" }, "Import");
  let clicks = 0;
  action.click = () => { clicks += 1; };
  const link = new Element("a", { href: "/p/abcdef123456/edit" }, "See your story");
  const page = { doc: fixtureDocument([action, link],
    [new Element("h1", {}, "See your story on Medium")], [editor]),
  location: { href: "https://medium.com/p/import" }, session: { fills: 0 } };
  const ui = onPageFixture(page);
  assert.equal(ui.importButton.disabled, true);
  assert.match(ui.lines(), /Draft result was already present before Import/);
  assert.equal(clicks, 0);
});

test("draft verification needs editor content and visible Publish control on edit route", () => {
  const location = { href: "https://medium.com/p/abcdef123456/edit" };
  const editor = new Element("div", { contenteditable: "true" }, "Imported story body");
  const publish = new Element("button", {}, "Publish");
  const title = new Element("h1", {}, "Saturation of Artificial Intelligence");
  assert.equal(inspector.inspectDraftEditor(fixtureDocument([publish], [title], [editor]), location)
    .verified, true);
  assert.equal(inspector.inspectDraftEditor(fixtureDocument([publish], [title], []), location)
    .verified, false);
  assert.equal(inspector.inspectDraftEditor(fixtureDocument([], [title], [editor]), location)
    .verified, false);
  assert.equal(inspector.inspectDraftEditor(fixtureDocument([publish], [title], [editor]),
    { href: "https://medium.com/new-story" }).verified, false);
});

test("Import-page reconciliation reads validation and success cues without clicking", () => {
  const location = { href: "https://medium.com/p/import" };
  const alert = new Element("p", { role: "alert" }, "Please enter a valid URL");
  const link = new Element("a", { href: "/p/abcdef123456/edit" }, "See your story");
  const result = inspector.inspectReconciliation(fixtureDocument([link], [], [], [alert]), location);
  assert.equal(result.validation.detected, true);
  assert.equal(result.validation.text, "Please enter a valid URL");
  assert.equal(result.importPage.seeYourStory, true);
  assert.equal(result.importPage.draftEditLink, true);
  assert.equal(result.status, "AMBIGUOUS");
  assert.equal(result.retryAllowed, false);
  const instruction = inspector.inspectReconciliation(fixtureDocument([], [], [], [
    new Element("p", {}, "Paste a link")]), location);
  assert.equal(instruction.validation.detected, false);
});

function storyRow(state, href = "/p/abcdef123456/edit") {
  const row = new Element("li", {}, `Saturation of Artificial Intelligence ${state}`);
  const title = new Element("h3", {}, "Saturation of Artificial Intelligence");
  const link = new Element("a", { href }, "Edit");
  const stamp = new Element("time", {}, "Oct 7, 2026");
  title.parentElement = row;
  link.parentElement = row;
  stamp.parentElement = row;
  title.closest = selector => selector.startsWith("article") ? row : null;
  row.querySelectorAll = selector => selector === "a" ? [link] :
    selector === "time" ? [stamp] : [];
  return { row, title, link, stamp };
}

function storyFixture(tabs, contentNodes = [], outsideNodes = []) {
  const root = new Element("main");
  const navigation = new Element("div");
  const heading = new Element("h1", {}, "Stories");
  const tablist = new Element("div", { role: "tablist" });
  const content = new Element("section", { "data-testid": "stories-content" });
  root.children = [navigation, content];
  navigation.parentElement = root;
  content.parentElement = root;
  navigation.children = [heading, tablist];
  heading.parentElement = navigation;
  tablist.parentElement = navigation;
  tablist.children = tabs;
  tabs.forEach(tab => { tab.parentElement = tablist; });
  const candidates = [...contentNodes];
  for (const node of [...contentNodes]) {
    if (node.querySelectorAll) {
      for (const nested of node.querySelectorAll("a") || [])
        if (nested.parentElement === node && !candidates.includes(nested)) candidates.push(nested);
    }
  }
  for (const node of candidates) {
    if (!node.parentElement) node.parentElement = content;
    const parent = node.parentElement;
    if (!parent.children.includes(node)) parent.children.push(node);
  }
  const all = [root, navigation, heading, tablist, ...tabs, content,
    ...candidates, ...outsideNodes];
  const matches = (node, part) => {
    const value = part.trim();
    const tag = value.match(/^[a-z][\w-]*/)?.[0];
    if (tag && node.tagName.toLowerCase() !== tag) return false;
    const attr = value.match(/\[([\w-]+)(\*?=)?['"]?([^'"\]]*)['"]?\]/);
    if (attr) {
      const actual = node.getAttribute(attr[1]);
      if (actual === null || attr[2] === "=" && actual !== attr[3] ||
          attr[2] === "*=" && !actual.includes(attr[3])) return false;
    }
    return Boolean(tag || attr);
  };
  const find = (scope, selector) => all.filter(node => node !== scope && scope.contains(node) &&
    selector.split(",").some(part => matches(node, part)));
  for (const node of all) {
    if (!Object.hasOwn(node, "querySelectorAll")) node.querySelectorAll = selector => find(node, selector);
    if (!Object.hasOwn(node, "querySelector")) node.querySelector = selector => find(node, selector)[0] || null;
    node.childElementCount = node.children.length;
  }
  const doc = fixtureDocument([...tabs, ...candidates, ...outsideNodes], [heading], [],
    [root, navigation, tablist, content]);
  doc.querySelectorAll = selector => all.filter(node =>
    selector.split(",").some(part => matches(node, part)));
  return { doc, root, tablist, content };
}

function storyTabs(selected = "Drafts") {
  return ["Drafts", "Published", "Responses", "Submissions"].map(label =>
    new Element("button", { role: "tab", "aria-selected": String(label === selected) }, label));
}

function sameHrefStoryTabs() {
  return ["Drafts", "Published", "Submissions"].map(label =>
    new Element("a", { role: "tab", href: "/me/stories",
      id: `tab-${label.toLowerCase()}` }, label));
}

test("Stories tab selection uses aria-selected and aria-current, not shared hrefs", () => {
  for (const [attribute, value, expected] of [
    ["aria-selected", "true", "DRAFTS_SELECTED"],
    ["aria-current", "page", "DRAFTS_SELECTED"],
    ["aria-selected", "true", "PUBLISHED_SELECTED"],
    ["aria-current", "page", "SUBMISSIONS_SELECTED"]
  ]) {
    const tabs = sameHrefStoryTabs();
    const chosen = expected === "PUBLISHED_SELECTED" ? tabs[1] :
      expected === "SUBMISSIONS_SELECTED" ? tabs[2] : tabs[0];
    chosen.attributes[attribute] = value;
    const result = inspector.inspectStoriesPage(fixtureDocument(tabs, [], []),
      { href: "https://medium.com/me/stories" });
    assert.equal(result.tabState, expected);
    assert.equal(result.draftControl.selected, expected === "DRAFTS_SELECTED");
    assert.equal(result.tabDiagnostics.length, 3);
    assert.equal(result.tabDiagnostics[0].hrefShape, "https://medium.com/me/stories");
  }
});

test("a unique selected class on the tab or its ancestor establishes Drafts", () => {
  for (const ancestor of [false, true]) {
    const tabs = sameHrefStoryTabs();
    if (ancestor) tabs[0].parentElement = new Element("li", { class: "nav-item is-selected" });
    else tabs[0].attributes.class = "storyTabActive";
    const result = inspector.inspectStoriesPage(fixtureDocument(tabs, [], []),
      { href: "https://medium.com/me/stories" });
    assert.equal(result.tabState, "DRAFTS_SELECTED");
    assert.match(result.draftsSelectedEvidence.join(" "), ancestor ? /ancestor/ : /tab/);
    assert.ok(ancestor ? result.tabDiagnostics[0].ancestorState.length :
      result.tabDiagnostics[0].stateClasses.includes("storyTabActive"));
  }
});

test("visible panel linked by aria-controls or aria-labelledby identifies its tab", () => {
  for (const linkMethod of ["aria-controls", "aria-labelledby"]) {
    const tabs = sameHrefStoryTabs();
    const panel = new Element("section", { role: "tabpanel", id: "draft-panel" },
      "No drafts yet");
    const hiddenPanel = new Element("section", { role: "tabpanel", id: "other-panel",
      "aria-hidden": "true" }, "Published stories");
    if (linkMethod === "aria-controls") tabs[0].attributes[linkMethod] = "draft-panel";
    else panel.attributes[linkMethod] = "tab-drafts";
    const tablist = new Element("div", { role: "tablist" });
    const result = inspector.inspectStoriesPage(
      fixtureDocument(tabs, [], [], [panel, hiddenPanel, tablist]),
      { href: "https://medium.com/me/stories" });
    assert.equal(result.tabState, "DRAFTS_SELECTED");
    assert.equal(result.tablistFound, true);
    assert.equal(result.visibleTabpanelCount, 1);
    assert.equal(result.hiddenTabpanelCount, 1);
    assert.equal(result.contentRegion, "Drafts-linked visible tabpanel");
  }
});

test("unstyled tabs with identical hrefs and promotional content remain unknown", () => {
  const tabs = sameHrefStoryTabs();
  const promo = new Element("p", {}, "Write, grow, and reach readers on Medium");
  const result = inspector.inspectStoriesPage(fixtureDocument(tabs, [], [], [promo]),
    { href: "https://medium.com/me/stories" });
  assert.equal(result.tabState, "STORIES_TAB_STATE_UNKNOWN");
  assert.equal(result.draftListConfirmed, false);
  assert.equal(result.draftListEmptyState, false);
  assert.equal(result.status, "AMBIGUOUS");
  assert.equal(result.postTabContentRegionIdentified, false);
  assert.equal(result.contentSnippets.length, 0);
});

test("visual difference corroborates a structural selection but cannot establish one", () => {
  const tabs = sameHrefStoryTabs();
  tabs[0].computedStyle = { fontWeight: "700", borderBottomWidth: "2px" };
  tabs[1].computedStyle = tabs[2].computedStyle =
    { fontWeight: "400", borderBottomWidth: "0px" };
  const doc = fixtureDocument(tabs, [], []);
  const unknown = inspector.inspectStoriesPage(doc, { href: "https://medium.com/me/stories" });
  assert.equal(unknown.tabState, "STORIES_TAB_STATE_UNKNOWN");
  assert.match(unknown.visualEvidence.join(" "), /Drafts: font-weight=700/);
  tabs[0].attributes["aria-selected"] = "true";
  const selected = inspector.inspectStoriesPage(doc, { href: "https://medium.com/me/stories" });
  assert.equal(selected.tabState, "DRAFTS_SELECTED");
  assert.equal(selected.tabDiagnostics[0].style.borderBottomWidth, "2px");
});

test("a selected Drafts empty state is conclusive without a story click", () => {
  const tabs = sameHrefStoryTabs();
  tabs[0].attributes["aria-selected"] = "true";
  const empty = new Element("p", {}, "No drafts yet");
  const result = inspector.inspectReconciliation(storyFixture(tabs, [empty]).doc,
    { href: "https://medium.com/me/stories" });
  assert.equal(result.status, "NO_DRAFT_FOUND");
  assert.equal(result.stories.draftListConfirmed, true);
  assert.equal(result.stories.listConclusivelyLoaded, true);
  assert.equal(result.stories.draftListEmptyState, true);
  assert.equal(result.stories.draftEntriesVisible, false);
});

test("a promotional panel and unrelated empty message cannot confirm the Drafts list", () => {
  const tabs = sameHrefStoryTabs();
  tabs[0].attributes["aria-selected"] = "true";
  tabs[0].attributes["aria-controls"] = "draft-panel";
  const draftPanel = new Element("section", { role: "tabpanel", id: "draft-panel" },
    "Write, grow, and reach readers on Medium");
  const otherPanel = new Element("section", { role: "tabpanel", id: "other-panel" },
    "No drafts yet");
  const strayEmpty = new Element("p", {}, "No drafts yet");
  strayEmpty.parentElement = otherPanel;
  const result = inspector.inspectStoriesPage(
    fixtureDocument(tabs, [], [], [draftPanel, otherPanel, strayEmpty]),
    { href: "https://medium.com/me/stories" });
  assert.equal(result.tabState, "DRAFTS_SELECTED");
  assert.equal(result.contentRegion, "Drafts-linked visible tabpanel");
  assert.equal(result.draftListConfirmed, false);
  assert.equal(result.listConclusivelyLoaded, false);
  assert.equal(result.status, "AMBIGUOUS");
});

function promoNodes() {
  const block = new Element("div", { class: "empty-promo" });
  const heading = new Element("h2", {}, "Write, grow, and reach readers on Medium");
  const body = new Element("p", {}, "Share your ideas with readers.");
  const action = new Element("a", { href: "/new-story" }, "Write a story");
  for (const node of [heading, body, action]) node.parentElement = block;
  return [block, heading, body, action];
}

test("selected Drafts explicit no-drafts text is scoped and conclusive", () => {
  const { doc, content } = storyFixture(storyTabs(), [
    new Element("p", {}, "You have no drafts yet")]);
  const result = inspector.inspectStoriesPage(doc, { href: "https://medium.com/me/stories" });
  assert.equal(result.status, "NO_DRAFT_FOUND");
  assert.equal(result.postTabContentRegionIdentified, true);
  assert.equal(result.postTabContentContainer.tag, "section");
  assert.equal(result.postTabContentContainer.childCount, content.children.length);
  assert.equal(result.draftListEmptyState, true);
  assert.match(result.emptyStateEvidence.join(" "), /Explicit text/);
  assert.equal(result.loadingEvidence, false);
  assert.equal(result.errorEvidence, false);
});

test("a dedicated promo block in Drafts gives structural empty evidence", () => {
  const { doc } = storyFixture(storyTabs(), promoNodes());
  const result = inspector.inspectStoriesPage(doc, { href: "https://medium.com/me/stories" });
  assert.equal(result.status, "NO_DRAFT_FOUND");
  assert.equal(result.promoBlock.found, true);
  assert.equal(result.promoBlock.dedicated, true);
  assert.equal(result.promoBlock.heading, "Write, grow, and reach readers on Medium");
  assert.deepEqual(result.promoBlock.nearbyBodyText, ["Share your ideas with readers."]);
  assert.equal(result.promoBlock.controls[0].text, "Write a story");
  assert.equal(result.promoBlock.childCount, 3);
  assert.equal(result.promoBlock.storyEntrySiblings, 0);
  assert.equal(result.promoBlock.loadingSiblings, 0);
  assert.equal(result.draftEntriesVisible, false);
  assert.equal(result.listConclusivelyLoaded, true);
});

test("promo outside the identified Drafts branch never counts as empty", () => {
  const { doc } = storyFixture(storyTabs(), [new Element("div")], promoNodes());
  const result = inspector.inspectStoriesPage(doc, { href: "https://medium.com/me/stories" });
  assert.equal(result.postTabContentRegionIdentified, true);
  assert.equal(result.promoBlock.found, false);
  assert.equal(result.draftListEmptyState, false);
  assert.equal(result.status, "AMBIGUOUS");
});

test("a lone promotional heading does not prove Drafts is empty", () => {
  const { doc } = storyFixture(storyTabs(), [
    new Element("h2", {}, "Write, grow, and reach readers on Medium")]);
  const result = inspector.inspectStoriesPage(doc, { href: "https://medium.com/me/stories" });
  assert.equal(result.promoBlock.found, true);
  assert.equal(result.promoBlock.dedicated, false);
  assert.equal(result.status, "AMBIGUOUS");
});

test("spinner, aria-busy, and error each override an otherwise empty Drafts block", () => {
  for (const extra of [
    new Element("div", { class: "loading-spinner" }),
    new Element("div", { "aria-busy": "true" }),
    new Element("p", { role: "alert" }, "Could not load drafts")
  ]) {
    const { doc } = storyFixture(storyTabs(), [
      new Element("p", {}, "No drafts"), extra]);
    const result = inspector.inspectStoriesPage(doc,
      { href: "https://medium.com/me/stories" });
    assert.equal(result.status, "AMBIGUOUS");
    assert.equal(result.listConclusivelyLoaded, false);
    assert.equal(result.draftListEmptyState, false);
    assert.equal(result.loadingEvidence || result.errorEvidence, true);
  }
});

test("a non-article story row and exact title are inventoried only inside Drafts", () => {
  const candidate = storyRow("Draft");
  const navigation = new Element("a", { href: "/p/123456abcdef/edit" },
    "Unrelated navigation story");
  const { doc } = storyFixture(storyTabs(), [candidate.row, candidate.title,
    candidate.link, candidate.stamp], [navigation]);
  const result = inspector.inspectStoriesPage(doc, { href: "https://medium.com/me/stories" });
  assert.equal(result.status, "VERIFIED_DRAFT_CANDIDATE");
  assert.equal(result.storyCardStructures.length, 1);
  assert.equal(result.storyCardStructures[0].tag, "li");
  assert.equal(result.exactTitleMatches, "1");
  assert.equal(result.inventory.some(item => item.visibleText ===
    "Unrelated navigation story"), false);
});

test("inspection of NO_DRAFT_FOUND leaves one Import attempt and retry disabled", () => {
  const { doc } = storyFixture(storyTabs(), [new Element("p", {}, "No drafts")]);
  const known = importSession.knownAttempt();
  const result = inspector.inspectReconciliation(doc,
    { href: "https://medium.com/me/stories" });
  assert.equal(result.status, "NO_DRAFT_FOUND");
  assert.equal(result.retryAllowed, false);
  assert.equal(known.clickAttempts, 1);
  assert.equal(known.lifecycleState, "IMPORT_RESULT_AMBIGUOUS");
  const source = fs.readFileSync(path.join(__dirname, "../inspector.js"), "utf8");
  assert.doesNotMatch(source.slice(source.indexOf("function inspectStoriesPage"),
    source.indexOf("async function waitForStoriesHydration")),
  /addEventListener\(|\.click\(/);
});

test("conflicting tab state remains unknown even when one tab has a selected class", () => {
  const tabs = sameHrefStoryTabs();
  tabs[0].attributes.class = "selected";
  tabs[1].attributes["aria-selected"] = "true";
  const result = inspector.inspectStoriesPage(fixtureDocument(tabs, [], []),
    { href: "https://medium.com/me/stories/drafts" });
  assert.equal(result.tabState, "STORIES_TAB_STATE_UNKNOWN");
  assert.equal(result.selectedTab, "Unknown");
  assert.equal(result.status, "AMBIGUOUS");
});

test("Stories exact-title draft is only a candidate, with no editor verification", () => {
  const candidate = storyRow("Draft");
  const doc = storyFixture(storyTabs(), [candidate.row, candidate.title, candidate.link,
    candidate.stamp]).doc;
  const result = inspector.inspectReconciliation(doc, { href: "https://medium.com/me/stories" });
  assert.equal(result.status, "VERIFIED_DRAFT_CANDIDATE");
  assert.equal(result.stories.pageRecognized, true);
  assert.equal(result.stories.draftControl.found, true);
  assert.equal(result.stories.draftControl.selected, true);
  assert.equal(result.stories.draftListConfirmed, true);
  assert.equal(result.stories.matchingCandidates, 1);
  assert.equal(result.stories.exactTitleCandidate, true);
  assert.equal(result.stories.candidateState, "Draft");
  assert.equal(result.stories.candidateHrefShape, "/p/[12-hex-id]/edit");
  assert.equal(result.stories.candidates[0].visibleTimestamp, "Oct 7, 2026");
  assert.equal(inspector.inspectImportResult(doc, { href: "https://medium.com/me/stories" })
    .verified, false);
});

test("published and duplicate same-title Stories results remain ambiguous", () => {
  const published = storyRow("Published", "/@writer/story-title-abcdef123456");
  const publishedDoc = fixtureDocument([...storyTabs("Published"), published.link],
    [published.title], [], [published.row]);
  const publishedResult = inspector.inspectReconciliation(publishedDoc,
    { href: "https://medium.com/me/stories/public" });
  assert.equal(publishedResult.stories.selectedTab, "Published");
  assert.equal(publishedResult.stories.matchingCandidates, 0);
  assert.equal(publishedResult.status, "AMBIGUOUS");
  const a = storyRow("Draft");
  const b = storyRow("Draft", "/p/123456abcdef/edit");
  const duplicates = storyFixture(storyTabs(), [a.row, a.title, a.link, a.stamp,
    b.row, b.title, b.link, b.stamp]).doc;
  const result = inspector.inspectReconciliation(duplicates,
    { href: "https://medium.com/me/stories/drafts" });
  assert.equal(result.stories.matchingCandidates, 2);
  assert.equal(result.status, "AMBIGUOUS");
});

test("no matching Stories candidate stays conservative unless empty draft state is explicit", () => {
  const blank = fixtureDocument([], [], []);
  assert.equal(inspector.inspectReconciliation(blank,
    { href: "https://medium.com/me/stories" }).status, "AMBIGUOUS");
  assert.equal(inspector.inspectReconciliation(blank,
    { href: "https://medium.com/me/stories/drafts" }).status, "AMBIGUOUS");
  const empty = storyFixture(storyTabs(), [new Element("p", {}, "No drafts yet")]).doc;
  assert.equal(inspector.inspectReconciliation(empty,
    { href: "https://medium.com/me/stories/drafts" }).status, "NO_DRAFT_FOUND");
});

test("Stories waits for delayed hydration and never touches a tab or draft", async () => {
  const tabs = storyTabs();
  const candidate = storyRow("Draft");
  const doc = storyFixture(tabs, [candidate.row, candidate.title, candidate.link,
    candidate.stamp]).doc;
  const originalQuery = doc.querySelectorAll.bind(doc);
  let hydrated = false;
  doc.querySelectorAll = selector => hydrated ? originalQuery(selector) : [];
  setTimeout(() => { hydrated = true; }, 20);
  const result = await inspector.waitForStoriesHydration(doc,
    { href: "https://medium.com/me/stories" }, { timeoutMs: 100, intervalMs: 5 });
  assert.equal(result.pageRecognized, true);
  assert.equal(result.uiHydrated, true);
  assert.ok(result.hydrationWaitElapsedMs >= 15);
  assert.equal(result.status, "VERIFIED_DRAFT_CANDIDATE");
});

test("a div story card with an editor link is found without opening it", () => {
  const row = new Element("div", {}, "Saturation of Artificial Intelligence Draft");
  const title = new Element("h3", {}, "Saturation of Artificial Intelligence");
  const link = new Element("a", { href: "/p/abcdef123456/edit" }, "Edit");
  title.parentElement = row;
  link.parentElement = row;
  row.querySelectorAll = selector => selector === "h1, h2, h3, h4" ? [title] :
    ["a[href]", "a"].includes(selector) ? [link] : [];
  const doc = storyFixture(storyTabs(), [row, title, link]).doc;
  const result = inspector.inspectReconciliation(doc, { href: "https://medium.com/me/stories" });
  assert.equal(result.status, "VERIFIED_DRAFT_CANDIDATE");
  assert.equal(result.stories.visibleDraftCandidateCount, 1);
});

test("unhydrated Stories remains ambiguous after a bounded wait", async () => {
  const doc = fixtureDocument([], [], []);
  const result = await inspector.waitForStoriesHydration(doc,
    { href: "https://medium.com/me/stories" }, { timeoutMs: 25, intervalMs: 5 });
  assert.equal(result.uiHydrated, false);
  assert.equal(result.status, "AMBIGUOUS");
  assert.ok(result.hydrationWaitElapsedMs >= 25);
});

test("published tab selected leaves the Drafts list uninspected", () => {
  const candidate = storyRow("Draft");
  const doc = fixtureDocument([...storyTabs("Published"), candidate.link],
    [candidate.title], [], [candidate.row]);
  const result = inspector.inspectReconciliation(doc, { href: "https://medium.com/me/stories" });
  assert.equal(result.stories.draftControl.found, true);
  assert.equal(result.stories.draftControl.selected, false);
  assert.equal(result.stories.draftListConfirmed, false);
  assert.equal(result.stories.matchingCandidates, 0);
  assert.equal(result.status, "AMBIGUOUS");
});

test("a published same-title row in selected Drafts is not a verified draft", () => {
  const published = storyRow("Published", "/@writer/story-title-abcdef123456");
  const doc = storyFixture(storyTabs(), [published.row, published.title, published.link,
    published.stamp]).doc;
  const result = inspector.inspectReconciliation(doc, { href: "https://medium.com/me/stories" });
  assert.equal(result.stories.candidateState, "Published");
  assert.equal(result.status, "AMBIGUOUS");
});

test("zero matches needs a conclusive Drafts list and incomplete lists stay ambiguous", () => {
  const other = storyRow("Draft");
  other.title.innerText = other.title.textContent = "Another story";
  const end = new Element("p", {}, "End of drafts");
  const doc = storyFixture(storyTabs(), [other.row, other.title, other.link,
    other.stamp, end]).doc;
  const complete = inspector.inspectReconciliation(doc, { href: "https://medium.com/me/stories" });
  assert.equal(complete.stories.draftListConfirmed, true);
  assert.equal(complete.stories.visibleDraftCandidateCount, 1);
  assert.equal(complete.status, "AMBIGUOUS");
  const loading = new Element("button", {}, "Load more");
  const incomplete = storyFixture(storyTabs(), [other.row, other.title, other.link,
    other.stamp, end, loading]).doc;
  const uncertain = inspector.inspectReconciliation(incomplete,
    { href: "https://medium.com/me/stories" });
  assert.equal(uncertain.stories.listIncomplete, true);
  assert.equal(uncertain.status, "AMBIGUOUS");
});

test("Stories content inspection is read-only and leaves the ambiguous attempt unchanged", async () => {
  const candidate = storyRow("Draft");
  const doc = storyFixture(storyTabs(), [candidate.row, candidate.title,
    candidate.link, candidate.stamp], [new Element("button", {}, "Publish")]).doc;
  const location = { href: "https://medium.com/me/stories" };
  const known = importSession.knownAttempt();
  let listener;
  let writes = 0;
  const chrome = { runtime: {
    onMessage: { addListener(fn) { listener = fn; } },
    sendMessage: async message => {
      if (message.type === "SET_MEDIUM_IMPORT_SESSION") writes += 1;
      return { ok: true, record: known };
    }
  } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../content.js"), "utf8"), {
    chrome, MediumImportSession: importSession, MediumSecondAttempt: secondAttempt,
    MediumInspector: { ...inspector,
      waitForStoriesHydration: async () => inspector.inspectStoriesPage(doc, location) },
    MediumPageControls: { mount() { throw Error("Stories mounted a control"); } },
    document: doc, location
  });
  await Promise.resolve();
  await Promise.resolve();
  const response = await new Promise(resolve => {
    assert.equal(listener({ type: "INSPECT_MEDIUM_PAGE" }, null, resolve), true);
  });
  assert.equal(response.reconciliation.status, "VERIFIED_DRAFT_CANDIDATE");
  assert.equal(response.importState, "IMPORT_RESULT_AMBIGUOUS");
  assert.equal(response.persistedClickAttempts, 1);
  assert.equal(response.lastReconciliation.status, "NO_DRAFT_FOUND");
  assert.equal(response.lastReconciliation.knownImportAttempts, 1);
  assert.equal(response.lastReconciliation.retryAllowed, false);
  assert.equal(response.reconciliation.retryAllowed, false);
  assert.equal(writes, 0);
  assert.equal(known.lifecycleState, "IMPORT_RESULT_AMBIGUOUS");
  assert.equal(known.clickAttempts, 1);
});

test("exact-title draft editor verifies without clicking Publish", () => {
  const doc = fixtureDocument([new Element("button", {}, "Publish")],
    [new Element("h1", {}, "Saturation of Artificial Intelligence")],
    [new Element("div", { contenteditable: "true" }, "Story body")]);
  const result = inspector.inspectReconciliation(doc,
    { href: "https://medium.com/p/abcdef123456/edit" });
  assert.equal(result.status, "VERIFIED_DRAFT_CREATED");
  assert.equal(result.editor.exactTitle, true);
  assert.equal(result.editor.editableBody, true);
  assert.equal(result.editor.publishControl, true);
  const wrongTitle = fixtureDocument([new Element("button", {}, "Publish")],
    [new Element("h1", {}, "Another story")],
    [new Element("div", { contenteditable: "true" }, "Story body")]);
  assert.equal(inspector.inspectReconciliation(wrongTitle,
    { href: "https://medium.com/p/abcdef123456/edit" }).status, "AMBIGUOUS");
});

test("native bubbling input verifies Fill with telemetry and no duplicate", () => {
  const page = mediumParagraphFixture(MEDIUM_DEFAULT_URL);
  const fill = inspector.fillVerifiedSourceUrl(page.doc, page.location, page.session);
  assert.equal(fill.fillDomResult, "EXACT_SOURCE_URL_PRESENT");
  assert.equal(fill.fingerprintMatch, true);
  assert.equal(fill.browserInputObserved, true);
  assert.equal(fill.syntheticInputFallbackUsed, false);
  assert.equal(fill.applicationInputSignalDelivered, true);
  assert.equal(fill.fillSynchronizationStatus, "VERIFIED");
  assert.equal(fill.fillEventCounts.input, 1);
  assert.equal(fill.fillEventCounts.trustedInput, 1);
  assert.equal(fill.fillEventCounts.beforeinput, 0);
  assert.equal(fill.fillEventCounts.change, 0);
  assert.equal(fill.fillEvents[0].dataLength, VERIFIED_URL.length);
  assert.equal(page.editor.eventListeners.get("input").size, 0);
  assert.equal(JSON.stringify(fill.fillEvents).includes(VERIFIED_URL), false);
});

test("missing native input uses one bubbling InputEvent after exact replacement", () => {
  const page = mediumParagraphFixture(MEDIUM_DEFAULT_URL);
  page.doc.execCommand = (_command, _ui, value) => {
    page.calls.insertText += 1;
    page.editor.textContent = value;
    page.editor.innerText = value;
    return true;
  };
  const fill = inspector.fillVerifiedSourceUrl(page.doc, page.location, page.session);
  assert.equal(fill.fillSynchronizationStatus, "VERIFIED");
  assert.equal(fill.browserInputObserved, false);
  assert.equal(fill.syntheticInputFallbackUsed, true);
  assert.equal(fill.fillEventCounts.input, 1);
  assert.equal(fill.fillEventCounts.syntheticInput, 1);
  assert.deepEqual(fill.fillEvents.map(event => [event.type, event.bubbles,
    event.composed, event.inputType, event.targetIsSelectedEditor]),
    [["input", true, true, "insertText", true]]);
});

test("popup inspection reports the last Fill without changing Import history", () => {
  const page = mediumParagraphFixture(MEDIUM_DEFAULT_URL);
  const fill = inspector.fillVerifiedSourceUrl(page.doc, page.location, page.session);
  const report = inspector.inspectDocument(page.doc, page.location);
  const rendered = vm.runInNewContext(
    fs.readFileSync(path.join(__dirname, "../popup.js"), "utf8") +
      "\nformatReport(report, status);",
    { report, status: { lastFillResult: fill,
      lastReconciliation: importSession.KNOWN_RECONCILIATION,
      persistedLifecycle: "IMPORT_RESULT_AMBIGUOUS", persistedClickAttempts: 1,
      importDiagnostics: { control: "DISABLED", clickAttempts: 1 } },
    document: { getElementById: () => ({ addEventListener() {} }) } });
  for (const expected of ["Source editor state: EXACT_SOURCE_URL_PRESENT",
    "Fill DOM result: EXACT_SOURCE_URL_PRESENT", "Fill fingerprint match: Yes",
    "Browser-generated input observed: Yes", "Synthetic input fallback used: No",
    "Application input signal delivered: Yes", "Fill synchronization status: VERIFIED",
    "Last reconciliation: NO_DRAFT_FOUND", "Persisted click attempts: 1",
    "Retry allowed: No", "Extension Import control: DISABLED"]) {
    assert.ok(rendered.includes(expected), expected);
  }
});

test("manual-attempt control is disabled and never writes click intent", () => {
  const page = mediumParagraphFixture(VERIFIED_URL);
  const writes = [];
  page.session.persist = () => { writes.push(page.session.importState); return true; };
  let clicks = 0;
  page.buttons[0].click = () => { clicks += 1; };
  const ui = onPageFixture(page);
  assert.equal(ui.importButton.click(), false);
  assert.deepEqual(writes, []);
  assert.equal(clicks, 0);
  assert.equal(ui.host.getImportDiagnostics().automaticNativeImportActivation, "DISABLED");
});

test("background storage bridge keeps known attempt terminal and rejects a reset", async () => {
  const storage = memoryStorage();
  let listener;
  const context = { MediumImportSession: importSession,
    MediumSecondAttempt: secondAttempt, URL,
    importScripts(...files) { assert.deepEqual(files,
      ["import-session.js", "second-attempt.js"]); },
    chrome: { storage: { session: storage }, runtime: { onMessage: {
      addListener(fn) { listener = fn; }
    } } } };
  vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../background.js"), "utf8"), context);
  const send = message => new Promise(resolve => {
    const handled = listener(message, { url: "https://medium.com/p/import" }, resolve);
    assert.equal(handled, true);
  });
  const initial = await send({ type: "GET_MEDIUM_IMPORT_SESSION" });
  assert.equal(initial.ok, true);
  assert.equal(initial.record.lifecycleState, "IMPORT_RESULT_AMBIGUOUS");
  assert.equal(initial.record.clickAttempts, 1);
  const reset = { ...initial.record, lifecycleState: "IMPORT_NOT_STARTED", clickAttempts: 0 };
  assert.equal((await send({ type: "SET_MEDIUM_IMPORT_SESSION", record: reset })).ok, false);
  const intent = { ...initial.record, lifecycleState: "IMPORT_CLICK_INTENT" };
  assert.equal((await send({ type: "SET_MEDIUM_IMPORT_SESSION", record: intent })).ok, false);
  assert.equal((await send({ type: "GET_MEDIUM_IMPORT_SESSION" })).record.lifecycleState,
    "IMPORT_RESULT_AMBIGUOUS");
});

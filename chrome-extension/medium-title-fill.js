(function (root) {
  "use strict";

  const TITLE = "h3[data-testid='editorTitleParagraph']";
  const BODY = "p[data-testid='editorParagraphText']";
  const ROOT = "div[role='textbox'][contenteditable='true'].postArticle-content.js-postField.js-notesSource.editable";
  const TYPES = ["focus", "beforeinput", "input", "change"];
  const normalize = value => String(value || "").replace(/[\u200b-\u200d\u2060\ufeff]/g, "")
    .replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
  function fingerprint(value) {
    let number = 2166136261;
    for (const character of normalize(value)) {
      number = Math.imul(number ^ character.codePointAt(0), 16777619);
    }
    return `fnv1a32:${(number >>> 0).toString(16).padStart(8, "0")}`;
  }
  const safeState = value => ["SCAFFOLDING_ONLY", "EMPTY"].includes(value);
  const prompt = node => Array.from(node?.querySelectorAll?.(".defaultValue.defaultValue--root") || []);
  const bodyPrompt = node => prompt(node).some(item => /^Tell your story[.!… ]*$/i.test(normalize(item.textContent)));
  const titlePrompt = node => prompt(node).some(item => normalize(item.textContent) === "Title");
  const inArticle = node => Boolean(node?.closest?.("article.postArticle.postArticle--full"));

  function resolve(doc, inspector, location, alreadyFilled) {
    const inspection = inspector.inspect(doc, location);
    const fail = reason => ({ ready: false, reason, inspection });
    if (alreadyFilled) return fail("TITLE_ALREADY_FILLED_BY_WORKFLOW");
    if (new URL(location.href).origin !== "https://medium.com" ||
        new URL(location.href).pathname !== "/new-story") return fail("NOT_NEW_STORY");
    if (inspection.classification !== "BLANK_DRAFT_EDITOR_VERIFIED" ||
        inspection.editorArchitecture !== "COMPOSITE_SINGLE_CONTENTEDITABLE" ||
        inspection.userAuthoredContentDetected !== "No" ||
        inspection.editorAppearsBlank !== "Yes") return fail("BLANK_EDITOR_NOT_VERIFIED");
    if (!inspection.compositeEditorRootDetected ||
        inspection.titleSubregionCandidateCount !== 1 ||
        inspection.bodySubregionCandidateCount !== 1 ||
        !safeState(inspection.titleSubregionState) ||
        !safeState(inspection.bodySubregionState) ||
        inspection.publishControlCount !== 1 || !inspection.publishControl)
      return fail("EDITOR_PRECONDITIONS_CHANGED");
    const roots = Array.from(doc.querySelectorAll(ROOT)).filter(inArticle);
    if (roots.length !== 1 || inspection.primaryCompositeEditor?.id !== roots[0].id)
      return fail("COMPOSITE_EDITOR_AMBIGUOUS");
    const editor = roots[0];
    const titles = Array.from(editor.querySelectorAll(TITLE));
    const bodies = Array.from(editor.querySelectorAll(BODY));
    if (titles.length !== 1 || bodies.length !== 1 ||
        !editor.contains(titles[0]) || !editor.contains(bodies[0]) ||
        titles[0] === bodies[0] || !inArticle(titles[0]) || !inArticle(bodies[0]))
      return fail("TITLE_OR_BODY_SUBREGION_AMBIGUOUS");
    const title = titles[0], body = bodies[0];
    if (inspection.selectedTitleSubregion?.data?.["data-testid"] !== "editorTitleParagraph" ||
        inspection.selectedBodySubregion?.data?.["data-testid"] !== "editorParagraphText")
      return fail("SUBREGION_IDENTITY_MISMATCH");
    if (inspection.titleSubregionState === "SCAFFOLDING_ONLY" && !titlePrompt(title))
      return fail("TITLE_PROMPT_UNRECOGNIZED");
    if (inspection.bodySubregionState === "SCAFFOLDING_ONLY" && !bodyPrompt(body))
      return fail("BODY_PROMPT_UNRECOGNIZED");
    return { ready: true, reason: "None", inspection, editor, title, body };
  }

  function eligibility(doc, inspector, location, story, alreadyFilled = false) {
    if (!story?.title || story.fingerprint !== fingerprint(story.title))
      return { ready: false, reason: "STORY_TITLE_SOURCE_INVALID",
        workflow: "MEDIUM_TITLE_NOT_FILLED" };
    try {
      const current = resolve(doc, inspector, location, alreadyFilled);
      return { ready: current.ready, reason: current.reason,
        workflow: current.ready ? "MEDIUM_TITLE_FILL_READY" :
          "MEDIUM_TITLE_NOT_FILLED" };
    } catch {
      return { ready: false, reason: "EDITOR_INSPECTION_FAILED",
        workflow: "MEDIUM_TITLE_NOT_FILLED" };
    }
  }

  function afterEdit(doc, editor, before, story) {
    // The h3 may be rebuilt. Its semantic role and editor ancestry decide identity.
    const roots = Array.from(doc.querySelectorAll(ROOT)).filter(inArticle);
    const currentRoot = roots.length === 1 ? roots[0] : editor;
    const titled = Array.from(currentRoot.querySelectorAll(TITLE));
    const semantic = Array.from(currentRoot.querySelectorAll("h3.graf--title"));
    const block = titled.length === 1 ? titled[0] : semantic.length === 1 ? semantic[0] : null;
    const bodies = Array.from(currentRoot.querySelectorAll(BODY));
    const body = bodies.length === 1 ? bodies[0] : null;
    const value = normalize(block?.textContent);
    const bodyUnchanged = Boolean(body &&
      (before.bodyState === "EMPTY" ? !normalize(body.textContent) : bodyPrompt(body)) &&
      body.innerHTML === before.bodyHTML && normalize(body.textContent) === before.bodyText);
    return { titleDataTestIdRemains: titled.length === 1,
      titleNodeRebuilt: Boolean(block && block !== before.title),
      titleBlockCount: semantic.length,
      titleExactMatch: value === story.title,
      titleNormalizedLength: value.length, titleFingerprint: fingerprint(value),
      titleFingerprintMatch: fingerprint(value) === story.fingerprint,
      defaultTitlePromptRemoved: Boolean(block && prompt(block).length === 0),
      bodySubregionStillPresent: Boolean(body),
      bodyState: bodyUnchanged ? before.bodyState : "UNKNOWN",
      bodyDefaultPromptStillPresent: Boolean(body && bodyPrompt(body)),
      bodyUserAuthoredContent: !bodyUnchanged,
      bodyUnchangedAfterTitleFill: bodyUnchanged,
      editorStructurallyValid: Boolean(roots.length === 1 && block &&
        semantic.length === 1 && body && currentRoot.isConnected !== false &&
        inArticle(currentRoot) && currentRoot.contains(block) && currentRoot.contains(body)) };
  }

  async function fill(doc, inspector, location, story, alreadyFilled = false,
    wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds))) {
    const result = { workflow: "MEDIUM_TITLE_NOT_FILLED", titleFillAttempted: false,
      titleFillFailure: "None", titleBrowserGeneratedInputObserved: false,
      titleSyntheticInputFallbackUsed: false, titleApplicationInputSignalDelivered: false,
      trustedTitleInputEvents: [], titleInputType: "Not observed", events: [],
      autosaveIndicatorObserved: "Not observed", draftIdentityAfterTitleFill: "Not observed",
      navigationOccurred: false, expectedTitle: story?.title || "Not available",
      expectedFingerprint: story?.fingerprint || "Not available" };
    if (!story?.title || story.fingerprint !== fingerprint(story.title)) {
      result.titleFillFailure = "STORY_TITLE_SOURCE_INVALID";
      return result;
    }
    let current;
    try { current = resolve(doc, inspector, location, alreadyFilled); }
    catch { result.titleFillFailure = "EDITOR_INSPECTION_FAILED"; return result; }
    if (!current.ready) { result.titleFillFailure = current.reason; return result; }
    result.workflow = "MEDIUM_TITLE_FILL_READY";
    const { editor, title, body } = current;
    const before = { title, bodyHTML: body.innerHTML, bodyText: normalize(body.textContent),
      bodyState: current.inspection.bodySubregionState };
    const startURL = location.href;
    let editing = false, fallback = false, usable = 0;
    const observe = event => {
      const targetTitle = event.target === title ||
        event.target?.matches?.(TITLE) && editor.contains(event.target);
      const rootTitleInput = event.target === editor && event.isTrusted &&
        event.inputType === "insertText" && event.data === story.title;
      const relevant = targetTitle || rootTitleInput;
      if (result.events.length < 40) result.events.push({ type: event.type,
        target: event.target === editor ? "editor" : targetTitle ? "title" : "other",
        trusted: Boolean(event.isTrusted), bubbles: Boolean(event.bubbles),
        inputType: event.inputType || null });
      if (event.type === "input" && (editing || fallback) && relevant && event.bubbles &&
          event.inputType === "insertText" && event.data === story.title) {
        usable += 1;
        result.titleInputType = event.inputType;
        if (event.isTrusted) {
          result.titleBrowserGeneratedInputObserved = true;
          result.trustedTitleInputEvents.push({ inputType: event.inputType,
            target: event.target === editor ? "editor" : "title" });
        }
      }
    };
    for (const type of TYPES) editor.addEventListener(type, observe, true);
    try {
      title.focus?.();
      editor.focus();
      // Focus may cause Medium to replace the editor. Re-resolve before selection.
      const focused = resolve(doc, inspector, location, alreadyFilled);
      if (!focused.ready || focused.editor !== editor || focused.title !== title ||
          focused.body !== body || body.innerHTML !== before.bodyHTML) {
        result.titleFillFailure = "EDITOR_CHANGED_ON_FOCUS";
        return result;
      }
      const selection = doc.getSelection();
      const range = doc.createRange();
      range.selectNodeContents(title);
      selection.removeAllRanges();
      selection.addRange(range);
      if (!title.contains(selection.anchorNode) ||
          !title.contains(selection.focusNode)) {
        result.titleFillFailure = "TITLE_SELECTION_FAILED";
        return result;
      }
      const selected = resolve(doc, inspector, location, alreadyFilled);
      if (!selected.ready || selected.editor !== editor || selected.title !== title ||
          selected.body !== body || body.innerHTML !== before.bodyHTML) {
        result.titleFillFailure = "EDITOR_CHANGED_BEFORE_EDIT";
        return result;
      }
      result.titleFillAttempted = true;
      result.workflow = "MEDIUM_TITLE_FILLED";
      editing = true;
      let commandSucceeded;
      try { commandSucceeded = Boolean(doc.execCommand("insertText", false, story.title)); }
      finally { editing = false; selection.removeAllRanges(); }
      Object.assign(result, afterEdit(doc, editor, before, story));
      if (result.titleExactMatch && result.titleFingerprintMatch &&
          result.bodyUnchangedAfterTitleFill && usable === 0) {
        const block = editor.querySelector(TITLE) || editor.querySelector("h3.graf--title");
        if (block && doc.defaultView?.InputEvent) {
          result.titleSyntheticInputFallbackUsed = true;
          fallback = true;
          try { block.dispatchEvent(new doc.defaultView.InputEvent("input", {
            bubbles: true, composed: true, inputType: "insertText", data: story.title })); }
          finally { fallback = false; }
        }
      }
      result.titleApplicationInputSignalDelivered = usable > 0;
      Object.assign(result, afterEdit(doc, editor, before, story));
      if (!commandSucceeded) result.titleFillFailure = "INSERT_TEXT_COMMAND_FAILED";
      else if (!result.titleExactMatch || !result.titleFingerprintMatch ||
          !result.defaultTitlePromptRemoved) result.titleFillFailure = "TITLE_POSTCHECK_FAILED";
      else if (!result.bodyUnchangedAfterTitleFill) result.titleFillFailure = "BODY_CHANGED";
      else if (!result.editorStructurallyValid) result.titleFillFailure = "EDITOR_STRUCTURE_CHANGED";
      else if (!result.titleApplicationInputSignalDelivered)
        result.titleFillFailure = "APPLICATION_INPUT_MISSING";
      result.workflow = result.titleFillFailure === "None" ?
        "MEDIUM_TITLE_VERIFIED" : "MEDIUM_TITLE_FILL_AMBIGUOUS";
      for (let index = 0; index < 8; index++) {
        const indicator = inspector.inspect(doc, location);
        if (indicator.draftSaveIndicator !== "Not observed")
          result.autosaveIndicatorObserved = indicator.draftSaveIndicator;
        result.draftIdentityAfterTitleFill = indicator.draftIdentity;
        result.draftIdentityEstablished = indicator.draftIdentityEstablished;
        result.draftRouteEstablishedByMedium = indicator.draftRouteEstablishedByMedium;
        result.navigationOccurred = location.href !== startURL;
        if (result.navigationOccurred) break;
        await wait(250);
      }
      const persisted = inspector.inspect(doc, location, story);
      if (persisted.draftContinuationSafe && result.titleFillFailure === "None") {
        result.titleExactMatch = persisted.titleExactMatch;
        result.titleFingerprint = persisted.titleFingerprint;
        result.titleFingerprintMatch = persisted.titleFingerprintMatch;
        result.bodyState = persisted.bodySubregionState;
        result.bodySubregionStillPresent = true;
        result.bodyDefaultPromptStillPresent = persisted.bodySubregionState ===
          "SCAFFOLDING_ONLY";
        result.editorStructurallyValid = true;
        result.workflow = "MEDIUM_TITLE_VERIFIED";
        return result;
      }
      Object.assign(result, afterEdit(doc, editor, before, story));
      if (!result.titleExactMatch || !result.titleFingerprintMatch ||
          !result.bodyUnchangedAfterTitleFill || !result.editorStructurallyValid) {
        result.titleFillFailure = result.bodyUnchangedAfterTitleFill ?
          "EDITOR_CHANGED_AFTER_EDIT" : "BODY_CHANGED";
        result.workflow = "MEDIUM_TITLE_FILL_AMBIGUOUS";
      }
      return result;
    } catch {
      result.titleFillFailure = "TITLE_EDIT_EXCEPTION";
      result.workflow = result.titleFillAttempted ? "MEDIUM_TITLE_FILL_AMBIGUOUS" :
        "MEDIUM_TITLE_NOT_FILLED";
      if (result.titleFillAttempted) {
        try { Object.assign(result, afterEdit(doc, editor, before, story)); }
        catch { /* The editor may have disappeared. Keep the ambiguous state. */ }
      }
      return result;
    } finally {
      editing = false;
      for (const type of TYPES) editor.removeEventListener(type, observe, true);
      doc.getSelection()?.removeAllRanges();
    }
  }

  const api = { eligibility, fill, fingerprint, normalize };
  root.MediumTitleFill = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(globalThis);

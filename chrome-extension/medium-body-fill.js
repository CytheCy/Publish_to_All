(function (root) {
  "use strict";

  const ROOT = "div[role='textbox'][contenteditable='true'].postArticle-content.js-postField.js-notesSource.editable";
  const TITLE = "h3[data-testid='editorTitleParagraph']";
  const EXPECTED_DRAFT = "fd6c1824c4de";
  const normalize = value => String(value || "").replace(/[\u200b-\u200d\u2060\ufeff]/g, "")
    .replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
  const fingerprint = value => {
    let number = 2166136261;
    for (const character of value) number = Math.imul(number ^ character.codePointAt(0), 16777619);
    return `fnv1a32:${(number >>> 0).toString(16).padStart(8, "0")}`;
  };
  const bodyText = node => root.MediumEditorInspector.extractBodyText(node);
  const blocks = editor => Array.from(editor.querySelector("section")?.children || [])
    .filter(node => /^(H[1-6]|P|DIV)$/.test(node.tagName) &&
      (["P", "DIV"].includes(node.tagName) || Boolean(bodyText(node))));
  const prompt = body => Array.from(body?.querySelectorAll?.(
    ".defaultValue.defaultValue--root") || [])
    .some(node => /^Tell your story[.!… ]*$/i.test(bodyText(node)));
  const titleEvidence = title => ({ normalizedTitle: normalize(title?.textContent),
    titleFingerprint: fingerprint(normalize(title?.textContent)),
    domIdentity: { tag: title?.tagName, dataTestId: title?.getAttribute("data-testid"),
      className: title?.className, parentTag: title?.parentElement?.tagName,
      parentChildIndex: Array.from(title?.parentElement?.children || []).indexOf(title) } });
  function sourceValid(story, body) {
    if (!story?.title || !body?.blocks?.length || body.source !== "In/01_01_2040.md" ||
        story.sourceHash !== body.sourceHash || body.unsupported?.length ||
        body.blocks.some(block => !block.text || !["heading", "paragraph"].includes(block.kind) ||
          block.kind === "heading" && block.level !== 1)) return false;
    const expected = body.blocks.map(block => normalize(block.text)).join("\n\n");
    return fingerprint(expected) === body.fingerprint && expected.length ===
      body.normalizedCharacterCount && body.paragraphCount ===
      body.blocks.filter(block => block.kind === "paragraph").length;
  }
  function resolve(doc, inspector, location, story, body, used, publishObserved = false) {
    let url;
    try { url = new URL(location.href); } catch { /* Route guard fails below. */ }
    const inspection = inspector.inspect(doc, location, story);
    if (!used && body?.blocks?.length &&
        inspector.reconcileBody?.(doc, inspection, body)?.currentBodyExactCanonicalMatch)
      return { ready: false, reason: "BODY_ALREADY_VERIFIED", inspection,
        diagnostics: { finalResult: "NOT_READY" } };
    const resolved = inspector.resolveBodySubregion(inspection);
    const editor = resolved?.root, title = resolved?.title, bodyNode = resolved?.body;
    const routeGuard = Boolean(url?.origin === "https://medium.com" &&
      url.pathname === `/p/${EXPECTED_DRAFT}/edit` &&
      inspection.editorRouteKind === "DRAFT_EDIT");
    const draftIdentityGuard = inspection.draftId === EXPECTED_DRAFT;
    const compositeRootGuard = Boolean(editor && title &&
      inspection.editorArchitecture === "COMPOSITE_SINGLE_CONTENTEDITABLE" &&
      inspection.compositeEditorRootDetected &&
      inspection.primaryCompositeEditor?.id === editor.id &&
      editor.closest("article.postArticle.postArticle--full") &&
      editor.contains(title) && (!bodyNode || editor.contains(bodyNode)) &&
      inspection.publishControlCount === 1);
    const titleGuard = Boolean(title && inspection.titleSubregionCandidateCount === 1 &&
      title.matches(TITLE) && inspection.titleExactMatch &&
      inspection.titleFingerprintMatch &&
      inspection.titleFingerprint === story?.fingerprint &&
      normalize(title.textContent) === story?.title &&
      fingerprint(normalize(title.textContent)) === story?.fingerprint);
    const bodyCandidateCount = resolved?.candidateCount ?? 0;
    const bodyUnique = bodyCandidateCount === 1 && Boolean(bodyNode) &&
      resolved.selected === inspection.selectedBodySubregion &&
      resolved.selected?.tag === "p" &&
      resolved.selected?.data?.["data-testid"] === "editorParagraphText" &&
      bodyNode.tagName === "P" &&
      bodyNode.getAttribute("data-testid") === "editorParagraphText" &&
      title?.compareDocumentPosition(bodyNode) & 4;
    const bodyState = inspection.bodySubregionState;
    const bodyStateGuard = ["SCAFFOLDING_ONLY", "EMPTY"].includes(bodyState) &&
      inspection.bodyStillBlank;
    const bodyPromptGuard = bodyState !== "SCAFFOLDING_ONLY" || prompt(bodyNode);
    const authoredBodyGuard = Boolean(bodyNode && bodyStateGuard && bodyPromptGuard &&
      (bodyState === "EMPTY" ? !bodyText(bodyNode) :
        bodyText(bodyNode) === normalize(Array.from(
          bodyNode.querySelectorAll(".defaultValue.defaultValue--root"))
          .map(bodyText).join(" "))));
    const continuationSafeGuard = Boolean(inspection.draftContinuationSafe &&
      inspection.workflowState === "MEDIUM_TITLE_VERIFIED_BODY_BLANK");
    const unsupportedMarkdownGuard = !body?.unsupported?.length;
    const diagnostics = { routeGuard, draftIdentityGuard, compositeRootGuard,
      titleGuard, bodySubregionCandidateCount: bodyCandidateCount,
      selectedBodySubregion: resolved?.selected || null, bodyState,
      authoredBodyCheck: authoredBodyGuard, continuationSafeGuard,
      unsupportedMarkdownGuard };
    const checks = [
      [used, "BODY_ALREADY_FILLED_BY_WORKFLOW"],
      [publishObserved, "PUBLISH_ACTION_OBSERVED"],
      [!unsupportedMarkdownGuard, "UNSUPPORTED_MARKDOWN_STRUCTURE"],
      [!sourceValid(story, body), "BODY_SOURCE_INVALID"],
      [!routeGuard, "DRAFT_ROUTE_MISMATCH"],
      [!draftIdentityGuard, "DRAFT_ID_MISMATCH"],
      [Boolean(doc.querySelector("[role='dialog'], dialog[open]")),
        "DIALOG_OR_SETTINGS_OPEN"],
      [!compositeRootGuard, "COMPOSITE_EDITOR_AMBIGUOUS"],
      [!titleGuard, "TITLE_CHANGED"],
      [bodyCandidateCount > 1 || bodyCandidateCount === 1 && !bodyUnique,
        "BODY_SUBREGION_AMBIGUOUS"],
      [bodyCandidateCount === 0, "BODY_SUBREGION_MISSING"],
      [!bodyStateGuard, "BODY_ALREADY_AUTHORED"],
      [!bodyPromptGuard, "BODY_PROMPT_UNRECOGNIZED"],
      [!authoredBodyGuard, "BODY_ALREADY_AUTHORED"],
      [!continuationSafeGuard, "EDITOR_PRECONDITIONS_CHANGED"]
    ];
    const reason = checks.find(([blocked]) => blocked)?.[1] || "None";
    return { ready: reason === "None", reason, editor, title, body: bodyNode,
      inspection, diagnostics };
  }
  function eligibility(doc, inspector, location, story, body, used = false,
    publishObserved = false) {
    try {
      const current = resolve(doc, inspector, location, story, body, used,
        publishObserved);
      return { ready: current.ready, reason: current.reason,
        diagnostics: { ...current.diagnostics, finalResult: current.ready ?
          "READY" : "NOT_READY" },
        workflow: current.ready ? "MEDIUM_BODY_FILL_READY" : "MEDIUM_BODY_NOT_FILLED" };
    } catch { return { ready: false, reason: "EDITOR_INSPECTION_FAILED",
      workflow: "MEDIUM_BODY_NOT_FILLED" }; }
  }
  function live(doc) {
    const roots = Array.from(doc.querySelectorAll(ROOT)).filter(node =>
      Boolean(node.closest("article.postArticle.postArticle--full")));
    const editor = roots.length === 1 ? roots[0] : null;
    const title = editor?.querySelectorAll(TITLE);
    return { editor, title: title?.length === 1 ? title[0] : null,
      blocks: editor ? blocks(editor) : [] };
  }
  function verify(doc, location, story, source, snapshot) {
    const now = live(doc), actual = now.blocks.slice(1);
    const texts = actual.map(bodyText);
    const exact = actual.length === source.blocks.length && actual.every((node, index) =>
      node.tagName.toLowerCase() === (source.blocks[index].kind === "heading" ? "h1" : "p") &&
      texts[index] === normalize(source.blocks[index].text));
    const titleAfter = titleEvidence(now.title);
    const titleUnchanged = Boolean(now.title &&
      titleAfter.normalizedTitle === snapshot.normalizedTitle &&
      titleAfter.titleFingerprint === snapshot.titleFingerprint &&
      JSON.stringify(titleAfter.domIdentity) === JSON.stringify(snapshot.domIdentity));
    return { bodyDefaultPromptRemoved: actual.every(node =>
      !node.querySelector(".defaultValue.defaultValue--root")),
      bodyUserAuthoredContent: actual.some(node => Boolean(bodyText(node))),
      bodyExactNormalizedMatch: exact,
      bodyFingerprint: fingerprint(texts.join("\n\n")),
      bodyFingerprintMatch: exact && fingerprint(texts.join("\n\n")) === source.fingerprint,
      bodyParagraphCount: actual.filter(node => node.tagName === "P").length,
      bodyParagraphCountMatch: exact && actual.filter(node => node.tagName === "P").length ===
        source.paragraphCount,
      titleUnchangedAfterBodyFill: titleUnchanged,
      titleExactMatch: titleAfter.normalizedTitle === story.title,
      titleFingerprintMatch: titleAfter.titleFingerprint === story.fingerprint,
      titleNodeRebuilt: Boolean(now.title && now.title !== snapshot.node),
      draftIdentityUnchanged: new URL(location.href).pathname === `/p/${EXPECTED_DRAFT}/edit`,
      navigationUnexpected: location.href !== snapshot.url,
      publishActionOccurred: false };
  }
  async function fill(doc, inspector, location, story, source, used = false,
    wait = ms => new Promise(resolve => setTimeout(resolve, ms)),
    publishObserved = () => false) {
    const result = { workflow: "MEDIUM_BODY_NOT_FILLED", bodyFillAttempted: false,
      bodyFillFailure: "None", bodyBrowserGeneratedInputObserved: false,
      bodySyntheticInputFallbackUsed: false, bodyApplicationInputDelivered: false,
      trustedBodyInputEventCount: 0, bodyInputTypes: [],
      autosaveIndicatorObserved: "Not observed", publishActionOccurred: false };
    let first;
    try { first = resolve(doc, inspector, location, story, source, used,
      publishObserved()); }
    catch { result.bodyFillFailure = "EDITOR_INSPECTION_FAILED"; return result; }
    if (!first.ready) { result.bodyFillFailure = first.reason; return result; }
    result.workflow = "MEDIUM_BODY_FILL_READY";
    const snapshot = { ...titleEvidence(first.title), node: first.title, url: location.href };
    result.beforeTitleSnapshot = { normalizedTitle: snapshot.normalizedTitle,
      titleFingerprint: snapshot.titleFingerprint, domIdentity: snapshot.domIdentity };
    let editor = first.editor;
    const observed = [];
    const onInput = event => {
      if (event.type !== "input" || !event.bubbles || !editor.contains(event.target) ||
          event.target?.closest?.(TITLE)) return;
      observed.push({ trusted: Boolean(event.isTrusted), inputType: event.inputType || "unknown" });
      if (event.isTrusted) result.trustedBodyInputEventCount++;
      if (!result.bodyInputTypes.includes(event.inputType || "unknown"))
        result.bodyInputTypes.push(event.inputType || "unknown");
    };
    editor.addEventListener("input", onInput, true);
    const followEditor = next => {
      if (next && next !== editor) {
        editor.removeEventListener("input", onInput, true);
        editor = next;
        editor.addEventListener("input", onInput, true);
      }
    };
    try {
      first.body.focus?.();
      editor.focus();
      const focused = resolve(doc, inspector, location, story, source, used,
        publishObserved());
      if (!focused.ready || focused.editor !== editor || focused.title !== first.title ||
          focused.body !== first.body) {
        result.bodyFillFailure = "EDITOR_CHANGED_ON_FOCUS"; return result;
      }
      const selection = doc.getSelection(), range = doc.createRange();
      const initialPrompt = first.body.querySelector(".defaultValue.defaultValue--root");
      if (initialPrompt) range.selectNode(initialPrompt);
      else range.selectNodeContents(first.body);
      selection.removeAllRanges(); selection.addRange(range);
      if (!first.body.contains(selection.anchorNode) ||
          !first.body.contains(selection.focusNode)) {
        result.bodyFillFailure = "BODY_SELECTION_FAILED"; return result;
      }
      const selected = resolve(doc, inspector, location, story, source, used,
        publishObserved());
      if (!selected.ready || selected.body !== first.body) {
        result.bodyFillFailure = "EDITOR_CHANGED_BEFORE_EDIT"; return result;
      }
      result.bodyFillAttempted = true;
      result.workflow = "MEDIUM_BODY_FILLED";
      let textInputDelivered = 0;
      for (let index = 0; index < source.blocks.length; index++) {
        if (location.href !== snapshot.url || publishObserved())
          throw Error("DRAFT_OR_PUBLISH_STATE_CHANGED");
        const part = source.blocks[index];
        if (index) {
          const previousState = live(doc);
          followEditor(previousState.editor);
          const previous = previousState.blocks[index];
          if (!previousState.title ||
              normalize(previousState.title.textContent) !== snapshot.normalizedTitle ||
              fingerprint(normalize(previousState.title.textContent)) !==
                snapshot.titleFingerprint ||
              !previous || bodyText(previous) !== source.blocks[index - 1].text)
            throw Error("PREVIOUS_BLOCK_CHANGED");
          const end = doc.createRange(); end.selectNodeContents(previous); end.collapse(false);
          selection.removeAllRanges(); selection.addRange(end);
          if (!doc.execCommand("insertParagraph", false)) throw Error("INSERT_PARAGRAPH_FAILED");
        }
        let now = live(doc);
        followEditor(now.editor);
        if (!now.editor || !now.title || now.blocks.length !== index + 2 ||
            normalize(now.title.textContent) !== snapshot.normalizedTitle)
          throw Error("EDITOR_STRUCTURE_CHANGED");
        let current = now.blocks[index + 1];
        if (index && bodyText(current)) throw Error("NEW_BLOCK_NOT_EMPTY");
        const at = doc.createRange(); at.selectNodeContents(current);
        const clonedPrompt = current.querySelector(".defaultValue.defaultValue--root");
        if (clonedPrompt) at.selectNode(clonedPrompt);
        selection.removeAllRanges(); selection.addRange(at);
        if (clonedPrompt) {
          if (!doc.execCommand("delete", false)) throw Error("PROMPT_DELETE_FAILED");
          now = live(doc); current = now.blocks[index + 1];
          followEditor(now.editor);
          if (!current || current.querySelector(".defaultValue.defaultValue--root") ||
              !now.title || normalize(now.title.textContent) !== snapshot.normalizedTitle)
            throw Error("PROMPT_NOT_REMOVED");
        }
        const beforeInput = observed.length;
        if (!doc.execCommand("insertText", false, part.text)) throw Error("INSERT_TEXT_FAILED");
        now = live(doc); current = now.blocks[index + 1];
        followEditor(now.editor);
        if (!current || bodyText(current) !== part.text ||
            !now.title || normalize(now.title.textContent) !== snapshot.normalizedTitle)
          throw Error("BLOCK_POSTCHECK_FAILED");
        const content = doc.createRange(); content.selectNodeContents(current);
        selection.removeAllRanges(); selection.addRange(content);
        if (!doc.execCommand("formatBlock", false,
            part.kind === "heading" ? "h1" : "p")) throw Error("FORMAT_BLOCK_FAILED");
        now = live(doc); current = now.blocks[index + 1];
        followEditor(now.editor);
        if (!current || current.tagName.toLowerCase() !==
            (part.kind === "heading" ? "h1" : "p") || !now.title ||
            normalize(now.title.textContent) !== snapshot.normalizedTitle)
          throw Error("BLOCK_FORMAT_MISMATCH");
        if (!observed.slice(beforeInput).some(event => event.inputType === "insertText") &&
            doc.defaultView?.InputEvent) {
          result.bodySyntheticInputFallbackUsed = true;
          current.dispatchEvent(new doc.defaultView.InputEvent("input", { bubbles: true,
            composed: true, inputType: "insertText", data: part.text }));
        }
        if (observed.slice(beforeInput).some(event => event.inputType === "insertText"))
          textInputDelivered++;
      }
      result.bodyBrowserGeneratedInputObserved = result.trustedBodyInputEventCount > 0;
      result.bodyApplicationInputDelivered = textInputDelivered === source.blocks.length;
      for (let index = 0; index < 8; index++) {
        const observation = inspector.inspect(doc, location, story);
        if (observation.draftSaveIndicator !== "Not observed")
          result.autosaveIndicatorObserved = observation.draftSaveIndicator;
        if (location.href !== snapshot.url) break;
        await wait(250);
      }
      Object.assign(result, verify(doc, location, story, source, snapshot));
      result.publishActionOccurred = publishObserved();
      if (!result.bodyDefaultPromptRemoved || !result.bodyUserAuthoredContent ||
          !result.bodyExactNormalizedMatch || !result.bodyFingerprintMatch ||
          !result.bodyParagraphCountMatch || !result.titleUnchangedAfterBodyFill ||
          !result.titleExactMatch || !result.titleFingerprintMatch ||
          !result.draftIdentityUnchanged || result.navigationUnexpected ||
          result.publishActionOccurred || !result.bodyApplicationInputDelivered)
        result.bodyFillFailure = "BODY_POSTCHECK_FAILED";
      result.workflow = result.bodyFillFailure === "None" ?
        "MEDIUM_BODY_VERIFIED" : "MEDIUM_BODY_FILL_AMBIGUOUS";
      return result;
    } catch (error) {
      result.bodyFillFailure = error.message || "BODY_EDIT_EXCEPTION";
      result.workflow = result.bodyFillAttempted ? "MEDIUM_BODY_FILL_AMBIGUOUS" :
        "MEDIUM_BODY_NOT_FILLED";
      if (result.bodyFillAttempted) {
        try { Object.assign(result, verify(doc, location, story, source, snapshot)); }
        catch { /* Preserve ambiguous result. */ }
      }
      result.publishActionOccurred = publishObserved();
      return result;
    } finally {
      editor.removeEventListener("input", onInput, true);
      doc.getSelection()?.removeAllRanges();
    }
  }
  const api = { eligibility, fill, fingerprint, normalize, sourceValid, verify };
  root.MediumBodyFill = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(globalThis);

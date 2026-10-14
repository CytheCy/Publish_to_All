(function (root) {
  "use strict";

  const DRAFT = "fd6c1824c4de";
  const URL_PATH = `/p/${DRAFT}/edit`;
  const TITLE = "h3[data-testid='editorTitleParagraph']";
  const INITIAL_FINGERPRINT = "fnv1a32:20ed70ba";
  const EXPECTED_TITLE = "Saturation of Artificial Intelligence";
  const TITLE_FINGERPRINT = "fnv1a32:5e81665d";
  const BODY_FINGERPRINT = "fnv1a32:342d11eb";
  const MAX_READS = 8;
  const normalize = root.MediumBodyFill.normalize;
  const fingerprint = root.MediumBodyFill.fingerprint;

  function sourceChecks(story, source) {
    const sourceText = Array.isArray(source?.blocks) ?
      source.blocks.map(item => normalize(item.text)).join("\n\n") : null;
    const sourceLoaded = Boolean(source && story &&
      source.source === "In/01_01_2040.md" &&
      story.sourceHash && story.sourceHash === source.sourceHash);
    const expectedBodyFingerprint = Boolean(source?.fingerprint === BODY_FINGERPRINT &&
      source?.normalizedCharacterCount === 58757 &&
      sourceText?.length === 58757 && fingerprint(sourceText) === BODY_FINGERPRINT);
    const canonicalBlocks = Boolean(source?.blocks?.length === 36 &&
      source?.headingCount === 18 && source?.paragraphCount === 18 &&
      !source?.unsupported?.length &&
      source.blocks[0]?.kind === "heading" && source.blocks[0]?.level === 1 &&
      normalize(source.blocks[0]?.text) === "01" &&
      source.blocks[1]?.kind === "paragraph" &&
      normalize(source.blocks[1]?.text).length === 905 &&
      fingerprint(normalize(source.blocks[1]?.text)) === "fnv1a32:ad559899" &&
      root.MediumBodyFill.sourceValid(story, source));
    return { sourceLoaded, expectedBodyFingerprint, canonicalBlocks };
  }

  function expectedBlock(source, index) {
    const block = source.blocks[index], text = normalize(block.text);
    return { number: index + 1, kind: block.kind, normalizedLength: text.length,
      fingerprint: fingerprint(text) };
  }

  // Audit authored leaves against the inspector's selected title and body blocks.
  // Containers inherit descendant text, so their textContent is never evidence by itself.
  function unexpectedContent(editor, title, body, blocks) {
    const candidates = [];
    const checkpointCandidates = [];
    const MAX_CANDIDATES = 12;
    const path = node => {
      const parts = [];
      for (let current = node.nodeType === 3 ? node.parentElement : node;
        current && parts.length < 12; current = current.parentElement) {
        const name = current.tagName.toLowerCase();
        const id = current.id ? `#${current.id.slice(0, 48)}` : "";
        const testId = current.getAttribute("data-testid");
        const siblings = Array.from(current.parentElement?.children || [])
          .filter(item => item.tagName === current.tagName);
        parts.unshift(`${name}${id}${testId ?
          `[data-testid="${testId.slice(0, 48)}"]` : ""}${siblings.length > 1 ?
          `:nth-of-type(${siblings.indexOf(current) + 1})` : ""}`);
        if (current === editor) break;
      }
      return `${parts.join(" > ")}${node.nodeType === 3 ? " > #text" : ""}`;
    };
    function add(node, reason, value = node.textContent || "", checkpointOnly = false) {
      const text = normalize(value);
      const candidate = {
        tag: node.nodeType === 3 ? "#text" : node.tagName.toLowerCase(),
        path: path(node), normalizedTextLength: text.length,
        normalizedTextFingerprint: fingerprint(text),
        safeShortText: text.slice(0, 48), reason
      };
      if (!checkpointOnly) {
        if (candidates.length < MAX_CANDIDATES) candidates.push(candidate);
        count++;
      }
      if (checkpointCandidates.length < MAX_CANDIDATES)
        checkpointCandidates.push(candidate);
      checkpointCount++;
    }
    let count = 0, checkpointCount = 0;
    const blockSet = new Set(blocks);
    // A second paragraph is authored content even when it is empty or hidden.
    for (const block of blocks) if (block !== body) add(block,
      "ADDITIONAL_INSPECTOR_STORY_BLOCK", block.textContent, true);
    function walk(node, inBody = false) {
      if (node === title) {
        for (const child of title.childNodes) walk(child, true);
        return;
      }
      if (node === body) {
        if (normalize(body.textContent) !== "01") add(body,
          "SELECTED_BODY_TEXT_NOT_CANONICAL_01", body.textContent, true);
        for (const child of body.childNodes) walk(child, true);
        return;
      }
      if (blockSet.has(node)) return;
      if (node.nodeType === 3) {
        if (!inBody && normalize(node.nodeValue)) add(node,
          "TEXT_OUTSIDE_SELECTED_TITLE_AND_BODY", node.nodeValue);
        return;
      }
      if (node.nodeType !== 1) return;
      const tag = node.tagName;
      if (/^(IMG|VIDEO|IFRAME|FIGURE|CANVAS|TABLE)$/.test(tag)) {
        add(node, "MEDIA_OR_EMBED_IN_STORY_EDITOR");
        return;
      }
      if (inBody) {
        if (!["FONT", "SPAN", "BR"].includes(tag) || node.hidden ||
            node.getAttribute("aria-hidden") === "true" ||
            node.getAttribute("contenteditable") === "false" ||
            node.classList.contains("defaultValue")) {
          add(node, "UNEXPECTED_SELECTED_SUBREGION_DESCENDANT");
          return;
        }
      } else if (/^H[1-6]$/.test(tag) || tag === "P") {
        add(node, "ADDITIONAL_AUTHORED_STORY_BLOCK");
        return;
      }
      for (const child of node.childNodes) walk(child, inBody);
    }
    for (const child of editor.childNodes) walk(child);
    return { count, candidates, truncated: count > candidates.length,
      checkpoint: { count: checkpointCount, candidates: checkpointCandidates,
        truncated: checkpointCount > checkpointCandidates.length } };
  }

  function inspect(doc, inspector, location, story, source, publishObserved) {
    let route = false;
    try {
      const url = new URL(location.href);
      route = url.origin === "https://medium.com" && url.pathname === URL_PATH &&
        !url.search && !url.hash;
    } catch { /* Closed below. */ }
    const inspection = inspector.inspect(doc, location, story);
    const resolved = inspector.resolveBodySubregion(inspection);
    const editor = resolved?.root || null;
    const title = resolved?.title || null;
    const titleText = normalize(title?.textContent);
    const titleHash = fingerprint(titleText);
    const blocks = resolved?.blockNodes || [];
    const unexpected = editor && title ? unexpectedContent(editor, title,
      resolved?.body, blocks) : { count: 0, candidates: [], truncated: false,
        checkpoint: { count: 0, candidates: [], truncated: false } };
    const records = (resolved?.blockStructure || []).map((item, index) => {
      const { node } = item, text = item.normalizedText;
      return { number: index + 1, kind: /^H[1-6]$/.test(node.tagName) ?
        "heading" : node.tagName === "P" ? "paragraph" : "unknown",
        tag: item.tag,
        normalizedLength: text.length, fingerprint: item.fingerprint, text,
        node };
    });
    const prefix = records.every((item, index) => {
      if (index >= (source?.blocks?.length || 0)) return false;
      const expected = expectedBlock(source, index);
      return item.text === normalize(source.blocks[index].text) &&
        item.normalizedLength === expected.normalizedLength &&
        item.fingerprint === expected.fingerprint &&
        item.kind === expected.kind && item.tag ===
          (expected.kind === "heading" ? "h1" : "p");
    });
    const editorStructure = Boolean(editor && title &&
      inspection.editorArchitecture === "COMPOSITE_SINGLE_CONTENTEDITABLE" &&
      inspection.compositeEditorRootDetected &&
      inspection.primaryCompositeEditor?.id === editor.id &&
      inspection.titleSubregionCandidateCount === 1 &&
      editor.closest("article.postArticle.postArticle--full") &&
      title.matches(TITLE));
    const publishSafe = Boolean(inspection.publishControlCount === 1 &&
      !publishObserved && !doc.querySelector("[role='dialog'], dialog[open]"));
    const sourceGuards = sourceChecks(story, source);
    const identityChecks = {
      exactRoute: route,
      draftEditKind: inspection.editorRouteKind === "DRAFT_EDIT",
      exactDraftId: inspection.draftId === DRAFT,
      uniqueEditorRoot: Boolean(editor),
      uniqueTitleNode: Boolean(title),
      compositeArchitecture: inspection.editorArchitecture ===
        "COMPOSITE_SINGLE_CONTENTEDITABLE",
      compositeRootDetected: Boolean(inspection.compositeEditorRootDetected),
      compositeEditorIdMatch: Boolean(editor &&
        inspection.primaryCompositeEditor?.id === editor.id),
      uniqueTitleSubregion: inspection.titleSubregionCandidateCount === 1,
      inspectorTitleExactMatch: Boolean(inspection.titleExactMatch),
      inspectorTitleFingerprintMatch: Boolean(inspection.titleFingerprintMatch),
      persistedTitleVerified: Boolean(inspection.titleVerifiedInPersistedDraft),
      expectedTitlePinned: story?.title === EXPECTED_TITLE,
      expectedTitleFingerprintPinned: story?.fingerprint === TITLE_FINGERPRINT,
      liveTitleEqualsExpected: Boolean(story && titleText === story.title),
      liveTitleFingerprintEqualsExpected: Boolean(story &&
        titleHash === story.fingerprint),
      inspectorTitleFingerprintPinned: inspection.titleFingerprint === TITLE_FINGERPRINT,
      uniquePublishControl: inspection.publishControlCount === 1,
      noPublishObserved: !publishObserved,
      noDialogOpen: !doc.querySelector("[role='dialog'], dialog[open]"),
      noUnexpectedEditorContent: Boolean(editor && title && !unexpected.count)
    };
    const safe = Object.values(identityChecks).every(Boolean) &&
      Object.values(sourceGuards).every(Boolean);
    return { safe, route, inspection, editor, editorStructure, publishSafe,
      title, titleText, titleHash, story, source, sourceGuards, identityChecks,
      blocks, records, prefix, unexpected,
      resolved, reconciliation: inspector.reconcileBody(doc, inspection, source,
        { publishObserved }), publishObserved };
  }

  function checkpoint(current) {
    const r = current.reconciliation, block = current.records[0];
    const story = current.story, source = current.source;
    const uniqueBody = current.resolved?.candidateCount === 1 &&
      current.resolved?.body === current.blocks[0] &&
      current.inspection.bodySubregionCandidateCount === 1 &&
      current.resolved?.body?.getAttribute("data-testid") === "editorParagraphText";
    const partialState = Boolean(r?.partialBodyDetected &&
      r.partialBodyRecoveryClassification === "EXACT_CANONICAL_PREFIX" &&
      r.currentBodyCanonicalPrefixMatch && r.currentBodyCanonicalTextPrefix &&
      r.canonicalPrefixMatchedThroughBlock === 1 &&
      r.canonicalPrefixMatchedCharacterCount === 2 &&
      r.expectedNextCanonicalBlock?.number === 2 &&
      r.expectedNextCanonicalBlock?.kind === "paragraph" &&
      r.expectedNextCanonicalBlock?.normalizedLength === 905 &&
      r.expectedNextCanonicalBlock?.fingerprint === "fnv1a32:ad559899");
    const bodyAvailable = Boolean(uniqueBody);
    const guards = {
      route: Boolean(current.route && current.inspection.editorRouteKind === "DRAFT_EDIT"),
      draftId: current.inspection.draftId === DRAFT,
      titleText: Boolean(story?.title === EXPECTED_TITLE && current.title &&
        current.titleText === story.title && current.inspection.titleExactMatch),
      titleFingerprint: Boolean(story?.fingerprint === TITLE_FINGERPRINT &&
        current.titleHash === story.fingerprint &&
        current.inspection.titleFingerprint === story.fingerprint &&
        current.inspection.titleFingerprintMatch),
      titleVerification: Boolean(current.inspection.titleVerifiedInPersistedDraft),
      ...current.sourceGuards,
      partialState: bodyAvailable ? partialState : "NOT_EVALUATED",
      partialFingerprint: bodyAvailable ? Boolean(
        r?.currentBodyFingerprint === INITIAL_FINGERPRINT &&
        current.resolved.fingerprint === INITIAL_FINGERPRINT &&
        block?.fingerprint === INITIAL_FINGERPRINT) : "NOT_EVALUATED",
      blockCount: bodyAvailable ? Boolean(r?.currentBodyBlockCount === 1 &&
        current.resolved.blockStructure.length === 1) : "NOT_EVALUATED",
      formattingRepair: bodyAvailable ? Boolean(r?.currentBodySafeText === "01" &&
        current.resolved.normalizedText === "01" && block?.text === "01" &&
        r?.currentBodyNormalizedLength === 2 && block?.normalizedLength === 2 &&
        block?.tag === "p" && !r?.currentBodyCanonicalFormattingComplete &&
        r?.formattingDiscrepancy?.split("/font")[0] ===
          "Expected heading block; observed paragraph") : "NOT_EVALUATED",
      publishState: current.publishSafe,
      editorStructure: current.editorStructure,
      bodySubregion: Boolean(uniqueBody),
      unexpectedContent: Boolean(!current.unexpected.checkpoint.count &&
        !r?.currentBodyOtherContentPresent && !r?.currentBodyMediaPresent &&
        current.records.slice(1).every(item => !item.text))
    };
    const reasons = {
      route: "DRAFT_ROUTE_MISMATCH", draftId: "DRAFT_ID_MISMATCH",
      titleText: "TITLE_TEXT_MISMATCH", titleFingerprint: "TITLE_FINGERPRINT_MISMATCH",
      titleVerification: "PERSISTED_TITLE_NOT_VERIFIED",
      sourceLoaded: "CANONICAL_SOURCE_NOT_LOADED",
      expectedBodyFingerprint: "EXPECTED_BODY_FINGERPRINT_MISMATCH",
      canonicalBlocks: "CANONICAL_BLOCKS_INVALID",
      publishState: "PUBLISH_STATE_UNSAFE",
      editorStructure: "COMPOSITE_EDITOR_AMBIGUOUS",
      bodySubregion: "BODY_SUBREGION_AMBIGUOUS",
      partialState: "BODY_NOT_EXACT_CANONICAL_PREFIX",
      partialFingerprint: "BODY_FINGERPRINT_MISMATCH",
      blockCount: "BODY_BLOCK_COUNT_MISMATCH",
      formattingRepair: "BLOCK_ONE_FORMAT_MISMATCH",
      unexpectedContent: "UNEXPECTED_BODY_CONTENT"
    };
    const failedGuard = Object.keys(reasons).find(name => guards[name] === false) || null;
    const reason = failedGuard === "formattingRepair" && block?.tag === "h1" &&
      r?.currentBodyCanonicalFormattingComplete && block?.text === "01" ?
      "BLOCK_ONE_ALREADY_FORMATTED" : failedGuard ? reasons[failedGuard] : "None";
    return { reason, failedGuard,
      guards, diagnostics: {
        expectedTitleAvailable: Boolean(story?.title), expectedTitle: story?.title || null,
        expectedTitleFingerprint: story?.fingerprint || null,
        liveTitle: current.titleText || null, liveTitleFingerprint: current.titleHash,
        canonicalBodySourceAvailable: current.sourceGuards.sourceLoaded,
        canonicalBodyBlocksAvailable: Boolean(source?.blocks?.length),
        expectedFullBodyFingerprint: source?.fingerprint || null,
        expectedDraftId: DRAFT, currentDraftId: current.inspection.draftId || null,
        partialReconciliationResult: r?.partialBodyRecoveryClassification || null,
        recoveryBodyResolver: "MediumEditorInspector.resolveBodySubregion(inspection)",
        recoveryBodyCandidateCount: current.resolved?.candidateCount ?? 0,
        recoveryBodySelectedNode: current.resolved?.selected || null,
        recoveryBodySharesInspectorNode: Boolean(current.resolved?.body &&
          current.records[0]?.node === current.resolved.body),
        unexpectedContentCandidateCount: current.unexpected.checkpoint.count,
        unexpectedContentCandidates: current.unexpected.checkpoint.candidates,
        unexpectedContentCandidatesTruncated: current.unexpected.checkpoint.truncated,
        unexpectedContentClassificationBug: !guards.unexpectedContent &&
          current.unexpected.checkpoint.count === 0,
        editorIdentityChecks: current.identityChecks } };
  }

  function eligibility(doc, inspector, location, story, source,
    { used = false, busy = false, verified = false, publishObserved = false } = {}) {
    if (verified) return { ready: false, reason: "BODY_ALREADY_VERIFIED",
      failedGuard: "workflowState",
      workflow: "MEDIUM_BODY_RECOVERY_VERIFIED" };
    if (busy) return { ready: false, reason: "RECOVERY_RUNNING",
      failedGuard: "workflowState",
      workflow: "MEDIUM_BODY_RECOVERY_RUNNING" };
    if (used) return { ready: false, reason: "RECOVERY_ALREADY_ATTEMPTED",
      failedGuard: "workflowState",
      workflow: "MEDIUM_BODY_RECOVERY_AMBIGUOUS" };
    try {
      const check = checkpoint(inspect(doc, inspector, location, story, source,
        publishObserved));
      return { ready: check.reason === "None", reason: check.reason,
        failedGuard: check.failedGuard, guards: check.guards,
        diagnostics: check.diagnostics,
        workflow: check.reason === "None" ? "MEDIUM_BODY_RECOVERY_READY" :
          "MEDIUM_BODY_RECOVERY_NOT_READY",
        recoveryStartBlock: check.reason === "None" ? 1 : null,
        recoveryNextCanonicalBlock: check.reason === "None" ? 2 : null,
        blockOneRequiresFormattingRepair: check.reason === "None" };
    } catch { return { ready: false, reason: "RECOVERY_INSPECTION_FAILED",
      failedGuard: "inspection",
      workflow: "MEDIUM_BODY_RECOVERY_NOT_READY" }; }
  }

  function choose(doc, node) {
    const selection = doc.getSelection(), range = doc.createRange();
    range.selectNodeContents(node);
    selection.removeAllRanges(); selection.addRange(range);
    return node.contains(selection.anchorNode) && node.contains(selection.focusNode);
  }

  async function stabilize(doc, inspector, location, story, source, publishObserved,
    accept, wait, result) {
    let previous = null;
    result.stableDOMObserved = false;
    for (let attempt = 1; attempt <= MAX_READS; attempt++) {
      result.postEditStabilizationAttempts++;
      let current = null;
      try { current = inspect(doc, inspector, location, story, source,
        publishObserved()); } catch { /* An incomplete DOM read can stabilize. */ }
      let route = false;
      try {
        const url = new URL(location.href);
        route = url.origin === "https://medium.com" && url.pathname === URL_PATH &&
          !url.search && !url.hash;
      } catch { /* Closed below. */ }
      if (!route || publishObserved() ||
          doc.querySelector("[role='dialog'], dialog[open]") ||
          current?.title && (current.titleText !== story.title ||
            current.titleHash !== TITLE_FINGERPRINT))
        throw Error("DRAFT_TITLE_OR_PUBLISH_STATE_CHANGED");
      const match = Boolean(current?.safe && accept(current));
      const signature = match ? current.records.map(item =>
        `${item.tag}:${item.normalizedLength}:${item.fingerprint}`).join("|") : null;
      if (match && signature === previous) {
        result.stableDOMObserved = true;
        return current;
      }
      previous = signature;
      if (attempt < MAX_READS) await wait(80);
    }
    throw Error("POST_EDIT_STABILIZATION_TIMEOUT");
  }

  function exactPrefix(current, source, count) {
    return current.unexpected.count === 0 && current.prefix &&
      current.records.length === count && current.records.every((item, index) => {
        const expected = expectedBlock(source, index);
        return item.normalizedLength === expected.normalizedLength &&
          item.fingerprint === expected.fingerprint && item.kind === expected.kind;
      });
  }

  function reportBlock(result, source, index) {
    result.lastVerifiedCanonicalBlock = index + 1;
    result.nextExpectedCanonicalBlock = index + 2 <= source.blocks.length ? index + 2 : null;
    result.completedBlocks.push(expectedBlock(source, index));
  }

  async function resume(doc, inspector, location, story, source, {
    used = false, publishObserved = () => false,
    wait = ms => new Promise(resolve => setTimeout(resolve, ms)),
    onProgress = async () => {}
  } = {}) {
    const result = { workflow: "MEDIUM_BODY_RECOVERY_NOT_READY",
      bodyRecoveryAttempted: false, bodyRecoveryFailure: "None",
      lastVerifiedCanonicalBlock: 0, nextExpectedCanonicalBlock: 1,
      completedBlocks: [], postEditStabilizationAttempts: 0,
      stableDOMObserved: false, mutationRepeated: false,
      publishActionOccurred: false };
    let current;
    try {
      current = inspect(doc, inspector, location, story, source, publishObserved());
      const reason = used ? "RECOVERY_ALREADY_ATTEMPTED" : checkpoint(current).reason;
      if (reason !== "None") { result.bodyRecoveryFailure = reason; return result; }
      // Focus and selection can cause editor reflow. Recheck the exact checkpoint
      // immediately before the first mutation.
      current.blocks[0].focus?.();
      current.editor.focus();
      current = inspect(doc, inspector, location, story, source, publishObserved());
      if (checkpoint(current).reason !== "None" || !choose(doc, current.blocks[0]))
        throw Error("RECOVERY_CHECKPOINT_CHANGED_ON_FOCUS");
      current = inspect(doc, inspector, location, story, source, publishObserved());
      if (checkpoint(current).reason !== "None")
        throw Error("RECOVERY_CHECKPOINT_CHANGED_BEFORE_EDIT");
      result.workflow = "MEDIUM_BODY_RECOVERY_RUNNING";
      result.bodyRecoveryAttempted = true;
      if (!doc.execCommand("formatBlock", false, "h1"))
        throw Error("BLOCK_ONE_FORMAT_COMMAND_FAILED");
      current = await stabilize(doc, inspector, location, story, source,
        publishObserved, state => exactPrefix(state, source, 1) &&
          state.records[0].tag === "h1", wait, result);
      result.blockOneText = current.records[0].text;
      result.blockOneFingerprint = current.records[0].fingerprint;
      result.blockOneCanonicalText = true;
      result.blockOneCanonicalFormatting = true;
      reportBlock(result, source, 0);
      await onProgress(result);
      for (let index = 1; index < source.blocks.length; index++) {
        current = inspect(doc, inspector, location, story, source, publishObserved());
        if (!current.safe || !exactPrefix(current, source, index))
          throw Error("PREVIOUS_CANONICAL_PREFIX_CHANGED");
        const previous = current.blocks[index - 1];
        const selection = doc.getSelection(), end = doc.createRange();
        end.selectNodeContents(previous); end.collapse(false);
        selection.removeAllRanges(); selection.addRange(end);
        if (!previous.contains(selection.anchorNode) ||
            !doc.execCommand("insertParagraph", false))
          throw Error("INSERT_PARAGRAPH_FAILED");
        current = await stabilize(doc, inspector, location, story, source,
          publishObserved, state => state.unexpected.count === 0 &&
            state.records.length === index + 1 &&
            ["p", "div"].includes(state.records[index].tag) &&
            state.records[index].text === "" &&
            state.records.slice(0, index).every((item, position) =>
              item.tag === (source.blocks[position].kind === "heading" ? "h1" : "p") &&
              item.text === normalize(source.blocks[position].text)), wait, result);
        if (!choose(doc, current.blocks[index])) throw Error("NEW_BLOCK_SELECTION_FAILED");
        const part = source.blocks[index];
        if (!doc.execCommand("insertText", false, part.text))
          throw Error("INSERT_TEXT_FAILED");
        current = await stabilize(doc, inspector, location, story, source,
          publishObserved, state => state.unexpected.count === 0 &&
            state.records.length === index + 1 &&
            ["p", "div"].includes(state.records[index].tag) &&
            state.records[index].text === normalize(part.text) &&
            state.records[index].fingerprint === expectedBlock(source, index).fingerprint &&
            state.records.slice(0, index).every((item, position) =>
              item.tag === (source.blocks[position].kind === "heading" ? "h1" : "p") &&
              item.text === normalize(source.blocks[position].text)), wait, result);
        if (part.kind === "heading" || current.records[index].tag !== "p") {
          if (!choose(doc, current.blocks[index]) ||
              !doc.execCommand("formatBlock", false,
                part.kind === "heading" ? "h1" : "p"))
            throw Error("FORMAT_BLOCK_FAILED");
          current = await stabilize(doc, inspector, location, story, source,
            publishObserved, state => exactPrefix(state, source, index + 1), wait, result);
        } else if (!exactPrefix(current, source, index + 1))
          throw Error("PARAGRAPH_POSTCHECK_FAILED");
        reportBlock(result, source, index);
        await onProgress(result);
      }
      current = inspect(doc, inspector, location, story, source, publishObserved());
      const text = current.records.map(item => item.text).join("\n\n");
      result.bodyExactNormalizedMatch = exactPrefix(current, source, 36) &&
        text === source.blocks.map(block => normalize(block.text)).join("\n\n") &&
        text.length === 58757;
      result.bodyFingerprint = fingerprint(text);
      result.bodyFingerprintMatch = result.bodyFingerprint === BODY_FINGERPRINT;
      result.bodyParagraphCount = current.records.filter(item => item.tag === "p").length;
      result.bodyHeadingCount = current.records.filter(item => item.tag === "h1").length;
      result.bodyParagraphCountMatch = result.bodyParagraphCount === 18;
      result.bodyHeadingCountMatch = result.bodyHeadingCount === 18;
      result.titleUnchanged = current.titleText === story.title &&
        current.titleHash === TITLE_FINGERPRINT;
      result.titleExactMatch = current.inspection.titleExactMatch;
      result.titleFingerprintMatch = current.inspection.titleFingerprintMatch;
      result.draftIdentityUnchanged = current.route &&
        current.inspection.draftId === DRAFT;
      result.publishActionOccurred = publishObserved();
      if (!current.safe || !result.bodyExactNormalizedMatch ||
          !result.bodyFingerprintMatch || !result.bodyParagraphCountMatch ||
          !result.bodyHeadingCountMatch || !result.titleUnchanged ||
          !result.titleExactMatch || !result.titleFingerprintMatch ||
          !result.draftIdentityUnchanged || result.publishActionOccurred)
        throw Error("FULL_BODY_POSTCHECK_FAILED");
      result.workflow = "MEDIUM_BODY_RECOVERY_VERIFIED";
      result.bodyWorkflow = "MEDIUM_BODY_VERIFIED";
      await onProgress(result);
      return result;
    } catch (error) {
      result.bodyRecoveryFailure = error?.message || "RECOVERY_EXCEPTION";
      result.workflow = result.bodyRecoveryAttempted ?
        "MEDIUM_BODY_RECOVERY_AMBIGUOUS" : "MEDIUM_BODY_RECOVERY_NOT_READY";
      result.publishActionOccurred = publishObserved();
      return result;
    } finally { doc.getSelection()?.removeAllRanges(); }
  }

  const api = { eligibility, resume, expectedBlock };
  root.MediumBodyRecovery = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(globalThis);

(function (root) {
  "use strict";

  const EDITABLES = "[contenteditable='true'], [contenteditable='plaintext-only']";
  const CONTROLS = "button, [role='button'], [role='menuitem']";
  const DATA_KEYS = ["data-placeholder", "data-testid", "data-test-id", "data-qa", "data-type"];
  const clean = value => String(value || "").replace(/\s+/g, " ").trim();
  const contentText = value => clean(String(value || "")
    .replace(/[\u200b-\u200d\u2060\ufeff]/g, "").replace(/\u00a0/g, " "));
  // Use the resolved element's full text, including direct text nodes and inline wrappers.
  const extractBodyText = node => contentText(node?.textContent || "");
  const attr = (node, name) => node?.getAttribute?.(name) || "";
  const tag = node => node?.tagName?.toLowerCase?.() || "";
  const classes = node => clean(typeof node?.className === "string" ? node.className : "")
    .split(" ").filter(Boolean).slice(0, 16);
  const fingerprint = value => {
    let hash = 2166136261;
    for (const character of String(value)) {
      hash ^= character.codePointAt(0);
      hash = Math.imul(hash, 16777619);
    }
    return `fnv1a32:${(hash >>> 0).toString(16).padStart(8, "0")}`;
  };
  const descriptor = node => node ? { tag: tag(node), role: attr(node, "role"),
    id: attr(node, "id"), classes: classes(node), dataTestId: attr(node, "data-testid") } : null;
  const chain = node => {
    const result = [];
    for (let current = node, depth = 0; current && depth < 16;
      current = current.parentElement, depth++) result.push(current);
    return result;
  };
  const placeholderMark = node => Boolean(attr(node, "placeholder") ||
    attr(node, "aria-placeholder") || attr(node, "data-placeholder") ||
    /placeholder|prompt|instruction|helper/i.test([attr(node, "data-testid"),
      ...classes(node)].join(" ")));
  const editorLike = node => /editor|story|article|prose|draft|writing/i.test([
    tag(node), attr(node, "role"), attr(node, "data-testid"), ...classes(node)
  ].join(" "));
  const visible = (node, doc) => {
    if (!node || node.hidden || attr(node, "aria-hidden") === "true") return false;
    for (let parent = node; parent; parent = parent.parentElement) {
      if (parent.hidden || attr(parent, "aria-hidden") === "true") return false;
      const style = doc.defaultView?.getComputedStyle?.(parent);
      if (style && (style.display === "none" || style.visibility === "hidden" ||
          style.visibility === "collapse")) return false;
    }
    return !node.getClientRects || node.getClientRects().length > 0;
  };
  const data = node => Object.fromEntries(DATA_KEYS.map(key => [key, attr(node, key)])
    .filter(([, value]) => value).map(([key, value]) => [key, clean(value).slice(0, 80)]));
  function pseudo(node, doc, kind) {
    const value = doc.defaultView?.getComputedStyle?.(node, kind)?.content || "none";
    const generated = !["none", "normal", '""', "''"].includes(value);
    const prompt = /^(?:["'])(?:title|headline|tell your story|write your story|start writing)[.!… ]*(?:["'])$/i;
    return { generated, content: generated ? {
      fingerprint: fingerprint(value), length: value.length,
      safePrompt: prompt.test(value) ? value.slice(1, -1) : null
    } : "none" };
  }
  function textParts(node, doc) {
    const parts = [];
    function walk(current, source, sourceElement, noneditable, hidden, marked, depth) {
      if (depth > 20 || parts.length >= 60) return;
      if (current.nodeType === 3) {
        if (current.nodeValue) parts.push({ raw: current.nodeValue, source,
          sourceElement: descriptor(sourceElement),
          noneditable, hidden, marked });
        return;
      }
      if (current !== node) {
        noneditable ||= attr(current, "contenteditable") === "false";
        hidden ||= !visible(current, doc);
        marked ||= placeholderMark(current);
      }
      for (const child of current.childNodes || []) walk(child,
        current === node ? "direct text" : `${tag(current)} descendant`, current,
        noneditable, hidden, marked, depth + 1);
    }
    if (node.childNodes) walk(node, "direct text", node, false, false, false, 0);
    else if (node.textContent || node.innerText) parts.push({
      raw: node.textContent || node.innerText, source: "element text (source unavailable)",
      sourceElement: descriptor(node),
      noneditable: false, hidden: false, marked: false });
    return parts;
  }
  function textState(node, doc, path) {
    const raw = String(node.textContent || node.innerText || "");
    const value = contentText(raw);
    const parts = textParts(node, doc).filter(part => contentText(part.raw));
    const editable = parts.filter(part => !part.noneditable && !part.hidden && !part.marked);
    const editableText = contentText(editable.map(part => part.raw).join(" "));
    const placeholder = contentText(attr(node, "placeholder") ||
      attr(node, "aria-placeholder") || attr(node, "data-placeholder"));
    const sameAsPlaceholder = Boolean(value && placeholder &&
      value.toLowerCase() === placeholder.toLowerCase());
    const media = node.querySelector?.("img, video, iframe, figure, canvas, table") || null;
    const clearScaffolding = parts.length > 0 && parts.every(part => part.marked);
    const state = !editableText && !media ? value ?
      clearScaffolding ? "SCAFFOLDING_ONLY" : "UNKNOWN" : "EMPTY" :
      clearScaffolding && !media ? "SCAFFOLDING_ONLY" :
        media || path !== "/new-story" ? "USER_CONTENT_PRESENT" : "UNKNOWN";
    return { currentState: state, normalizedTextLength: value.length,
      normalizedInnerTextLength: contentText(node.innerText || "").length,
      normalizedTextContentLength: contentText(node.textContent || "").length,
      textEvidence: { safeUiText: clearScaffolding && value.length <= 80 ? value : null,
        matchesPlaceholderAttribute: sameAsPlaceholder,
        fingerprint: value ? fingerprint(value) : null, characterCount: raw.length,
        whitespaceCount: (raw.match(/\s/g) || []).length,
        childSource: parts.slice(0, 12).map(part => ({ source: part.source,
          sourceElement: part.sourceElement,
          kind: part.hidden ? "hidden" : part.noneditable ? "nested noneditable UI" :
            part.marked ? "placeholder/instruction" : "editable text",
          length: contentText(part.raw).length })),
        origin: state === "EMPTY" ? "empty editor" : clearScaffolding ?
          "placeholder/scaffolding or nested UI" :
          state === "USER_CONTENT_PRESENT" ? "editable content" : "unresolved DOM text" } };
  }
  function candidate(node, doc, index, path) {
    const descendants = Array.from(node.querySelectorAll?.("*") || []);
    const raw = String(node.textContent || "");
    const parentChain = chain(node).slice(1);
    const nearest = parentChain.find(editorLike);
    const childStructure = Array.from(node.children || []).slice(0, 10)
      .map(child => tag(child)).join(", ") || "None";
    return { index: index + 1, tag: tag(node), role: attr(node, "role"),
      contenteditable: attr(node, "contenteditable"), ariaLabel: attr(node, "aria-label"),
      ariaPlaceholder: attr(node, "aria-placeholder"),
      placeholder: attr(node, "placeholder"), dataPlaceholder: attr(node, "data-placeholder"),
      dataTestId: attr(node, "data-testid"), id: attr(node, "id"), classes: classes(node),
      parent: descriptor(node.parentElement), nearestEditorLikeAncestor: descriptor(nearest),
      ancestry: parentChain.map(descriptor), tabindex: attr(node, "tabindex"),
      spellcheck: attr(node, "spellcheck"), autocapitalize: attr(node, "autocapitalize"),
      inputmode: attr(node, "inputmode"), ariaMultiline: attr(node, "aria-multiline"),
      before: pseudo(node, doc, "::before"), after: pseudo(node, doc, "::after"),
      childStructure, childElementCount: node.children?.length || 0,
      descendantContenteditableCount: descendants.filter(child =>
        ["true", "plaintext-only"].includes(attr(child, "contenteditable"))).length,
      brOnly: descendants.some(child => tag(child) === "br") && !contentText(raw) &&
        descendants.every(child => ["br", "p", "div", "section"].includes(tag(child))),
      emptyParagraph: descendants.some(child => tag(child) === "p" &&
        !contentText(child.textContent)),
      emptySection: descendants.some(child => tag(child) === "section" &&
        !contentText(child.textContent)),
      zeroWidthCharacters: /[\u200b-\u200d\u2060\ufeff]/.test(raw),
      nonBreakingSpaces: /\u00a0/.test(raw),
      placeholderLikeDescendants: descendants.filter(placeholderMark).length,
      ariaHiddenDescendants: descendants.filter(child => attr(child, "aria-hidden") === "true").length,
      noneditableDescendants: descendants.filter(child => attr(child, "contenteditable") === "false").length,
      immediateEditableDescendantPseudos: descendants.filter(child =>
        ["true", "plaintext-only"].includes(attr(child, "contenteditable")))
        .slice(0, 5).map(child => ({ ...descriptor(child),
          before: pseudo(child, doc, "::before"), after: pseudo(child, doc, "::after") })),
      ...textState(node, doc, path), visible: visible(node, doc) };
  }
  function classify(summary, index, count) {
    const title = [], body = [];
    const add = (list, reason, points) => list.push({ reason, points });
    const named = [summary.ariaLabel, summary.ariaPlaceholder, summary.placeholder,
      summary.dataPlaceholder, summary.before.content?.safePrompt,
      summary.after.content?.safePrompt].join(" ").toLowerCase();
    const ownHint = [summary.dataTestId, summary.id, ...summary.classes]
      .join(" ").toLowerCase();
    const parent = [summary.parent?.role, summary.parent?.dataTestId,
      ...(summary.parent?.classes || [])].join(" ").toLowerCase();
    if (/\b(title|headline)\b/.test(named)) add(title, "title label or prompt", 3);
    if (/\b(body|story|writing|write|prose|content)\b/.test(named))
      add(body, "body label or prompt", 3);
    if (/\b(title|headline)\b/.test(ownHint)) add(title, "title class/id hint", 1);
    if (/\b(body|story|writing|write|prose|content)\b/.test(ownHint))
      add(body, "body class/id hint", 1);
    if (/^h[1-6]$/.test(summary.tag) || summary.role === "heading")
      add(title, "heading semantics", 2);
    if (/\b(title|headline)\b/.test(parent)) add(title, "title wrapper", 2);
    if (/\b(body|prose|paragraph|article)\b/.test(parent)) add(body, "body wrapper", 2);
    if (summary.ariaMultiline === "false" || summary.contenteditable === "plaintext-only")
      add(title, "single-line editing semantics", 1);
    if (summary.ariaMultiline === "true") add(body, "multiline editing semantics", 2);
    if (summary.childStructure.split(", ").some(value => ["section", "p"].includes(value)))
      add(body, "section or paragraph structure", 2);
    if (index === 0 && count === 2) add(title, "first editable in DOM", 1);
    if (index === 1 && count === 2) add(body, "second editable in DOM", 1);
    const titleScore = title.reduce((sum, item) => sum + item.points, 0);
    const bodyScore = body.reduce((sum, item) => sum + item.points, 0);
    return { title, body, titleScore, bodyScore,
      titleMatch: title.length >= 2 && titleScore >= 3 && titleScore >= bodyScore + 2,
      bodyMatch: body.length >= 2 && bodyScore >= 3 && bodyScore >= titleScore + 2 };
  }
  function control(node, doc) {
    const name = clean(attr(node, "aria-label") || attr(node, "title") ||
      node.innerText || node.textContent);
    return { tag: tag(node), role: attr(node, "role") || (tag(node) === "button" ? "button" : ""),
      name: name.slice(0, 80), visible: visible(node, doc),
      enabled: !node.disabled && attr(node, "aria-disabled") !== "true",
      data: data(node), ariaHaspopup: attr(node, "aria-haspopup") || "Not observed" };
  }
  const STORY_BLOCKS = new Set(["section", "h1", "h2", "h3", "h4", "p", "div", "span"]);
  const DEFAULT_CLASSES = ["defaultValue", "defaultValue--root"];
  const draftIdFromPath = path => /^\/p\/([0-9a-fA-F]{12})\/edit\/?$/.exec(path)?.[1] || null;
  const isDefaultValue = node => tag(node) === "span" &&
    DEFAULT_CLASSES.every(value => classes(node).includes(value));
  const within = (node, ancestor) => chain(node).includes(ancestor);
  const ownText = node => contentText(Array.from(node.childNodes || [])
    .filter(child => child.nodeType === 3).map(child => child.nodeValue).join(" "));
  const structuralData = node => Object.fromEntries([
    ...DATA_KEYS, "data-block-type", "data-node-type", "data-field", "data-editor"
  ].map(key => [key, attr(node, key)]).filter(([, value]) => value)
    .map(([key, value]) => [key, clean(value).slice(0, 80)]));
  function blockSummary(node, order) {
    return { domOrder: order, tag: tag(node), role: attr(node, "role"),
      name: attr(node, "name"), id: attr(node, "id"), classes: classes(node),
      contenteditable: attr(node, "contenteditable"),
      parent: descriptor(node.parentElement), data: structuralData(node),
      normalizedOwnTextLength: ownText(node).length,
      normalizedDescendantTextLength: extractBodyText(node).length,
      childStructure: Array.from(node.children || []).slice(0, 12)
        .map(child => `${tag(child)}${classes(child).length ? `.${classes(child).join(".")}` : ""}`),
      childElementCount: node.children?.length || 0 };
  }
  function compositeTextState(node, defaultNodes, doc) {
    let promptText = 0, otherText = 0, hiddenText = 0;
    const visit = (current, hidden, inPrompt) => {
      if (current.nodeType === 3) {
        const length = contentText(current.nodeValue).length;
        if (hidden) hiddenText += length;
        else if (inPrompt) promptText += length;
        else otherText += length;
        return;
      }
      const nextHidden = hidden || !visible(current, doc);
      const nextPrompt = inPrompt || defaultNodes.includes(current);
      for (const child of current.childNodes || []) visit(child, nextHidden, nextPrompt);
    };
    visit(node, false, false);
    const media = Boolean(node.querySelector?.("img, video, iframe, figure, canvas, table"));
    const state = media || otherText && promptText ? "USER_CONTENT_PRESENT" :
      otherText || hiddenText ? "UNKNOWN" : promptText ? "SCAFFOLDING_ONLY" : "EMPTY";
    return { state, promptTextLength: promptText, otherTextLength: otherText,
      hiddenTextLength: hiddenText, mediaPresent: media };
  }
  function inspectComposite(doc, path, editables, publish, story) {
    const routeKind = path === "/new-story" ? "NEW_STORY" :
      draftIdFromPath(path) ? "DRAFT_EDIT" : "UNKNOWN";
    const eligible = editables.filter(item => {
      const node = item.node;
      const article = chain(node).slice(1).find(parent => tag(parent) === "article" &&
        classes(parent).includes("postArticle"));
      const marks = ["postArticle-content", "js-postField", "js-notesSource", "editable"]
        .filter(value => classes(node).includes(value));
      return routeKind !== "UNKNOWN" && article && attr(node, "contenteditable") === "true" &&
        attr(node, "role") === "textbox" && marks.length >= 2 &&
        Array.from(node.children || []).some(child => tag(child) === "section") &&
        publish.length === 1;
    });
    const root = eligible.length === 1 ? eligible[0] : null;
    const auxiliary = editables.filter(item => item !== root &&
      tag(item.node.parentElement) === "body" && attr(item.node, "tabindex") === "-1" &&
      !item.summary.nearestEditorLikeAncestor && !item.node.children?.length &&
      !contentText(item.node.textContent)).map(item => ({ index: item.summary.index,
        classification: "AUXILIARY_CONTENTEDITABLE",
        reason: "Empty body-level contenteditable with tabindex=-1, no children or editor ancestor",
        candidate: descriptor(item.node) }));
    const empty = { rootDetected: Boolean(root), candidateIndex: root?.summary.index || null,
      root: root ? { ...root.summary, data: structuralData(root.node) } : null,
      rootEvidence: root ? [`${routeKind} route`,
        "article.postArticle ancestor", "contenteditable=true", "role=textbox",
        "multiple postArticle editor classes", "section child", "one visible Publish control"] : [],
      auxiliary, defaultPrompts: [], blocks: [], titleCandidates: [], bodyCandidates: [],
      selectedTitle: null, selectedBody: null, titleState: "UNKNOWN", bodyState: "UNKNOWN",
      userContentDetected: "Unknown", blank: false, nonempty: false, conflict: false };
    if (!root) return empty;
    const descendants = Array.from(root.node.querySelectorAll("*"));
    const visibleNodes = descendants.filter(node => visible(node, doc));
    const defaultNodes = visibleNodes.filter(isDefaultValue);
    const prompts = defaultNodes.map((node, index) => ({ index: index + 1,
      text: contentText(node.textContent).length <= 80 ? contentText(node.textContent) : null,
      length: contentText(node.textContent).length, parent: descriptor(node.parentElement),
      grandparent: node.parentElement?.parentElement ? {
        ...descriptor(node.parentElement.parentElement),
        childStructure: Array.from(node.parentElement.parentElement.children || [])
          .slice(0, 12).map(child => tag(child)) } : null,
      domOrder: descendants.indexOf(node) + 1 }));
    const blocks = visibleNodes.filter(node => STORY_BLOCKS.has(tag(node)))
      .map(node => ({ node, summary: blockSummary(node, descendants.indexOf(node) + 1) }));
    const meaningful = blocks.filter(({ node }) => {
      const kind = tag(node);
      if (!["h1", "h2", "h3", "h4", "p", "div"].includes(kind)) return false;
      if (kind === "div" && chain(node).slice(1).some(parent =>
        ["h1", "h2", "h3", "h4", "p"].includes(tag(parent)) && within(parent, root.node)))
        return false;
      const hint = [attr(node, "name"), attr(node, "data-placeholder"),
        attr(node, "data-testid"),
        ...classes(node)].join(" ");
      return kind !== "div" || /title|headline|body|prose|paragraph/i.test(hint) ||
        defaultNodes.some(prompt => prompt.parentElement === node) ||
        ownText(node).length > 0;
    });
    const scored = meaningful.map(({ node, summary }, index) => {
      const localPrompts = defaultNodes.filter(prompt => within(prompt, node));
      const promptText = localPrompts.map(prompt => contentText(prompt.textContent)).join(" ");
      const hint = [attr(node, "name"), attr(node, "data-placeholder"),
        attr(node, "data-testid"), attr(node, "data-field"), ...classes(node)].join(" ");
      const titleEvidence = [], bodyEvidence = [];
      if (/^h[1-4]$/.test(tag(node)) &&
          !(index > 0 && /editorParagraphText/i.test(hint)))
        titleEvidence.push("heading tag");
      if (tag(node) === "p") bodyEvidence.push("paragraph tag");
      if (/title|headline/i.test(hint)) titleEvidence.push("title-like attribute or class");
      if (/body|prose|editorParagraphText|(?:^|[\s_-])paragraph(?:$|[\s_-])/i.test(hint))
        bodyEvidence.push("body-like attribute or class");
      if (story?.title && contentText(node.textContent) === story.title &&
          tag(node) !== "p") titleEvidence.push("expected exact title text");
      if (/title|headline/i.test(promptText)) titleEvidence.push("Medium default-value title prompt");
      if (/story|write|body/i.test(promptText)) bodyEvidence.push("Medium default-value writing prompt");
      if (index === 0) titleEvidence.push("first story block");
      if (index > 0) bodyEvidence.push("after first story block");
      return { node, summary: { ...summary, defaultPromptIndexes: localPrompts
        .map(prompt => defaultNodes.indexOf(prompt) + 1), titleEvidence, bodyEvidence } };
    });
    const titles = scored.filter(item => item.summary.titleEvidence.length >= 2 &&
      item.summary.titleEvidence.some(value => value !== "first story block") &&
      !item.summary.bodyEvidence.some(value => value !== "after first story block"));
    const bodies = scored.filter(item => item.summary.bodyEvidence.length >= 2 &&
      item.summary.bodyEvidence.some(value => value !== "after first story block") &&
      !item.summary.titleEvidence.some(value => value !== "first story block"));
    const title = titles.length === 1 ? titles[0] : null;
    const body = bodies.length === 1 ? bodies[0] : null;
    let titleState = title ? compositeTextState(title.node, defaultNodes, doc).state : "UNKNOWN";
    const bodyState = body ? compositeTextState(body.node, defaultNodes, doc).state : "UNKNOWN";
    const rootState = compositeTextState(root.node, defaultNodes, doc);
    const conflict = editables.some(item => item !== root &&
      !auxiliary.some(excluded => excluded.index === item.summary.index));
    const unique = title && body && title.node !== body.node &&
      !within(title.node, body.node) && !within(body.node, title.node) &&
      title.summary.domOrder < body.summary.domOrder && !conflict;
    const blank = Boolean(unique && [titleState, bodyState].every(state =>
      ["EMPTY", "SCAFFOLDING_ONLY"].includes(state)) &&
      ["EMPTY", "SCAFFOLDING_ONLY"].includes(rootState.state) &&
      defaultNodes.every(prompt => within(prompt, title.node) || within(prompt, body.node)) &&
      descendants.length <= 200);
    const titleText = title ? contentText(title.node.textContent) : "";
    const titleFingerprint = titleText ? fingerprint(titleText) : null;
    const titleExactMatch = Boolean(story?.title && titleText === story.title);
    const titleFingerprintMatch = Boolean(story?.fingerprint &&
      titleFingerprint === story.fingerprint);
    if (titleExactMatch && titleState === "UNKNOWN" &&
        !defaultNodes.some(prompt => within(prompt, title.node)))
      titleState = "USER_CONTENT_PRESENT";
    let outsideText = false;
    const findOutsideText = node => {
      if (node === title?.node || node === body?.node) return;
      if (node.nodeType === 3) { outsideText ||= Boolean(contentText(node.nodeValue)); return; }
      for (const child of node.childNodes || []) findOutsideText(child);
    };
    findOutsideText(root.node);
    const bodyBlank = Boolean(body && ["EMPTY", "SCAFFOLDING_ONLY"].includes(bodyState) &&
      (bodyState !== "SCAFFOLDING_ONLY" || defaultNodes.some(prompt =>
        within(prompt, body.node) && /^Tell your story[.!… ]*$/i.test(
          contentText(prompt.textContent)))));
    const titleVerified = Boolean(routeKind === "DRAFT_EDIT" && title &&
      titles.length === 1 && !conflict && titleExactMatch && titleFingerprintMatch);
    const continuationSafe = Boolean(titleVerified && unique && bodyBlank && !outsideText &&
      !rootState.mediaPresent && !rootState.hiddenTextLength &&
      defaultNodes.every(prompt => within(prompt, body.node)) &&
      descendants.length <= 200 && publish.length === 1);
    return { ...empty, rootNode: root.node, titleNode: title?.node || null,
      bodyNode: body?.node || null,
      defaultPrompts: prompts, blocks: blocks.slice(0, 200)
      .map(item => item.summary), titleCandidates: titles.map(item => item.summary),
    bodyCandidates: bodies.map(item => item.summary), selectedTitle: title?.summary || null,
    selectedBody: body?.summary || null, titleState, bodyState,
    userContentDetected: rootState.state === "USER_CONTENT_PRESENT" ? "Yes" :
      rootState.state === "UNKNOWN" ? "Unknown" : "No",
    blank, nonempty: Boolean(unique && [titleState, bodyState, rootState.state]
      .includes("USER_CONTENT_PRESENT")), conflict,
    titleText, titleFingerprint, titleExactMatch, titleFingerprintMatch, titleVerified,
    bodyBlank, continuationSafe };
  }
  function inspect(doc, location, story = root.PublishToAllStory) {
    let url;
    try { url = new URL(location.href); } catch { url = null; }
    const medium = url?.origin === "https://medium.com";
    const path = url?.pathname || "";
    const draftId = draftIdFromPath(path);
    const editorUrlShape = draftId ?
      "/p/[story-id]/edit" : /^\/new-story\/?$/i.test(path) ?
        "/new-story" : medium ? "Other Medium path" : "Not Medium";
    const routeRecognized = medium && ["/new-story", "/p/[story-id]/edit"]
      .includes(editorUrlShape);
    const routeKind = !medium ? "UNKNOWN" : draftId ? "DRAFT_EDIT" :
      editorUrlShape === "/new-story" ? "NEW_STORY" : "UNKNOWN";
    const empty = { detected: false, editorRouteRecognized: routeRecognized,
      editorRouteKind: routeKind, draftId, draftEditorRoute: routeKind === "DRAFT_EDIT",
      draftIdentityEstablished: routeKind === "DRAFT_EDIT",
      draftRouteEstablishedByMedium: routeKind === "DRAFT_EDIT",
      titleExactMatch: false, titleFingerprint: null, titleFingerprintMatch: false,
      titleVerifiedInPersistedDraft: false, bodyStillBlank: false,
      draftContinuationSafe: false,
      editorStructuralEvidencePresent: false, classification: "NOT_MEDIUM_EDITOR",
      editorUrlShape, draftIdentity: "Not observed", titleEditorCandidateCount: 0,
      bodyEditorCandidateCount: 0, selectedTitleEditor: null, selectedBodyEditor: null,
      titleCurrentState: "UNKNOWN", bodyCurrentState: "UNKNOWN",
      publishControlCount: 0, publishControl: null, draftSaveIndicator: "Not observed",
      editorAppearsBlank: "Unknown", editorContainer: "Not observed",
      commonEditorRoot: null, editableRelationship: "Not observed",
      domOrder: "Not observed", titleBodyOrder: "Unresolved",
      editorArchitecture: "UNKNOWN", compositeEditorRootDetected: false,
      compositeEditorCandidateIndex: null, compositeEditorEvidence: [],
      primaryCompositeEditor: null, auxiliaryContenteditablesExcluded: [],
      defaultValuePrompts: [], compositeStructuralBlocks: [],
      titleSubregionCandidateCount: 0, bodySubregionCandidateCount: 0,
      selectedTitleSubregion: null, selectedBodySubregion: null,
      titleSubregionState: "UNKNOWN", bodySubregionState: "UNKNOWN",
      userAuthoredContentDetected: "Unknown",
      settingsControls: [], editableCandidates: [], workflowState: "MEDIUM_EDITOR_NOT_INSPECTED" };
    if (!medium || !doc?.querySelectorAll) return empty;
    const nodes = Array.from(doc.querySelectorAll(EDITABLES))
      .filter(node => visible(node, doc));
    const editables = nodes.map((node, index) => ({ node,
      summary: candidate(node, doc, index, path.replace(/\/$/, "")) }));
    const classified = editables.map(item => ({ ...item,
      labels: classify(item.summary, item.summary.index - 1, editables.length) }));
    const titleCandidates = classified.filter(item => item.labels.titleMatch);
    const bodyCandidates = classified.filter(item => item.labels.bodyMatch);
    const controls = Array.from(doc.querySelectorAll(CONTROLS))
      .filter(node => visible(node, doc)).map(node => control(node, doc));
    const publish = controls.filter(item => /^publish(?: story)?$/i.test(item.name));
    const composite = inspectComposite(doc, path.replace(/\/$/, ""), editables, publish,
      story);
    const settings = controls.filter(item => /^(more|more settings|story settings|settings|\.\.\.|⋯|•••)$|(?:story|post) (?:menu|settings)|more options/i
      .test(item.name));
    const indicators = Array.from(doc.querySelectorAll("[role='status'], [aria-live], [data-testid]"))
      .filter(node => visible(node, doc)).map(node => clean(node.innerText || node.textContent))
      .filter(value => /^(saved|saving|draft|draft saved|all changes saved)$/i.test(value));
    const editorAncestor = classified.some(item => item.summary.nearestEditorLikeAncestor);
    const chains = nodes.map(chain);
    const common = chains.length > 1 ? chains[0].find(node =>
      chains.every(other => other.includes(node))) : null;
    const usefulCommon = common && !["body", "html"].includes(tag(common)) ? common : null;
    const nested = nodes.some((node, index) => nodes.some((other, otherIndex) =>
      index !== otherIndex && chain(node).includes(other)));
    const editorStructure = classified.some(item => /^h[1-6]$/.test(item.summary.tag) ||
      item.summary.childStructure.split(", ").some(value => ["section", "p"].includes(value)));
    const separateStructuralEvidence = nodes.length >= 2 &&
      (editorAncestor || editorStructure || Boolean(usefulCommon && editorLike(usefulCommon))) &&
      (routeRecognized || publish.length > 0);
    const structuralEvidence = separateStructuralEvidence || composite.rootDetected;
    const detected = structuralEvidence;
    const title = titleCandidates.length === 1 ? titleCandidates[0] : null;
    const body = bodyCandidates.length === 1 ? bodyCandidates[0] : null;
    const unique = detected && title && body && title.node !== body.node &&
      classified.length === 2 && !nested && publish.length === 1;
    const states = [title?.summary.currentState, body?.summary.currentState];
    const nonempty = unique && states.includes("USER_CONTENT_PRESENT");
    const blank = unique && states.every(value =>
      ["EMPTY", "SCAFFOLDING_ONLY"].includes(value));
    const useComposite = composite.rootDetected;
    const verifiedBlank = useComposite ? composite.blank : blank;
    const verifiedNonempty = useComposite ? composite.nonempty : nonempty;
    const classification = !detected ? "NOT_MEDIUM_EDITOR" :
      verifiedNonempty ? "NONEMPTY_DRAFT" : verifiedBlank ?
        "BLANK_DRAFT_EDITOR_VERIFIED" : "EDITOR_STRUCTURE_AMBIGUOUS";
    const result = { ...empty, detected, editorStructuralEvidencePresent: structuralEvidence,
      classification, editorUrlShape, editorRouteKind: routeKind, draftId,
      draftEditorRoute: routeKind === "DRAFT_EDIT",
      draftIdentityEstablished: routeKind === "DRAFT_EDIT",
      draftRouteEstablishedByMedium: routeKind === "DRAFT_EDIT",
      titleExactMatch: composite.titleExactMatch || false,
      titleFingerprint: composite.titleFingerprint || null,
      titleFingerprintMatch: composite.titleFingerprintMatch || false,
      titleVerifiedInPersistedDraft: composite.titleVerified || false,
      bodyStillBlank: composite.bodyBlank || false,
      draftContinuationSafe: composite.continuationSafe || false,
      draftIdentity: draftId ?
        `Medium story path ${path}` : editorUrlShape === "/new-story" ?
          "New story editor; identity not yet observed" : "Not observed",
      titleEditorCandidateCount: titleCandidates.length,
      bodyEditorCandidateCount: bodyCandidates.length,
      selectedTitleEditor: title?.summary || null,
      selectedBodyEditor: body?.summary || null,
      titleCurrentState: title?.summary.currentState || "UNKNOWN",
      bodyCurrentState: body?.summary.currentState || "UNKNOWN",
      publishControlCount: publish.length, publishControl: publish.length === 1 ? publish[0] : null,
      draftSaveIndicator: indicators[0] || "Not observed",
      editorAppearsBlank: verifiedBlank ? "Yes" : verifiedNonempty ? "No" : "Unknown",
      editorContainer: editorAncestor ? "Editor/story ancestor observed" :
        usefulCommon ? "Shared editor container observed" : "Not observed",
      commonEditorRoot: usefulCommon ? descriptor(usefulCommon) : null,
      editableRelationship: useComposite ? "Single composite editor with title/body descendants" :
        nested ? "Nested contenteditables" :
        unique ? "Separate title and body editors" :
        nodes.length === 2 && usefulCommon ? "Separate editables under a common root" :
          nodes.length === 2 ? "Separate editables; editor root unresolved" : "Not resolved",
      domOrder: useComposite ? "Composite root with ordered descendant blocks" :
        nodes.length === 2 ? "Candidate 1 before candidate 2" : "Not resolved",
      titleBodyOrder: useComposite && composite.selectedTitle && composite.selectedBody ?
        composite.selectedTitle.domOrder < composite.selectedBody.domOrder ?
          "Title before body" : "Body before title" : unique ? title.summary.index < body.summary.index ?
        "Title before body" : "Body before title" : "Unresolved",
      editorArchitecture: useComposite ? "COMPOSITE_SINGLE_CONTENTEDITABLE" :
        unique ? "SEPARATE_EDITABLES" : "UNKNOWN",
      compositeEditorRootDetected: composite.rootDetected,
      compositeEditorCandidateIndex: composite.candidateIndex,
      compositeEditorEvidence: composite.rootEvidence,
      primaryCompositeEditor: composite.root,
      auxiliaryContenteditablesExcluded: composite.auxiliary,
      defaultValuePrompts: composite.defaultPrompts,
      compositeStructuralBlocks: composite.blocks,
      titleSubregionCandidateCount: composite.titleCandidates.length,
      bodySubregionCandidateCount: composite.bodyCandidates.length,
      selectedTitleSubregion: composite.selectedTitle,
      selectedBodySubregion: composite.selectedBody,
      titleSubregionState: composite.titleState,
      bodySubregionState: composite.bodyState,
      userAuthoredContentDetected: useComposite ? composite.userContentDetected :
        nonempty ? "Yes" : blank ? "No" : "Unknown",
      settingsControls: settings, editableCandidates: classified.map(item => ({
        ...item.summary, titleEvidence: item.labels.title, bodyEvidence: item.labels.body,
        titleScore: item.labels.titleScore, bodyScore: item.labels.bodyScore })),
      workflowState: composite.continuationSafe ? "MEDIUM_TITLE_VERIFIED_BODY_BLANK" :
        verifiedBlank ? "BLANK_DRAFT_EDITOR_VERIFIED" : "MEDIUM_EDITOR_NOT_INSPECTED" };
    // Keep live DOM references local; inspection reports cross the extension message boundary.
    const bodyDescendants = Array.from(composite.rootNode?.querySelectorAll?.("*") || []);
    const titleIndex = bodyDescendants.indexOf(composite.titleNode);
    const bodyBlocks = titleIndex < 0 ? [] : bodyDescendants.slice(titleIndex + 1)
      .filter(node => /^(H[1-6]|P)$/.test(node.tagName) ||
        node.tagName === "DIV" &&
        !node.querySelector("h1, h2, h3, h4, h5, h6, p, section, div"));
    Object.defineProperty(result, "resolvedBodySubregion", { value: {
      root: composite.rootNode || null, title: composite.titleNode || null,
      body: composite.bodyNode || null,
      candidateCount: composite.bodyCandidates.length,
      selected: composite.selectedBody || null,
      normalizedText: extractBodyText(composite.bodyNode),
      fingerprint: composite.bodyNode ? fingerprint(extractBodyText(composite.bodyNode)) : null,
      blockNodes: bodyBlocks,
      blockStructure: bodyBlocks.map((node, index) => ({
        number: index + 1, node, tag: tag(node),
        normalizedText: extractBodyText(node),
        fingerprint: fingerprint(extractBodyText(node)) })) } });
    return result;
  }

  const resolveBodySubregion = inspection => inspection?.resolvedBodySubregion || null;
  // Read-only reconciliation. A paragraph containing a heading's text is a valid
  // intermediate state before formatBlock, but it is not a completed Medium block.
  function reconcileBody(doc, inspection, source, { publishObserved = false } = {}) {
    const resolved = resolveBodySubregion(inspection);
    const nodes = resolved?.blockNodes || [];
    const titleIndex = resolved?.root && resolved?.title &&
      within(resolved.title, resolved.root) ? 0 : -1;
    let otherContentPresent = false;
    let passedTitle = false;
    const findOtherContent = node => {
      if (node === resolved?.title) { passedTitle = true; return; }
      if (passedTitle && nodes.includes(node)) return;
      if (passedTitle && node.nodeType === 3) {
        otherContentPresent ||= Boolean(contentText(node.nodeValue));
        return;
      }
      if (passedTitle && /^(IMG|VIDEO|IFRAME|FIGURE|CANVAS|TABLE)$/.test(
        node.tagName || "")) otherContentPresent = true;
      for (const child of node.childNodes || []) findOtherContent(child);
    };
    if (resolved?.root && titleIndex >= 0) findOtherContent(resolved.root);
    const mediaPresent = nodes.some(node => Boolean(node.querySelector(
      "img, video, iframe, figure, canvas, table")));
    const prompt = nodes.some(node => Boolean(node.querySelector(
      ".defaultValue.defaultValue--root")));
    const selectedBodyText = extractBodyText(resolved?.body);
    const texts = nodes.map(node => node === resolved?.body ? selectedBodyText :
      extractBodyText(node));
    const nonempty = nodes.filter((node, index) => texts[index]);
    const current = texts.filter(Boolean).join("\n\n");
    const expected = (source?.blocks || []).map(block => contentText(block.text)).join("\n\n");
    const fullFingerprintMatch = Boolean(source?.fingerprint &&
      fingerprint(current) === source.fingerprint);
    const exact = Boolean(source?.blocks?.length && !prompt && !otherContentPresent &&
      !mediaPresent &&
      nodes.length === source.blocks.length && nodes.every((node, index) =>
        node.tagName.toLowerCase() === (source.blocks[index].kind === "heading" ?
          "h1" : "p") && texts[index] === contentText(source.blocks[index].text)) &&
      fullFingerprintMatch);
    const textPrefix = Boolean(current && expected.startsWith(current) && !prompt &&
      !otherContentPresent && !mediaPresent &&
      nonempty.length === nodes.length && nodes.length <= (source?.blocks?.length || 0) &&
      nodes.every((node, index) => {
        const block = source.blocks[index];
        return contentText(block.text).startsWith(texts[index]) &&
          (index === nodes.length - 1 || texts[index] === contentText(block.text));
      }));
    let through = 0;
    if (textPrefix) {
      while (through < nodes.length && texts[through] ===
          contentText(source.blocks[through].text)) through++;
    }
    const next = source?.blocks?.[through] || null;
    const formattingComplete = Boolean(textPrefix && nodes.every((node, index) =>
      node.tagName.toLowerCase() === (source.blocks[index].kind === "heading" ?
        "h1" : "p")));
    const formattingDiscrepancy = !textPrefix || formattingComplete ? null :
      nodes.map((node, index) => {
        const expectedKind = source.blocks[index].kind === "heading" ? "heading" : "paragraph";
        const expectedTag = expectedKind === "heading" ? "h1" : "p";
        const observedTag = tag(node);
        const wrapper = node.querySelector("font") ? "/font wrapper" : "";
        return observedTag === expectedTag ? null :
          `Expected ${expectedKind} block; observed ${observedTag === "p" ?
            "paragraph" : observedTag}${wrapper}`;
      }).filter(Boolean).join("; ");
    const structure = nodes.slice(0, 40).map(node => ({
      tag: tag(node), dataTestId: attr(node, "data-testid") || null,
      children: Array.from(node.children).slice(0, 8).map(tag),
      normalizedLength: extractBodyText(node).length }));
    const reliable = Boolean(resolved?.root && titleIndex >= 0 && source?.blocks?.length);
    const uniqueBody = Boolean(reliable && resolved?.body &&
      resolved.candidateCount === 1 && nodes.includes(resolved.body) &&
      inspection?.bodySubregionCandidateCount === 1);
    const trustedDraft = Boolean(inspection?.editorRouteKind === "DRAFT_EDIT" &&
      inspection?.titleExactMatch && inspection?.titleFingerprintMatch &&
      inspection?.titleVerifiedInPersistedDraft && !publishObserved &&
      inspection?.draftId === "fd6c1824c4de");
    const verifiedExact = Boolean(reliable && trustedDraft && exact);
    const partial = Boolean(uniqueBody && trustedDraft && !prompt &&
      (current || otherContentPresent || mediaPresent) && !exact);
    return {
      partialBodyDetected: partial,
      bodyInspectionReliable: reliable,
      currentBodyNormalizedLength: reliable ? current.length : null,
      currentBodyFingerprint: reliable ? fingerprint(current) : null,
      currentBodyBlockCount: reliable ? nodes.length : null,
      currentBodyDOMStructure: reliable ? structure : [],
      currentBodyOtherContentPresent: Boolean(otherContentPresent),
      currentBodyMediaPresent: Boolean(mediaPresent),
      currentBodySafeText: reliable && current.length <= 8 ?
        JSON.stringify(current).slice(1, -1) : null,
      currentBodyMatchesMarkdownHeadingMarkup: /^#{1,6}$/.test(current),
      currentBodyExactCanonicalMatch: verifiedExact,
      currentBodyCanonicalPrefixMatch: reliable && textPrefix,
      currentBodyCanonicalTextPrefix: reliable && textPrefix,
      currentBodyCanonicalFormattingComplete: formattingComplete,
      formattingDiscrepancy,
      canonicalPrefixMatchedThroughBlock: reliable && textPrefix ? through : 0,
      canonicalPrefixMatchedCharacterCount: reliable && textPrefix ? current.length : 0,
      expectedNextCanonicalBlock: next ? { number: through + 1, kind: next.kind,
        normalizedLength: contentText(next.text).length,
        fingerprint: fingerprint(contentText(next.text)) } : null,
      partialBodyRecoveryClassification: !partial || otherContentPresent || mediaPresent ?
        "UNKNOWN" : textPrefix ?
        "EXACT_CANONICAL_PREFIX" : "NONCANONICAL_PARTIAL_CONTENT"
    };
  }
  const api = { inspect, resolveBodySubregion, reconcileBody, extractBodyText };
  root.MediumEditorInspector = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(globalThis);

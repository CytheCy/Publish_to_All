(function (root) {
  "use strict";

  const KNOWN_PATHS = new Set([
    "/", "/me", "/me/stories", "/me/stories/drafts", "/me/stories/public",
    "/me/settings", "/m/signin", "/m/signup", "/p/import"
  ]);
  const SIGNED_OUT = new Set(["sign in", "sign up", "get started"]);
  const OWNER_CONTROLS = new Set(["edit profile", "sign out", "settings", "stories",
    "your stories", "write", "notifications"]);
  const ACCOUNT_CUES = new Set(["profile", "account", "avatar", "user menu",
    "account menu", "profile menu", "user options menu"]);
  const SOURCE_URL = "https://cyporter.substack.com/p/saturation-of-artificial-intelligence-f8d";
  const SOURCE_FINGERPRINT =
    "2a5f676c8743eda141101e793cc4ce988ba6f029d0805c0498271bd072937349";
  const IMPORT_HEADING = "See your story on Medium";
  const MEDIUM_DEFAULT_HOST = "www.yoursite.org";
  const OBSERVED_DEFAULT_FINGERPRINT =
    "0729da24628af2a65580e38e7931b60e41bcbbdc639b9b3a306fcd8de00f828c";
  const fillHelper = root.MediumFill ||
    (typeof module === "object" && module.exports ? require("./medium-fill.js") : null);
  const EXAMPLE_HOSTS = new Set(["example.com", "www.example.com"]);
  const inspectedTargets = new WeakMap();
  const SHA256_CONSTANTS = [
    0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
    0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
    0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
    0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
    0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
    0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
    0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
    0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2
  ];

  function normalized(value) {
    return String(value || "").replace(/\s+/g, " ").trim().toLowerCase();
  }

  function editorText(value) {
    return String(value || "").replace(/[\u200b-\u200d\u2060\ufeff]/g, "")
      .replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
  }

  function sourceText(element) {
    const tag = element.tagName.toLowerCase();
    return editorText(tag === "input" || tag === "textarea" ? element.value : element.textContent);
  }

  function sourceRepresentation(element) {
    const tag = element.tagName.toLowerCase();
    if (tag === "input" || tag === "textarea") return "value";
    if (Array.from(element.children || []).some(child =>
      child.tagName?.toLowerCase() === "p")) return "contenteditable paragraph";
    return "textContent";
  }

  function absoluteEditorUrl(text) {
    // URL() repairs some malformed strings (for example a missing host after
    // three slashes). Require an explicit authority before parsing.
    if (!/^https?:\/\/[^\s/?#\\]+(?:[/?#][^\s\\]*)?$/i.test(text)) return false;
    try {
      const url = new URL(text);
      return ["http:", "https:"].includes(url.protocol) && Boolean(url.hostname);
    } catch {
      return false;
    }
  }

  function sha256(text) {
    const bytes = Array.from(new TextEncoder().encode(text));
    const bitLength = BigInt(bytes.length) * 8n;
    bytes.push(0x80);
    while (bytes.length % 64 !== 56) bytes.push(0);
    for (let shift = 56n; shift >= 0n; shift -= 8n) {
      bytes.push(Number((bitLength >> shift) & 0xffn));
    }
    const hash = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a,
      0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
    const rotate = (value, amount) => (value >>> amount) | (value << (32 - amount));
    for (let offset = 0; offset < bytes.length; offset += 64) {
      const words = new Array(64);
      for (let index = 0; index < 16; index++) {
        const start = offset + index * 4;
        words[index] = (bytes[start] << 24) | (bytes[start + 1] << 16) |
          (bytes[start + 2] << 8) | bytes[start + 3];
      }
      for (let index = 16; index < 64; index++) {
        const a = words[index - 15];
        const b = words[index - 2];
        words[index] = (words[index - 16] + (rotate(a, 7) ^ rotate(a, 18) ^ (a >>> 3)) +
          words[index - 7] + (rotate(b, 17) ^ rotate(b, 19) ^ (b >>> 10))) | 0;
      }
      let [a, b, c, d, e, f, g, h] = hash;
      for (let index = 0; index < 64; index++) {
        const choice = (e & f) ^ (~e & g);
        const majority = (a & b) ^ (a & c) ^ (b & c);
        const first = (h + (rotate(e, 6) ^ rotate(e, 11) ^ rotate(e, 25)) +
          choice + SHA256_CONSTANTS[index] + words[index]) | 0;
        const second = ((rotate(a, 2) ^ rotate(a, 13) ^ rotate(a, 22)) + majority) | 0;
        h = g; g = f; f = e; e = (d + first) | 0;
        d = c; c = b; b = a; a = (first + second) | 0;
      }
      for (const [index, value] of [a, b, c, d, e, f, g, h].entries()) {
        hash[index] = (hash[index] + value) | 0;
      }
    }
    return hash.map(value => (value >>> 0).toString(16).padStart(8, "0")).join("");
  }

  function existingUrlDiagnostics(text) {
    if (!absoluteEditorUrl(text)) return null;
    const url = new URL(text);
    const authority = text.match(/^https?:\/\/([^/?#]+)/i)[1];
    const segments = url.pathname.split("/").filter(Boolean);
    return {
      scheme: url.protocol.slice(0, -1), hostname: url.hostname,
      portPresent: /:\d+$/.test(authority),
      pathSegmentCount: segments.length, pathCharacterLength: url.pathname.length,
      pathShape: segments.length ? "/" + segments.map((_, index) =>
        index === 0 && segments[0] === "p" ? "p" : "[segment]").join("/") : "/",
      queryPresent: text.includes("?"), fragmentPresent: text.includes("#"),
      credentialsPresent: authority.includes("@"), normalizedUrlLength: text.length,
      matchesIntendedSourceExactly: text === SOURCE_URL,
      sameHostnameAsIntendedSource: url.hostname === new URL(SOURCE_URL).hostname,
      obviousExampleDomainUrl: EXAMPLE_HOSTS.has(url.hostname),
      safeUrlFingerprint: sha256(text)
    };
  }

  function editorState(element, pageUrl) {
    const tag = element.tagName.toLowerCase();
    const text = sourceText(element);
    const displayed = editorText(tag === "input" || tag === "textarea" ? "" : element.innerText);
    function placeholderMatches(node) {
      for (const attribute of ["data-placeholder", "placeholder", "aria-placeholder"]) {
        const value = node.getAttribute(attribute);
        if (value && editorText(value) === text) return attribute;
      }
      for (const child of node.children || []) {
        const nested = placeholderMatches(child);
        if (nested) return nested;
      }
      return "";
    }
    function meaningfulStructure(node) {
      return Array.from(node.children || []).some(child => {
        if (!["br", "p", "div", "span"].includes(child.tagName.toLowerCase())) return true;
        return meaningfulStructure(child);
      });
    }
    const structurePresent = tag === "div" && meaningfulStructure(element);
    const beginsWithHttp = /^https?:\/\//i.test(text);
    const looksLikeAbsoluteUrl = absoluteEditorUrl(text);
    const wordCount = text ? text.split(/\s+/).length : 0;
    const instructionLike = text.length <= 120 && wordCount >= 2 && wordCount <= 18 &&
      /^(?:paste|enter|type)\b/i.test(text) && /\b(?:url|link)\b/i.test(text) &&
      !looksLikeAbsoluteUrl;
    const attributeMatch = text ? placeholderMatches(element) : "";
    const classes = new Set(String(element.getAttribute("class") || "").split(/\s+/));
    const mediumInstructionStructure = safeUrl(pageUrl) === "https://medium.com/p/import" &&
      tag === "div" && element.getAttribute("role") === "textbox" &&
      ["true", "plaintext-only"].includes(element.getAttribute("contenteditable")) &&
      classes.has("textInput") && classes.has("textInput--large") &&
      element.children?.length === 1 && element.children[0].tagName.toLowerCase() === "p" &&
      !structurePresent;
    const displayConflict = tag === "div" && Boolean(text || displayed) && displayed !== text;
    const placeholderEvidence = attributeMatch ? `Content matches ${attributeMatch} attribute` :
      instructionLike && mediumInstructionStructure ?
        "Instruction pattern in Medium source textbox with a single paragraph; origin unverified" :
        instructionLike ? "Instruction pattern only; origin unverified" : "None";
    const state = structurePresent || displayConflict ? "AMBIGUOUS_CONTENT" :
      !text ? "EMPTY_OR_SCAFFOLDING" :
        text === SOURCE_URL ? "EXACT_SOURCE_URL_PRESENT" :
          looksLikeAbsoluteUrl ? "DIFFERENT_URL_PRESENT" :
            attributeMatch && instructionLike ? "PLACEHOLDER_OR_INSTRUCTION" :
              attributeMatch || instructionLike || beginsWithHttp ? "AMBIGUOUS_CONTENT" :
                "NON_URL_MEANINGFUL_CONTENT";
    const fillSafe = ["EMPTY_OR_SCAFFOLDING", "PLACEHOLDER_OR_INSTRUCTION"].includes(state);
    return {
      empty: state === "EMPTY_OR_SCAFFOLDING", state, fillSafe,
      normalizedTextLength: text.length, looksLikeAbsoluteUrl, beginsWithHttp,
      entireContentOneUrl: looksLikeAbsoluteUrl, wordCount,
      containsWhitespaceSeparatedProse: wordCount > 1 && !looksLikeAbsoluteUrl,
      instructionLike, placeholderEvidence,
      existingUrl: looksLikeAbsoluteUrl ? existingUrlDiagnostics(text) : null,
      visibleText: !text ? "[empty or scaffolding]" :
        state === "PLACEHOLDER_OR_INSTRUCTION" ? "[text redacted — instructional content]" :
          "[existing content redacted]"
    };
  }

  function safeText(value) {
    let text = String(value || "").replace(/\s+/g, " ").trim();
    text = text.replace(/https?:\/\/\S+/gi, "[URL redacted]");
    text = text.replace(/[\w.+-]+@[\w.-]+/g, "[email redacted]");
    text = text.replace(/\b(?:[\w-]+\.)+[a-z]{2,}(?:\/\S*)?/gi, "[URL redacted]");
    text = text.replace(/\b(?:access[_-]?token|refresh[_-]?token|token|secret|password|code|authorization|session(?:[_-]?id)?)\s*[:=]\s*\S+/gi,
      "[secret redacted]");
    text = text.replace(/\b[\w-]{24,}\b/g, "[identifier redacted]");
    return text.slice(0, 120);
  }

  function safeUrl(raw) {
    try {
      const url = new URL(raw);
      if (url.protocol !== "https:" || url.hostname !== "medium.com" || url.port ||
          url.username || url.password) {
        return "[unrecognized URL redacted]";
      }
      let path = "/[path redacted]";
      if (KNOWN_PATHS.has(url.pathname)) {
        path = url.pathname;
      } else if (/^\/@[^/]+\/?$/.test(url.pathname)) {
        path = "/@[profile redacted]";
      }
      return "https://medium.com" + path;
    } catch {
      return "[unrecognized URL redacted]";
    }
  }

  function safeHref(href, pageUrl) {
    try {
      return safeUrl(new URL(href, pageUrl).href);
    } catch {
      return "[unrecognized URL redacted]";
    }
  }

  function visible(element, doc) {
    for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
      if (node.hidden || node.hasAttribute("hidden") ||
          node.getAttribute("aria-hidden") === "true") return false;
      const style = doc.defaultView.getComputedStyle(node);
      if (style.display === "none" || style.visibility === "hidden" ||
          style.visibility === "collapse" || style.opacity === "0") return false;
    }
    return element.getClientRects().length > 0;
  }

  function accessibleName(element, doc) {
    const aria = element.getAttribute("aria-label");
    if (aria) return safeText(aria);
    const labelledBy = element.getAttribute("aria-labelledby");
    if (labelledBy) {
      const label = labelledBy.split(/\s+/).map(id => doc.getElementById(id)?.textContent || "").join(" ");
      if (label.trim()) return safeText(label);
    }
    if (element.labels?.length) {
      const label = Array.from(element.labels, item => item.textContent || "").join(" ");
      if (label.trim()) return safeText(label);
    }
    // Form values, including the value of input[type=submit], are never names.
    if (["input", "textarea"].includes(element.tagName.toLowerCase()) ||
        element.getAttribute("role") === "textbox") {
      return safeText(element.getAttribute("title") || element.getAttribute("placeholder") || "");
    }
    const text = element.innerText || "";
    if (text.trim()) return safeText(text);
    const imageAlt = element.querySelector?.("img[alt]")?.getAttribute("alt");
    const svgTitle = element.querySelector?.("svg title")?.textContent;
    // An avatar's alt text can contain a person's name. Retain only generic cues.
    const genericImageAlt = ACCOUNT_CUES.has(normalized(imageAlt)) ? imageAlt : "";
    return safeText(genericImageAlt || svgTitle || element.getAttribute("title") ||
      element.getAttribute("placeholder") || "");
  }

  function roleOf(element) {
    const explicit = element.getAttribute("role");
    if (explicit) return safeText(explicit);
    const tag = element.tagName.toLowerCase();
    if (tag === "a" && element.hasAttribute("href")) return "link";
    if (tag === "button") return "button";
    if (tag === "input") {
      return ["button", "submit"].includes(element.getAttribute("type")) ? "button" : "textbox";
    }
    if (tag === "textarea") return "textbox";
    if (/^h[1-6]$/.test(tag)) return "heading";
    return "";
  }

  function controlOf(element, doc, pageUrl) {
    const href = element.getAttribute("href");
    const name = accessibleName(element, doc);
    return {
      tag: element.tagName.toLowerCase(),
      role: roleOf(element),
      accessibleName: name,
      visibleText: element.tagName.toLowerCase() === "input" ? "" : safeText(element.innerText || ""),
      href: href === null ? null : safeHref(href, pageUrl),
      dataTestId: safeText(element.getAttribute("data-testid")),
      inNavigation: Boolean(element.closest("nav, header, [role='navigation']")),
      inDialog: Boolean(element.closest("dialog, [role='dialog']")),
      ariaHaspopup: safeText(element.getAttribute("aria-haspopup")),
      ariaExpanded: safeText(element.getAttribute("aria-expanded"))
    };
  }

  function getEffectiveButtonType(element) {
    return element.tagName.toLowerCase() === "button" && typeof element.type === "string" ?
      element.type : null;
  }

  function nativeImportIdentity(element, doc, pageUrl) {
    const control = controlOf(element, doc, pageUrl);
    const typeAttributeValue = element.getAttribute("type");
    const effectiveDomType = getEffectiveButtonType(element);
    const connected = typeof element.isConnected === "boolean" ? element.isConnected :
      Array.from(doc.querySelectorAll("button, input[type='button'], input[type='submit'], [role='button']"))
        .includes(element);
    return {
      ...control,
      type: effectiveDomType,
      typeAttributePresent: typeAttributeValue !== null,
      typeAttributeValue,
      effectiveDomType,
      // An unknown explicit value defaults to submit in HTML. Keep that
      // malformed attribute from silently becoming an authorized action.
      typeAttributeValid: typeAttributeValue === null ||
        ["submit", "button", "reset"].includes(typeAttributeValue.toLowerCase()),
      visible: visible(element, doc),
      enabled: !element.disabled && element.getAttribute("aria-disabled") !== "true",
      connected
    };
  }

  function isAccountMenu(control) {
    return control.role === "button" &&
      /^(?:open )?(?:user|account|profile)(?: options)? menu$/.test(normalized(control.accessibleName)) &&
      (control.ariaHaspopup === "menu" || control.ariaHaspopup === "true" ||
        control.ariaExpanded === "true" || control.ariaExpanded === "false");
  }

  function isStories(control) {
    if (control.role !== "link" && control.role !== "button" && control.role !== "menuitem") return false;
    const name = normalized(control.accessibleName);
    const text = normalized(control.visibleText);
    const named = ["stories", "your stories"].includes(name) ||
      ["stories", "your stories"].includes(text);
    const storiesHref = control.role === "link" &&
      /^https:\/\/medium\.com\/me\/stories(?:\/(?:drafts|public))?$/.test(control.href || "");
    return (named && (control.inNavigation || control.role === "menuitem" || storiesHref)) ||
      storiesHref;
  }

  function storySummary(control) {
    if (!control) return { found: false };
    return {
      found: true,
      elementType: control.tag,
      role: control.role,
      accessibleName: control.accessibleName,
      visibleText: control.visibleText,
      href: control.href,
      dataTestId: control.dataTestId
    };
  }

  function authEvidence(controls, pageUrl) {
    const names = new Set(controls.map(control => normalized(control.accessibleName)));
    const owner = controls.filter(control => OWNER_CONTROLS.has(normalized(control.accessibleName)) ||
        ACCOUNT_CUES.has(normalized(control.accessibleName)))
      .map(control => control.accessibleName);
    const onSignInPage = ["https://medium.com/m/signin", "https://medium.com/m/signup"]
      .includes(safeUrl(pageUrl));
    const signedOut = controls.filter(control => SIGNED_OUT.has(normalized(control.accessibleName)) &&
      (control.inNavigation || control.inDialog || onSignInPage ||
        ["https://medium.com/m/signin", "https://medium.com/m/signup"].includes(control.href)))
      .map(control => control.accessibleName);
    const accountMenu = controls.find(isAccountMenu);
    if (accountMenu) owner.push(accountMenu.accessibleName);
    const stories = controls.find(isStories);
    const editProfile = names.has("edit profile") &&
      safeUrl(pageUrl).endsWith("/@[profile redacted]");
    const signedInNavigation = Boolean(accountMenu && stories &&
      (names.has("write") || names.has("notifications")));
    const strongOwner = editProfile || signedInNavigation ||
      (names.has("sign out") && (names.has("settings") || Boolean(stories)));
    const conflictingOwner = strongOwner || names.has("edit profile") || names.has("sign out") ||
      Boolean(accountMenu) || Boolean(stories) || names.has("notifications") ||
      names.has("settings") ||
      controls.some(control => ACCOUNT_CUES.has(normalized(control.accessibleName)));
    const classification = strongOwner && signedOut.length === 0 ? "AUTHENTICATED" :
      signedOut.length > 0 && !conflictingOwner ? "NOT_AUTHENTICATED" : "UNKNOWN";
    return {
      classification,
      accountCues: [...new Set(owner)],
      signedOutControls: [...new Set(signedOut)],
      decisiveEvidence: classification === "AUTHENTICATED" ?
        (editProfile ? ["Edit profile on a profile page"] : signedInNavigation ?
          [accountMenu.accessibleName, stories.accessibleName || "Stories destination",
            names.has("write") ? "Write" : "Notifications"] :
          ["Sign out", stories ? "Stories" : "Settings"]) :
        classification === "NOT_AUTHENTICATED" ? [...new Set(signedOut)] : []
    };
  }

  function isImportHeading(heading) {
    return [heading.accessibleName, heading.visibleText].some(value =>
      value === IMPORT_HEADING);
  }

  function isUrlInput(input) {
    if (!input.visible || input.disabled || input.inAccountChrome) return false;
    if (input.editorHintExcluded) return false;
    if (!["input", "textarea"].includes(input.tag) && input.role !== "textbox") return false;
    if (input.tag === "input" && !["url", "text"].includes(input.type)) return false;
    if (input.tag === "textarea" && input.type !== "textarea") return false;
    if (/search medium|site search/i.test([input.accessibleName, input.placeholder,
      input.ariaLabel, input.dataPlaceholder].join(" "))) return false;
    if (/\b(?:comment|reply|message|article body|story title)\b/i.test(
      [input.accessibleName, input.placeholder, input.ariaLabel, input.dataPlaceholder].join(" "))) return false;
    const evidence = [input.accessibleName, input.placeholder, input.ariaLabel,
      input.dataPlaceholder, input.nearbyText].join(" ");
    if (input.role === "textbox" && input.tag === "div") {
      return ["true", "", "plaintext-only"].includes(input.contenteditable) &&
        input.contenteditable !== undefined;
    }
    return input.type === "url" || /\b(?:url|link)\b/i.test(evidence) ||
      (/\bpaste\b/i.test(evidence) && /\bstory\b/i.test(evidence));
  }

  function isImportAction(control) {
    return control.role === "button" &&
      [control.accessibleName, control.visibleText].some(value =>
        /^(?:import(?: a| your)? story|import)$/i.test(value || ""));
  }

  function isMediumDefaultExample(input, onImportPage, headingVerified, uniqueEditor, uniqueAction) {
    const url = input?.existingUrl;
    return Boolean(onImportPage && headingVerified && uniqueEditor && uniqueAction &&
      input.tag === "div" && input.role === "textbox" &&
      ["true", "plaintext-only"].includes(input.contenteditable) &&
      input.editorState === "DIFFERENT_URL_PRESENT" && input.entireContentOneUrl && url &&
      url.hostname === MEDIUM_DEFAULT_HOST && !url.portPresent &&
      !url.credentialsPresent && !url.queryPresent && !url.fragmentPresent &&
      url.pathSegmentCount === 1);
  }

  function importSummary(pageUrl, headings, inputs, controls, primaryTitle, inventory,
      nativeActionCount) {
    const matchingHeadings = headings.filter(isImportHeading);
    const matchingInputs = inputs.filter(input => input.nativeSourceSelected === undefined ?
      isUrlInput(input) : input.nativeSourceSelected);
    const pageControls = controls.filter(control => !control.inAccountChrome);
    const matchingActions = pageControls.filter(isImportAction);
    const onImportPage = safeUrl(pageUrl) === "https://medium.com/p/import";
    const input = onImportPage && matchingInputs.length === 1 ? matchingInputs[0] : null;
    const actionCount = nativeActionCount ?? matchingActions.length;
    const action = onImportPage && actionCount === 1 && matchingActions.length === 1 &&
      matchingActions[0].enabled ?
      matchingActions[0] : null;
    const headingVerified = matchingHeadings.length === 1 ||
      primaryTitle?.text === IMPORT_HEADING;
    const found = Boolean(onImportPage && headingVerified && input && action);
    const mediumDefaultExampleRecognized = isMediumDefaultExample(input, onImportPage,
      matchingHeadings.length === 1, matchingInputs.length === 1, Boolean(action));
    const selectedState = mediumDefaultExampleRecognized ? "MEDIUM_DEFAULT_EXAMPLE_URL" :
      input?.editorState;
    const selectedFillSafe = mediumDefaultExampleRecognized ||
      Boolean(input?.editorFillSafe ?? input?.currentValueEmpty);
    const sourceStatus = !onImportPage ? "WRONG_PAGE" :
      matchingInputs.length === 0 ? "SOURCE_EDITOR_NOT_FOUND" :
          matchingInputs.length > 1 ? "SOURCE_EDITOR_AMBIGUOUS" :
            !headingVerified ? "IMPORT_HEADING_NOT_FOUND" :
            !action ? "IMPORT_ACTION_NOT_UNIQUE" :
              selectedState === "EXACT_SOURCE_URL_PRESENT" ? "SOURCE_URL_ALREADY_PRESENT" :
                !selectedFillSafe ? "SOURCE_EDITOR_NOT_EMPTY" :
                  input.tag !== "div" ? "UNSUPPORTED_EDITOR_TYPE" : "READY";
    const fillDisabledReason = sourceStatus === "READY" ? "None" :
      sourceStatus === "SOURCE_EDITOR_NOT_EMPTY" && input ? selectedState : sourceStatus;
    return {
      found, headingVerified, sourceStatus, fillAllowed: found && sourceStatus === "READY",
      matchingHeadingCount: matchingHeadings.length,
      matchingSourceEditorCount: matchingInputs.length,
      matchingImportActionCount: actionCount,
      fillDisabledReason,
      heading: matchingHeadings.length === 1 ? matchingHeadings[0].visibleText : null,
      primaryTitle: onImportPage ? (primaryTitle || null) : null,
      inventory: onImportPage ? inventory : [],
      inputCandidates: onImportPage ? inputs : [],
      actionCandidates: onImportPage ? pageControls.filter(item => item.role === "button").slice(0, 50) : [],
      sourceUrlInput: input ? {
        tag: input.tag, type: input.type, accessibleName: input.accessibleName,
        placeholder: input.placeholder, dataTestId: input.dataTestId,
        currentValueEmpty: input.currentValueEmpty, editorState: selectedState,
        contenteditable: input.contenteditable, ariaMultiline: input.ariaMultiline,
        dataPlaceholder: input.dataPlaceholder, editorClasses: input.editorClasses,
        childElementCount: input.childElementCount, childStructure: input.childStructure,
        normalizedVisibleText: input.normalizedVisibleText,
        normalizedTextLength: input.normalizedTextLength,
        looksLikeAbsoluteUrl: input.looksLikeAbsoluteUrl,
        beginsWithHttp: input.beginsWithHttp,
        entireContentOneUrl: input.entireContentOneUrl,
        wordCount: input.wordCount,
        containsWhitespaceSeparatedProse: input.containsWhitespaceSeparatedProse,
        instructionLike: input.instructionLike,
        placeholderEvidence: input.placeholderEvidence,
        existingUrl: input.existingUrl,
        mediumDefaultExampleRecognized,
        defaultHostname: mediumDefaultExampleRecognized ? MEDIUM_DEFAULT_HOST : null,
        observedDefaultFingerprintMatches: mediumDefaultExampleRecognized &&
          input.existingUrl?.safeUrlFingerprint === OBSERVED_DEFAULT_FINGERPRINT,
        fillReason: mediumDefaultExampleRecognized ? "Verified Medium default example URL" :
          selectedFillSafe ? "Verified empty or placeholder source editor" : "None",
        fillSafe: selectedFillSafe,
        ...(input.role ? { role: input.role } : {}),
        ...(input.nearbyText ? { nearbyText: input.nearbyText } : {})
      } : null,
      importAction: action ? {
        tag: action.tag, role: action.role, accessibleName: action.accessibleName,
        visibleText: action.visibleText, type: action.type,
        ...(action.effectiveDomType !== undefined ? {
          typeAttributePresent: action.typeAttributePresent,
          typeAttributeValue: action.typeAttributeValue,
          effectiveDomType: action.effectiveDomType,
          typeAttributeValid: action.typeAttributeValid,
          visible: action.visible, connected: action.connected
        } : {}),
        enabled: action.enabled, dataTestId: action.dataTestId,
        ...(action.nearbyText ? { nearbyText: action.nearbyText } : {})
      } : null,
      backCancelClose: found ? controls.filter(control =>
        ["back", "go back", "cancel", "close", "close dialog"].includes(normalized(control.accessibleName)))
        .map(control => ({ role: control.role, accessibleName: control.accessibleName })) : []
    };
  }

  function analyzeSnapshot(snapshot) {
    const controls = snapshot.controls || [];
    const authentication = authEvidence(controls, snapshot.pageUrl);
    return {
      contentScriptRan: true,
      page: safeUrl(snapshot.pageUrl),
      title: safeText(snapshot.title),
      authentication,
      stories: storySummary(controls.find(isStories)),
      importInterface: importSummary(snapshot.pageUrl, snapshot.headings || [],
        snapshot.inputs || [], controls, snapshot.primaryTitle, snapshot.inventory || [],
        snapshot.nativeActionCount)
    };
  }

  function nearText(element, doc) {
    const describedBy = element.getAttribute("aria-describedby");
    if (describedBy) {
      const text = describedBy.split(/\s+/).map(id => doc.getElementById(id)?.textContent || "").join(" ");
      if (text.trim()) return safeText(text);
    }
    const parent = element.parentElement;
    if (!parent || ["body", "main", "form"].includes(parent.tagName.toLowerCase())) return "";
    if (element.getAttribute("role") === "textbox" ||
        parent.querySelector?.("textarea, [contenteditable], [role='textbox']")) return "";
    const text = String(parent.textContent || "").replace(/\s+/g, " ").trim();
    return text.length <= 200 ? safeText(text) : "";
  }

  function inAccountChrome(element) {
    return Boolean(element.closest("nav, header, footer, [role='navigation'], [role='menu'], [role='menubar']"));
  }

  function inventoryOf(element, doc, pageUrl) {
    const tag = element.tagName.toLowerCase();
    const role = roleOf(element);
    const type = normalized(element.getAttribute("type") ||
      (tag === "textarea" ? "textarea" : tag === "input" ? "text" : ""));
    if ((tag === "input" && ["hidden", "password"].includes(type)) ||
        element.getAttribute("autocomplete") === "current-password") return null;
    const textbox = tag === "textarea" || role === "textbox";
    const item = { tag };
    if (role) item.role = role;
    if (type) item.type = safeText(type);
    if (!textbox && tag !== "input") {
      const text = safeText(element.innerText || "");
      if (text) item.visibleText = text;
    }
    const name = accessibleName(element, doc);
    if (name) item.accessibleName = name;
    for (const [attribute, field] of [["placeholder", "placeholder"], ["aria-label", "ariaLabel"],
      ["aria-labelledby", "ariaLabelledby"], ["name", "name"], ["id", "id"],
      ["data-testid", "dataTestId"], ["contenteditable", "contenteditable"],
      ["aria-multiline", "ariaMultiline"], ["data-placeholder", "dataPlaceholder"]]) {
      const value = element.getAttribute(attribute);
          if (value !== null && (value || attribute === "contenteditable")) {
            item[field] = safeText(value);
          }
    }
    const href = element.getAttribute("href");
    if (href !== null) item.href = safeHref(href, pageUrl);
    if (role === "button" || tag === "input" || tag === "textarea" || textbox) {
      item.disabled = Boolean(element.disabled || element.getAttribute("aria-disabled") === "true");
    }
    if (textbox) {
      const state = editorState(element, pageUrl);
      const editorHints = [item.accessibleName, item.placeholder, item.ariaLabel,
        item.dataPlaceholder].join(" ");
      item.editorHintExcluded = /search medium|site search|\b(?:comment|reply|message|article body|story title)\b/i
        .test(editorHints);
      item.currentValueEmpty = state.empty;
      item.editorState = state.state;
      item.editorFillSafe = state.fillSafe;
      item.normalizedVisibleText = state.visibleText;
      item.normalizedTextLength = state.normalizedTextLength;
      item.looksLikeAbsoluteUrl = state.looksLikeAbsoluteUrl;
      item.beginsWithHttp = state.beginsWithHttp;
      item.entireContentOneUrl = state.entireContentOneUrl;
      item.wordCount = state.wordCount;
      item.containsWhitespaceSeparatedProse = state.containsWhitespaceSeparatedProse;
      item.instructionLike = state.instructionLike;
      item.placeholderEvidence = state.placeholderEvidence;
      item.existingUrl = state.existingUrl;
      // Attributes can echo the editor's text. Compare raw attributes before
      // safeText truncates them, then send only match evidence to the popup.
      const content = editorText(tag === "input" || tag === "textarea" ? element.value : element.textContent);
      if (content) {
        for (const [attribute, field] of [["placeholder", "placeholder"],
          ["data-placeholder", "dataPlaceholder"], ["aria-label", "ariaLabel"],
          ["title", "accessibleName"]]) {
          if (editorText(element.getAttribute(attribute)) === content) {
            item[field] = "[editor text redacted]";
            if (["placeholder", "aria-label"].includes(attribute)) {
              item.accessibleName = "[editor text redacted]";
            }
          }
        }
        const labelledBy = element.getAttribute("aria-labelledby");
        if (labelledBy && editorText(labelledBy.split(/\s+/)
          .map(id => doc.getElementById(id)?.textContent || "").join(" ")) === content) {
          item.accessibleName = "[editor text redacted]";
        }
        if (element.labels?.length && editorText(Array.from(element.labels,
          label => label.textContent || "").join(" ")) === content) {
          item.accessibleName = "[editor text redacted]";
        }
      }
      item.childElementCount = element.childElementCount || 0;
      item.childStructure = Array.from(element.children || []).slice(0, 8)
        .map(child => child.tagName.toLowerCase() +
          (child.hasAttribute("data-placeholder") ? "[data-placeholder]" : ""))
        .join(", ");
      item.editorClasses = String(element.getAttribute("class") || "").split(/\s+/)
        .filter(name => /editor|contenteditable|prose|input|text/i.test(name))
        .map(safeText).slice(0, 6).join(" ");
    }
    if (tag === "input" || tag === "textarea" || role === "button" || textbox) {
      const context = nearText(element, doc);
      if (context) item.nearbyText = textbox && tag === "div" ?
        "[related text redacted]" : context;
    }
    return item;
  }

  function primaryTitleOf(doc, headings, elements) {
    const matchingHeading = headings.find(isImportHeading);
    if (matchingHeading) return { text: matchingHeading.visibleText || matchingHeading.accessibleName,
      elementType: matchingHeading.tag, role: matchingHeading.role };
    const matches = elements.filter(element => visible(element, doc) && !inAccountChrome(element))
      .filter(element => accessibleName(element, doc) === IMPORT_HEADING);
    if (!matches.length) return null;
    const element = matches[matches.length - 1];
    return { text: safeText(element.innerText || accessibleName(element, doc)),
      elementType: element.tagName.toLowerCase(), role: roleOf(element) };
  }

  function nativeElement(element, doc) {
    // Document queries do not normally cross Shadow DOM. Check the root as well
    // so a nonstandard selector adapter cannot admit an extension control.
    return element.getRootNode?.() ? element.getRootNode() === doc :
      !element.closest?.("#publish-to-all-medium-controls");
  }

  function sourceCandidates(doc, pageUrl) {
    return Array.from(doc.querySelectorAll("input, textarea, [role='textbox']"))
      .filter(element => nativeElement(element, doc) && visible(element, doc) &&
        !inAccountChrome(element))
      .filter(element => {
        const item = inventoryOf(element, doc, pageUrl);
        return item && isUrlInput({ ...item, visible: true, inAccountChrome: false,
          accessibleName: item.accessibleName || "", placeholder: item.placeholder || "",
          nearbyText: item.nearbyText || "" });
      });
  }

  function nativeImportCandidates(doc, pageUrl) {
    return Array.from(doc.querySelectorAll(
      "button, input[type='button'], input[type='submit'], [role='button']"))
      .filter(element => nativeElement(element, doc) && !inAccountChrome(element))
      .filter(element => isImportAction(controlOf(element, doc, pageUrl)) ||
        (element.tagName.toLowerCase() === "button" &&
          /^(?:import(?: a| your)? story|import)$/i.test(
            String(element.textContent || "").trim())));
  }

  function inspectDocument(doc, location) {
    const pageUrl = location.href;
    const onImportPage = safeUrl(pageUrl) === "https://medium.com/p/import";
    const controls = Array.from(doc.querySelectorAll("a, button, input[type='button'], input[type='submit'], [role='link'], [role='button'], [role='menuitem']"))
      .filter(element => nativeElement(element, doc) && visible(element, doc))
      .map(element => {
        const control = element.tagName.toLowerCase() === "button" ?
          nativeImportIdentity(element, doc, pageUrl) : controlOf(element, doc, pageUrl);
        if (control.type === undefined) control.type = safeText(element.getAttribute("type") || element.type || "");
        if (control.enabled === undefined) control.enabled = !element.disabled &&
          element.getAttribute("aria-disabled") !== "true";
        control.inAccountChrome = inAccountChrome(element);
        if (onImportPage && !control.inAccountChrome) control.nearbyText = nearText(element, doc);
        return control;
      });
    const headings = Array.from(doc.querySelectorAll("h1, h2, h3, h4, h5, h6, [role='heading']"))
      .filter(element => visible(element, doc))
      .map(element => controlOf(element, doc, pageUrl));
    const inventory = onImportPage ? Array.from(doc.querySelectorAll(
      "h1, h2, h3, label, input, textarea, button, a, [role='button'], [role='textbox'], [role='link']"))
      .filter(element => visible(element, doc) && !inAccountChrome(element))
      .map(element => inventoryOf(element, doc, pageUrl)).filter(Boolean).slice(0, 50) : [];
    const selectedSources = onImportPage ? new Set(sourceCandidates(doc, pageUrl)) : new Set();
    const inputs = onImportPage ? Array.from(doc.querySelectorAll("input, textarea, [role='textbox']"))
      .filter(element => nativeElement(element, doc) && visible(element, doc) && !inAccountChrome(element))
      .map(element => ({ item: inventoryOf(element, doc, pageUrl), element }))
      .filter(({ item }) => Boolean(item))
      .map(({ item, element }) => ({ ...item, visible: true,
        nativeSourceSelected: selectedSources.has(element),
        accessibleName: item.accessibleName || "", placeholder: item.placeholder || "",
        nearbyText: item.nearbyText || "", dataTestId: item.dataTestId || "",
        inAccountChrome: false })) : [];
    const primaryTitle = onImportPage ? primaryTitleOf(doc, headings,
      Array.from(doc.querySelectorAll("div, span, p, [role='region'], [role='main'], [aria-label], [aria-labelledby]"))) : null;
    const nativeActions = onImportPage ? nativeImportCandidates(doc, pageUrl) : [];
    const report = analyzeSnapshot({ pageUrl, title: doc.title, controls, headings, inputs,
      primaryTitle, inventory, nativeActionCount: onImportPage ?
        nativeActions.length : undefined });
    inspectedTargets.set(report, {
      editor: selectedSources.size === 1 ? [...selectedSources][0] : null,
      action: nativeActions.length === 1 ? nativeActions[0] : null
    });
    return report;
  }

  function inspectImportRaw(doc, location, report) {
    const page = "https://medium.com/p/import" === location.href;
    const editors = page ? sourceCandidates(doc, location.href) : [];
    const actions = page ? nativeImportCandidates(doc, location.href) : [];
    const editor = editors.length === 1 ? editors[0] : null;
    const action = actions.length === 1 ? actions[0] : null;
    const source = editor ? sourceText(editor) : null;
    const fingerprint = source === null ? null : sha256(source);
    const inspectorSource = report?.importInterface?.sourceUrlInput;
    const inspected = report ? inspectedTargets.get(report) : null;
    const sourceIdentityMatch = !inspected || inspected.editor === editor;
    const actionIdentityMatch = !inspected || inspected.action === action;
    const inspectorFingerprint = inspectorSource?.existingUrl?.safeUrlFingerprint || null;
    const inspectorLength = inspectorSource?.normalizedTextLength ?? null;
    const control = action ? nativeImportIdentity(action, doc, location.href) : null;
    const connected = control?.connected || false;
    const sourceExact = source === SOURCE_URL;
    const fingerprintMatch = fingerprint === SOURCE_FINGERPRINT;
    const textExact = control?.visibleText === "Import";
    const nameExact = control?.accessibleName === "Import";
    const actionVisible = control?.visible || false;
    const actionEnabled = control?.enabled || false;
    const sourceReason = !page ? "IMPORT_PAGE_MISMATCH" :
      editors.length !== 1 ? "SOURCE_EDITOR_COUNT_MISMATCH" :
        !sourceIdentityMatch ? "SOURCE_EDITOR_STALE" :
          !sourceExact ? "SOURCE_RAW_NORMALIZATION_MISMATCH" :
          !fingerprintMatch ? "SOURCE_RAW_FINGERPRINT_MISMATCH" : "None";
    const actionReason = actions.length !== 1 ? "IMPORT_ACTION_COUNT_MISMATCH" :
      !connected || !actionIdentityMatch ? "IMPORT_ACTION_STALE" :
        control.tag !== "button" ? "IMPORT_ACTION_TAG_MISMATCH" :
          (control.effectiveDomType !== "submit" || !control.typeAttributeValid) ?
            "IMPORT_ACTION_TYPE_MISMATCH" :
            control.role !== "button" ? "IMPORT_ACTION_ROLE_MISMATCH" :
              !actionVisible ? "IMPORT_ACTION_NOT_VISIBLE" :
                !actionEnabled ? "IMPORT_ACTION_DISABLED" :
                  !textExact ? "IMPORT_ACTION_TEXT_MISMATCH" :
                    !nameExact ? "IMPORT_ACTION_ACCESSIBLE_NAME_MISMATCH" : "None";
    return {
      sourceGuard: sourceReason === "None", actionGuard: actionReason === "None",
      reason: sourceReason !== "None" ? sourceReason : actionReason,
      source: {
        exactMatch: sourceExact, fingerprintMatch, normalizedLength: source?.length ?? null,
        representation: editor ? sourceRepresentation(editor) : null,
        inspectorNormalizedLength: inspectorLength,
        resolverNormalizedLength: source?.length ?? null,
        normalizedValuesEqual: inspectorLength === source?.length &&
          inspectorFingerprint === fingerprint && inspectorLength !== null,
        inspectorFingerprint, resolverFingerprint: fingerprint,
        normalizedEquality: sourceExact, fingerprintEquality: fingerprintMatch,
        identityMatchesInspection: sourceIdentityMatch
      },
      action: {
        rawMatch: actionReason === "None", candidateCount: actions.length,
        tag: control?.tag || null,
        type: control?.effectiveDomType || null,
        typeAttributePresent: control?.typeAttributePresent || false,
        typeAttributeValue: control?.typeAttributeValue ?? null,
        effectiveDomType: control?.effectiveDomType || null,
        effectiveTypeMatch: control?.effectiveDomType === "submit",
        typeAttributeValid: control?.typeAttributeValid || false,
        visibleTextExact: textExact, accessibleNameExact: nameExact,
        visible: actionVisible, enabled: actionEnabled, connected,
        identityMatchesInspection: actionIdentityMatch
      },
      targets: sourceReason === "None" && actionReason === "None" ? { editor, action } : null
    };
  }

  function verifiedImportTargets(doc, location) {
    return inspectImportRaw(doc, location).targets;
  }

  function inspectDraftEditor(doc, location) {
    const result = inspectReconciliation(doc, location);
    return { verified: result.pageKind === "DRAFT_EDITOR" &&
      result.status === "VERIFIED_DRAFT_CREATED" };
  }

  function inspectImportResult(doc, location) {
    const reconciliation = inspectReconciliation(doc, location);
    if (reconciliation.status !== "VERIFIED_DRAFT_CREATED")
      return { verified: false, kind: "NONE" };
    return { verified: true, kind: reconciliation.pageKind === "DRAFT_EDITOR" ?
      "DRAFT_EDITOR" : "DRAFT_LIST" };
  }

  const TARGET_TITLE = "Saturation of Artificial Intelligence";
  const DRAFT_EDIT_PATH = /^\/p\/[a-f0-9]{12}\/edit$/;
  const STORIES_PATHS = new Set(["/me/stories", "/me/stories/drafts",
    "/me/stories/public"]);
  const STORY_ROW_SELECTOR = "article, li, [role='listitem'], " +
    "[data-testid*='story-card'], [data-testid*='story-item']";

  function mediumPath(href, base) {
    try {
      const url = new URL(href, base);
      return url.origin === "https://medium.com" && !url.search && !url.hash ?
        url.pathname : null;
    } catch { return null; }
  }

  function storiesHrefShape(href, base) {
    if (href === null) return null;
    try {
      const url = new URL(href, base);
      if (url.origin !== "https://medium.com" || url.username || url.password)
        return "[unrecognized URL redacted]";
      if (DRAFT_EDIT_PATH.test(url.pathname))
        return "https://medium.com/p/[12-hex-id]/edit";
      if (/^\/@[^/]+\/[^/]+$/.test(url.pathname))
        return "https://medium.com/@[profile]/[story]";
      return safeHref(href, base);
    } catch { return "[unrecognized URL redacted]"; }
  }

  function storyStateAttributes(node) {
    const names = ["aria-selected", "aria-current", "data-state", "data-selected"];
    const values = names.filter(name => node.getAttribute(name) !== null)
      .map(name => `${name}=${safeText(node.getAttribute(name))}`);
    const classes = String(node.getAttribute("class") || "").split(/\s+/).filter(Boolean);
    const stateClasses = classes.filter(value =>
      (/(?:^|[-_:])(?:active|selected|current)(?=$|[-_:]|[A-Z])/i.test(value) ||
        /(?:Active|Selected|Current)(?=$|[A-Z])/.test(value)) &&
      !/(?:inactive|unselected|notselected)/i.test(value));
    return { values, classes: classes.slice(0, 20).map(safeText),
      stateClasses: stateClasses.slice(0, 10).map(safeText) };
  }

  function storyTabState(node, doc, pageUrl) {
    const direct = storyStateAttributes(node);
    const ancestors = [];
    for (let parent = node.parentElement, depth = 0;
      parent && depth < 5; parent = parent.parentElement, depth += 1) {
      const state = storyStateAttributes(parent);
      if (state.stateClasses.length || state.values.some(value =>
        /^(?:aria-current=(?!false)|aria-selected=true|data-state=(?:active|selected|current)|data-selected=true)/i.test(value))) {
        ancestors.push({ tag: parent.tagName.toLowerCase(), role: roleOf(parent),
          stateClasses: state.stateClasses, stateAttributes: state.values });
      }
    }
    const computed = doc.defaultView.getComputedStyle(node);
    const style = { fontWeight: String(computed.fontWeight || ""),
      textDecorationLine: String(computed.textDecorationLine || ""),
      borderBottomStyle: String(computed.borderBottomStyle || ""),
      borderBottomWidth: String(computed.borderBottomWidth || ""),
      opacity: String(computed.opacity || "") };
    const parent = node.parentElement;
    return { tag: node.tagName.toLowerCase(), role: roleOf(node),
      visibleText: safeText(editorText(node.innerText || node.textContent)),
      accessibleName: accessibleName(node, doc),
      ariaSelected: node.getAttribute("aria-selected"),
      ariaCurrent: node.getAttribute("aria-current"),
      ariaControls: safeText(node.getAttribute("aria-controls")),
      tabIndex: node.getAttribute("tabindex"),
      dataTestId: safeText(node.getAttribute("data-testid")),
      classes: direct.classes, id: safeText(node.getAttribute("id")),
      hrefShape: storiesHrefShape(node.getAttribute("href"), pageUrl),
      parentTag: parent?.tagName?.toLowerCase() || null,
      parentRole: parent ? roleOf(parent) : null,
      parentClasses: parent ? storyStateAttributes(parent).classes : [],
      stateClasses: direct.stateClasses, stateAttributes: direct.values,
      ancestorState: ancestors, style };
  }

  function storyNodeSummary(node) {
    if (!node) return null;
    return { tag: node.tagName.toLowerCase(), role: roleOf(node),
      classes: String(node.getAttribute("class") || "").split(/\s+/).filter(Boolean)
        .slice(0, 12).map(safeText), childCount: node.children?.length ?? 0 };
  }

  function storiesContentStructure(doc, tabs, heading, draftPanel) {
    let tablist = tabs[0]?.closest?.("[role='tablist']") || null;
    if (!tablist && tabs.length > 1) {
      for (let node = tabs[0]?.parentElement; node &&
        !["body", "html"].includes(node.tagName.toLowerCase());
        node = node.parentElement) {
        if (tabs.every(tab => node.contains?.(tab))) { tablist = node; break; }
      }
    }
    if (draftPanel) return { root: tablist?.parentElement || null, tablist,
      content: draftPanel, identified: true, reason: "Drafts-linked visible tabpanel" };
    if (!tablist || !heading) return { root: null, tablist, content: null,
      identified: false, reason: "Stories heading or tab container not located" };
    for (let root = tablist.parentElement; root &&
      !["body", "html"].includes(root.tagName.toLowerCase());
      root = root.parentElement) {
      if (!root.contains?.(heading)) continue;
      let branch = tablist;
      while (branch.parentElement !== root) branch = branch.parentElement;
      const siblings = Array.from(root.children || []);
      const after = siblings.slice(siblings.indexOf(branch) + 1)
        .filter(node => !["script", "style"].includes(node.tagName.toLowerCase()));
      // A single post-navigation branch can be tied to this Stories root.
      const content = after.length === 1 ? after[0] :
        after.length > 1 ? after.find(node =>
          ["main", "region"].includes(roleOf(node)) ||
          ["main", "section"].includes(node.tagName.toLowerCase())) : null;
      if (content) return { root, tablist, content, identified: true,
        reason: "Post-tab branch in bounded Stories root" };
    }
    return { root: null, tablist, content: null, identified: false,
      reason: "Post-tab branch not uniquely identified" };
  }

  function inspectStoriesPage(doc, location) {
    const path = mediumPath(location.href, location.href);
    const result = { pageRecognized: false, uiHydrated: false, hydrationWaitElapsedMs: 0,
      draftControl: { found: false, tag: null, role: null, text: null,
        selected: false, hrefShape: null }, selectedTab: "Unknown",
      tabState: "STORIES_TAB_STATE_UNKNOWN", selectedEvidence: [],
      draftsSelectedEvidence: [], tabDiagnostics: [], visualEvidence: [],
      tablistFound: false, visibleTabpanelCount: 0, hiddenTabpanelCount: 0,
      visibleTabpanels: [], contentSnippets: [], contentRegion: "Not identified",
      contentKind: "Unknown",
      storiesRootContainer: null, tablistContainer: null,
      postTabContentContainer: null, postTabContentRegionIdentified: false,
      postTabContentEvidence: "Not identified", visibleContentChildCount: 0,
      contentRegionRendered: false,
      postTabInventory: [], promoBlock: { found: false, heading: null,
        nearbyBodyText: [], controls: [], childCount: 0, storyEntrySiblings: 0,
        loadingSiblings: 0, dedicated: false }, emptyStateEvidence: [],
      loadingEvidence: false, loadingDetails: [], errorEvidence: false,
      errorDetails: [], storyCardStructures: [],
      inventory: [], draftListConfirmed: false, listConclusivelyLoaded: false,
      draftListEmptyState: false, draftEntriesVisible: false,
      listIncomplete: false, visibleDraftCandidateCount: 0,
      matchingCandidates: 0, exactTitleCandidate: false, exactTitleMatches: "0",
      candidateState: "Unknown", candidateHrefShape: "Not observed", candidates: [],
      entries: [],
      status: "AMBIGUOUS" };
    if (!STORIES_PATHS.has(path)) return result;
    const elements = selector => Array.from(doc.querySelectorAll(selector))
      .filter(node => nativeElement(node, doc) && visible(node, doc) &&
        !node.closest?.("header, footer, [role='menu'], [role='menubar'], #publish-to-all-medium-controls"));
    const text = node => editorText(node.innerText || node.textContent || "");
    const selected = node => node.getAttribute("aria-selected") === "true" ||
      (node.getAttribute("aria-current") && node.getAttribute("aria-current") !== "false") ||
      ["active", "selected", "current"].includes(node.getAttribute("data-state")) ||
      node.getAttribute("data-selected") === "true";
    const candidateTabs = elements("a, button, [role='tab'], [role='link']")
      .filter(node => ["Drafts", "Published", "Responses", "Submissions"]
        .some(label => normalized(text(node)) === normalized(label) ||
          normalized(node.getAttribute("aria-label")) === normalized(label)));
    const roleTabs = elements("[role='tab']");
    const tabs = roleTabs.length ? candidateTabs.filter(node =>
      node.getAttribute("role") === "tab") : candidateTabs;
    const label = node => [text(node), node.getAttribute("aria-label")]
      .find(value => ["drafts", "published", "responses", "submissions"]
        .includes(normalized(value))) || "";
    const labels = new Set(tabs.map(node => normalized(label(node))));
    const storiesHeading = elements("h1, h2, h3").find(node =>
      ["stories", "your stories"].includes(normalized(text(node))));
    const heading = Boolean(storiesHeading);
    result.pageRecognized = labels.size >= 2 || (heading && labels.size >= 1);
    result.uiHydrated = result.pageRecognized;
    result.tabDiagnostics = roleTabs.map(node => storyTabState(node, doc, location.href));
    result.tablistFound = elements("[role='tablist']").length > 0;
    const allPanels = Array.from(doc.querySelectorAll("[role='tabpanel']"))
      .filter(node => nativeElement(node, doc));
    const visiblePanels = allPanels.filter(node => visible(node, doc));
    result.visibleTabpanelCount = visiblePanels.length;
    result.hiddenTabpanelCount = allPanels.length - visiblePanels.length;
    result.visibleTabpanels = visiblePanels.slice(0, 10).map(node => ({
      tag: node.tagName.toLowerCase(), id: safeText(node.getAttribute("id")),
      ariaLabelledby: safeText(node.getAttribute("aria-labelledby")),
      ariaControls: safeText(node.getAttribute("aria-controls")),
      hidden: Boolean(node.hidden || node.hasAttribute("hidden")),
      ariaHidden: node.getAttribute("aria-hidden"),
      childElementCount: node.children?.length ?? node.childElementCount ?? 0,
      snippet: safeText(text(node)) }));
    const evidence = new Map(tabs.map(node => [node, []]));
    const addUnique = (predicate, description) => {
      const matches = tabs.filter(predicate);
      if (matches.length === 1) evidence.get(matches[0]).push(description);
    };
    addUnique(node => node.getAttribute("aria-selected") === "true", "aria-selected=true");
    addUnique(node => Boolean(node.getAttribute("aria-current")) &&
      node.getAttribute("aria-current") !== "false", "aria-current");
    addUnique(node => ["active", "selected", "current"].includes(node.getAttribute("data-state")) ||
      node.getAttribute("data-selected") === "true", "selected data attribute");
    addUnique(node => storyStateAttributes(node).stateClasses.length > 0,
      "unique selected-state class on tab");
    addUnique(node => storyTabState(node, doc, location.href).ancestorState.some(state =>
        state.stateClasses.length || state.stateAttributes.some(value =>
          /^(?:aria-current=(?!false)|aria-selected=true|data-state=(?:active|selected|current)|data-selected=true)/i.test(value))),
    "unique selected-state class or attribute on ancestor");
    for (const panel of visiblePanels) {
      const panelId = panel.getAttribute("id");
      const labelledby = String(panel.getAttribute("aria-labelledby") || "").split(/\s+/);
      addUnique(node => Boolean((panelId && node.getAttribute("aria-controls") === panelId) ||
        (node.getAttribute("id") && labelledby.includes(node.getAttribute("id")))),
      "visible tabpanel linked to tab");
    }
    const evidenced = tabs.filter(node => evidence.get(node).length);
    const positive = tabs.filter(node => selected(node) ||
      storyStateAttributes(node).stateClasses.length ||
      storyTabState(node, doc, location.href).ancestorState.length);
    if (evidenced.length === 1 && positive.length <= 1 &&
      (!positive.length || positive[0] === evidenced[0]) &&
      evidenced[0].getAttribute("aria-selected") !== "false" &&
      ["Drafts", "Published", "Submissions"]
      .includes(label(evidenced[0]))) {
      result.selectedTab = label(evidenced[0]);
      result.selectedEvidence = evidence.get(evidenced[0]);
      result.tabState = `${result.selectedTab.toUpperCase()}_SELECTED`;
    }
    result.draftsSelectedEvidence = result.selectedTab === "Drafts" ?
      result.selectedEvidence : [];
    if (tabs.length >= 3) {
      const styles = tabs.map(node => storyTabState(node, doc, location.href).style);
      for (const [property, title] of [["fontWeight", "font-weight"],
        ["textDecorationLine", "text-decoration-line"],
        ["borderBottomStyle", "border-bottom-style"],
        ["borderBottomWidth", "border-bottom-width"], ["opacity", "opacity"]]) {
        const values = styles.map(style => style[property]);
        const unique = values.findIndex(value => value &&
          values.filter(other => other === value).length === 1 &&
          new Set(values.filter(other => other !== value)).size === 1);
        if (unique >= 0) result.visualEvidence.push(`${label(tabs[unique])}: ${title}=${safeText(values[unique])}; others=${safeText(values.find((_, index) => index !== unique))}`);
      }
    }
    const draftTabs = tabs.filter(node => normalized(label(node)) === "drafts");
    if (draftTabs.length === 1) {
      const node = draftTabs[0];
      const href = node.getAttribute("href");
      result.draftControl = { found: true, tag: node.tagName.toLowerCase(),
        role: roleOf(node), text: safeText(text(node) || label(node)),
        selected: result.tabState === "DRAFTS_SELECTED",
        hrefShape: storiesHrefShape(href, location.href) };
    }
    const draftPanel = visiblePanels.find(panel => {
      const panelId = panel.getAttribute("id");
      const labelledby = String(panel.getAttribute("aria-labelledby") || "").split(/\s+/);
      return draftTabs.some(tab => (panelId && tab.getAttribute("aria-controls") === panelId) ||
        (tab.getAttribute("id") && labelledby.includes(tab.getAttribute("id"))));
    });
    const structure = storiesContentStructure(doc, tabs, storiesHeading, draftPanel);
    result.storiesRootContainer = storyNodeSummary(structure.root);
    result.tablistContainer = storyNodeSummary(structure.tablist);
    result.postTabContentContainer = storyNodeSummary(structure.content);
    result.postTabContentRegionIdentified = structure.identified;
    result.postTabContentEvidence = structure.reason;
    result.contentRegion = structure.identified ? structure.reason : "Not identified";
    const content = structure.content;
    if (!content) return result;
    const inDraftRegion = node => node === content || content.contains?.(node);
    const regionElements = selector => [content, ...Array.from(content.querySelectorAll?.(selector) || [])]
      .filter((node, index, nodes) => nodes.indexOf(node) === index &&
        nativeElement(node, doc) && visible(node, doc));
    result.visibleContentChildCount = Array.from(content.children || [])
      .filter(node => visible(node, doc)).length;
    const loadingNodes = regionElements("[aria-busy='true'], [role='progressbar'], " +
      "[data-state='loading'], [class*='skeleton'], [class*='spinner'], " +
      "[aria-label], [role='status'], button, a, p, span")
      .filter(node => node.getAttribute("aria-busy") === "true" ||
        node.getAttribute("role") === "progressbar" ||
        node.getAttribute("data-state") === "loading" ||
        /(?:skeleton|spinner|loading)/i.test(String(node.getAttribute("class") || "")) ||
        /^(?:loading(?:\.\.\.)?|load more|show more|next)$/i.test(text(node)) ||
        /^(?:loading|please wait)$/i.test(node.getAttribute("aria-label") || ""));
    result.loadingDetails = loadingNodes.slice(0, 10).map(node =>
      `${node.tagName.toLowerCase()} ${safeText(text(node) || node.getAttribute("aria-label") ||
        node.getAttribute("role") || node.getAttribute("class"))}`);
    result.loadingEvidence = loadingNodes.length > 0;
    const errorNodes = regionElements("[role='alert'], [aria-invalid='true'], " +
      "[data-state='error'], h1, h2, h3, p, button, a, [role='status']")
      .filter(node => node.getAttribute("role") === "alert" ||
        node.getAttribute("aria-invalid") === "true" ||
        node.getAttribute("data-state") === "error" ||
        /\b(?:error|failed|network problem|could not load|unable to load|try again|retry)\b/i.test(text(node)));
    result.errorDetails = errorNodes.slice(0, 10).map(node => safeText(text(node)));
    result.errorEvidence = errorNodes.length > 0;
    result.listIncomplete = result.loadingEvidence || result.errorEvidence;
    const contentNodes = regionElements("h1, h2, h3, h4, p, [role='status']")
      .filter(node => text(node).length <= 240);
    result.contentSnippets = contentNodes.slice(0, 20).map(node => ({
      tag: node.tagName.toLowerCase(), role: roleOf(node), text: safeText(text(node)),
      parentTag: node.parentElement?.tagName?.toLowerCase() || null,
      parentRole: node.parentElement ? roleOf(node.parentElement) : null }));
    const titleNodes = regionElements("h1, h2, h3, h4, a, [role='link']");
    function rowFor(node) {
      const semantic = node.closest?.(STORY_ROW_SELECTOR);
      if (semantic && inDraftRegion(semantic) && semantic !== content) return semantic;
      let parent = node.parentElement;
      for (let depth = 0; parent && parent !== content && depth < 6;
        depth += 1, parent = parent.parentElement) {
        const headings = Array.from(parent.querySelectorAll?.("h1, h2, h3, h4") || [])
          .filter(child => visible(child, doc));
        const links = Array.from(parent.querySelectorAll?.("a[href]") || [])
          .filter(child => visible(child, doc));
        const storyLinks = links.filter(link => {
          const path = mediumPath(link.getAttribute("href"), location.href) || "";
          return DRAFT_EDIT_PATH.test(path) || /^\/@[^/]+\/[^/]+$/.test(path);
        });
        if ((headings.length === 1 && storyLinks.length > 0) ||
            (headings.length === 0 && storyLinks.length === 1 &&
              text(storyLinks[0]).length > 5) ||
            (headings.length === 1 && parent.querySelector?.("time") &&
              parent.querySelector?.("button, [role='button']"))) return parent;
      }
      return null;
    }
    const rows = regionElements(STORY_ROW_SELECTOR).filter(node => node !== content)
      .filter(node => !tabs.includes(node) &&
        (node.querySelector?.("h1, h2, h3, h4, a, [role='link']") ||
          (node.querySelectorAll?.("a") || []).length))
      .filter(node => Array.from(node.querySelectorAll?.("a[href], a") || [])
        .some(link => {
          const linkPath = mediumPath(link.getAttribute("href"), location.href) || "";
          return DRAFT_EDIT_PATH.test(linkPath) || /^\/@[^/]+\/[^/]+$/.test(linkPath);
        }) || Boolean(node.querySelector?.("h1, h2, h3, h4")) &&
          Boolean(node.querySelector?.("time")) &&
          Boolean(node.querySelector?.("button, [role='button']")));
    for (const node of titleNodes) {
      if (!["h1", "h2", "h3", "h4", "a"].includes(node.tagName.toLowerCase())) continue;
      const row = rowFor(node);
      if (row && !rows.includes(row) && visible(row, doc) && inDraftRegion(row))
        rows.push(row);
    }
    result.storyCardStructures = rows.slice(0, 25).map(row => ({
      tag: row.tagName.toLowerCase(), role: roleOf(row),
      classes: storyNodeSummary(row).classes,
      headingCount: row.querySelectorAll?.("h1, h2, h3, h4")?.length || 0,
      linkCount: row.querySelectorAll?.("a[href]")?.length || 0,
      timeCount: row.querySelectorAll?.("time")?.length || 0,
      buttonCount: row.querySelectorAll?.("button, [role='button']")?.length || 0,
      text: safeText(text(row)) }));
    result.postTabInventory = regionElements("h1, h2, h3, h4, p, a, button, img, " +
      "[role='button'], [role='link'], [role='listitem'], article, li, time")
      .filter(node => node !== content && !node.closest?.("nav, header, footer, [role='navigation']"))
      .slice(0, 60).map(node => ({ tag: node.tagName.toLowerCase(), role: roleOf(node),
        visibleText: safeText(node.tagName.toLowerCase() === "img" ?
          node.getAttribute("alt") : text(node)),
        hrefShape: storiesHrefShape(node.getAttribute("href"), location.href) }));
    result.inventory = result.postTabInventory;
    if (!result.uiHydrated || result.tabState !== "DRAFTS_SELECTED") return result;
    const explicitEmpty = contentNodes.find(node =>
      /^(?:you have no drafts(?: yet)?|no drafts(?: yet)?|no draft stories|you haven't written any drafts(?: yet)?)\.?$/i.test(text(node)));
    const promoHeading = contentNodes.find(node =>
      /write, grow, and reach readers on medium/i.test(text(node)));
    if (promoHeading) {
      let block = promoHeading;
      while (block.parentElement && block.parentElement !== content &&
        block.parentElement !== draftPanel) block = block.parentElement;
      const controls = Array.from(block.querySelectorAll?.("a, button, [role='button']") || [])
        .filter(node => visible(node, doc));
      const body = Array.from(block.querySelectorAll?.("p, h1, h2, h3, h4") || [])
        .filter(node => visible(node, doc) && node !== promoHeading && text(node));
      const siblings = Array.from(block.parentElement?.children || [])
        .filter(node => node !== block && visible(node, doc));
      const dedicated = block !== promoHeading && siblings.length === 0 &&
        (body.length > 0 || controls.length > 0) && rows.length === 0;
      result.promoBlock = { found: true, heading: safeText(text(promoHeading)),
        nearbyBodyText: body.slice(0, 8).map(node => safeText(text(node))),
        controls: controls.slice(0, 8).map(node => ({ tag: node.tagName.toLowerCase(),
          text: safeText(text(node)), hrefShape: storiesHrefShape(
            node.getAttribute("href"), location.href) })),
        childCount: block.children?.length ?? 0,
        storyEntrySiblings: siblings.filter(node => rows.some(row => node.contains?.(row))).length,
        loadingSiblings: siblings.filter(node => loadingNodes.some(item => node.contains?.(item))).length,
        dedicated };
    }
    const structuralEmpty = result.promoBlock.dedicated &&
      !result.loadingEvidence && !result.errorEvidence && rows.length === 0;
    result.contentRegionRendered = Boolean(result.visibleContentChildCount &&
      (explicitEmpty || result.promoBlock.dedicated || rows.length));
    result.emptyStateEvidence = [
      ...(explicitEmpty ? [`Explicit text: ${safeText(text(explicitEmpty))}`] : []),
      ...(structuralEmpty ? ["Dedicated promo block occupies selected Drafts content region",
        "Post-tab region identified", "Content block rendered with body text or action",
        "No loading indicator", "No error indicator", "No story entries"] : [])
    ];
    result.draftListEmptyState = Boolean(result.contentRegionRendered &&
      (explicitEmpty || structuralEmpty) &&
      !result.loadingEvidence && !result.errorEvidence && !rows.length);
    result.draftListConfirmed = rows.length > 0 || result.draftListEmptyState;
    result.listConclusivelyLoaded = result.draftListEmptyState;
    result.contentKind = result.draftListEmptyState ?
      explicitEmpty ? "Explicit Drafts empty state" : "Structural Drafts empty state" :
      rows.length ? "Visible story entries" : promoHeading ?
        "Promotional block; empty state unconfirmed" : "Unknown";
    const entries = [];
    const candidates = [];
    let draftEntryCount = 0;
    for (const row of rows) {
      const heading = titleNodes.find(node =>
        ["h1", "h2", "h3", "h4"].includes(node.tagName.toLowerCase()) &&
        (rowFor(node) || node.parentElement) === row);
      const titleNode = heading || titleNodes.find(node =>
        (rowFor(node) || node.parentElement) === row &&
        node.tagName.toLowerCase() === "a" && text(node) !== "Edit");
      if (!titleNode) continue;
      const title = editorText(text(titleNode));
      if (!title) continue;
      const links = [titleNode, ...Array.from(row.querySelectorAll?.("a") || []),
        ...Array.from(row.querySelectorAll?.("[role='link']") || [])];
      const paths = links.map(link => link.getAttribute?.("href"))
        .map(href => href && mediumPath(href, location.href)).filter(Boolean);
      const edit = paths.find(value => DRAFT_EDIT_PATH.test(value));
      const href = edit || paths[0];
      const rowText = text(row);
      const published = /\bpublished\b/i.test(rowText);
      const draft = !published && (/\bdraft\b/i.test(rowText) || Boolean(edit));
      const time = Array.from(row.querySelectorAll?.("time") || [])
        .filter(item => visible(item, doc)).map(text).find(Boolean);
      const entry = { title: safeText(title), state: published ? "Published" :
        draft ? "Draft" : "Unknown", hrefShape: edit ? "/p/[12-hex-id]/edit" :
          href ? "/[Medium story path]" : "Not observed",
        editUrlExists: Boolean(edit), usableMediumHref: Boolean(href),
        visibleTimestamp: time ? safeText(time) : null };
      if (entry.state === "Draft") draftEntryCount += 1;
      if (entries.length < 40) entries.push(entry);
      if (normalized(title) === normalized(TARGET_TITLE)) candidates.push(entry);
    }
    result.entries = entries;
    result.candidates = candidates;
    result.visibleDraftCandidateCount = draftEntryCount;
    result.draftEntriesVisible = entries.length > 0;
    result.matchingCandidates = candidates.length;
    result.exactTitleCandidate = candidates.length > 0;
    result.exactTitleMatches = candidates.length > 1 ? ">1" : String(candidates.length);
    if (candidates.length === 1) {
      result.candidateState = candidates[0].state;
      result.candidateHrefShape = candidates[0].hrefShape;
      if (candidates[0].state === "Draft" && candidates[0].usableMediumHref &&
          !result.loadingEvidence && !result.errorEvidence)
        result.status = "VERIFIED_DRAFT_CANDIDATE";
    } else if (candidates.length > 1) {
      result.candidateHrefShape = "Multiple candidates";
    } else if (result.listConclusivelyLoaded && !result.draftEntriesVisible &&
        result.draftListEmptyState && !result.loadingEvidence && !result.errorEvidence) {
      result.status = "NO_DRAFT_FOUND";
    }
    return result;
  }

  async function waitForStoriesHydration(doc, location, options = {}) {
    const timeoutMs = options.timeoutMs ?? 4500;
    const intervalMs = options.intervalMs ?? 100;
    const settleMs = options.settleMs ?? 350;
    const now = options.now || (() => Date.now());
    const started = now();
    let inspection = inspectStoriesPage(doc, location);
    if (!STORIES_PATHS.has(mediumPath(location.href, location.href))) return inspection;
    let signature = "";
    let lastChange = started;
    while (now() - started < timeoutMs) {
      const current = JSON.stringify([inspection.uiHydrated, inspection.selectedTab,
        inspection.postTabContentRegionIdentified, inspection.contentRegionRendered,
        inspection.promoBlock.dedicated, inspection.loadingEvidence,
        inspection.errorEvidence, inspection.draftListConfirmed, inspection.listIncomplete,
        inspection.visibleDraftCandidateCount, inspection.matchingCandidates,
        inspection.listConclusivelyLoaded, inspection.candidateHrefShape,
        inspection.entries.map(item => [item.title, item.state, item.hrefShape])]);
      if (current !== signature) { signature = current; lastChange = now(); }
      if (inspection.uiHydrated &&
        inspection.tabState !== "STORIES_TAB_STATE_UNKNOWN" &&
        !inspection.loadingEvidence && !inspection.errorEvidence &&
        (inspection.selectedTab !== "Drafts" || inspection.draftListConfirmed) &&
        now() - lastChange >= settleMs) break;
      await new Promise(resolve => setTimeout(resolve, Math.min(intervalMs,
        Math.max(1, timeoutMs - (now() - started)))));
      inspection = inspectStoriesPage(doc, location);
    }
    inspection.hydrationWaitElapsedMs = now() - started;
    return inspection;
  }

  function inspectReconciliation(doc, location, storiesInspection = null) {
    const path = mediumPath(location.href, location.href);
    const all = selector => Array.from(doc.querySelectorAll(selector))
      .filter(node => visible(node, doc) && !inAccountChrome(node));
    const text = node => editorText(node.innerText || node.textContent || "");
    const links = all("a, [role='link']");
    const result = { status: "AMBIGUOUS", pageKind: "OTHER", retryAllowed: false,
      validation: { detected: false, text: "Not observed" },
      importPage: { seeYourStory: false, draftEditLink: false,
        confirmation: "Not observed", error: "Not observed" },
      stories: { matchingCandidates: 0, exactTitleCandidate: false,
        candidateState: "Unknown", candidateHrefShape: "Not observed",
        candidates: [], draftListConfirmed: false },
      editor: { exactTitle: false, editableBody: false, publishControl: false,
        unpublished: false, hrefShape: "Not observed" } };
    if (path === "/p/import") {
      result.pageKind = "IMPORT";
      const draftLinks = links.filter(link => DRAFT_EDIT_PATH.test(
        mediumPath(link.getAttribute("href"), location.href) || ""));
      result.importPage.seeYourStory = links.some(link => text(link) === "See your story");
      result.importPage.draftEditLink = draftLinks.length === 1;
      const messages = all("[role='alert'], [aria-live], [aria-invalid], [data-testid], p, span")
        .map(text).filter(Boolean);
      const source = sourceCandidates(doc, location.href)[0];
      const described = String(source?.getAttribute("aria-describedby") || "")
        .split(/\s+/).filter(Boolean)
        .map(id => doc.getElementById(id)).filter(node => node && visible(node, doc))
        .map(text);
      const validation = [...described, ...messages].find(value =>
        /(?:invalid|unsupported|missing|required|could not|cannot|unable|error|failed|please enter|please paste).*?(?:url|link|story|import)|(?:url|link|story|import).*?(?:invalid|unsupported|missing|required|could not|cannot|unable|error|failed)/i.test(value));
      const confirmation = messages.find(value =>
        /(?:imported|successfully imported|see your story)/i.test(value));
      if (validation) {
        result.validation = { detected: true, text: safeText(validation) };
        result.importPage.error = safeText(validation);
      } else if (source?.getAttribute("aria-invalid") === "true") {
        result.validation = { detected: true, text: "Source editor marked invalid" };
        result.importPage.error = "Source editor marked invalid";
      }
      if (confirmation) result.importPage.confirmation = safeText(confirmation);
      // The Import page alone does not prove title or unpublished editor state.
      return result;
    }
    if (STORIES_PATHS.has(path)) {
      result.pageKind = "STORIES";
      result.stories = storiesInspection || inspectStoriesPage(doc, location);
      result.status = result.stories.status;
      return result;
    }
    if (DRAFT_EDIT_PATH.test(path || "")) {
      result.pageKind = "DRAFT_EDITOR";
      const editable = all("[contenteditable='true'], [contenteditable='plaintext-only']");
      const titles = all("h1, [role='heading'], [contenteditable='true'], [contenteditable='plaintext-only']");
      result.editor.exactTitle = titles.some(node => text(node) === TARGET_TITLE);
      result.editor.editableBody = editable.some(node => text(node) !== TARGET_TITLE);
      result.editor.publishControl = all("button, [role='button']")
        .some(node => text(node) === "Publish" || node.getAttribute("aria-label") === "Publish");
      result.editor.unpublished = result.editor.publishControl &&
        !all("[role='status'], [role='alert']").some(node => /\bpublished\b/i.test(text(node)));
      result.editor.hrefShape = "/p/[12-hex-id]/edit";
      if (result.editor.exactTitle && result.editor.editableBody &&
          result.editor.publishControl && result.editor.unpublished)
        result.status = "VERIFIED_DRAFT_CREATED";
    }
    return result;
  }

  function fillVerifiedSourceUrl(doc, location, session) {
    const report = inspectDocument(doc, location);
    const status = report.importInterface.sourceStatus;
    const replacingDefaultExample = report.importInterface.sourceUrlInput?.editorState ===
      "MEDIUM_DEFAULT_EXAMPLE_URL";
    const result = { status, urlFillAttempted: false, defaultExampleReplaced: false,
      fillSucceeded: false, exactUrlPresentAfterward: false,
      fillDomResult: "FAILED", fingerprintMatch: false,
      browserInputObserved: false, syntheticInputFallbackUsed: false,
      applicationInputSignalDelivered: false, fillSynchronizationStatus: "UNVERIFIED",
      fillEvents: [], fillEventCounts: { beforeinput: 0, input: 0, change: 0,
        trustedInput: 0, syntheticInput: 0 },
      importClicked: false, formSubmitted: false, navigationOccurred: false,
      importButtonStillExists: Boolean(report.importInterface.importAction),
      pageRemainsImport: report.page === "https://medium.com/p/import" };
    if (session.fills >= 1) return { ...result, status: "FILL_LIMIT_REACHED" };
    if (status === "SOURCE_URL_ALREADY_PRESENT") return result;
    if (!report.importInterface.fillAllowed) return result;
    if (!replacingDefaultExample ||
        report.importInterface.sourceUrlInput?.observedDefaultFingerprintMatches !== true)
      return { ...result, status: "DEFAULT_EXAMPLE_NOT_VERIFIED" };
    function importActions() {
      return nativeImportCandidates(doc, location.href)
        .filter(element => visible(element, doc) &&
          !element.disabled && element.getAttribute("aria-disabled") !== "true");
    }
    const candidates = sourceCandidates(doc, location.href);
    if (candidates.length !== 1) {
      return { ...result, status: candidates.length ? "SOURCE_EDITOR_AMBIGUOUS" :
        "SOURCE_EDITOR_NOT_FOUND" };
    }
    const editor = candidates[0];
    const originalText = String(editor.textContent || "");
    const originalActions = importActions();
    if (originalActions.length !== 1) return { ...result, status: "REFUSE" };
    function stillVerified() {
      const fresh = inspectDocument(doc, location);
      const freshCandidates = sourceCandidates(doc, location.href);
      const freshActions = importActions();
      return fresh.importInterface.fillAllowed && freshCandidates.length === 1 &&
        freshCandidates[0] === editor && freshActions.length === 1 &&
        freshActions[0] === originalActions[0] &&
        fresh.importInterface.sourceUrlInput?.editorState === "MEDIUM_DEFAULT_EXAMPLE_URL" &&
        fresh.importInterface.sourceUrlInput?.observedDefaultFingerprintMatches === true &&
        String(editor.textContent || "") === originalText;
    }
    const startUrl = location.href;
    const onSubmit = event => { result.formSubmitted = true; event.preventDefault(); };
    doc.addEventListener("submit", onSubmit, true);
    try {
      const edited = fillHelper.editSource(doc, {
        resolveEditor: () => {
          const fresh = sourceCandidates(doc, location.href);
          return fresh.length === 1 ? fresh[0] : null;
        },
        verifyDefault: current => current === editor && stillVerified() &&
          !result.formSubmitted && startUrl === location.href,
        sourceUrl: SOURCE_URL, fingerprint: SOURCE_FINGERPRINT,
        normalize: editorText, hash: sha256
      });
      if (edited.attempted) session.fills += 1;
      result.urlFillAttempted = edited.attempted;
      result.fillDomResult = edited.exactDom ? "EXACT_SOURCE_URL_PRESENT" : "FAILED";
      result.fingerprintMatch = edited.fingerprintMatch;
      result.browserInputObserved = edited.browserInputObserved;
      result.syntheticInputFallbackUsed = edited.syntheticInputFallbackUsed;
      result.applicationInputSignalDelivered = edited.applicationInputSignalDelivered;
      result.fillSynchronizationStatus = edited.synchronizationStatus;
      result.fillEvents = edited.events;
      result.fillEventCounts = edited.counts;
      if (edited.reason !== "None") result.status = edited.reason;
    } catch {
      result.status = "INSERT_TEXT_FAILED";
    } finally {
      doc.removeEventListener("submit", onSubmit, true);
    }
    result.navigationOccurred = location.href !== startUrl;
    result.pageRemainsImport = ["https://medium.com/p/import"].includes(location.href);
    const after = inspectDocument(doc, location);
    result.exactUrlPresentAfterward = after.importInterface.found &&
      after.importInterface.sourceUrlInput?.editorState ===
      "EXACT_SOURCE_URL_PRESENT" &&
      after.importInterface.sourceUrlInput.existingUrl?.matchesIntendedSourceExactly === true &&
      after.importInterface.sourceUrlInput.existingUrl?.sameHostnameAsIntendedSource === true;
    result.fillDomResult = result.exactUrlPresentAfterward ?
      "EXACT_SOURCE_URL_PRESENT" : "FAILED";
    result.fingerprintMatch = result.fingerprintMatch &&
      after.importInterface.sourceUrlInput?.existingUrl?.safeUrlFingerprint ===
        SOURCE_FINGERPRINT;
    if (!result.exactUrlPresentAfterward || !result.fingerprintMatch)
      result.fillSynchronizationStatus = "UNVERIFIED";
    result.importButtonStillExists = Boolean(after.importInterface.importAction);
    if (result.urlFillAttempted && (result.formSubmitted || result.navigationOccurred ||
        !result.pageRemainsImport || !result.importButtonStillExists)) {
      result.status = "UNEXPECTED_PAGE_CHANGE";
      result.fillSynchronizationStatus = "UNVERIFIED";
    }
    else if (result.exactUrlPresentAfterward && result.urlFillAttempted &&
        result.fillSynchronizationStatus === "VERIFIED") result.status = "URL_FILLED";
    else if (result.status === "READY") result.status = "URL_VERIFICATION_FAILED";
    result.fillSucceeded = result.status === "URL_FILLED";
    result.defaultExampleReplaced = replacingDefaultExample && result.fillSucceeded;
    return result;
  }

  const api = { analyzeSnapshot, inspectDocument, fillVerifiedSourceUrl,
    verifiedImportTargets, inspectImportRaw, inspectDraftEditor, inspectImportResult,
    inspectReconciliation, inspectStoriesPage, waitForStoriesHydration, safeText, safeUrl };
  root.MediumInspector = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(globalThis);

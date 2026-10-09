(function (root) {
  "use strict";

  const TYPES = ["beforeinput", "input", "change", "focus", "blur"];

  function editSource(doc, options) {
    const { resolveEditor, verifyDefault, sourceUrl, fingerprint, normalize, hash } = options;
    const result = {
      attempted: false, commandSucceeded: false, exactDom: false,
      fingerprintMatch: false, browserInputObserved: false,
      syntheticInputFallbackUsed: false, applicationInputSignalDelivered: false,
      synchronizationStatus: "UNVERIFIED", events: [],
      counts: { beforeinput: 0, input: 0, change: 0, trustedInput: 0,
        syntheticInput: 0 }, reason: "REFUSE"
    };
    // Resolve at the point of mutation, independent of any earlier UI inspection.
    const editor = resolveEditor();
    if (!editor || editor.tagName?.toLowerCase() !== "div" ||
        !["true", "plaintext-only"].includes(editor.getAttribute?.("contenteditable")) ||
        !verifyDefault(editor)) return result;
    let observing = true;
    let editing = false;
    let fallbackDispatching = false;
    let usableInputCount = 0;
    let trustedUsableInputCount = 0;
    const observe = event => {
      if (!observing) return;
      const inputType = typeof event.inputType === "string" &&
        /^(?:insert|delete|history|format)[A-Za-z]{0,40}$/.test(event.inputType) ?
          event.inputType : null;
      const record = {
        type: event.type, targetIsSelectedEditor: event.target === editor,
        bubbles: Boolean(event.bubbles), cancelable: Boolean(event.cancelable),
        composed: Boolean(event.composed), isTrusted: Boolean(event.isTrusted),
        inputType,
        dataLength: typeof event.data === "string" ? event.data.length : null
      };
      if (result.events.length < 40) result.events.push(record);
      if (event.type === "beforeinput") result.counts.beforeinput += 1;
      if (event.type === "change") result.counts.change += 1;
      if (event.type === "input") {
        result.counts.input += 1;
        if (record.isTrusted) result.counts.trustedInput += 1;
        else result.counts.syntheticInput += 1;
        if ((editing || fallbackDispatching) && record.targetIsSelectedEditor &&
            record.bubbles) {
          usableInputCount += 1;
          if (record.isTrusted) trustedUsableInputCount += 1;
        }
      }
    };
    for (const type of TYPES) editor.addEventListener?.(type, observe);
    try {
      editor.focus();
      if (resolveEditor() !== editor || !verifyDefault(editor)) return result;
      const range = doc.createRange();
      range.selectNodeContents(editor);
      const selection = doc.getSelection();
      selection.removeAllRanges();
      selection.addRange(range);
      try {
        if (resolveEditor() !== editor || !verifyDefault(editor)) return result;
        result.attempted = true;
        editing = true;
        result.commandSucceeded = Boolean(doc.execCommand("insertText", false, sourceUrl));
      } finally {
        editing = false;
        selection.removeAllRanges();
      }
      const normalizedText = normalize(editor.textContent);
      const selectedEditorStillCurrent = resolveEditor() === editor &&
        editor.isConnected !== false;
      result.exactDom = selectedEditorStillCurrent && normalizedText === sourceUrl;
      result.fingerprintMatch = selectedEditorStillCurrent &&
        hash(normalizedText) === fingerprint;
      result.browserInputObserved = trustedUsableInputCount > 0;
      let inputDelivered = usableInputCount > 0;
      if (result.exactDom && result.fingerprintMatch && !inputDelivered) {
        const InputEventClass = doc.defaultView?.InputEvent || root.InputEvent;
        if (typeof InputEventClass === "function" && editor.dispatchEvent) {
          result.syntheticInputFallbackUsed = true;
          fallbackDispatching = true;
          try {
            editor.dispatchEvent(new InputEventClass("input", {
              bubbles: true, composed: true, inputType: "insertText", data: sourceUrl
            }));
          } finally {
            fallbackDispatching = false;
          }
          inputDelivered = usableInputCount > 0;
        }
      }
      result.applicationInputSignalDelivered = inputDelivered;
      result.synchronizationStatus = result.exactDom && result.fingerprintMatch &&
        inputDelivered ? "VERIFIED" : "UNVERIFIED";
      result.reason = result.commandSucceeded ? "None" : "INSERT_TEXT_FAILED";
      return result;
    } catch {
      result.reason = "INSERT_TEXT_FAILED";
      return result;
    } finally {
      observing = false;
      for (const type of TYPES) editor.removeEventListener?.(type, observe);
    }
  }

  const api = { editSource };
  root.MediumFill = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(globalThis);

(function (root) {
  "use strict";

  const HOST_ID = "publish-to-all-medium-title-controls";
  let generation = 0;

  function onEditor(location) {
    try {
      const url = new URL(location.href);
      return url.origin === "https://medium.com" && url.pathname === "/new-story";
    } catch { return false; }
  }

  function domState(doc, host, panel, button) {
    const view = doc.defaultView;
    const style = node => node && view?.getComputedStyle?.(node);
    const buttonStyle = style(button), panelStyle = style(panel), hostStyle = style(host);
    const rect = button?.getBoundingClientRect?.();
    const center = rect && rect.width > 0 && rect.height > 0 ?
      { x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 } : null;
    const shadowHit = center ? host?.shadowRoot?.elementFromPoint?.(center.x, center.y) : null;
    const documentHit = center ? doc.elementFromPoint?.(center.x, center.y) : null;
    const shadowContainsHit = shadowHit === button || Boolean(button?.contains?.(shadowHit));
    let hitTest = "UNAVAILABLE";
    if (center && documentHit && documentHit !== host && documentHit !== button &&
        !host?.shadowRoot?.contains?.(documentHit))
      hitTest = `BLOCKED_BY_${documentHit.tagName || "UNKNOWN"}`;
    else if (shadowHit) hitTest = shadowContainsHit ? "BUTTON" :
      `BLOCKED_BY_${shadowHit.tagName || "UNKNOWN"}`;
    else if (documentHit === button) hitTest = "BUTTON";
    else if (center && (doc.elementFromPoint || host?.shadowRoot?.elementFromPoint))
      hitTest = "BLOCKED_OR_OUTSIDE_VIEWPORT";
    const ancestors = [];
    for (let node = button; node; node = node.parentElement) ancestors.push(node);
    if (host) ancestors.push(host);
    const disabledFieldset = ancestors.some(node => node.tagName === "FIELDSET" && node.disabled);
    const inert = ancestors.some(node => node.inert || node.hasAttribute?.("inert"));
    const ariaAncestor = ancestors.some(node => node.getAttribute?.("aria-disabled") === "true");
    const pointerAncestor = ancestors.some(node => style(node)?.pointerEvents === "none");
    const hiddenAncestor = ancestors.some(node => node.hidden ||
      ["hidden", "collapse"].includes(style(node)?.visibility) || style(node)?.display === "none");
    const opacity = ancestors.find(node => style(node)?.opacity === "0");
    const reason = !button ? "CONTROL_MISSING" : !button.isConnected ? "CONTROL_DETACHED" :
      button.disabled ? "DISABLED_PROPERTY" : button.hasAttribute("disabled") ? "DISABLED_ATTRIBUTE" :
        disabledFieldset ? "DISABLED_FIELDSET" : inert ? "INERT_ANCESTOR" :
          ariaAncestor ? "ARIA_DISABLED" : pointerAncestor ? "POINTER_EVENTS_NONE" :
            hiddenAncestor ? "HIDDEN_ANCESTOR" : opacity ? "ZERO_OPACITY" :
              !center ? "EMPTY_BOUNDING_BOX" : hitTest !== "BUTTON" ? hitTest : "None";
    return {
      present: Boolean(button && host?.shadowRoot?.contains(button)),
      disabledProperty: Boolean(button?.disabled),
      disabledAttribute: Boolean(button?.hasAttribute?.("disabled")),
      ariaDisabled: button?.getAttribute?.("aria-disabled") ?? "Absent",
      pointerEvents: buttonStyle?.pointerEvents ?? "Unavailable",
      cursor: buttonStyle?.cursor ?? "Unavailable",
      visibility: buttonStyle?.visibility ?? "Unavailable",
      display: buttonStyle?.display ?? "Unavailable",
      opacity: buttonStyle?.opacity ?? "Unavailable",
      boundingBox: rect ? `${Math.round(rect.left)}, ${Math.round(rect.top)}, ` +
        `${Math.round(rect.width)} x ${Math.round(rect.height)}` : "Unavailable",
      hitTest,
      panelPointerEvents: panelStyle?.pointerEvents ?? "Unavailable",
      panelZIndex: panelStyle?.zIndex ?? "Unavailable",
      hostPointerEvents: hostStyle?.pointerEvents ?? "Unavailable",
      hostDisplay: hostStyle?.display ?? "Unavailable",
      hostZIndex: hostStyle?.zIndex ?? "Unavailable",
      disabledFieldset, inert, ariaAncestor, zeroOpacity: Boolean(opacity),
      blockingReason: reason,
      interactability: reason === "None" ? "ENABLED" : "BLOCKED"
    };
  }

  function mount(doc, location, readiness, activate, options = {}) {
    const hostId = options.hostId || HOST_ID;
    const bodyMode = options.bodyMode === true;
    const recoveryMode = options.recoveryMode === true;
    // Repeated hosts can survive an extension reload. Dispose each old listener.
    for (const old of Array.from(doc.querySelectorAll(`[id="${hostId}"]`))) {
      old.disposeTitlePanel?.();
      old.remove();
    }
    if (!((bodyMode || recoveryMode) ? /^https:\/\/medium\.com\/p\/fd6c1824c4de\/edit(?:[?#]|$)/
      .test(location.href) : onEditor(location)) || !doc.body) return null;
    const host = doc.createElement("div");
    host.id = hostId;
    const shadow = host.attachShadow({ mode: "open" });
    const style = doc.createElement("style");
    style.textContent = `:host { all: initial; position: fixed; right: 24px; bottom: ${recoveryMode ? "100px" : "24px"};
      z-index: 2147483647; pointer-events: auto; color-scheme: light; }
      .panel { box-sizing: border-box; width: 286px; max-width: calc(100vw - 48px);
        padding: 14px; border: 1px solid #9cbbdb; border-radius: 10px;
        background: #f5f9ff; color: #172b46; box-shadow: 0 4px 18px #172b462e;
        font: 13px/1.45 system-ui, sans-serif; pointer-events: auto; }
      button { box-sizing: border-box; width: 100%; padding: 9px; border: 0;
        border-radius: 6px; background: #244b78; color: white;
        font: 600 13px system-ui, sans-serif; pointer-events: auto; cursor: pointer; }
      button:disabled { opacity: .55; cursor: not-allowed; }`;
    const panel = doc.createElement("section");
    panel.className = "panel";
    panel.setAttribute("aria-label", recoveryMode ? "Publish to All Medium body recovery control" :
      bodyMode ? "Publish to All Medium body control" :
      "Publish to All Medium title control");
    const button = doc.createElement("button");
    button.type = "button";
    button.textContent = recoveryMode ? "Resume verified Medium body" :
      bodyMode ? "Fill verified Medium body" :
      "Fill verified Medium title";
    panel.append(button);
    shadow.append(style, panel);
    doc.body.append(host);
    const currentGeneration = ++generation;
    let attached = false, disposed = false, busy = false;
    let invocations = 0, pointerReceived = false, clickReceived = false;
    let currentReadiness = null;
    function refresh() {
      if (disposed) return null;
      currentReadiness = readiness();
      const enabled = currentReadiness?.ready === true && !busy;
      button.disabled = !enabled;
      if (enabled) button.removeAttribute("disabled");
      else button.setAttribute("disabled", "");
      button.setAttribute("aria-disabled", enabled ? "false" : "true");
      return currentReadiness;
    }
    const onPointer = () => { if (!disposed) pointerReceived = true; };
    const onClick = async () => {
      if (disposed || busy) return;
      clickReceived = true;
      invocations++;
      const fresh = refresh();
      if (!fresh?.ready) return;
      busy = true;
      refresh();
      try { await activate(fresh); }
      finally { busy = false; refresh(); }
    };
    button.addEventListener("pointerdown", onPointer);
    button.addEventListener("click", onClick);
    attached = true;
    host.titleControl = button;
    if (bodyMode) host.bodyControl = button;
    if (recoveryMode) host.recoveryControl = button;
    host.titleGeneration = currentGeneration;
    host.refreshTitlePanel = refresh;
    host.getTitleDiagnostics = () => {
      const logical = refresh();
      const dom = domState(doc, host, panel, button);
      return { logicalEligibility: logical?.ready ? "READY" : "NOT_READY",
        logicalReason: logical?.reason || "None", ...dom,
        uiSafetyReason: logical?.ready && dom.interactability !== "ENABLED" ?
          dom.blockingReason : "None",
        currentHostUnique: doc.querySelectorAll(`[id="${hostId}"]`).length === 1,
        currentControlGeneration: currentGeneration,
        listenerAttached: attached && !disposed,
        listenerCount: attached && !disposed ? 1 : 0,
        listenerGeneration: currentGeneration,
        handlerInvocationCount: invocations,
        pointerEventReceived: pointerReceived,
        clickEventReceived: clickReceived };
    };
    host.disposeTitlePanel = () => {
      if (disposed) return;
      disposed = true;
      button.removeEventListener("pointerdown", onPointer);
      button.removeEventListener("click", onClick);
      attached = false;
    };
    if (bodyMode || recoveryMode) {
      host.refreshBodyPanel = refresh;
      host.getBodyDiagnostics = host.getTitleDiagnostics;
      host.disposeBodyPanel = host.disposeTitlePanel;
    }
    if (recoveryMode) {
      host.refreshRecoveryPanel = refresh;
      host.getRecoveryDiagnostics = host.getTitleDiagnostics;
      host.disposeRecoveryPanel = host.disposeTitlePanel;
    }
    refresh();
    return host;
  }

  function mountBody(doc, location, readiness, activate) {
    return mount(doc, location, readiness, activate,
      { bodyMode: true, hostId: "publish-to-all-medium-body-controls" });
  }

  function mountRecovery(doc, location, readiness, activate) {
    return mount(doc, location, readiness, activate,
      { recoveryMode: true, hostId: "publish-to-all-medium-recovery-controls" });
  }

  const api = { mount, mountBody, mountRecovery, domState, onEditor };
  root.MediumTitleControls = api;
  if (typeof module === "object" && module.exports) module.exports = api;
})(globalThis);

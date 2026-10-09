"use strict";

const assert = require("node:assert/strict");
const { createHash } = require("node:crypto");
const test = require("node:test");
const fill = require("../medium-fill.js");

const DEFAULT = "http://www.yoursite.org/your-post";
const SOURCE = "https://cyporter.substack.com/p/saturation-of-artificial-intelligence-f8d";
const FINGERPRINT = "2a5f676c8743eda141101e793cc4ce988ba6f029d0805c0498271bd072937349";
const sha = value => createHash("sha256").update(value).digest("hex");

function fixture({ nativeEvents = [], replacement = SOURCE, expectedFingerprint = FINGERPRINT,
    switchOnFocus = false, inputOnFocus = false, inputEventAvailable = true,
    initialValue = DEFAULT } = {}) {
  const listeners = new Map();
  let currentEditor;
  const editor = {
    tagName: "DIV", textContent: initialValue,
    getAttribute: name => name === "contenteditable" ? "true" : null,
    addEventListener(type, callback) {
      if (!listeners.has(type)) listeners.set(type, new Set());
      listeners.get(type).add(callback);
    },
    removeEventListener(type, callback) { listeners.get(type)?.delete(callback); },
    dispatchEvent(event) {
      Object.defineProperty(event, "target", { value: event.target || editor,
        configurable: true });
      for (const callback of listeners.get(event.type) || []) callback(event);
      return true;
    },
    focus() {
      if (switchOnFocus) currentEditor = { ...editor };
      if (inputOnFocus) editor.dispatchEvent({ type: "input", bubbles: true,
        composed: true, isTrusted: true, inputType: "insertText", data: "x" });
    }
  };
  currentEditor = editor;
  const calls = { resolve: 0, commands: 0, ranges: 0 };
  const doc = {
    defaultView: inputEventAvailable ? { InputEvent: class {
      constructor(type, options) { Object.assign(this, { type, isTrusted: false,
        cancelable: false }, options); }
    } } : {},
    createRange: () => ({ selectNodeContents(node) {
      assert.equal(node, editor); calls.ranges += 1;
    } }),
    getSelection: () => ({ removeAllRanges() {}, addRange() {} }),
    execCommand(command, ui, value) {
      assert.equal(command, "insertText");
      assert.equal(ui, false);
      assert.equal(value, SOURCE);
      calls.commands += 1;
      editor.textContent = replacement;
      for (const event of nativeEvents) editor.dispatchEvent({ type: "input",
        bubbles: true, composed: true, cancelable: false,
        isTrusted: true, inputType: "insertText", data: value, ...event });
      return true;
    }
  };
  const result = fill.editSource(doc, {
    resolveEditor: () => { calls.resolve += 1; return currentEditor; },
    verifyDefault: target => target === editor && target.textContent === DEFAULT,
    sourceUrl: SOURCE, fingerprint: expectedFingerprint,
    normalize: value => value.trim(), hash: sha
  });
  return { result, calls, editor, listeners };
}

test("fresh editor identity and verified default are required before mutation", () => {
  const wrongDefault = fixture({ initialValue: "https://other.example/story" });
  assert.equal(wrongDefault.calls.commands, 0);
  assert.equal(wrongDefault.result.attempted, false);
  const stale = fixture({ switchOnFocus: true });
  assert.equal(stale.calls.resolve >= 2, true);
  assert.equal(stale.calls.commands, 0);
  assert.equal(stale.result.synchronizationStatus, "UNVERIFIED");
  for (const set of stale.listeners.values()) assert.equal(set.size, 0);
  const other = fixture({ replacement: "different" });
  assert.equal(other.result.exactDom, false);
  assert.equal(other.result.syntheticInputFallbackUsed, false);
});

test("trusted bubbling editor input is observed once without fallback", () => {
  const { result, calls, listeners } = fixture({ nativeEvents: [{}] });
  assert.equal(calls.commands, 1);
  assert.equal(result.commandSucceeded, true);
  assert.equal(result.synchronizationStatus, "VERIFIED");
  assert.equal(result.browserInputObserved, true);
  assert.equal(result.syntheticInputFallbackUsed, false);
  assert.equal(result.counts.input, 1);
  assert.equal(result.counts.trustedInput, 1);
  for (const set of listeners.values()) assert.equal(set.size, 0);
});

test("exact DOM without native input dispatches one bubbling fallback", () => {
  const { result } = fixture();
  assert.equal(result.exactDom, true);
  assert.equal(result.fingerprintMatch, true);
  assert.equal(result.browserInputObserved, false);
  assert.equal(result.syntheticInputFallbackUsed, true);
  assert.equal(result.applicationInputSignalDelivered, true);
  assert.equal(result.counts.input, 1);
  assert.equal(result.counts.syntheticInput, 1);
  assert.deepEqual(result.events.map(event => [event.bubbles, event.composed,
    event.inputType, event.dataLength, event.targetIsSelectedEditor]),
    [[true, true, "insertText", SOURCE.length, true]]);
});

test("wrong target and non-bubbling input require the fallback", () => {
  for (const nativeEvent of [{ target: {} }, { bubbles: false }]) {
    const { result } = fixture({ nativeEvents: [nativeEvent] });
    assert.equal(result.browserInputObserved, false);
    assert.equal(result.syntheticInputFallbackUsed, true);
    assert.equal(result.counts.input, 2);
    assert.equal(result.counts.syntheticInput, 1);
    assert.equal(result.synchronizationStatus, "VERIFIED");
  }
});

test("input emitted during focus does not prove replacement synchronization", () => {
  const { result } = fixture({ inputOnFocus: true });
  assert.equal(result.counts.input, 2);
  assert.equal(result.browserInputObserved, false);
  assert.equal(result.syntheticInputFallbackUsed, true);
});

test("failed DOM or fingerprint stays unverified without fallback", () => {
  for (const options of [{ replacement: "wrong" },
    { expectedFingerprint: "0".repeat(64) }]) {
    const { result } = fixture(options);
    assert.equal(result.synchronizationStatus, "UNVERIFIED");
    assert.equal(result.applicationInputSignalDelivered, false);
    assert.equal(result.syntheticInputFallbackUsed, false);
  }
});

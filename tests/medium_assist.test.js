"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const {clipboardItemFor} = require("../src/publish_to_all/medium_assist.js");

class Item {
  constructor(parts) { this.parts = parts; }
}

test("body clipboard item carries rich HTML and readable plain text", async () => {
  const item = clipboardItemFor("body", Promise.resolve({
    html: "<h1>01</h1>\n<p>Paragraph</p>", plain: "01\n\nParagraph"
  }), Item, Blob);
  assert.deepEqual(Object.keys(item.parts).sort(), ["text/html", "text/plain"]);
  assert.equal((await item.parts["text/html"]).type, "text/html");
  assert.equal(await (await item.parts["text/html"]).text(), "<h1>01</h1>\n<p>Paragraph</p>");
  assert.equal(await (await item.parts["text/plain"]).text(), "01\n\nParagraph");
});

test("title and subtitle clipboard items carry plain text only", async () => {
  for (const kind of ["title", "subtitle"]) {
    const item = clipboardItemFor(kind, Promise.resolve({plain: "Prepared text"}), Item, Blob);
    assert.deepEqual(Object.keys(item.parts), ["text/plain"]);
    assert.equal(await (await item.parts["text/plain"]).text(), "Prepared text");
  }
});

test("a rejected source guard produces no clipboard content", async () => {
  const guard = Promise.reject(new Error("SOURCE_CHANGED_SINCE_PREPARATION"));
  const item = clipboardItemFor("body", guard, Item, Blob);
  await assert.rejects(item.parts["text/html"], /SOURCE_CHANGED_SINCE_PREPARATION/);
  await assert.rejects(item.parts["text/plain"], /SOURCE_CHANGED_SINCE_PREPARATION/);
});

test("the page makes only a local copy request after a user click", async () => {
  let click;
  const status = {textContent: ""};
  const requests = [];
  const writes = [];
  const button = {dataset: {copy: "body"}, addEventListener: (_event, handler) => { click = handler; }};
  const script = fs.readFileSync(path.join(__dirname, "../src/publish_to_all/medium_assist.js"), "utf8");
  vm.runInNewContext(script, {
    Blob, ClipboardItem: Item, window: {ClipboardItem: Item},
    document: {
      querySelector: () => ({content: "local-token"}),
      getElementById: () => status,
      querySelectorAll: () => [button],
    },
    navigator: {clipboard: {write: async items => { writes.push(items[0]); }}},
    fetch: async (url, options) => {
      requests.push([url, options.method, options.headers["X-Assist-Token"]]);
      return {ok: true, json: async () => ({html: "<h1>01</h1>", plain: "01"})};
    },
  });
  assert.deepEqual(requests, []);
  await click();
  assert.deepEqual(requests, [["/copy/body", "POST", "local-token"]]);
  assert.equal(writes.length, 1);
  assert.deepEqual(Object.keys(writes[0].parts).sort(), ["text/html", "text/plain"]);
  assert.equal(status.textContent, "Copied");
});

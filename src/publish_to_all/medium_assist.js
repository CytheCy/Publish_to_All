"use strict";

// Promise-valued ClipboardItem parts let write() begin within the user's click.
function clipboardItemFor(kind, payloadPromise, ClipboardItemType = ClipboardItem,
                          BlobType = Blob) {
  const plain = payloadPromise.then(payload =>
    new BlobType([payload.plain], {type: "text/plain"}));
  if (kind === "body") {
    const html = payloadPromise.then(payload =>
      new BlobType([payload.html], {type: "text/html"}));
    return new ClipboardItemType({"text/html": html, "text/plain": plain});
  }
  return new ClipboardItemType({"text/plain": plain});
}

if (typeof module !== "undefined") module.exports = {clipboardItemFor};

if (typeof document !== "undefined") {
  const token = document.querySelector('meta[name="assist-token"]').content;
  const status = document.getElementById("copy-status");
  for (const button of document.querySelectorAll("button[data-copy]")) {
    button.addEventListener("click", async () => {
      const kind = button.dataset.copy;
      status.textContent = "Preparing copy…";
      if (!navigator.clipboard || !navigator.clipboard.write || !window.ClipboardItem) {
        status.textContent = "Rich clipboard is unavailable in this browser. Open this page in a current browser.";
        return;
      }
      // This local request rechecks the exact source bytes before returning any copy data.
      const payloadPromise = fetch(`/copy/${kind}`, {
        method: "POST", cache: "no-store", headers: {"X-Assist-Token": token}
      }).then(async response => {
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.code === "SOURCE_CHANGED_SINCE_PREPARATION"
          ? "SOURCE_CHANGED_SINCE_PREPARATION: Prepared source changed. Rerun medium-assist before copying."
          : (payload.error || "Copy failed."));
        return payload;
      });
      try {
        await navigator.clipboard.write([clipboardItemFor(kind, payloadPromise)]);
        status.textContent = "Copied";
      } catch (error) {
        status.textContent = error.message || "Copy failed. Check clipboard permission and try again.";
      }
    });
  }
}

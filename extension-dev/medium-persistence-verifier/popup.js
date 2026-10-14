"use strict";

const button = document.getElementById("run");
const result = document.getElementById("result");
button.addEventListener("click", async () => {
  button.disabled = true;
  result.textContent = "One browser-managed authenticated GET pending";
  try {
    result.textContent = JSON.stringify(await chrome.runtime.sendMessage({
      type: "VERIFY_PRODUCTION_PERSISTENCE"
    }), null, 2);
  } catch {
    result.textContent = "VERIFIER_MESSAGE_FAILED";
  }
});

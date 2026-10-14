"use strict";

const buttons = [document.getElementById("run-a"), document.getElementById("run-b")];
const result = document.getElementById("result");
for (const [index, button] of buttons.entries()) {
  button.addEventListener("click", async () => {
    for (const choice of buttons) choice.disabled = true;
    result.textContent = "One browser-managed authenticated request attempted: pending";
    try {
      const report = await chrome.runtime.sendMessage({
        type: index === 0 ? "RUN_DRAFT_A" : "RUN_DRAFT_B"
      });
      result.textContent = JSON.stringify(report, null, 2);
    } catch {
      result.textContent = "PROBE_MESSAGE_FAILED";
    }
  });
}

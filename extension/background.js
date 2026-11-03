// Service worker: thin wiring between the popup and the sync controller.
import { BACKEND_URL } from "./src/config.js";
import { createController } from "./src/controller.js";

const controller = createController({
  chrome,
  fetch: (...args) => fetch(...args),
  backendUrl: BACKEND_URL,
  now: () => Date.now(),
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
});

controller.recoverInterrupted().catch(() => {});

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (sender?.id !== chrome.runtime.id) return false;
  const pending = controller.handle(msg);
  if (!pending) return false;
  pending.then(sendResponse).catch(() => sendResponse({ ok: false, error: "Request failed." }));
  return true; // keep the channel open for the async response
});

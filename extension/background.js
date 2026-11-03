// Service worker: orchestration only. Step 0 = ping the backend; SYNC lands in step 6.
import { BACKEND_URL } from "./src/config.js";

async function ping() {
  const r = await fetch(`${BACKEND_URL}/health`);
  return { ok: r.ok, status: r.status, body: await r.json() };
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  const handlers = {
    PING: ping,
    SYNC: async () => ({ ok: false, error: "not implemented yet (roadmap step 6)" }),
  };
  const handler = handlers[msg?.type];
  if (!handler) return false;
  handler()
    .then(sendResponse)
    .catch((e) => sendResponse({ ok: false, error: String(e) }));
  return true; // keep the channel open for the async response
});

// Service worker: thin wiring between the popup, the daily auto-sync and the sync controller.
import { ALARM, ALARM_MINUTES, IDLE_SECONDS, badge, pickDue } from "./src/auto.js";
import { createController } from "./src/controller.js";
import { createIndexer } from "./src/local/indexer.js";
import { localStore } from "./src/local/store.js";

// Lectures are stored in this browser (IndexedDB) and indexed in an offscreen document.
const indexer = createIndexer(chrome);
const store = localStore({ onSaved: () => indexer.kick() });

function showBadge(all) {
  const b = badge(all);
  chrome.action?.setBadgeText?.({ text: b.text })?.catch?.(() => {});
  chrome.action?.setBadgeBackgroundColor?.({ color: b.color })?.catch?.(() => {});
}

// A worker with no extension events for 30 seconds is stopped, even mid-fetch. Any extension call
// resets that timer, so a cheap one runs while a sync is going.
function keepAlive() {
  const id = setInterval(() => chrome.runtime.getPlatformInfo?.().catch?.(() => {}), 20000);
  return () => clearInterval(id);
}

const controller = createController({
  chrome,
  fetch: (...args) => fetch(...args),
  store,
  now: () => Date.now(),
  sleep: (ms) => new Promise((resolve) => setTimeout(resolve, ms)),
  keepAlive,
  onAuto: showBadge,
  onIdle: () => tick().catch(() => {}),
});

controller.recoverInterrupted().catch(() => {});

// Alarms survive the worker stopping; creating one with the same name replaces it.
async function ensureAlarm() {
  if (!chrome.alarms) return;
  const existing = await chrome.alarms.get(ALARM);
  if (existing?.periodInMinutes !== ALARM_MINUTES) await chrome.alarms.create(ALARM, { delayInMinutes: 1, periodInMinutes: ALARM_MINUTES });
}
ensureAlarm().catch(() => {});
controller.readAuto().then(showBadge).catch(() => {});

// Starts the next course that still needs today's sync, but only while someone is at the
// computer, so the first sync of the day happens when it's first used, not at 5 AM on a desk.
async function tick() {
  if (controller.isRunning()) return;
  if (chrome.idle && (await chrome.idle.queryState(IDLE_SECONDS)) !== "active") return;
  await controller.recoverInterrupted();
  const courseId = pickDue(await controller.readAuto(), Date.now());
  if (courseId) controller.startAuto(courseId);
}

chrome.alarms?.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM) tick().catch(() => {});
});
chrome.idle?.setDetectionInterval?.(IDLE_SECONDS);
chrome.idle?.onStateChanged.addListener((state) => {
  if (state === "active") tick().catch(() => {});
});
chrome.runtime.onStartup?.addListener(() => tick().catch(() => {}));

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (sender?.id !== chrome.runtime.id) return false;
  if (msg?.target === "offscreen") return false; // for the offscreen document, not the worker
  // The app page asks for indexing when it opens (catches up after a worker restart); the
  // offscreen document reports when nothing is left, so it can be closed.
  if (msg?.type === "INGEST") {
    indexer.kick().then(() => sendResponse({ ok: true }), () => sendResponse({ ok: false }));
    return true;
  }
  if (msg?.type === "INGEST_DONE") {
    indexer.close().catch(() => {});
    return false;
  }
  const pending = controller.handle(msg);
  if (!pending) return false;
  pending.then(sendResponse).catch(() => sendResponse({ ok: false, error: "Request failed." }));
  return true; // keep the channel open for the async response
});

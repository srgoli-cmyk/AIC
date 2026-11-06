// Service worker: thin wiring between the popup, the daily auto-sync and the sync controller.
import { ALARM, ALARM_MINUTES, IDLE_SECONDS, badge, pickDue } from "./src/auto.js";
import { BACKEND_URL } from "./src/config.js";
import { createController } from "./src/controller.js";

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
  backendUrl: BACKEND_URL,
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
  const pending = controller.handle(msg);
  if (!pending) return false;
  pending.then(sendResponse).catch(() => sendResponse({ ok: false, error: "Request failed." }));
  return true; // keep the channel open for the async response
});

// Connects messages from the popup to the sync. Progress and the last summary live in
// chrome.storage.local so the popup can close and reopen mid-sync. Only counts, the course id,
// timestamps, fixed messages and stage names are stored.
import { confirmBackend } from "./backend.js";
import { parseCourseId } from "./canvas.js";
import { MESSAGES, SyncError, messageFor } from "./errors.js";
import { runSync } from "./sync.js";

const STATE_KEYS = ["status", "stage", "done", "total", "courseId", "message"];

function cleanState(s) {
  const out = {};
  for (const k of STATE_KEYS) {
    const v = s[k];
    if (typeof v === "number" || typeof v === "string") out[k] = v;
  }
  return out;
}

export function createController({ chrome, fetch, backendUrl, now, sleep }) {
  let running = false;
  const save = (state) => chrome.storage.local.set({ syncState: cleanState(state) });

  async function ping() {
    try {
      await confirmBackend(fetch, backendUrl);
      const r = await fetch(`${backendUrl}/health`);
      return { ok: r.ok, status: r.status, body: await r.json() };
    } catch (e) {
      return { ok: false, error: e instanceof SyncError ? e.message : "Backend check failed." };
    }
  }

  async function run(tabUrl, courseId) {
    let state = { status: "running", stage: "backend", done: 0, total: 0, courseId };
    const report = (patch) => {
      state = { ...state, ...patch };
      return Promise.resolve(save(state)).catch(() => {});
    };
    try {
      await save(state);
      const summary = await runSync({
        tabUrl,
        fetch,
        tabs: chrome.tabs,
        scripting: chrome.scripting,
        backendUrl,
        now,
        sleep,
        report: (patch) => void report(patch),
        // Remembered outside the worker's memory, so a restarted worker can close the tab.
        tabOpened: (id) => chrome.storage.session.set({ syncTab: id }),
        tabClosed: () => chrome.storage.session.remove("syncTab"),
      });
      await chrome.storage.local.set({
        lastSync: summary,
        syncState: cleanState({ status: "done", courseId }),
      });
    } catch (e) {
      await save({ status: "error", courseId, message: messageFor(e) });
    } finally {
      running = false;
    }
  }

  async function start() {
    if (running) return { ok: false, error: MESSAGES.BUSY };
    running = true;
    let tabUrl;
    try {
      const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
      tabUrl = tab?.url;
    } catch {
      tabUrl = undefined;
    }
    const courseId = parseCourseId(tabUrl);
    if (!courseId) {
      running = false;
      return { ok: false, error: MESSAGES.NOT_COURSE_PAGE };
    }
    void run(tabUrl, courseId); // keeps going after the popup closes
    return { ok: true, started: true };
  }

  // A worker that was stopped mid-sync cannot finish it: close its tab and mark the state.
  // Does nothing once a run is under way in this worker.
  async function recoverInterrupted() {
    if (running) return;
    const { syncState } = await chrome.storage.local.get("syncState");
    const { syncTab } = await chrome.storage.session.get("syncTab");
    if (running) return;
    if (Number.isInteger(syncTab)) {
      try {
        await chrome.tabs.remove(syncTab);
      } catch {
        // Already closed.
      }
      await chrome.storage.session.remove("syncTab");
    }
    if (syncState?.status === "running") {
      await save({ status: "error", courseId: syncState.courseId, message: MESSAGES.INTERRUPTED });
    }
  }

  // Opening the popup wakes a sleeping worker; recovery runs before the popup reads the state.
  async function status() {
    try {
      await recoverInterrupted();
    } catch {
      // The state is shown as stored.
    }
    return { ok: true, running };
  }

  function handle(msg) {
    if (msg?.type === "PING") return ping();
    if (msg?.type === "SYNC") return start();
    if (msg?.type === "STATUS") return status();
    return null;
  }

  return { handle, start, recoverInterrupted };
}

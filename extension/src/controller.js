// Connects messages from the popup to the sync. Progress and the last summary live in
// chrome.storage.local so the popup can close and reopen mid-sync. Only counts, the course id,
// timestamps, fixed messages and stage names are stored.
import { AUTO_KEY, afterFailure, afterSuccess } from "./auto.js";
import { confirmBackend } from "./backend.js";
import { parseCourseId } from "./canvas.js";
import { CANVAS_ORIGIN } from "./config.js";
import { MESSAGES, SyncError, messageFor } from "./errors.js";
import { runSync } from "./sync.js";
import { runAllSync } from "./syncAll.js";

const STATE_KEYS = ["status", "stage", "done", "total", "courseId", "message", "mode", "scope"];

function cleanState(s) {
  const out = {};
  for (const k of STATE_KEYS) {
    const v = s[k];
    if (typeof v === "number" || typeof v === "string") out[k] = v;
  }
  return out;
}

export function createController({ chrome, fetch, backendUrl, now, sleep, keepAlive, onAuto, onIdle }) {
  let running = false;
  const save = (state) => chrome.storage.local.set({ syncState: cleanState(state) });

  async function readAuto() {
    const { [AUTO_KEY]: all } = await chrome.storage.local.get(AUTO_KEY);
    return all && typeof all === "object" ? all : {};
  }

  async function writeAuto(all) {
    await chrome.storage.local.set({ [AUTO_KEY]: all });
    try {
      onAuto?.(all);
    } catch {
      // The badge is cosmetic.
    }
  }

  async function updateAuto(courseId, fn) {
    const all = await readAuto();
    const next = fn(all[courseId]);
    if (next) all[courseId] = next;
    else delete all[courseId];
    await writeAuto(all);
    return all;
  }

  async function ping() {
    try {
      await confirmBackend(fetch, backendUrl);
      const r = await fetch(`${backendUrl}/health`);
      return { ok: r.ok, status: r.status, body: await r.json() };
    } catch (e) {
      return { ok: false, error: e instanceof SyncError ? e.message : "Backend check failed." };
    }
  }

  // The courses the last podcast sync covered, so a failed run can mark them all.
  async function podcastCourseIds() {
    return Object.entries(await readAuto())
      .filter(([, c]) => c?.via === "podcast")
      .map(([id]) => Number(id));
  }

  async function run(tabUrl, courseId, { auto = false, all = false } = {}) {
    let state = { status: "running", stage: "backend", done: 0, total: 0, courseId, ...(auto ? { mode: "auto" } : {}), ...(all ? { scope: "all" } : {}) };
    const stopKeepAlive = keepAlive?.();
    const report = (patch) => {
      state = { ...state, ...patch };
      return Promise.resolve(save(state)).catch(() => {});
    };
    try {
      await save(state);
      const sync = all ? runAllSync : runSync;
      const summary = await sync({
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
        syncState: cleanState({ status: "done", courseId, ...(all ? { scope: "all" } : {}) }),
      });
      if (all) {
        const covered = new Set(summary.courses.map((c) => c.courseId));
        const t = now();
        const book = await readAuto();
        for (const c of summary.courses) book[c.courseId] = afterSuccess(book[c.courseId], { ...c, via: "podcast" }, t);
        // A class that left My Podcasts still had its morning try.
        for (const id of await podcastCourseIds()) if (!covered.has(id) && book[id]) book[id] = { ...book[id], lastAttempt: t };
        await writeAuto(book);
      } else {
        await updateAuto(courseId, (prev) => afterSuccess(prev, summary, now()));
      }
    } catch (e) {
      await save({ status: "error", courseId, message: messageFor(e), ...(auto ? { mode: "auto" } : {}), ...(all ? { scope: "all" } : {}) });
      try {
        const code = e instanceof SyncError ? e.code : "UNKNOWN";
        if (all) {
          const book = await readAuto();
          for (const id of await podcastCourseIds()) {
            const next = afterFailure(book[id], code, now());
            if (next) book[id] = next;
          }
          await writeAuto(book);
        } else {
          await updateAuto(courseId, (prev) => afterFailure(prev, code, now()));
        }
      } catch {
        // The next tick tries again.
      }
    } finally {
      running = false;
      try {
        stopKeepAlive?.();
      } catch {
        // Nothing to stop.
      }
      // Lets the worker start the next course that still needs today's sync.
      if (auto) Promise.resolve().then(() => onIdle?.()).catch(() => {});
    }
  }

  /** Starts a sync nobody clicked for. False when one is already running. */
  // A course kept by the podcast sync is updated by syncing every class at once.
  function startAuto(courseId) {
    if (running || !Number.isSafeInteger(courseId) || courseId < 1) return false;
    running = true;
    void (async () => {
      let viaPodcast = false;
      try {
        viaPodcast = (await readAuto())[courseId]?.via === "podcast";
      } catch {
        // Treated as a Canvas course.
      }
      if (viaPodcast) await run(null, null, { auto: true, all: true });
      else await run(`${CANVAS_ORIGIN}/courses/${courseId}`, courseId, { auto: true });
    })();
    return true;
  }

  async function setAuto(msg) {
    // { all: true } switches every class the podcast sync keeps.
    if (msg?.all === true) {
      if (typeof msg.on !== "boolean") return { ok: false, error: "Bad request." };
      const book = await readAuto();
      const ids = Object.keys(book).filter((id) => book[id]?.via === "podcast");
      for (const id of ids) book[id] = { ...book[id], on: msg.on, loginNeeded: false, failed: false };
      await writeAuto(book);
      return { ok: ids.length > 0, on: msg.on };
    }
    const courseId = msg?.courseId;
    if (!Number.isSafeInteger(courseId) || courseId < 1 || typeof msg.on !== "boolean") return { ok: false, error: "Bad request." };
    const all = await updateAuto(courseId, (prev) => (prev ? { ...prev, on: msg.on, loginNeeded: false, failed: false } : null));
    return { ok: Boolean(all[courseId]), on: all[courseId]?.on === true };
  }

  async function seen() {
    const all = await readAuto();
    for (const c of Object.values(all)) if (c) c.unseen = 0;
    await writeAuto(all);
    return { ok: true };
  }

  function startAll() {
    if (running) return Promise.resolve({ ok: false, error: MESSAGES.BUSY });
    running = true;
    void run(null, null, { all: true }); // keeps going after the popup closes
    return Promise.resolve({ ok: true, started: true });
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
    if (msg?.type === "SYNC_ALL") return startAll();
    if (msg?.type === "STATUS") return status();
    if (msg?.type === "AUTO_SET") return setAuto(msg);
    if (msg?.type === "SEEN") return seen();
    return null;
  }

  return { handle, start, startAll, startAuto, readAuto, recoverInterrupted, isRunning: () => running };
}

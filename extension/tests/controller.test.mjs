import { test } from "node:test";
import assert from "node:assert/strict";
import { createController } from "../src/controller.js";
import { courseLink, formatLastSync, formatState } from "../src/summary.js";
import { COURSE, baseDeps, fakeClock, makeKaf, makeScripting, makeServer, makeTabs, tracker } from "./helpers.mjs";

function setup({ url = `https://canvas.ucsd.edu/courses/${COURSE}`, server = {}, gate } = {}) {
  const t = tracker();
  const k = makeKaf({ tracker: tracker() });
  const s = makeServer({ tracker: t, ...server });
  const scripting = makeScripting([{ frameId: 1, env: k.env }], t);
  if (gate) {
    const real = scripting.executeScript;
    scripting.executeScript = async (o) => {
      await gate;
      return real(o);
    };
  }
  const store = {};
  const session = {};
  const sessionWrites = [];
  const writes = [];
  const clock = fakeClock();
  const chrome = {
    tabs: { ...makeTabs(), query: async () => [{ url }] },
    scripting,
    storage: {
      session: {
        set: async (items) => void (sessionWrites.push(JSON.stringify(items)), Object.assign(session, items)),
        get: async (key) => ({ [key]: session[key] }),
        remove: async (key) => void delete session[key],
      },
      local: {
        set: async (items) => void (writes.push(JSON.stringify(items)), Object.assign(store, JSON.parse(JSON.stringify(items)))),
        get: async (key) => (typeof key === "string" ? { [key]: store[key] } : { ...store }),
      },
    },
  };
  const controller = createController({ chrome, fetch: s.fetch, backendUrl: "http://localhost:8000", now: baseDeps({ server: s, scripting }).now, sleep: clock.sleep });
  return { controller, store, session, sessionWrites, writes, s, chrome };
}

const settle = async (store) => {
  for (let i = 0; i < 400 && store.syncState?.status === "running" || i < 3; i++) await new Promise((r) => setTimeout(r, 1));
};

test("SYNC starts in the background and finishes with a summary in storage", async () => {
  const { controller, store } = setup();
  assert.deepEqual(await controller.handle({ type: "SYNC" }), { ok: true, started: true });
  await settle(store);
  assert.equal(store.syncState.status, "done");
  assert.equal(store.lastSync.lectures, 3);
  assert.equal(store.lastSync.pending, 0);
  assert.equal(store.lastSync.courseId, COURSE);
});

test("a second click while syncing reports that a sync is running", async () => {
  let open;
  const gate = new Promise((r) => (open = r));
  const { controller, store, s } = setup({ gate });
  assert.equal((await controller.handle({ type: "SYNC" })).ok, true);
  const second = await controller.handle({ type: "SYNC" });
  assert.deepEqual(second, { ok: false, error: "A sync is already running." });
  open();
  await settle(store);
  assert.equal(store.syncState.status, "done");
  assert.equal(s.requests.filter((r) => r.method === "POST").length, 1);
  assert.equal((await controller.handle({ type: "SYNC" })).ok, true, "a new sync can start after one ends");
  await settle(store);
});

test("two clicks at the same moment start one sync", async () => {
  const { controller, store, s } = setup();
  const [a, b] = await Promise.all([controller.handle({ type: "SYNC" }), controller.handle({ type: "SYNC" })]);
  assert.deepEqual([a.ok, b.ok].sort(), [false, true]);
  await settle(store);
  assert.equal(s.requests.filter((r) => r.method === "POST").length, 1);
});

test("not a course page: a clear message, nothing stored, nothing requested", async () => {
  const { controller, store, s, writes } = setup({ url: "https://example.com/" });
  const res = await controller.handle({ type: "SYNC" });
  assert.equal(res.ok, false);
  assert.match(res.error, /Open a Canvas course page/);
  assert.equal(s.requests.length, 0);
  assert.equal(writes.length, 0);
  assert.deepEqual(store, {});
});

test("a failed sync stores a fixed message and keeps the last summary", async () => {
  const { controller, store } = setup({ server: { backendTitle: "Other App" } });
  await controller.handle({ type: "SYNC" });
  await settle(store);
  assert.equal(store.syncState.status, "error");
  assert.match(store.syncState.message, /another app/);
});

test("storage holds only counts, ids, timestamps and fixed text; no secrets", async () => {
  const { controller, store, writes } = setup();
  await controller.handle({ type: "SYNC" });
  await settle(store);
  const text = writes.join("\n");
  assert.doesNotMatch(text, /djJ8/);
  assert.ok(!text.includes("SECRET"));
  assert.ok(!text.includes("-->"), "no captions");
  assert.ok(!text.includes("Lecture 1_"), "no titles");
  assert.ok(!text.includes("http"), "no URLs");
  assert.deepEqual(Object.keys(store.syncState).sort(), ["courseId", "status"]);
  assert.deepEqual(Object.keys(store.lastSync).sort(), ["code", "courseId", "finishedAt", "lectures", "notes", "pending", "skipped"]);
  for (const w of writes) for (const k of Object.keys(JSON.parse(w))) assert.ok(["syncState", "lastSync", "autoSync"].includes(k));
  assert.deepEqual(Object.keys(store.autoSync), [String(COURSE)]);
  assert.deepEqual(Object.keys(store.autoSync[COURSE]).sort(), ["code", "failed", "lastAttempt", "lastSuccess", "lectures", "loginNeeded", "on", "unseen"]);
});

test("a sync that was running when the worker stopped is marked interrupted", async () => {
  const { controller, store } = setup();
  store.syncState = { status: "running", stage: "captions", courseId: COURSE };
  await controller.recoverInterrupted();
  assert.equal(store.syncState.status, "error");
  assert.match(store.syncState.message, /interrupted/);
});

test("the background tab id is kept in session storage while it is open and removed after", async () => {
  const { controller, store, session, sessionWrites, chrome } = setup();
  await controller.handle({ type: "SYNC" });
  await settle(store);
  assert.deepEqual(sessionWrites, ['{"syncTab":1001}']);
  assert.equal(session.syncTab, undefined);
  assert.deepEqual(chrome.tabs.state.removed, [1001]);
});

test("recovery closes a leftover tab and marks the stuck state", async () => {
  const { controller, store, session, chrome } = setup();
  store.syncState = { status: "running", stage: "captions", done: 2, total: 9, courseId: COURSE };
  session.syncTab = 4242;
  await controller.recoverInterrupted();
  assert.deepEqual(chrome.tabs.state.removed, [4242]);
  assert.equal(session.syncTab, undefined);
  assert.equal(store.syncState.status, "error");
  assert.equal(store.syncState.stage, undefined, "no stale progress stays");
});

test("recovery does nothing once a run has started in this worker", async () => {
  let open;
  const gate = new Promise((r) => (open = r));
  const { controller, store, chrome } = setup({ gate });
  await controller.handle({ type: "SYNC" });
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(store.syncState.status, "running");
  await controller.recoverInterrupted();
  assert.equal(store.syncState.status, "running");
  assert.deepEqual(chrome.tabs.state.removed, []);
  open();
  await settle(store);
  assert.equal(store.syncState.status, "done");
});

test("STATUS runs recovery and reports whether a sync is running", async () => {
  const { controller, store } = setup();
  store.syncState = { status: "running", courseId: COURSE };
  assert.deepEqual(await controller.handle({ type: "STATUS" }), { ok: true, running: false });
  assert.equal(store.syncState.status, "error");
});

test("PING still works and refuses another app on the port", async () => {
  const ok = setup();
  assert.deepEqual(await ok.controller.handle({ type: "PING" }), { ok: true, status: 200, body: { status: "ok" } });
  const other = setup({ server: { backendTitle: "Other App" } });
  const res = await other.controller.handle({ type: "PING" });
  assert.equal(res.ok, false);
  assert.match(res.error, /another app/);
});

test("unknown messages are ignored", () => {
  assert.equal(setup().controller.handle({ type: "NOPE" }), null);
  assert.equal(setup().controller.handle(undefined), null);
});

test("popup text: summary line, progress, errors", () => {
  const last = { courseId: 12345, lectures: 22, pending: 2, finishedAt: "2026-11-03T12:00:00.000Z", notes: ["A note."], skipped: { NO_CAPTIONS: 2 } };
  const text = formatLastSync(last, () => "TIME");
  assert.match(text, /^Course 12345$/m);
  assert.match(text, /^22 lectures, 2 pending captions, last synced TIME$/m);
  assert.match(text, /^A note\.$/m);
  assert.match(text, /Skipped: NO_CAPTIONS x2/);
  assert.equal(formatLastSync(undefined), "Not synced yet.");
  assert.match(formatState({ status: "running", stage: "captions", done: 3, total: 10 }, undefined), /Reading captions: 3 of 10/);
  assert.match(formatState({ status: "error", message: "Sync failed." }, last, () => "T"), /^Sync failed: Sync failed\./);
});

test("popup: reads storage on open, re-renders on change, sync click shows refusals", async () => {
  const els = {};
  const element = (id) =>
    (els[id] ??= { id, textContent: "", handlers: {}, addEventListener(type, fn) { this.handlers[type] = fn; } });
  let listener;
  const sent = [];
  const order = [];
  globalThis.document = { getElementById: element };
  globalThis.chrome = {
    runtime: { getURL: (p) => `chrome-extension://me/${p}`, sendMessage: async (m) => (sent.push(m), order.push(m.type), m.type === "SYNC" ? { ok: false, error: "A sync is already running." } : { ok: true }) },
    storage: {
      local: { get: async () => (order.push("storage.get"), { lastSync: { courseId: 5, lectures: 1, pending: 0, finishedAt: "2026-11-03T12:00:00.000Z", notes: [], skipped: {} } }) },
      onChanged: { addListener: (fn) => (listener = fn) },
    },
  };
  await import("../popup.js");
  await new Promise((r) => setTimeout(r, 5));
  assert.match(els.status.textContent, /1 lectures, 0 pending captions, last synced/);
  listener({ syncState: { newValue: { status: "running", stage: "files", done: 0, total: 0 } } }, "local");
  assert.match(els.status.textContent, /Reading course files/);
  listener({ syncState: { newValue: { status: "running" } } }, "sync");
  await els.sync.handlers.click();
  assert.match(els.status.textContent, /already running/);
  await els.ping.handlers.click();
  assert.deepEqual(sent.map((m) => m.type), ["STATUS", "SEEN", "SYNC", "PING"], "the worker is asked about its status first");
  assert.deepEqual(order.slice(0, 3), ["STATUS", "SEEN", "storage.get"], "the state is read only after the worker answered");
  delete globalThis.document;
  delete globalThis.chrome;
});

test("the course link opens the search page on that course, or nothing without a valid course", () => {
  const base = "http://localhost:8000";
  assert.equal(courseLink({ courseId: 12345 }, base, "/app/"), "http://localhost:8000/app/?course=canvas-12345");
  assert.equal(courseLink(undefined, base, "/app/"), null);
  assert.equal(courseLink({ courseId: "12345" }, base, "/app/"), null);
  assert.equal(courseLink({ courseId: 0 }, base, "/app/"), null);
});

test("the last sync shows the course code when it has one", () => {
  const text = formatLastSync({ courseId: 12345, code: "MATH 20A", lectures: 3, pending: 0, finishedAt: "x", notes: [] }, () => "T");
  assert.match(text, /^MATH 20A$/m);
});

test("Canvas course codes are shortened for display", async () => {
  const { displayCode } = await import("../src/summary.js");
  assert.equal(displayCode("MATH018_FA26_001"), "MATH 018");
  assert.equal(displayCode("DSC010_FA26_001"), "DSC 010");
  assert.equal(displayCode("MATH 20A"), "MATH 20A");
  assert.equal(displayCode("ORG_HDSIOrientation_AY2627"), "ORG_HDSIOrientation_AY2627");
  assert.equal(displayCode(undefined), undefined);
});

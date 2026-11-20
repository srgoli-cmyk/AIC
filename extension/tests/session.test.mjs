// An expired or invalid session: any 401 from the backend means signed out. Synthetic data only.
import { test } from "node:test";
import assert from "node:assert/strict";
import { confirmBackend, confirmSession, getLectures, postSync } from "../src/backend.js";
import { createController } from "../src/controller.js";
import { MESSAGES, SyncError } from "../src/errors.js";
import { syncSlides } from "../src/slides.js";
import { runSync } from "../src/sync.js";
import { runAllSync } from "../src/syncAll.js";
import { COURSE, baseDeps, fakeClock, makeKaf, makeScripting, makeServer, makeTabs, res, tracker } from "./helpers.mjs";

const BASE = "http://localhost:8000";
const TOKEN = "tok_test";
const expired = (e) => e instanceof SyncError && e.code === "AUTH_EXPIRED";
const always401 = async (url) => (String(url).endsWith("/openapi.json") ? res({ info: { title: "AI Lecture Search" } }) : res({ detail: "x" }, { status: 401 }));

test("every backend call turns a 401 into AUTH_EXPIRED", async () => {
  await assert.rejects(confirmSession(always401, BASE, TOKEN), expired);
  await assert.rejects(getLectures(always401, BASE, COURSE, TOKEN), expired);
  await assert.rejects(postSync(always401, BASE, { lectures: [] }, TOKEN), expired);
});

test("a 401 on /openapi.json means another app: BACKEND_OTHER, and the token is kept", async () => {
  await assert.rejects(confirmBackend(async () => res("", { status: 401 }), BASE), (e) => e instanceof SyncError && e.code === "BACKEND_OTHER");
  const wrap = (f) => async (url, init) => (String(url).endsWith("/openapi.json") ? res("", { status: 401 }) : f(url, init));
  const { controller, store } = controllerSetup({}, {}, wrap);
  await controller.handle({ type: "SYNC" });
  await settle(store);
  assert.equal(store.authToken, "tok_test");
  assert.equal(store.syncState.message, MESSAGES.BACKEND_OTHER);
});

test("confirmSession sends the Bearer token without credentials and reports other failures", async () => {
  const s = makeServer();
  await confirmSession(s.fetch, BASE, TOKEN);
  assert.equal(s.requests[0].url, `${BASE}/auth/me`);
  assert.equal(s.requests[0].init.headers.Authorization, `Bearer ${TOKEN}`);
  assert.equal(s.requests[0].init.credentials, "omit");
  await assert.rejects(confirmSession(async () => res("", { status: 500 }), BASE, TOKEN), (e) => e.code === "BACKEND_REJECTED");
  await assert.rejects(confirmSession(async () => Promise.reject(new TypeError("x")), BASE, TOKEN), (e) => e.code === "BACKEND_DOWN");
});

// A backend where one path answers 401 and slide files exist.
function slidesWorld(failOn) {
  const fetch = async (url, init = {}) => {
    const u = new URL(url);
    const method = init.method || "GET";
    if (u.pathname === "/openapi.json") return res({ info: { title: "AI Lecture Search" } });
    if (u.origin === BASE) {
      if (failOn(u.pathname, method)) return res({ detail: "x" }, { status: 401 });
      if (u.pathname.endsWith("/documents") && method === "GET") return res(options.stored ?? []);
      return res({ status: "stored" });
    }
    if (u.pathname.endsWith("/public_url")) return res(`while(1);${JSON.stringify({ public_url: "https://inst-fs-pdx-prod.inscloudgate.net/files/1?t=x" })}`);
    return { ...res("%PDF-1.4 fake"), arrayBuffer: async () => new TextEncoder().encode("%PDF-1.4 fake").buffer };
  };
  const options = {};
  return { fetch, options };
}
const pdf = (id) => ({ id, display_name: `W${id}.pdf`, "content-type": "application/pdf", size: 100, modified_at: "2026-10-01T00:00:00Z" });
const runSlides = (w, files) => syncSlides({ fetchFn: w.fetch, backendUrl: BASE, courseId: COURSE, files, token: TOKEN });

test("slides: a 401 on the document list, an upload or a delete is AUTH_EXPIRED, not a counted failure", async () => {
  await assert.rejects(runSlides(slidesWorld((p, m) => p.endsWith("/documents") && m === "GET"), [pdf(1)]), expired);
  await assert.rejects(runSlides(slidesWorld((p, m) => m === "PUT"), [pdf(1)]), expired);
  const w = slidesWorld((p, m) => m === "DELETE");
  // Two revisions of one deck: the older stored copy is deleted once the newer one is current.
  w.options.stored = [{ doc_id: "canvas-file-1", version: "old" }];
  const newer = { ...pdf(2), display_name: "W1.pdf", modified_at: "2026-10-02T00:00:00Z" };
  const older = { ...pdf(1), display_name: "W1.pdf" };
  await assert.rejects(runSlides(w, [older, newer]), expired);
});

function kafSetup(server = {}) {
  const t = tracker();
  const k = makeKaf({ tracker: tracker() });
  const s = makeServer({ tracker: t, ...server });
  const scripting = makeScripting([{ frameId: 1, env: k.env }], t);
  const deps = { ...baseDeps({ server: s, scripting }), token: TOKEN };
  return { s, scripting, deps, tabs: deps.tabs };
}

const touchedCanvasOrKaltura = (s, scripting, tabs) =>
  s.requests.some((r) => !r.url.startsWith(BASE)) || scripting.calls.length > 0 || tabs.state.created.length > 0;

test("single sync: an expired token is caught by /auth/me before any Canvas or Kaltura request", async () => {
  const { s, scripting, deps, tabs } = kafSetup({ sessionExpired: true });
  await assert.rejects(runSync(deps), expired);
  assert.deepEqual(s.requests.map((r) => new URL(r.url).pathname), ["/openapi.json", "/auth/me"]);
  assert.equal(touchedCanvasOrKaltura(s, scripting, tabs), false);
});

test("single sync: a 401 later on lectures, /sync or documents is AUTH_EXPIRED", async () => {
  for (const path of [`/courses/canvas-${COURSE}/lectures`, "/sync", `/courses/canvas-${COURSE}/documents`]) {
    const { deps } = kafSetup({ expireOn: (p) => p === path });
    await assert.rejects(runSync(deps), expired, path);
  }
});

const COURSES = [
  { id: 999901, course_code: "TEST18_FA26_004" },
  { id: 999902, course_code: "TEST10_FA26_002" },
  { id: 999903, course_code: "TEST11_FA26_001" },
];

function allWorld({ expiredToken = false } = {}) {
  const requests = [];
  const fetch = async (url) => {
    const u = new URL(url);
    requests.push(String(url));
    if (u.pathname === "/openapi.json") return res({ info: { title: "AI Lecture Search" } });
    if (u.pathname === "/auth/me") return expiredToken ? res({ detail: "x" }, { status: 401 }) : res({ id: "usr_1" });
    if (u.pathname === "/api/v1/courses") return res(`while(1);${JSON.stringify(COURSES)}`);
    return res("", { status: 404 });
  };
  return { fetch, requests };
}

const allDeps = (w, runOne) => ({ fetch: w.fetch, backendUrl: BASE, token: TOKEN, now: () => 0, sleep: async () => {}, report: () => {}, runOne });

test("sync all: an expired token is caught by /auth/me before Canvas is asked for the course list", async () => {
  const w = allWorld({ expiredToken: true });
  let ran = 0;
  await assert.rejects(runAllSync(allDeps(w, async () => void ran++)), expired);
  assert.equal(ran, 0);
  assert.deepEqual(w.requests.map((r) => new URL(r).pathname), ["/openapi.json", "/auth/me"]);
});

test("sync all: the session is confirmed once, and the first AUTH_EXPIRED stops the run with no further Canvas tab", async () => {
  const w = allWorld();
  const tabs = makeTabs();
  const seen = [];
  await assert.rejects(
    runAllSync(
      allDeps(w, async (d) => {
        seen.push(d.tabUrl);
        assert.equal(d.sessionChecked, true);
        await tabs.create({ url: d.tabUrl });
        throw new SyncError("AUTH_EXPIRED");
      }),
    ),
    expired,
  );
  assert.equal(seen.length, 1);
  assert.equal(tabs.state.created.length, 1);
  assert.equal(w.requests.filter((r) => r.endsWith("/auth/me")).length, 1);
});

function controllerSetup(server = {}, extraStore = {}, wrapFetch = (f) => f) {
  const t = tracker();
  const k = makeKaf({ tracker: tracker() });
  const s = makeServer({ tracker: t, ...server });
  const scripting = makeScripting([{ frameId: 1, env: k.env }], t);
  const store = { authToken: TOKEN, authUser: { email: "student@ucsd.edu" }, someSetting: "keep", ...extraStore };
  const tabs = { ...makeTabs(), query: async () => [{ url: `https://canvas.ucsd.edu/courses/${COURSE}` }] };
  const chrome = {
    tabs,
    scripting,
    storage: {
      session: { set: async () => {}, get: async (key) => ({ [key]: undefined }), remove: async () => {} },
      local: {
        set: async (items) => void Object.assign(store, JSON.parse(JSON.stringify(items))),
        get: async (key) => (typeof key === "string" ? { [key]: store[key] } : { ...store }),
        remove: async (keys) => void [].concat(keys).forEach((k) => delete store[k]),
      },
    },
  };
  let idle = 0;
  const clock = fakeClock();
  const controller = createController({ chrome, fetch: wrapFetch(s.fetch), backendUrl: BASE, now: () => Date.parse("2026-11-03T12:00:00Z"), sleep: clock.sleep, onIdle: () => idle++ });
  return { controller, store, s, tabs, scripting, idle: () => idle };
}

const settle = async (store) => {
  for (let i = 0; i < 400 && (store.syncState?.status === "running" || i < 3); i++) await new Promise((r) => setTimeout(r, 1));
};

test("AUTH_EXPIRED clears the token and user, keeps other settings, and leaves the signed-out state", async () => {
  const { controller, store, s, tabs, scripting } = controllerSetup({ sessionExpired: true });
  assert.deepEqual(await controller.handle({ type: "SYNC" }), { ok: true, started: true });
  await settle(store);
  assert.equal(store.authToken, undefined);
  assert.equal(store.authUser, undefined);
  assert.equal(store.someSetting, "keep");
  assert.equal(store.authError, "session_expired");
  assert.equal(store.syncState.status, "error");
  assert.equal(store.syncState.message, MESSAGES.AUTH_EXPIRED);
  assert.equal(MESSAGES.AUTH_EXPIRED, "Your session ended. Sign in again.");
  assert.equal(touchedCanvasOrKaltura(s, scripting, tabs), false);
  // Signed out: the next click is refused without any network call.
  const before = s.requests.length;
  assert.equal((await controller.handle({ type: "SYNC" })).ok, false);
  assert.equal((await controller.handle({ type: "SYNC_ALL" })).ok, false);
  assert.equal(s.requests.length, before);
});

test("sync all through the controller: an expired token stops at /auth/me and clears the sign-in", async () => {
  const { controller, store, s } = controllerSetup({ sessionExpired: true });
  await controller.handle({ type: "SYNC_ALL" });
  await settle(store);
  assert.equal(store.authToken, undefined);
  assert.equal(store.syncState.scope, "all");
  assert.ok(s.requests.every((r) => r.url.startsWith(BASE)), "Canvas was never asked");
});

test("auto-sync does not loop on 401 and does not retry once signed out", async () => {
  const course = { on: true, code: "MATH 20A", lectures: 1, lastAttempt: 0, lastSuccess: 0 };
  const { controller, store, s, idle } = controllerSetup({ sessionExpired: true }, { autoSync: { [COURSE]: course } });
  assert.equal(controller.startAuto(COURSE), true);
  await settle(store);
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(store.authToken, undefined);
  assert.equal(store.autoSync[COURSE].loginNeeded, true);
  assert.equal(idle(), 1, "one nudge after the run, not a loop");
  const count = s.requests.length;
  assert.equal(count, 2, "openapi and /auth/me, nothing else");
  // The nudge finds the course already tried today, and with no token a start makes no request.
  controller.startAuto(COURSE);
  await settle(store);
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(s.requests.length, count);
});

test("the worker's idle tick starts nothing while signed out", async () => {
  let idleListener;
  const server = makeServer();
  const stored = { autoSync: { [COURSE]: { on: true, lectures: 1, lastAttempt: 0 } } };
  globalThis.fetch = server.fetch;
  globalThis.chrome = {
    runtime: { id: "me", onMessage: { addListener: () => {} } },
    tabs: { query: async () => [], remove: async () => {} },
    scripting: { executeScript: async () => [] },
    idle: { setDetectionInterval() {}, queryState: async () => "active", onStateChanged: { addListener: (fn) => (idleListener = fn) } },
    storage: {
      local: { get: async (key) => (typeof key === "string" ? { [key]: stored[key] } : { ...stored }), set: async (i) => void Object.assign(stored, i) },
      session: { get: async () => ({}), remove: async () => {} },
    },
  };
  try {
    await import(`../background.js?${Math.random()}`);
    await new Promise((r) => setTimeout(r, 5));
    idleListener("active");
    await new Promise((r) => setTimeout(r, 10));
    assert.equal(server.requests.length, 0);
    assert.equal(stored.autoSync[COURSE].lastAttempt, 0, "nothing was started");
  } finally {
    delete globalThis.chrome;
    delete globalThis.fetch;
  }
});

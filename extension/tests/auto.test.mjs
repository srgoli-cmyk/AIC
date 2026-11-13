import { test } from "node:test";
import assert from "node:assert/strict";
import { afterFailure, afterSuccess, badge, dayStart, dueToday, pickDue } from "../src/auto.js";
import { createController } from "../src/controller.js";
import { autoLine, formatState } from "../src/summary.js";
import { COURSE, fakeClock, makeKaf, makeScripting, makeServer, makeTabs, tracker } from "./helpers.mjs";

const H = 3600000;
// Local times, so the tests hold in any time zone.
const local = (d, h, m = 0) => new Date(2026, 10, d, h, m).getTime();
const rec = (over = {}) => ({ on: true, lectures: 5, failed: false, loginNeeded: false, unseen: 0, ...over });

test("the sync day starts at 5 AM local time", () => {
  assert.equal(dayStart(local(4, 9)), local(4, 5));
  assert.equal(dayStart(local(4, 5)), local(4, 5));
  assert.equal(dayStart(local(4, 4, 59)), local(3, 5), "before 5 AM still counts as yesterday");
});

test("a course is due once per sync day, whether the last try worked or not", () => {
  assert.equal(dueToday(rec({ lastAttempt: local(3, 22) }), local(4, 4)), false, "night owl: still yesterday");
  assert.equal(dueToday(rec({ lastAttempt: local(3, 22) }), local(4, 8)), true, "first use in the morning");
  assert.equal(dueToday(rec({ lastAttempt: local(4, 8) }), local(4, 15)), false, "already tried today");
  assert.equal(dueToday(rec({ lastAttempt: local(4, 8), failed: true }), local(4, 15)), false, "no retries until tomorrow");
  assert.equal(dueToday(rec({ lastAttempt: local(1, 9) }), local(4, 14)), true, "first use after days away");
  assert.equal(dueToday(rec({ on: false }), local(4, 9)), false);
  assert.equal(dueToday(rec({ loginNeeded: true, lastAttempt: local(3, 9) }), local(4, 9)), true, "tries again each day");
  assert.equal(dueToday(undefined, local(4, 9)), false);
});

test("pickDue goes through every due course, longest-waiting first, and skips bad ids", () => {
  const now = local(4, 9);
  const all = { 1: rec({ lastAttempt: local(3, 9) }), 2: rec({ lastAttempt: local(2, 9) }), 3: rec({ lastAttempt: local(4, 8) }), abc: rec(), 0: rec() };
  assert.equal(pickDue(all, now), 2);
  all[2].lastAttempt = now;
  assert.equal(pickDue(all, now), 1);
  all[1].lastAttempt = now;
  assert.equal(pickDue(all, now), null);
  assert.equal(pickDue(undefined, now), null);
});

test("records: success counts new lectures, failure flags a login, untracked failures are dropped", () => {
  const first = afterSuccess(undefined, { lectures: 10, pending: 1, code: "MATH 18" }, 100);
  assert.equal(first.on, true);
  assert.equal(first.unseen, 0, "the first sync is not 'new'");
  const next = afterSuccess({ ...first, on: false }, { lectures: 12, pending: 0, code: "MATH 18" }, 200);
  assert.equal(next.on, false, "a switched-off course stays off");
  assert.equal(next.unseen, 2);
  const failed = afterFailure(next, "CANVAS_LOGIN", 300);
  assert.equal(failed.loginNeeded, true);
  assert.equal(failed.failed, true);
  assert.equal(failed.lastAttempt, 300);
  assert.equal(afterFailure(failed, "BACKEND_DOWN", 400).loginNeeded, false);
  assert.equal(afterFailure(undefined, "CANVAS_LOGIN", 1), null);
});

test("badge: ! for a login, +n for new lectures, nothing otherwise", () => {
  assert.deepEqual(badge({ 1: rec({ unseen: 2 }), 2: rec({ unseen: 1 }) }), { text: "+3", color: "#f58bb4" });
  assert.equal(badge({ 1: rec({ unseen: 2, loginNeeded: true }) }).text, "!");
  assert.equal(badge({ 1: rec({ unseen: 2, on: false }) }).text, "");
  assert.equal(badge({ 1: rec({ unseen: 500 }) }).text, "99+");
  assert.equal(badge(undefined).text, "");
});

test("popup text for auto-sync", () => {
  assert.equal(autoLine(rec({ on: false })), "Off. New lectures sync only when you click.");
  assert.match(autoLine(rec({ loginNeeded: true })), /login expired/);
  assert.match(autoLine(rec({ failed: true })), /Tries again tomorrow/);
  assert.equal(autoLine(rec({ lastSuccess: 5 }), () => "T"), "Syncs each morning. Last T.");
  assert.match(formatState({ status: "running", stage: "captions", done: 1, total: 2, mode: "auto" }), /^Checking for new lectures\. Reading captions: 1 of 2/);
  assert.match(formatState({ status: "error", message: "X", mode: "auto" }), /^Background sync failed: X/);
});

// ---------- Controller ----------

function setup(server = {}) {
  const k = makeKaf({ tracker: tracker() });
  const s = makeServer({ tracker: tracker(), ...server });
  const scripting = makeScripting([{ frameId: 1, env: k.env }]);
  const store = {};
  const session = {};
  const tabs = { ...makeTabs(), query: async () => [{ url: `https://canvas.ucsd.edu/courses/${COURSE}` }] };
  const chrome = {
    tabs,
    scripting,
    storage: {
      session: { set: async (i) => void Object.assign(session, i), get: async (key) => ({ [key]: session[key] }), remove: async (key) => void delete session[key] },
      local: {
        set: async (items) => void Object.assign(store, JSON.parse(JSON.stringify(items))),
        get: async (key) => (typeof key === "string" ? { [key]: store[key] } : { ...store }),
      },
    },
  };
  const clock = fakeClock();
  let t = local(4, 9);
  const badges = [];
  let alive = 0;
  let idle = 0;
  const controller = createController({
    chrome,
    fetch: s.fetch,
    backendUrl: "http://localhost:8000",
    now: () => t,
    sleep: clock.sleep,
    keepAlive: () => (alive++, () => alive--),
    onAuto: (all) => badges.push(badge(all).text),
    onIdle: () => idle++,
  });
  return { controller, store, s, badges, alive: () => alive, idle: () => idle };
}

const settle = async (store) => {
  for (let i = 0; i < 400 && (store.syncState?.status === "running" || i < 3); i++) await new Promise((r) => setTimeout(r, 1));
};

test("a manual sync starts tracking the course", async () => {
  const { controller, store, alive, idle } = setup();
  await controller.handle({ type: "SYNC" });
  await settle(store);
  const c = store.autoSync[COURSE];
  assert.equal(c.on, true);
  assert.equal(c.lectures, 3);
  assert.equal(c.code, "MATH 20A");
  assert.equal(c.lastAttempt, local(4, 9));
  assert.equal(alive(), 0, "keep-alive stopped");
  assert.equal(idle(), 0, "a manual sync doesn't chain into others");
});

test("startAuto syncs the course page without a tab open, marks the state as auto, and counts new lectures", async () => {
  const { controller, store, badges, idle } = setup();
  store.autoSync = { [COURSE]: rec({ lectures: 1, code: "MATH 20A", lastSuccess: 0 }) };
  assert.equal(controller.startAuto(COURSE), true);
  assert.equal(controller.startAuto(COURSE), false, "one at a time");
  await new Promise((r) => setTimeout(r, 1));
  assert.equal(store.syncState.mode, "auto");
  await settle(store);
  assert.equal(store.syncState.status, "done");
  assert.equal(store.autoSync[COURSE].unseen, 2);
  assert.equal(badges.at(-1), "+2");
  await new Promise((r) => setTimeout(r, 2));
  assert.equal(idle(), 1, "asks for the next due course");
  await controller.handle({ type: "SEEN" });
  assert.equal(store.autoSync[COURSE].unseen, 0);
  assert.equal(badges.at(-1), "");
});

test("a failed background sync is recorded and not retried today", async () => {
  const { controller, store, badges } = setup({ backendDown: true });
  store.autoSync = { [COURSE]: rec({ lastSuccess: 0 }) };
  controller.startAuto(COURSE);
  await settle(store);
  assert.equal(store.syncState.status, "error");
  assert.equal(store.syncState.mode, "auto");
  assert.equal(store.autoSync[COURSE].failed, true);
  assert.equal(store.autoSync[COURSE].loginNeeded, false);
  assert.equal(dueToday(store.autoSync[COURSE], local(4, 20)), false);
  assert.equal(badges.at(-1), "");
});

test("AUTO_SET switches a tracked course and refuses anything else", async () => {
  const { controller, store } = setup();
  store.autoSync = { [COURSE]: rec({ loginNeeded: true, failed: true }) };
  assert.deepEqual(await controller.handle({ type: "AUTO_SET", courseId: COURSE, on: false }), { ok: true, on: false });
  assert.equal(store.autoSync[COURSE].loginNeeded, false);
  assert.deepEqual(await controller.handle({ type: "AUTO_SET", courseId: 777, on: true }), { ok: false, on: false });
  assert.equal((await controller.handle({ type: "AUTO_SET", courseId: "1", on: true })).ok, false);
  assert.equal((await controller.handle({ type: "AUTO_SET", courseId: COURSE, on: "yes" })).ok, false);
  assert.equal(store.autoSync[777], undefined);
});

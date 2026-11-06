import { test } from "node:test";
import assert from "node:assert/strict";
import { makeServer } from "./helpers.mjs";

// background.js talks to the global chrome and fetch, so each import gets fakes first.
async function load({ server = makeServer(), stored = {}, session = {} } = {}) {
  let listener;
  const removed = [];
  globalThis.fetch = server.fetch;
  globalThis.chrome = {
    runtime: { id: "me", onMessage: { addListener: (fn) => (listener = fn) } },
    tabs: { query: async () => [{ url: "https://example.com/" }], remove: async (id) => void removed.push(id) },
    scripting: { executeScript: async () => [] },
    storage: {
      local: { get: async () => stored, set: async (items) => void Object.assign(stored, items) },
      session: { get: async () => session, remove: async () => {} },
    },
  };
  await import(`../background.js?${Math.random()}`);
  await new Promise((r) => setTimeout(r, 5)); // let the startup recovery finish
  return { listener, removed, stored };
}

const ask = (listener, msg, sender = { id: "me" }) =>
  new Promise((resolve) => {
    const keep = listener(msg, sender, resolve);
    if (keep !== true) resolve("no response");
  });

test.afterEach(() => {
  delete globalThis.chrome;
  delete globalThis.fetch;
});

test("messages from another sender are ignored", async () => {
  const { listener } = await load();
  assert.equal(listener({ type: "PING" }, { id: "someone-else" }, () => {}), false);
  assert.equal(listener({ type: "PING" }, undefined, () => {}), false);
});

test("unknown messages are not answered", async () => {
  const { listener } = await load();
  assert.equal(await ask(listener, { type: "NOPE" }), "no response");
  assert.equal(await ask(listener, undefined), "no response");
});

test("PING and SYNC are routed to the controller", async () => {
  const { listener } = await load();
  assert.deepEqual(await ask(listener, { type: "PING" }), { ok: true, status: 200, body: { status: "ok" } });
  const sync = await ask(listener, { type: "SYNC" });
  assert.equal(sync.ok, false);
  assert.match(sync.error, /Open a Canvas course page/);
});

test("a handler that throws answers with a fixed message, not the error text", async () => {
  const { listener } = await load();
  globalThis.chrome.tabs.query = async () => {
    throw new Error("secret detail");
  };
  globalThis.chrome.storage.local.get = async () => {
    throw new Error("secret detail");
  };
  const res = await ask(listener, { type: "STATUS" });
  assert.ok(!JSON.stringify(res).includes("secret detail"));
});

test("startup recovery marks an interrupted sync and closes its tab", async () => {
  const { stored, removed } = await load({ stored: { syncState: { status: "running", courseId: 5 } }, session: { syncTab: 77 } });
  assert.equal(stored.syncState.status, "error");
  assert.deepEqual(removed, [77]);
});

test("the daily alarm is created, and a tick syncs a due course only while the computer is in use", async () => {
  const stored = { autoSync: { 12345: { on: true, lastSuccess: 0, failures: 0, starts: [] } } };
  const created = [];
  let onAlarm;
  let onState;
  let state = "idle";
  const badges = [];
  globalThis.fetch = makeServer().fetch;
  globalThis.chrome = {
    runtime: { id: "me", onMessage: { addListener: () => {} } },
    idle: { queryState: async () => state, setDetectionInterval: () => {}, onStateChanged: { addListener: (fn) => (onState = fn) } },
    alarms: { get: async () => undefined, create: async (name, o) => void created.push([name, o]), onAlarm: { addListener: (fn) => (onAlarm = fn) } },
    action: { setBadgeText: async (b) => void badges.push(b.text), setBadgeBackgroundColor: async () => {} },
    tabs: { query: async () => [], remove: async () => {}, create: async () => { throw new Error("no tabs in tests"); } },
    scripting: { executeScript: async () => [] },
    storage: {
      local: { get: async (k) => (typeof k === "string" ? { [k]: stored[k] } : stored), set: async (items) => void Object.assign(stored, items) },
      session: { get: async () => ({}), set: async () => {}, remove: async () => {} },
    },
  };
  await import(`../background.js?${Math.random()}`);
  await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(created, [["autosync", { delayInMinutes: 1, periodInMinutes: 15 }]]);
  onAlarm({ name: "other" });
  onAlarm({ name: "autosync" });
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(stored.syncState, undefined, "nobody at the computer");
  state = "active";
  onState("active");
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(stored.syncState?.courseId, 12345);
  assert.equal(stored.syncState?.mode, "auto");
});

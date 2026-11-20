import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import {
  authLoginUrl,
  handleAuthRedirect,
  authLogout,
  startLogin,
  makeNonce,
  challengeFor,
  PENDING_KEY,
} from "../src/auth.js";
import { getLectures, postSync } from "../src/backend.js";
import { syncSlides } from "../src/slides.js";
import { res } from "./helpers.mjs";

const BASE = "http://localhost:8000";

function makeChromeStorage() {
  const store = {};
  const session = {};
  const removed = [];
  return {
    store,
    session,
    removed,
    chrome: {
      storage: {
        session: {
          get: async (key) => ({ [key]: session[key] }),
          set: async (items) => {
            Object.assign(session, items);
          },
          remove: async (key) => {
            delete session[key];
          },
        },
        local: {
          get: async (keys) => {
            if (Array.isArray(keys)) {
              const out = {};
              for (const k of keys) out[k] = store[k];
              return out;
            }
            if (typeof keys === "string") return { [keys]: store[keys] };
            return { ...store };
          },
          set: async (items) => {
            Object.assign(store, items);
          },
          remove: async (keys) => {
            const list = Array.isArray(keys) ? keys : [keys];
            removed.push(...list);
            for (const k of list) delete store[k];
          },
        },
      },
    },
  };
}

const NONCE = "test_nonce_not_a_secret";
const T0 = 1_000_000;
const DONE = `${BASE}/auth/extension/done?code=single_use_auth_code_xyz`;

function withPending(h, over = {}) {
  h.session[PENDING_KEY] = { nonce: NONCE, tabId: 42, startedAt: T0, ...over };
  h.chrome.tabs = { remove: async () => {} };
}
function counter() {
  const requests = [];
  const fetchFn = async (url, init) => {
    requests.push({ url, init });
    return res({ token: "ext_tok", user: { id: "u" } });
  };
  return { requests, fetchFn };
}
const call = (h, fetchFn, over = {}) =>
  handleAuthRedirect({ url: DONE, tabId: 42, chrome: h.chrome, fetchFn, backendUrl: BASE, now: () => T0 + 1000, ...over });

test("authLoginUrl carries only the challenge", () => {
  assert.equal(
    authLoginUrl("abc-_", "http://localhost:8000"),
    "http://localhost:8000/auth/google/login?client=extension&nonce=abc-_"
  );
});

test("the challenge is base64url(sha256(nonce)) without padding", async () => {
  const nonce = makeNonce();
  const want = createHash("sha256").update(nonce).digest("base64url");
  assert.equal(await challengeFor(nonce), want);
  assert.ok(!want.includes("="));
});

test("the nonce is at least 32 bytes of randomness, and differs each time", () => {
  const a = makeNonce();
  assert.match(a, /^[A-Za-z0-9_-]+$/);
  assert.ok(Buffer.from(a, "base64url").length >= 32);
  assert.notEqual(a, makeNonce());
});

test("startLogin opens the tab with the challenge and keeps the nonce only in session storage", async () => {
  const h = makeChromeStorage();
  const created = [];
  h.chrome.tabs = { create: async (o) => (created.push(o), { id: 7 }) };
  assert.equal(await startLogin({ chrome: h.chrome, backendUrl: BASE, now: () => T0 }), true);
  const p = h.session[PENDING_KEY];
  assert.equal(p.tabId, 7);
  assert.equal(p.startedAt, T0);
  assert.ok(Buffer.from(p.nonce, "base64url").length >= 32);
  const u = new URL(created[0].url);
  assert.equal(u.searchParams.get("nonce"), await challengeFor(p.nonce));
  assert.ok(!created[0].url.includes(p.nonce));
  assert.deepEqual(h.store, {}); // nothing in chrome.storage.local

  // A second login replaces the first.
  h.chrome.tabs.create = async () => ({ id: 8 });
  await startLogin({ chrome: h.chrome, backendUrl: BASE, now: () => T0 + 5 });
  assert.equal(h.session[PENDING_KEY].tabId, 8);
  assert.notEqual(h.session[PENDING_KEY].nonce, p.nonce);
});

test("a done-page in a tab the extension didn't open is ignored completely", async () => {
  const h = makeChromeStorage();
  withPending(h);
  const closed = [];
  h.chrome.tabs.remove = async (id) => closed.push(id);
  const { requests, fetchFn } = counter();
  assert.equal(await call(h, fetchFn, { tabId: 43 }), false);
  assert.equal(requests.length, 0);
  assert.deepEqual(closed, []);
  assert.deepEqual(h.store, {});
  assert.ok(h.session[PENDING_KEY]); // still pending for the real tab
});

test("a code with no pending login is ignored", async () => {
  const h = makeChromeStorage();
  h.chrome.tabs = { remove: async () => {} };
  const { requests, fetchFn } = counter();
  assert.equal(await call(h, fetchFn), false);
  assert.equal(requests.length, 0);
  assert.deepEqual(h.store, {});
});

test("an old pending login (over 10 minutes) is ignored", async () => {
  const h = makeChromeStorage();
  withPending(h);
  const { requests, fetchFn } = counter();
  assert.equal(await call(h, fetchFn, { now: () => T0 + 10 * 60 * 1000 + 1 }), false);
  assert.equal(requests.length, 0);
  assert.deepEqual(h.store, {});
});

test("the pending login is cleared after one attempt, success or failure", async () => {
  for (const ok of [true, false]) {
    const h = makeChromeStorage();
    withPending(h);
    const requests = [];
    const fetchFn = async (url, init) => {
      requests.push({ url, init });
      // Cleared before the result is awaited.
      assert.equal(h.session[PENDING_KEY], undefined);
      return ok ? res({ token: "t", user: {} }) : res({ detail: "no" }, { status: 400 });
    };
    assert.equal(await call(h, fetchFn), true);
    assert.equal(await call(h, fetchFn), false);
    assert.equal(requests.length, 1);
  }
});

test("two events for the same page exchange once", async () => {
  const h = makeChromeStorage();
  withPending(h);
  const { requests, fetchFn } = counter();
  await Promise.all([call(h, fetchFn), call(h, fetchFn)]);
  assert.equal(requests.length, 1);
});

test("nothing sensitive is written to chrome.storage.local except the token", async () => {
  const h = makeChromeStorage();
  withPending(h);
  const { fetchFn } = counter();
  await call(h, fetchFn);
  const dump = JSON.stringify(h.store);
  assert.deepEqual(Object.keys(h.store).sort(), ["authError", "authToken", "authUser"]);
  assert.ok(!dump.includes(NONCE));
  assert.ok(!dump.includes("single_use_auth_code_xyz"));
});

test("handleAuthRedirect ignores non-matching URLs", async () => {
  const { chrome } = makeChromeStorage();
  let fetched = false;
  const fetchFn = async () => {
    fetched = true;
    return res({});
  };

  assert.equal(await handleAuthRedirect({ url: null, tabId: 1, chrome, fetchFn, backendUrl: BASE }), false);
  assert.equal(await handleAuthRedirect({ url: "https://canvas.ucsd.edu", tabId: 1, chrome, fetchFn, backendUrl: BASE }), false);
  assert.equal(await handleAuthRedirect({ url: "http://localhost:8000/api/other", tabId: 1, chrome, fetchFn, backendUrl: BASE }), false);
  assert.equal(fetched, false);
});

test("handleAuthRedirect exchanges code for token, stores credentials, and closes tab", async () => {
  const h = makeChromeStorage();
  const { chrome, store } = h;
  withPending(h);
  const closedTabs = [];
  chrome.tabs = { remove: async (id) => closedTabs.push(id) };

  const requests = [];
  const fetchFn = async (url, init) => {
    requests.push({ url, init });
    return res({
      token: "ext_test_token_123",
      user: { id: "usr_1", email: "triton@ucsd.edu", name: "Triton" },
    });
  };

  const handled = await handleAuthRedirect({
    url: `${BASE}/auth/extension/done?code=single_use_auth_code_xyz`,
    tabId: 42,
    chrome,
    fetchFn,
    backendUrl: BASE,
    now: () => T0 + 1000,
  });

  assert.equal(handled, true);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, `${BASE}/auth/extension/exchange`);
  assert.equal(requests[0].init.method, "POST");
  assert.deepEqual(JSON.parse(requests[0].init.body), { code: "single_use_auth_code_xyz", nonce: NONCE });
  assert.equal(store.authToken, "ext_test_token_123");
  assert.deepEqual(store.authUser, { id: "usr_1", email: "triton@ucsd.edu", name: "Triton" });
  assert.equal(store.authError, null);
  assert.deepEqual(closedTabs, [42]);
});

test("handleAuthRedirect records error parameter and closes tab", async () => {
  const h = makeChromeStorage();
  const { chrome, store } = h;
  withPending(h, { tabId: 99 });
  const closedTabs = [];
  chrome.tabs = { remove: async (id) => closedTabs.push(id) };

  const handled = await handleAuthRedirect({
    url: `${BASE}/auth/extension/done?error=ucsd_only`,
    tabId: 99,
    chrome,
    fetchFn: async () => res({}),
    backendUrl: BASE,
    now: () => T0 + 1000,
  });

  assert.equal(handled, true);
  assert.equal(store.authError, "ucsd_only");
  assert.equal(store.authToken, undefined);
  assert.deepEqual(closedTabs, [99]);
});

test("a bare done page (no code, no error) keeps the pending login and leaves the tab open", async () => {
  const h = makeChromeStorage();
  withPending(h);
  const closed = [];
  h.chrome.tabs = { remove: async (id) => closed.push(id) };
  const { requests, fetchFn } = counter();
  const handled = await call(h, fetchFn, { url: `${BASE}/auth/extension/done` });
  assert.equal(handled, false);
  assert.ok(h.session[PENDING_KEY], "pending login still there");
  assert.deepEqual(closed, []);
  assert.deepEqual(requests, []);
  assert.equal(h.store.authError, undefined);
  // The real redirect afterwards still works.
  assert.equal(await call(h, fetchFn), true);
  assert.equal(h.store.authToken, "ext_tok");
});

test("handleAuthRedirect passes only known backend error keys through, else login_failed", async () => {
  for (const [given, stored] of [["login_failed", "login_failed"], ["ucsd_only", "ucsd_only"], ["network_error", "login_failed"], ["<b>x</b>", "login_failed"], ["unverified_email", "login_failed"]]) {
    const h = makeChromeStorage();
    withPending(h, { tabId: 7 });
    await handleAuthRedirect({
      url: `${BASE}/auth/extension/done?error=${encodeURIComponent(given)}`,
      tabId: 7,
      chrome: h.chrome,
      fetchFn: async () => res({}),
      backendUrl: BASE,
      now: () => T0 + 1000,
    });
    assert.equal(h.store.authError, stored, given);
  }
});

test("handleAuthRedirect records network_error when the exchange request throws", async () => {
  const h = makeChromeStorage();
  withPending(h, { tabId: 11 });
  await handleAuthRedirect({
    url: `${BASE}/auth/extension/done?code=some_code`,
    tabId: 11,
    chrome: h.chrome,
    fetchFn: async () => {
      throw new TypeError("fetch failed");
    },
    backendUrl: BASE,
    now: () => T0 + 1000,
  });
  assert.equal(h.store.authError, "network_error");
  assert.equal(h.store.authToken, undefined);
});

test("handleAuthRedirect records exchange_failed on non-200 exchange response", async () => {
  const h = makeChromeStorage();
  const { chrome, store } = h;
  withPending(h, { tabId: 10 });

  const fetchFn = async () => res({ detail: "Code expired" }, { status: 400 });

  const handled = await handleAuthRedirect({
    url: `${BASE}/auth/extension/done?code=bad_code`,
    tabId: 10,
    chrome,
    fetchFn,
    backendUrl: BASE,
    now: () => T0 + 1000,
  });

  assert.equal(handled, true);
  assert.equal(store.authError, "exchange_failed");
  assert.equal(store.authToken, undefined);
});

test("authLogout calls backend /auth/logout with Bearer token and clears all student session storage", async () => {
  const { chrome, store, removed } = makeChromeStorage();
  store.authToken = "tok_abc";
  store.authUser = { email: "triton@ucsd.edu" };
  store.authError = null;
  store.lastSync = { courseId: 10, code: "CSE 150A" };
  store.syncState = { status: "done" };
  store.auto = { 10: { on: true } };

  const calls = [];
  const fetchFn = async (url, init) => {
    calls.push({ url, init });
    return res({ ok: true });
  };

  await authLogout({ token: "tok_abc", fetchFn, backendUrl: BASE, chrome });

  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, `${BASE}/auth/logout`);
  assert.equal(calls[0].init.headers.Authorization, "Bearer tok_abc");
  assert.ok(removed.includes("authToken"));
  assert.ok(removed.includes("authUser"));
  assert.ok(removed.includes("authError"));
  assert.ok(removed.includes("lastSync"));
  assert.ok(removed.includes("syncState"));
  assert.ok(removed.includes("auto"));
  assert.equal(store.authToken, undefined);
  assert.equal(store.lastSync, undefined);
  assert.equal(store.syncState, undefined);
  assert.equal(store.auto, undefined);
});

test("getLectures and postSync send Bearer authorization header and handle 401", async () => {
  const requests = [];
  const fetchFn = async (url, init) => {
    requests.push({ url, init });
    if (url.endsWith("/openapi.json")) {
      return res({ info: { title: "AI Lecture Search" } });
    }
    if (init?.headers?.Authorization === "Bearer valid_token") {
      if (url.includes("/lectures")) return res([{ entry_id: "1_a", has_captions: true }]);
      return res({ status: "ok" });
    }
    return res({ detail: "Unauthorized" }, { status: 401 });
  };

  // With valid token
  const lectures = await getLectures(fetchFn, BASE, 12345, "valid_token");
  assert.equal(lectures.length, 1);
  assert.equal(requests[1].init.headers.Authorization, "Bearer valid_token");

  await postSync(fetchFn, BASE, { lectures: [] }, "valid_token");
  const postReq = requests.find((r) => r.init?.method === "POST");
  assert.equal(postReq.init.headers.Authorization, "Bearer valid_token");

  // With invalid/expired token or 401
  await assert.rejects(
    getLectures(fetchFn, BASE, 12345, "bad_token"),
    (e) => e.code === "AUTH_EXPIRED"
  );
  await assert.rejects(
    postSync(fetchFn, BASE, { lectures: [] }, "bad_token"),
    (e) => e.code === "AUTH_EXPIRED"
  );
});

test("slides sync carries Bearer authorization header", async () => {
  const calls = [];
  const fetchFn = async (url, init) => {
    calls.push({ url, init });
    if (url.endsWith("/openapi.json")) return res({ info: { title: "AI Lecture Search" } });
    if (url.includes("/documents") && (!init || !init.method || init.method === "GET")) {
      return res([]);
    }
    return res({ status: "stored" });
  };

  await syncSlides({
    fetchFn,
    backendUrl: BASE,
    courseId: 12345,
    files: [{ id: 1, display_name: "lecture1.pdf", "content-type": "application/pdf", size: 500, modified_at: "2026-11-01T00:00:00Z" }],
    token: "test_tok",
    report: () => {},
  });

  const getDoc = calls.find((c) => c.url.includes("/documents"));
  assert.ok(getDoc);
  assert.equal(getDoc.init?.headers?.Authorization, "Bearer test_tok");
});

import { test } from "node:test";
import assert from "node:assert/strict";
import { authLoginUrl, handleAuthRedirect, authLogout } from "../src/auth.js";
import { getLectures, postSync } from "../src/backend.js";
import { syncSlides } from "../src/slides.js";
import { res } from "./helpers.mjs";

const BASE = "http://localhost:8000";

function makeChromeStorage() {
  const store = {};
  const removed = [];
  return {
    store,
    removed,
    chrome: {
      storage: {
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

test("authLoginUrl points to /auth/google/login?client=extension", () => {
  assert.equal(authLoginUrl("http://localhost:8000"), "http://localhost:8000/auth/google/login?client=extension");
  assert.ok(authLoginUrl().endsWith("/auth/google/login?client=extension"));
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
  const { chrome, store } = makeChromeStorage();
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
  });

  assert.equal(handled, true);
  assert.equal(requests.length, 1);
  assert.equal(requests[0].url, `${BASE}/auth/extension/exchange`);
  assert.equal(requests[0].init.method, "POST");
  assert.deepEqual(JSON.parse(requests[0].init.body), { code: "single_use_auth_code_xyz" });
  assert.equal(store.authToken, "ext_test_token_123");
  assert.deepEqual(store.authUser, { id: "usr_1", email: "triton@ucsd.edu", name: "Triton" });
  assert.equal(store.authError, null);
  assert.deepEqual(closedTabs, [42]);
});

test("handleAuthRedirect records error parameter and closes tab", async () => {
  const { chrome, store } = makeChromeStorage();
  const closedTabs = [];
  chrome.tabs = { remove: async (id) => closedTabs.push(id) };

  const handled = await handleAuthRedirect({
    url: `${BASE}/auth/extension/done?error=ucsd_only`,
    tabId: 99,
    chrome,
    fetchFn: async () => res({}),
    backendUrl: BASE,
  });

  assert.equal(handled, true);
  assert.equal(store.authError, "ucsd_only");
  assert.equal(store.authToken, undefined);
  assert.deepEqual(closedTabs, [99]);
});

test("handleAuthRedirect records exchange_failed on non-200 exchange response", async () => {
  const { chrome, store } = makeChromeStorage();
  chrome.tabs = { remove: async () => {} };

  const fetchFn = async () => res({ detail: "Code expired" }, { status: 400 });

  const handled = await handleAuthRedirect({
    url: `${BASE}/auth/extension/done?code=bad_code`,
    tabId: 10,
    chrome,
    fetchFn,
    backendUrl: BASE,
  });

  assert.equal(handled, true);
  assert.equal(store.authError, "exchange_failed");
  assert.equal(store.authToken, undefined);
});

test("authLogout calls backend /auth/logout with Bearer token and clears storage", async () => {
  const { chrome, store, removed } = makeChromeStorage();
  store.authToken = "tok_abc";
  store.authUser = { email: "triton@ucsd.edu" };
  store.authError = null;

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
  assert.equal(store.authToken, undefined);
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

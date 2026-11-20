// Authentication helpers for the Chrome extension (Google OIDC flow with a nonce/challenge pair).
//
// Login binding: the extension makes a random nonce, sends only its SHA-256 challenge in the login
// URL, and remembers the nonce and the login tab in chrome.storage.session. The handoff page is
// exchanged only in that tab, once, within PENDING_TTL_MS, together with the nonce. A code obtained
// elsewhere (an attacker's own login) can't be exchanged because it was not bound to this nonce.
import { AUTH_ERR, BACKEND_AUTH_ERRORS } from "./authErrors.js";
import { BACKEND_URL } from "./config.js";

export const PENDING_KEY = "pendingLogin";
export const PENDING_TTL_MS = 10 * 60 * 1000;

function base64url(bytes) {
  let bin = "";
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** 32 random bytes (256 bits), base64url without padding. */
export function makeNonce() {
  return base64url(crypto.getRandomValues(new Uint8Array(32)));
}

/** base64url (no padding) of SHA-256 over the nonce's UTF-8 bytes. */
export async function challengeFor(nonce) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(nonce));
  return base64url(new Uint8Array(digest));
}

/** URL to begin Google OIDC authentication in a new browser tab. */
export function authLoginUrl(challenge, backendUrl = BACKEND_URL) {
  return `${backendUrl}/auth/google/login?client=extension&nonce=${encodeURIComponent(challenge)}`;
}

/**
 * Start a login: new nonce, open the login tab with the challenge, remember the pending login in
 * chrome.storage.session. A new login replaces any previous pending one.
 */
export async function startLogin({ chrome, backendUrl = BACKEND_URL, now = Date.now }) {
  const nonce = makeNonce();
  const challenge = await challengeFor(nonce);
  await chrome.storage.session.remove(PENDING_KEY);
  const tab = await chrome.tabs.create({ url: authLoginUrl(challenge, backendUrl) });
  if (!Number.isInteger(tab?.id)) return false;
  await chrome.storage.session.set({ [PENDING_KEY]: { nonce, tabId: tab.id, startedAt: now() } });
  return true;
}

let queue = Promise.resolve();

/**
 * Handle tab navigation to the extension auth handoff page (/auth/extension/done).
 * Only the tab opened by the pending login, within the time limit, is handled; everything else is
 * ignored without a request, a stored error or closing the tab. The pending login is cleared after
 * this one attempt, before the exchange result is awaited.
 * Returns true if the URL was handled.
 */
export function handleAuthRedirect(args) {
  // Serialized so two tab events for the same page can't both read the pending login.
  const run = queue.then(() => handleOne(args));
  queue = run.catch(() => {});
  return run;
}

async function handleOne({ url, tabId, chrome, fetchFn = fetch, backendUrl = BACKEND_URL, now = Date.now }) {
  if (!url || typeof url !== "string") return false;
  let parsed;
  try {
    parsed = new URL(url);
    const backend = new URL(backendUrl);
    const isLocalhost =
      (parsed.hostname === "localhost" || parsed.hostname === "127.0.0.1") &&
      (backend.hostname === "localhost" || backend.hostname === "127.0.0.1") &&
      parsed.port === backend.port;
    if (!(parsed.origin === backend.origin || isLocalhost) || !parsed.pathname.endsWith("/auth/extension/done")) {
      return false;
    }
  } catch {
    return false;
  }

  let pending;
  try {
    pending = (await chrome.storage.session.get(PENDING_KEY))?.[PENDING_KEY];
  } catch {
    return false;
  }
  if (
    !pending ||
    typeof pending.nonce !== "string" ||
    !Number.isInteger(tabId) ||
    pending.tabId !== tabId ||
    !Number.isFinite(pending.startedAt) ||
    now() - pending.startedAt > PENDING_TTL_MS ||
    now() < pending.startedAt
  ) {
    return false;
  }

  const code = parsed.searchParams.get("code");
  const error = parsed.searchParams.get("error");
  // A bare done page carries nothing to act on: keep the pending login and leave the tab alone.
  if (!code && !error) return false;

  // One attempt only: clear before awaiting the exchange.
  await chrome.storage.session.remove(PENDING_KEY);

  if (code) {
    try {
      const res = await fetchFn(`${backendUrl}/auth/extension/exchange`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ code, nonce: pending.nonce }),
      });
      if (res.ok) {
        const data = await res.json();
        await chrome.storage.local.set({
          authToken: data.token,
          authUser: data.user,
          authError: null,
        });
      } else {
        await chrome.storage.local.set({ authError: AUTH_ERR.EXCHANGE_FAILED });
      }
    } catch {
      await chrome.storage.local.set({ authError: AUTH_ERR.NETWORK_ERROR });
    }
  } else if (error) {
    const known = BACKEND_AUTH_ERRORS.includes(error) ? error : AUTH_ERR.LOGIN_FAILED;
    await chrome.storage.local.set({ authError: known });
  }

  // Close the login handoff tab
  if (chrome.tabs?.remove) {
    try {
      Promise.resolve(chrome.tabs.remove(tabId)).catch(() => {});
    } catch {
      // Closing is cosmetic
    }
  }
  return true;
}

/**
 * Revoke the current extension session on the backend and clear local storage.
 */
export async function authLogout({ token, fetchFn = fetch, backendUrl = BACKEND_URL, chrome }) {
  if (token) {
    try {
      await fetchFn(`${backendUrl}/auth/logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      });
    } catch {
      // Best-effort remote revocation
    }
  }
  await chrome.storage.local.remove([
    "authToken",
    "authUser",
    "authError",
    "lastSync",
    "syncState",
    "auto",
  ]);
  try {
    await chrome.action?.setBadgeText?.({ text: "" });
  } catch {
    // Badge cleanup is cosmetic
  }
}

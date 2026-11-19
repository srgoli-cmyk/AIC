// Authentication helpers for the Chrome extension (Option A Google OIDC flow).
import { BACKEND_URL } from "./config.js";

/** URL to begin Google OIDC authentication in a new browser tab. */
export function authLoginUrl(backendUrl = BACKEND_URL) {
  return `${backendUrl}/auth/google/login?client=extension`;
}

/**
 * Handle tab navigation to the extension auth handoff page (/auth/extension/done).
 * Exchanges the single-use code for an extension Bearer session token, stores it in
 * chrome.storage.local, and closes the temporary auth tab.
 * Returns true if the URL matched the handoff page and was handled.
 */
export async function handleAuthRedirect({ url, tabId, chrome, fetchFn = fetch, backendUrl = BACKEND_URL }) {
  if (!url || typeof url !== "string") return false;
  try {
    const parsed = new URL(url);
    const backendOrigin = new URL(backendUrl).origin;
    if (parsed.origin === backendOrigin && parsed.pathname.endsWith("/auth/extension/done")) {
      const code = parsed.searchParams.get("code");
      const error = parsed.searchParams.get("error");
      if (code) {
        try {
          const res = await fetchFn(`${backendUrl}/auth/extension/exchange`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code }),
          });
          if (res.ok) {
            const data = await res.json();
            await chrome.storage.local.set({
              authToken: data.token,
              authUser: data.user,
              authError: null,
            });
          } else {
            await chrome.storage.local.set({ authError: "exchange_failed" });
          }
        } catch {
          await chrome.storage.local.set({ authError: "network_error" });
        }
      } else if (error) {
        await chrome.storage.local.set({ authError: error });
      }

      // Close the login handoff tab
      if (Number.isInteger(tabId) && chrome.tabs?.remove) {
        chrome.tabs.remove(tabId).catch(() => {});
      }
      return true;
    }
  } catch {
    // Ignore invalid URLs
  }
  return false;
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
  await chrome.storage.local.remove(["authToken", "authUser", "authError"]);
}

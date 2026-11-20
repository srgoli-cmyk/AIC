// Calls to the local backend (contracts/api.md). Every call that carries course data first confirms
// that the server on the port really is this app, because another program may own the port.
import { BACKEND_TITLE, FETCH_TIMEOUT_MS, KS_PREFIX, SYNC_POST_TIMEOUT_MS } from "./config.js";
import { SyncError } from "./errors.js";
import { timeoutSignal } from "./util.js";

export const courseKey = (canvasCourseId) => `canvas-${canvasCourseId}`;

export async function confirmBackend(fetchFn, base) {
  let r;
  try {
    r = await fetchFn(`${base}/openapi.json`, { credentials: "omit", signal: timeoutSignal(FETCH_TIMEOUT_MS) });
  } catch {
    throw new SyncError("BACKEND_DOWN");
  }
  let title;
  try {
    title = r.ok ? JSON.parse(await r.text())?.info?.title : undefined;
  } catch {
    title = undefined;
  }
  // No token is sent here, so a 401 means another app holds the port.
  if (title !== BACKEND_TITLE) throw new SyncError("BACKEND_OTHER");
}

export const isExpired = (e) => e instanceof SyncError && e.code === "AUTH_EXPIRED";

/** Any 401 from the backend means the session is gone. */
export function throwIfExpired(r) {
  if (r?.status === 401) throw new SyncError("AUTH_EXPIRED");
}

/**
 * GET /auth/me with the Bearer token: confirms the session before anything touches Canvas or
 * Kaltura. 401 is AUTH_EXPIRED. Nothing from the reply is kept.
 */
export async function confirmSession(fetchFn, base, token) {
  let r;
  try {
    r = await fetchFn(`${base}/auth/me`, {
      credentials: "omit",
      headers: { Authorization: `Bearer ${token}` },
      signal: timeoutSignal(FETCH_TIMEOUT_MS),
    });
  } catch {
    throw new SyncError("BACKEND_DOWN");
  }
  throwIfExpired(r);
  if (!r.ok) throw new SyncError("BACKEND_REJECTED", `HTTP ${Number(r.status) | 0}`);
}

/** [{ entry_id, has_captions }]; a course the backend does not know has none. */
export async function getLectures(fetchFn, base, canvasCourseId, token = null) {
  await confirmBackend(fetchFn, base);
  let r;
  try {
    // Forward bearer token when authenticated
    const headers = token ? { Authorization: `Bearer ${token}` } : {};
    r = await fetchFn(`${base}/courses/${courseKey(canvasCourseId)}/lectures`, {
      credentials: "omit",
      headers,
      signal: timeoutSignal(FETCH_TIMEOUT_MS),
    });
  } catch {
    throw new SyncError("BACKEND_DOWN");
  }
  if (r.status === 401) throw new SyncError("AUTH_EXPIRED");
  if (r.status === 404) return [];
  if (!r.ok) throw new SyncError("BACKEND_REJECTED", `HTTP ${Number(r.status) | 0}`);
  let data;
  try {
    data = JSON.parse(await r.text());
  } catch {
    throw new SyncError("BACKEND_REJECTED", "not JSON");
  }
  if (!Array.isArray(data)) throw new SyncError("BACKEND_REJECTED", "not a list");
  return data
    .filter((l) => typeof l?.entry_id === "string")
    .map((l) => ({ entry_id: l.entry_id, has_captions: l.has_captions === true }));
}

const KS_SHAPE = new RegExp(`${KS_PREFIX.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&")}[A-Za-z0-9+/=_-]{40,}`);

export function looksLikeSession(text) {
  return KS_SHAPE.test(text);
}

export async function postSync(fetchFn, base, payload, token = null) {
  const body = JSON.stringify(payload);
  if (looksLikeSession(body)) throw new SyncError("KS_IN_PAYLOAD");
  await confirmBackend(fetchFn, base);
  let r;
  try {
    // Forward bearer token in Authorization header
    const headers = {
      "Content-Type": "application/json",
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    };
    r = await fetchFn(`${base}/sync`, {
      method: "POST",
      credentials: "omit",
      headers,
      body,
      signal: timeoutSignal(SYNC_POST_TIMEOUT_MS),
    });
  } catch {
    throw new SyncError("BACKEND_DOWN");
  }
  if (r.status === 401) throw new SyncError("AUTH_EXPIRED");
  if (!r.ok) throw new SyncError("BACKEND_REJECTED", `HTTP ${Number(r.status) | 0}`);
  // The reply only matters for its counts; a reply that cannot be read is not a failure.
  try {
    const reply = JSON.parse(await r.text());
    const other = reply?.lectures_skipped_other_course;
    return { skippedOtherCourse: Number.isInteger(other) && other > 0 ? other : 0 };
  } catch {
    return { skippedOtherCourse: 0 };
  }
}

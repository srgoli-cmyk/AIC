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
  if (title !== BACKEND_TITLE) throw new SyncError("BACKEND_OTHER");
}

/** [{ entry_id, has_captions }]; a course the backend does not know has none. */
export async function getLectures(fetchFn, base, canvasCourseId) {
  await confirmBackend(fetchFn, base);
  let r;
  try {
    r = await fetchFn(`${base}/courses/${courseKey(canvasCourseId)}/lectures`, {
      credentials: "omit",
      signal: timeoutSignal(FETCH_TIMEOUT_MS),
    });
  } catch {
    throw new SyncError("BACKEND_DOWN");
  }
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

/** The local HTTP backend as a store: the same calls the IndexedDB store (local/store.js) has. */
export function httpStore(fetchFn, base) {
  return {
    confirm: () => confirmBackend(fetchFn, base),
    lectures: (canvasCourseId) => getLectures(fetchFn, base, canvasCourseId),
    save: (payload) => postSync(fetchFn, base, payload),
  };
}

export async function postSync(fetchFn, base, payload) {
  const body = JSON.stringify(payload);
  if (looksLikeSession(body)) throw new SyncError("KS_IN_PAYLOAD");
  await confirmBackend(fetchFn, base);
  let r;
  try {
    r = await fetchFn(`${base}/sync`, {
      method: "POST",
      credentials: "omit",
      headers: { "Content-Type": "application/json" },
      body,
      signal: timeoutSignal(SYNC_POST_TIMEOUT_MS),
    });
  } catch {
    throw new SyncError("BACKEND_DOWN");
  }
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

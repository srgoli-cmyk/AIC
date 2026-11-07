// Where the page gets its data. Three modes:
// - inside the extension (chrome-extension://): this browser's lecture store (src/local/api.js);
// - ?demo: synthetic data (demo.js);
// - otherwise: the FastAPI backend (contracts/api.md), for the team's server setup.
// Errors carry a message that is safe to show.
import { DEMO_COURSES, demoAnswer, demoCourse } from "./demo.js";

const params = new URLSearchParams(location.search);
export const extension = location.protocol === "chrome-extension:";
// Served by the backend at /ext/: same origin. Served on its own (`make web`): the default port.
const sameOrigin = location.pathname.startsWith("/ext/");
export const BACKEND = (params.get("backend") || (sameOrigin ? location.origin : "http://localhost:8000")).replace(/\/$/, "");
export let demo = params.has("demo");

export class BackendError extends Error {}

export function useDemo() {
  demo = true;
}

// Loaded only inside the extension: it reads chrome.* and the lecture store.
let localApi = null;
const local = () => (localApi ??= import("../src/local/api.js"));

async function viaLocal(fn) {
  try {
    return await fn(await local());
  } catch (e) {
    if (e?.message === "course not synced") throw new BackendError("course not synced");
    if (e?.name === "AnswerUnavailable") throw new BackendError(e.message);
    if (e?.name === "ModelLoadError") throw new BackendError("The search model could not load. Reload the page and try again.");
    throw new BackendError("Something went wrong reading your lectures.");
  }
}

async function call(path, init, timeoutMs) {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(`${BACKEND}${path}`, { ...init, signal: ctrl.signal });
  } catch (e) {
    throw new BackendError(
      e.name === "AbortError" ? "The backend took too long to answer." : "Could not reach the backend. Is `make start` running?"
    );
  } finally {
    clearTimeout(timer);
  }
  let body;
  try {
    body = await res.json();
  } catch {
    throw new BackendError(`The backend returned ${res.status}.`);
  }
  if (!res.ok) {
    throw new BackendError(typeof body?.detail === "string" ? body.detail : `The backend returned ${res.status}.`);
  }
  return body;
}

const pause = (ms) => new Promise((r) => setTimeout(r, ms));

export async function health() {
  if (demo || extension) return true;
  try {
    await call("/health", {}, 4000);
    return true;
  } catch {
    return false;
  }
}

export async function courses() {
  if (demo) return DEMO_COURSES;
  if (extension) return viaLocal((l) => l.courses());
  const list = await call("/courses", {}, 10000);
  if (!Array.isArray(list)) throw new BackendError("The backend sent a list this page cannot read.");
  return list;
}

export async function course(courseId) {
  if (demo) {
    await pause(250);
    const c = demoCourse(courseId);
    if (!c) throw new BackendError("course not synced");
    return c;
  }
  if (extension) {
    const c = await viaLocal((l) => l.course(courseId));
    if (!c) throw new BackendError("course not synced");
    return c;
  }
  const c = await call(`/courses/${encodeURIComponent(courseId)}`, {}, 10000);
  if (typeof c?.code !== "string" || !Array.isArray(c.lectures)) throw new BackendError("The backend sent a course this page cannot read.");
  return c;
}

export async function ask(courseId, question, entryId) {
  if (demo) {
    await pause(1400);
    return demoAnswer(question, entryId);
  }
  if (extension) return viaLocal((l) => l.ask(courseId, question, entryId));
  const body = { course_id: courseId, question };
  if (entryId) body.entry_id = entryId;
  const res = await call(
    "/ask",
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) },
    120000
  );
  if (typeof res?.answer !== "string" || !Array.isArray(res.sources)) {
    throw new BackendError("The backend sent an answer this page cannot read.");
  }
  return res;
}

/** Only inside the extension: removes a course and everything stored for it. */
export async function removeCourse(courseId) {
  if (!extension || demo) return;
  await viaLocal((l) => l.removeCourse(courseId));
}

/** Only inside the extension: indexing progress, and a listener for changes. */
export async function indexing(onChange) {
  if (!extension || demo) return null;
  const l = await local();
  l.kickIndexing();
  l.onIndexing(onChange);
  return l.indexing();
}

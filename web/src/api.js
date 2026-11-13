// Calls to the FastAPI backend (contracts/api.md). Errors carry a message that is safe to show.
import { DEMO_COURSES, demoAnswer, demoCourse, demoStudy } from "./demo.js";

const params = new URLSearchParams(location.search);
// Served by the backend at /app/: same origin. Served on its own (`make web`): the default port.
const sameOrigin = location.pathname.startsWith("/app/");
export const BACKEND = (params.get("backend") || (sameOrigin ? location.origin : "http://localhost:8000")).replace(/\/$/, "");
export let demo = params.has("demo");

export class BackendError extends Error {}

export function useDemo() {
  demo = true;
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
  if (res.status === 204) return null;
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
  if (demo) return true;
  try {
    await call("/health", {}, 4000);
    return true;
  } catch {
    return false;
  }
}

export async function courses() {
  if (demo) return DEMO_COURSES;
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
  const c = await call(`/courses/${encodeURIComponent(courseId)}`, {}, 10000);
  if (typeof c?.code !== "string" || !Array.isArray(c.lectures)) throw new BackendError("The backend sent a course this page cannot read.");
  return c;
}

export async function ask(courseId, question, entryId) {
  if (demo) {
    await pause(1400);
    return demoAnswer(question, entryId);
  }
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

/**
 * A lesson ("teach"), flashcards or practice problems ("quiz") written from one lecture
 * (POST /study). `{ empty: true }` when the lecture has nothing indexed yet.
 */
export async function study(entryId, kind) {
  if (demo) {
    await pause(1200);
    return demoStudy(kind);
  }
  let res;
  try {
    // A lesson is a long reply, and practice problems are written and then checked.
    res = await call("/study", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ entry_id: entryId, kind }) }, 300000); // writing, then a second model checks the math
  } catch (err) {
    if (err.message === "lecture not indexed") return { empty: true };
    throw err;
  }
  const list = kind === "teach" ? res?.sections : kind === "flashcards" ? res?.cards : res?.questions;
  if (!Array.isArray(list) || !list.length) throw new BackendError("The backend sent a study set this page cannot read.");
  if (kind !== "teach") return res;
  return {
    sections: res.sections.map((x) => ({ ...x, problems: (x.problems || []).map((q) => ({ ...q, steps: q.steps || [], fromLecture: q.from_lecture === true })) })),
    check: Array.isArray(res.check) ? res.check : [],
  };
}

// ---------- Slides and the course website ----------

const enc = encodeURIComponent;
const JSON_HEADERS = { "Content-Type": "application/json" };

/** The course's documents (Canvas files and pages from its website). */
export async function documents(courseId) {
  if (demo) return [];
  const list = await call(`/courses/${enc(courseId)}/documents`, {}, 10000);
  return Array.isArray(list) ? list : [];
}

/** The course's website and how its sync is going, or null. */
export async function site(courseId) {
  if (demo) return null;
  return call(`/courses/${enc(courseId)}/site`, {}, 10000);
}

/** Search the web for the course's website: { site, candidates }. Up to a minute. */
export async function searchSite(courseId) {
  const res = await call(`/courses/${enc(courseId)}/site/search`, { method: "POST" }, 120000);
  return { site: res?.site ?? null, candidates: Array.isArray(res?.candidates) ? res.candidates : [] };
}

export async function pickSite(courseId, url) {
  return call(`/courses/${enc(courseId)}/site`, { method: "PUT", headers: JSON_HEADERS, body: JSON.stringify({ url }) }, 30000);
}

export async function resyncSite(courseId) {
  return call(`/courses/${enc(courseId)}/site/sync`, { method: "POST" }, 10000);
}

export async function forgetSite(courseId) {
  return call(`/courses/${enc(courseId)}/site`, { method: "DELETE" }, 10000);
}

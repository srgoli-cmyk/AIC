// Canvas REST calls. They run in the background worker with the student's cookies; host_permissions
// make them same-privilege requests, so CORS does not apply. Pure functions: fetch is injected.
import { CANVAS_HOST, CANVAS_ORIGIN, FETCH_TIMEOUT_MS, MAX_CANVAS_PAGES, MAX_FILES, MAX_INSTRUCTORS, MAX_SECTIONS } from "./config.js";
import { SyncError } from "./errors.js";
import { clipText, timeoutSignal } from "./util.js";

export function isCanvasUrl(value) {
  try {
    const u = new URL(value);
    return u.protocol === "https:" && u.hostname === CANVAS_HOST && u.port === "" && !u.username && !u.password;
  } catch {
    return false;
  }
}

/** The course id of https://canvas.ucsd.edu/courses/<id>[/...], or null. */
export function parseCourseId(url) {
  if (typeof url !== "string" || !isCanvasUrl(url)) return null;
  const m = new URL(url).pathname.match(/^\/courses\/([1-9]\d{0,14})(?:\/|$)/);
  return m ? Number(m[1]) : null;
}

// Canvas prefixes JSON bodies with "while(1);".
export function stripPrefix(text) {
  return text.replace(/^\s*while\s*\(\s*1\s*\)\s*;?/, "");
}

export function nextLink(header) {
  if (typeof header !== "string") return null;
  for (const m of header.matchAll(/<([^>]*)>([^,<]*)/g)) {
    if (/;\s*rel\s*=\s*"?next"?/.test(m[2])) return m[1];
  }
  return null;
}

async function getPage(fetchFn, url) {
  let r;
  try {
    r = await fetchFn(url, { credentials: "include", headers: { Accept: "application/json" }, signal: timeoutSignal(FETCH_TIMEOUT_MS) });
  } catch {
    throw new SyncError("CANVAS_FAILED", "network");
  }
  if (!r.ok) throw new SyncError("CANVAS_FAILED", `HTTP ${Number(r.status) | 0}`);
  let data;
  try {
    data = JSON.parse(stripPrefix(await r.text()));
  } catch {
    throw new SyncError("CANVAS_FAILED", "not JSON");
  }
  return { data, next: nextLink(r.headers?.get?.("Link")) };
}

/** Follows Link rel="next" for a list endpoint. Next links off canvas.ucsd.edu are refused. */
export async function getList(fetchFn, url) {
  const items = [];
  let next = url;
  for (let page = 0; next; page++) {
    if (page >= MAX_CANVAS_PAGES) break;
    if (!isCanvasUrl(next)) throw new SyncError("CANVAS_FAILED", "unexpected next link");
    const { data, next: following } = await getPage(fetchFn, next);
    if (!Array.isArray(data)) throw new SyncError("CANVAS_FAILED", "not a list");
    items.push(...data);
    next = following;
  }
  return items;
}

const clip = clipText;

function isoDay(value) {
  if (typeof value !== "string") return undefined;
  const t = Date.parse(value);
  return Number.isFinite(t) ? new Date(t).toISOString().slice(0, 10) : undefined;
}

/**
 * Allowlisted course fields: { id, course_code, name, term?: { name, start?, end? }, instructors }.
 * Instructors are the course's teachers by display name only (no ids, emails or avatars).
 */
export async function fetchCourse(fetchFn, courseId) {
  const { data } = await getPage(fetchFn, `${CANVAS_ORIGIN}/api/v1/courses/${courseId}?include[]=term&include[]=teachers`);
  const course = { id: courseId, course_code: clip(data?.course_code, 100), name: clip(data?.name, 300) };
  const term = clip(data?.term?.name, 50);
  if (term) {
    course.term = { name: term };
    const start = isoDay(data.term.start_at);
    const end = isoDay(data.term.end_at);
    if (start) course.term.start = start;
    if (end) course.term.end = end;
  }
  const names = (Array.isArray(data?.teachers) ? data.teachers : []).map((t) => clip(t?.display_name, 200)).filter(Boolean);
  course.instructors = [...new Set(names)].slice(0, MAX_INSTRUCTORS);
  return course;
}

/** Section names only, e.g. "A00". Students cannot always list sections, so callers treat a failure as none. */
export async function fetchSections(fetchFn, courseId) {
  const raw = await getList(fetchFn, `${CANVAS_ORIGIN}/api/v1/courses/${courseId}/sections?per_page=100`);
  const names = raw.map((s) => clip(s?.name, 100)).filter(Boolean);
  return [...new Set(names)].slice(0, MAX_SECTIONS);
}

/** Allowlisted file fields. The signed download url Canvas returns is dropped here. */
export async function fetchFiles(fetchFn, courseId) {
  const raw = await getList(fetchFn, `${CANVAS_ORIGIN}/api/v1/courses/${courseId}/files?per_page=100`);
  const files = [];
  for (const f of raw) {
    const name = clip(f?.display_name, 300);
    if (!Number.isSafeInteger(f?.id) || f.id < 1 || !name) continue;
    const file = { id: f.id, display_name: name };
    const type = clip(f["content-type"], 100);
    if (type) file["content-type"] = type;
    files.push(file);
    if (files.length >= MAX_FILES) break;
  }
  return files;
}

/** full_url of the tab labelled Media Gallery. No tool id is hardcoded. */
export async function findMediaGalleryUrl(fetchFn, courseId) {
  const tabs = await getList(fetchFn, `${CANVAS_ORIGIN}/api/v1/courses/${courseId}/tabs`);
  const tab = tabs.find((t) => typeof t?.label === "string" && t.label.trim().toLowerCase() === "media gallery");
  if (!tab) throw new SyncError("NO_MEDIA_GALLERY");
  if (!isCanvasUrl(tab.full_url)) throw new SyncError("TAB_FAILED", "not a Canvas URL");
  return tab.full_url;
}

/** The student's active courses as { id, course_code } only. */
export async function fetchMyCourses(fetchFn) {
  const raw = await getList(fetchFn, `${CANVAS_ORIGIN}/api/v1/courses?enrollment_state=active&per_page=100`);
  const out = [];
  for (const c of raw) {
    const code = clip(c?.course_code, 100);
    if (Number.isSafeInteger(c?.id) && c.id >= 1 && code) out.push({ id: c.id, course_code: code });
  }
  return out;
}

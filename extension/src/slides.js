// Slides: the course's PDF and PowerPoint files, downloaded from Canvas with the student's login
// and handed to the local backend, which reads their text (contracts/api.md, PUT documents).
// A file is sent once: the backend reports the version it holds, and unchanged files are skipped.
// Nothing here keeps a file, its signed download link or its contents after the request.
import { CANVAS_ORIGIN, FETCH_TIMEOUT_MS, FILE_DOWNLOAD_HOSTS, MAX_SLIDE_BYTES, MAX_SLIDE_FILES, SLIDE_TIMEOUT_MS } from "./config.js";
import { confirmBackend, courseKey } from "./backend.js";
import { getList, stripPrefix } from "./canvas.js";
import { clipText, timeoutSignal } from "./util.js";

const TYPES = new Set(["application/pdf", "application/vnd.openxmlformats-officedocument.presentationml.presentation"]);

/** PDF or PowerPoint (.pptx), by Canvas's type or the name, and small enough to send. */
export function isSlideFile(file) {
  if (!file || !Number.isSafeInteger(file.id) || typeof file.display_name !== "string") return false;
  if (Number.isFinite(file.size) && (file.size <= 0 || file.size > MAX_SLIDE_BYTES)) return false;
  return TYPES.has(file["content-type"]) || /\.(pdf|pptx)$/i.test(file.display_name);
}

/** What changes when the file does: its size and when Canvas says it was last modified. */
export function versionOf(file) {
  return `${Number.isFinite(file.size) ? file.size : "?"}:${typeof file.modified_at === "string" ? file.modified_at : "?"}`.slice(0, 100);
}

// A deck posted again: "Week 3 (1).pdf", "Week 3_1015.pdf" (a month and day), "Week 3_v2.pdf",
// "Week 3 copy.pdf". A four-digit year such as "_2026" is not a date suffix (no month 20).
const COPY_SUFFIX = [/\s*\(\d{1,2}\)$/, /[ _-](?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])$/, /[ _-]v\d{1,2}$/, /[ _-]copy$/];

/** The name a deck goes by once copy and revision suffixes are taken off. */
export function deckKey(name) {
  let key = String(name).toLowerCase().replace(/\.(pdf|pptx)$/, "").trim();
  for (;;) {
    const next = COPY_SUFFIX.reduce((k, re) => k.replace(re, ""), key).trim();
    if (next === key) return key;
    key = next;
  }
}

const newer = (a, b) => (Date.parse(a.modified_at ?? "") || 0) - (Date.parse(b.modified_at ?? "") || 0) || a.id - b.id;

/**
 * One file per deck, the most recently modified. `older` lists the other copies' ids and
 * `replacedBy` maps each to the id of the copy that is kept instead.
 */
export function latestRevisions(files) {
  const best = new Map();
  for (const f of files) {
    const key = deckKey(f.display_name);
    const held = best.get(key);
    if (!held || newer(f, held) > 0) best.set(key, f);
  }
  const kept = new Set([...best.values()].map((f) => f.id));
  const older = files.filter((f) => !kept.has(f.id));
  return {
    kept: files.filter((f) => kept.has(f.id)),
    older: older.map((f) => f.id),
    replacedBy: new Map(older.map((f) => [f.id, best.get(deckKey(f.display_name)).id])),
  };
}

// Lecture slides and notes are sent first; exams, solutions and the syllabus last, so a course
// with more files than one sync takes still gets its lectures in the first one.
const FIRST = /\b(lec(ture)?s?|slides?|notes?|chapter|ch)[\s_\-.]*\d|lecture|slides|chapter/i;
const LAST = /exam|final|midterm|soln|solution|answer|key\b|quiz|syllabus|practice/i;
export function slideRank(file) {
  const name = file.display_name.replace(/_/g, " ");
  if (LAST.test(name)) return 2;
  return FIRST.test(name) ? 0 : 1;
}

function fileFields(f) {
  const name = clipText(f?.display_name, 300);
  if (!Number.isSafeInteger(f?.id) || f.id < 1 || !name) return null;
  const file = { id: f.id, display_name: name };
  const type = clipText(f["content-type"], 100);
  if (type) file["content-type"] = type;
  if (Number.isFinite(f.size) && f.size >= 0) file.size = f.size;
  if (typeof f.modified_at === "string" && Number.isFinite(Date.parse(f.modified_at))) file.modified_at = f.modified_at.slice(0, 40);
  return file;
}

/**
 * Files that are linked from Modules. Many courses hide the Files tab, so the file list is
 * forbidden, but a student can still read each file a module links to. Returns the same
 * allowlisted fields as fetchFiles; a file that cannot be read is left out.
 */
export async function fetchModuleFiles(fetchFn, courseId, limit = MAX_SLIDE_FILES) {
  const modules = await getList(fetchFn, `${CANVAS_ORIGIN}/api/v1/courses/${courseId}/modules?include[]=items&per_page=50`);
  const ids = [];
  for (const m of modules) {
    for (const item of Array.isArray(m?.items) ? m.items : []) {
      if (item?.type === "File" && Number.isSafeInteger(item.content_id) && item.content_id > 0 && !ids.includes(item.content_id)) ids.push(item.content_id);
    }
  }
  const files = [];
  for (const id of ids.slice(0, limit)) {
    try {
      const r = await fetchFn(`${CANVAS_ORIGIN}/api/v1/courses/${courseId}/files/${id}`, {
        credentials: "include",
        headers: { Accept: "application/json" },
        signal: timeoutSignal(FETCH_TIMEOUT_MS),
      });
      if (!r.ok) continue;
      const file = fileFields(JSON.parse(stripPrefix(await r.text())));
      if (file) files.push(file);
    } catch {
      // Locked or deleted: skip it.
    }
  }
  return files;
}

/** Files by id, the first list winning; used to join the Files tab with Modules. */
export function mergeFiles(...lists) {
  const byId = new Map();
  for (const list of lists) for (const f of list) if (!byId.has(f.id)) byId.set(f.id, f);
  return [...byId.values()];
}

function allowedDownloadUrl(url) {
  try {
    const u = new URL(url);
    return u.protocol === "https:" && FILE_DOWNLOAD_HOSTS.some((h) => u.hostname === h || u.hostname.endsWith(`.${h}`));
  } catch {
    return false;
  }
}

/**
 * The file's bytes, or null. Canvas is asked for the file's signed link with the login; the link
 * itself is fetched without it. Canvas keeps its files on its file service
 * (`*.inscloudgate.net`), which allows any origin but not a request that carries cookies, so a
 * download that followed Canvas's redirect with the login would be blocked, and no extra host
 * permission is needed this way. A link or a redirect to a host that is not Canvas's is refused.
 */
async function download(fetchFn, courseId, file) {
  let r;
  try {
    const link = await fetchFn(`${CANVAS_ORIGIN}/api/v1/files/${file.id}/public_url`, {
      credentials: "include",
      headers: { Accept: "application/json" },
      signal: timeoutSignal(FETCH_TIMEOUT_MS),
    });
    if (!link.ok) return null;
    const signed = JSON.parse(stripPrefix(await link.text()))?.public_url;
    if (typeof signed !== "string" || !allowedDownloadUrl(signed)) return null;
    r = await fetchFn(signed, { credentials: "omit", signal: timeoutSignal(SLIDE_TIMEOUT_MS) });
  } catch {
    return null;
  }
  if (!r.ok || (r.url && !allowedDownloadUrl(r.url))) return null;
  const declared = Number(r.headers?.get?.("Content-Length"));
  if (Number.isFinite(declared) && declared > MAX_SLIDE_BYTES) return null;
  try {
    const bytes = await r.arrayBuffer();
    return bytes.byteLength > 0 && bytes.byteLength <= MAX_SLIDE_BYTES ? bytes : null;
  } catch {
    return null;
  }
}

async function storedVersions(fetchFn, base, courseId) {
  const r = await fetchFn(`${base}/courses/${courseKey(courseId)}/documents`, { credentials: "omit", signal: timeoutSignal(FETCH_TIMEOUT_MS) });
  if (!r.ok) throw new Error("documents");
  const list = JSON.parse(await r.text());
  return new Map((Array.isArray(list) ? list : []).filter((d) => typeof d?.doc_id === "string").map((d) => [d.doc_id, d.version]));
}

/**
 * Sends every new or changed slide file of the course to the backend.
 * Returns { found, sent, unchanged, failed, unreadable, later }. Never throws: slides are an
 * extra, and a failure here must not fail the lecture sync that already succeeded.
 */
export async function syncSlides({ fetchFn, backendUrl, courseId, files, report = () => {} }) {
  const out = { found: 0, sent: 0, unchanged: 0, failed: 0, unreadable: 0, later: 0, older: 0 };
  const all = files.filter(isSlideFile);
  out.found = all.length;
  const { kept: slides, older, replacedBy } = latestRevisions(all);
  out.older = older.length;
  if (!slides.length) return out;
  let stored;
  try {
    await confirmBackend(fetchFn, backendUrl);
    stored = await storedVersions(fetchFn, backendUrl, courseId);
  } catch {
    out.failed = slides.length;
    return out;
  }
  const todo = slides
    .filter((f) => stored.get(`canvas-file-${f.id}`) !== versionOf(f))
    .sort((a, b) => slideRank(a) - slideRank(b) || (Date.parse(b.modified_at ?? "") || 0) - (Date.parse(a.modified_at ?? "") || 0));
  out.unchanged = slides.length - todo.length;
  out.later = Math.max(0, todo.length - MAX_SLIDE_FILES);
  const now = todo.slice(0, MAX_SLIDE_FILES);
  // Files the backend holds in their current version, so far.
  const current = new Set(slides.filter((f) => stored.get(`canvas-file-${f.id}`) === versionOf(f)).map((f) => f.id));
  report({ stage: "slides", done: 0, total: now.length });
  for (const [i, file] of now.entries()) {
    const bytes = await download(fetchFn, courseId, file);
    if (!bytes) {
      out.failed++;
    } else {
      const query = new URLSearchParams({ name: file.display_name, version: versionOf(file) });
      try {
        const r = await fetchFn(`${backendUrl}/courses/${courseKey(courseId)}/documents/canvas/${file.id}?${query}`, {
          method: "PUT",
          credentials: "omit",
          headers: { "Content-Type": "application/octet-stream" },
          body: bytes,
          signal: timeoutSignal(SLIDE_TIMEOUT_MS),
        });
        if (r.ok) {
          out.sent++;
          current.add(file.id);
        } else if (r.status === 422) out.unreadable++; // scanned pages, or not really a PDF
        else out.failed++;
      } catch {
        out.failed++;
      }
    }
    report({ stage: "slides", done: i + 1, total: now.length });
  }
  // An older copy the backend holds is dropped only once its newer copy is stored, so a deck is
  // never left out of search; if the newer one failed or waits for the next sync, both stay.
  for (const id of older) {
    if (!stored.has(`canvas-file-${id}`) || !current.has(replacedBy.get(id))) continue;
    try {
      await fetchFn(`${backendUrl}/courses/${courseKey(courseId)}/documents/canvas/${id}`, {
        method: "DELETE",
        credentials: "omit",
        signal: timeoutSignal(FETCH_TIMEOUT_MS),
      });
    } catch {
      // Left for the next sync.
    }
  }
  return out;
}

/** One line for the popup, or null when the course has no slide files. */
export function slidesNote(r) {
  if (!r.found) return null;
  const parts = [];
  if (r.sent) parts.push(`${r.sent} new`);
  if (r.unchanged) parts.push(`${r.unchanged} already synced`);
  if (r.unreadable) parts.push(`${r.unreadable} with no readable text`);
  if (r.failed) parts.push(`${r.failed} could not be downloaded`);
  if (r.later) parts.push(`${r.later} left for the next sync`);
  if (r.older) parts.push(`${r.older} older ${r.older === 1 ? "copy" : "copies"} skipped`);
  return `Slides: ${r.found} PDF and PowerPoint files (${parts.join(", ")}).`;
}

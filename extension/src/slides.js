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

/** The file's bytes, or null. Refuses a download that ends on a host that is not Canvas's. */
async function download(fetchFn, courseId, file) {
  let r;
  try {
    r = await fetchFn(`${CANVAS_ORIGIN}/courses/${courseId}/files/${file.id}/download?download_frd=1`, {
      credentials: "include",
      signal: timeoutSignal(SLIDE_TIMEOUT_MS),
    });
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
  const out = { found: 0, sent: 0, unchanged: 0, failed: 0, unreadable: 0, later: 0 };
  const slides = files.filter(isSlideFile);
  out.found = slides.length;
  if (!slides.length) return out;
  let stored;
  try {
    await confirmBackend(fetchFn, backendUrl);
    stored = await storedVersions(fetchFn, backendUrl, courseId);
  } catch {
    out.failed = slides.length;
    return out;
  }
  const todo = slides.filter((f) => stored.get(`canvas-file-${f.id}`) !== versionOf(f));
  out.unchanged = slides.length - todo.length;
  out.later = Math.max(0, todo.length - MAX_SLIDE_FILES);
  const now = todo.slice(0, MAX_SLIDE_FILES);
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
        if (r.ok) out.sent++;
        else if (r.status === 422) out.unreadable++; // scanned pages, or not really a PDF
        else out.failed++;
      } catch {
        out.failed++;
      }
    }
    report({ stage: "slides", done: i + 1, total: now.length });
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
  return `Slides: ${r.found} PDF and PowerPoint files (${parts.join(", ")}).`;
}

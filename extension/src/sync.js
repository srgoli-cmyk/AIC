// The sync orchestrator. Everything it touches is injected (fetch, tabs, scripting, clock), and
// every step is sequential: one request or one injected call at a time.
import { confirmBackend, getLectures, postSync } from "./backend.js";
import { fetchModuleFiles, isSlideFile, mergeFiles, slidesNote, syncSlides } from "./slides.js";
import { fetchCourse, fetchFiles, fetchSections, findMediaGalleryUrl, isCanvasUrl, parseCourseId } from "./canvas.js";
import {
  CATEGORY_POLL_MS,
  CATEGORY_PROBE_MS,
  CATEGORY_WAIT_MS,
  ENTRY_CALL_MS,
  GALLERY_CALL_MS,
  KAF_HOST,
  KS_PREFIX,
  MAX_FRAME_ERRORS_IN_ROW,
  MAX_GALLERY_PAGES,
  MAX_LECTURES,
} from "./config.js";
import { SyncError } from "./errors.js";
import { collectEntryIds, findCategory, readEntryCaptions } from "./frame.js";
import { buildSyncPayload } from "./payload.js";
import { clipText, hasSessionLikeToken } from "./util.js";

const CATEGORY_ID = /^\d{1,15}$/;
const ENTRY_ID = /^[0-9]_[a-z0-9]{8}$/;
const MAX_SRT = 2000000;
const MAX_CREATED_AT = 4102444800; // the year 2100, in seconds

// Codes are plain capitals, optionally behind a call name; anything else is dropped.
export function safeCode(code) {
  return typeof code === "string" && /^([a-z]{1,10}:)?([A-Z0-9_]{1,64}|empty)$/.test(code) ? code : "UNKNOWN";
}

/** Validates what a frame returned for one entry. Only allowlisted fields survive. */
export function normalizeEntry(raw, entryId, host = KAF_HOST) {
  if (!raw || typeof raw !== "object" || raw.host !== host) return { ok: false, codes: ["FRAME_ERROR"] };
  if (raw.entryId !== undefined && raw.entryId !== entryId) return { ok: false, codes: ["FRAME_ERROR"] };
  if (raw.ok !== true) {
    const failures = Array.isArray(raw.failures) ? raw.failures.slice(0, 6).map(safeCode) : [];
    return { ok: false, codes: [safeCode(raw.reason), ...failures], entryId };
  }
  if (!entryId) return { ok: false, codes: ["NO_ENTRY"] };
  if (typeof raw.srt !== "string" || !raw.srt.includes("-->")) return { ok: false, codes: ["BAD_RESULT"] };
  if (raw.srt.length > MAX_SRT) return { ok: false, codes: ["TOO_LARGE"] };
  const name = clipText(raw.title, 300);
  if (!name) return { ok: false, codes: ["NO_TITLE"] };
  const fromPage = raw.titleSource !== "kaltura";
  if (fromPage && hasSessionLikeToken(name)) return { ok: false, codes: ["NO_TITLE"] };
  const entry = {
    id: entryId,
    name,
    duration: !fromPage && Number.isFinite(raw.duration) && raw.duration >= 0 ? raw.duration : 0,
    srt: raw.srt,
  };
  if (!fromPage && Number.isFinite(raw.createdAt) && raw.createdAt >= 0 && raw.createdAt <= MAX_CREATED_AT) {
    entry.createdAt = raw.createdAt;
  }
  const ksLength = Number.isInteger(raw.ksLength) && raw.ksLength > 0 && raw.ksLength < 10000 ? raw.ksLength : undefined;
  return { ok: true, entry, fromPage, ksLength };
}

// Rejects when the promise has not settled after `ms`. Built from a timer so a stuck frame cannot
// hold the sync; the timer functions can be replaced in tests.
function withDeadline(deps, promise, ms) {
  return new Promise((resolve, reject) => {
    const set = deps.setTimer ?? setTimeout;
    const clear = deps.clearTimer ?? clearTimeout;
    const timer = set(() => reject(new Error("deadline")), ms);
    promise.then(
      (value) => {
        clear(timer);
        resolve(value);
      },
      (error) => {
        clear(timer);
        reject(error);
      },
    );
  });
}

export async function runIn(deps, target, func, args, ms) {
  const results = await withDeadline(deps, deps.scripting.executeScript({ target, func, args }), ms);
  return Array.isArray(results) ? results : [];
}

// Polls until a frame on the lecture site reports its category id. The overall wait holds even if
// a frame never answers, because each probe is limited by the time left.
async function waitForCategory(deps, tabId) {
  const { sleep, now } = deps;
  const start = now();
  let sawKaf = false;
  for (;;) {
    const left = CATEGORY_WAIT_MS - (now() - start);
    let results = [];
    try {
      results = await runIn(deps, { tabId, allFrames: true }, findCategory, [], Math.max(1, Math.min(CATEGORY_PROBE_MS, left)));
    } catch {
      // The tab may still be loading, or a frame is stuck; try again until the time is up.
    }
    for (const r of results) {
      const v = r?.result;
      if (!v || v.host !== KAF_HOST) continue;
      sawKaf = true;
      if (CATEGORY_ID.test(String(v.categoryId)) && Number.isInteger(r.frameId)) {
        return { frameId: r.frameId, categoryId: String(v.categoryId) };
      }
    }
    if (now() - start >= CATEGORY_WAIT_MS) throw new SyncError(sawKaf ? "KAF_FRAME_NO_CATEGORY" : "NO_KAF_FRAME");
    await sleep(CATEGORY_POLL_MS);
  }
}

// One retry covers a frame that navigated while the call was running: find the frame again first.
async function readGallery(deps, tabId, frame) {
  let results;
  for (let attempt = 0; ; attempt++) {
    try {
      results = await runIn(deps, { tabId, frameIds: [frame.frameId] }, collectEntryIds, [frame.categoryId, MAX_GALLERY_PAGES], GALLERY_CALL_MS);
      break;
    } catch {
      if (attempt > 0) throw new SyncError("NO_ENTRIES", "frame call failed");
      frame = await waitForCategory(deps, tabId);
    }
  }
  const v = results[0]?.result;
  if (!v || v.host !== KAF_HOST || !Array.isArray(v.entryIds)) throw new SyncError("NO_ENTRIES", "bad result");
  const entryIds = [...new Set(v.entryIds.filter((id) => typeof id === "string" && ENTRY_ID.test(id)))];
  const partial = v.problem ? safeCode(v.problem) : null;
  if (entryIds.length === 0) throw new SyncError("NO_ENTRIES", partial || undefined);
  return { entryIds, partial, frame };
}

export function bump(map, key) {
  map[key] = (map[key] || 0) + 1;
}

export function courseError(e) {
  const detail = e instanceof SyncError ? e.detail : undefined;
  // 401 and 403 are a missing login; Canvas sends a login page (not JSON) when redirected.
  if (detail === "HTTP 401" || detail === "HTTP 403" || detail === "not JSON") return new SyncError("CANVAS_LOGIN", detail);
  return new SyncError("CANVAS_FAILED", detail);
}

/**
 * Runs the whole sync for the course shown in `tabUrl`.
 * Returns { courseId, code, lectures, pending, finishedAt, notes, skipped }: counts, fixed text and
 * the course code (shown on the popup's link to the course page).
 * deps.tabOpened(id) / deps.tabClosed() let the caller remember the background tab.
 */
export async function runSync(deps) {
  const { fetch: fetchFn, tabs, backendUrl, report, now } = deps;
  const courseId = parseCourseId(deps.tabUrl);
  if (!courseId) throw new SyncError("NOT_COURSE_PAGE");
  const notes = [];
  const skipped = {};

  report({ stage: "backend", done: 0, total: 0 });
  await confirmBackend(fetchFn, backendUrl);

  report({ stage: "course", done: 0, total: 0 });
  let course;
  try {
    course = await fetchCourse(fetchFn, courseId);
  } catch (e) {
    throw courseError(e);
  }
  if (!course.course_code || !course.name) throw new SyncError("COURSE_INCOMPLETE");
  try {
    course.sections = await fetchSections(fetchFn, courseId);
  } catch {
    // Students can't always list sections. The course syncs without them.
  }

  report({ stage: "files", done: 0, total: 0 });
  let files = [];
  let filesHidden = false;
  try {
    files = await fetchFiles(fetchFn, courseId);
  } catch {
    // Often forbidden for students. An empty list leaves already stored files alone.
    filesHidden = true;
  }
  // Files linked from Modules can be read even when the Files tab is hidden.
  try {
    files = mergeFiles(files, await fetchModuleFiles(fetchFn, courseId));
  } catch {
    // No Modules, or they are hidden too.
  }
  if (filesHidden && !files.length) notes.push("Course files could not be read, so none were synced.");

  report({ stage: "gallery", done: 0, total: 0 });
  let galleryUrl = null;
  try {
    galleryUrl = await findMediaGalleryUrl(fetchFn, courseId);
  } catch (e) {
    // Syncing every class, a course without recordings still has its slides read.
    if (!(deps.slidesOnlyOk && e instanceof SyncError && e.code === "NO_MEDIA_GALLERY")) throw e;
  }
  if (galleryUrl === null && !files.some(isSlideFile)) throw new SyncError("NOTHING_TO_SYNC");
  if (galleryUrl !== null && !isCanvasUrl(galleryUrl)) throw new SyncError("TAB_FAILED");
  if (galleryUrl === null) notes.push("No Media Gallery, so only slides were synced.");

  const entries = [];
  let todoCount = 0;
  let processed = 0;
  let noSession = 0;
  let titleFallbacks = 0;
  let storedBefore = new Map();
  let tab;
  let categoryId; // the gallery the listed entries came from; set once the list is read
  if (galleryUrl !== null) {
    try {
      try {
        tab = await tabs.create({ url: galleryUrl, active: false });
      } catch {
        throw new SyncError("TAB_FAILED");
      }
      if (!Number.isInteger(tab?.id)) throw new SyncError("TAB_FAILED");
      await deps.tabOpened?.(tab.id);

      let frame = await waitForCategory(deps, tab.id);
      const gallery = await readGallery(deps, tab.id, frame);
      frame = gallery.frame;
      categoryId = CATEGORY_ID.test(String(frame.categoryId)) ? String(frame.categoryId) : undefined;
      if (gallery.partial) notes.push(`The gallery list may be incomplete (${gallery.partial}).`);

      storedBefore = new Map((await getLectures(fetchFn, backendUrl, courseId)).map((l) => [l.entry_id, l.has_captions]));
      const wanted = gallery.entryIds.filter((id) => storedBefore.get(id) !== true);
      todoCount = wanted.length;
      // Only what fits in one sync is read, so nothing is downloaded and then dropped.
      const todo = wanted.slice(0, MAX_LECTURES);
      if (wanted.length > todo.length) {
        notes.push(`${wanted.length - todo.length} lectures will be read on the next sync (${MAX_LECTURES} per sync).`);
      }

      let preferredLength = null;
      let frameErrors = 0;
      report({ stage: "captions", done: 0, total: todo.length });
      for (const [i, id] of todo.entries()) {
        let outcome;
        try {
          const results = await runIn(
            deps,
            { tabId: tab.id, frameIds: [frame.frameId] },
            readEntryCaptions,
            [id, frame.categoryId, preferredLength, KS_PREFIX],
            ENTRY_CALL_MS,
          );
          outcome = normalizeEntry(results[0]?.result, id);
        } catch {
          outcome = { ok: false, codes: ["FRAME_ERROR"] };
        }
        processed++;
        if (outcome.ok) {
          frameErrors = 0;
          entries.push(outcome.entry);
          if (outcome.fromPage) titleFallbacks++;
          if (outcome.ksLength) preferredLength = outcome.ksLength;
        } else {
          const [reason, ...details] = outcome.codes;
          bump(skipped, reason);
          if (reason === "NO_SESSION") noSession++;
          for (const d of details) bump(skipped, d);
          frameErrors = reason === "FRAME_ERROR" ? frameErrors + 1 : 0;
        }
        report({ stage: "captions", done: i + 1, total: todo.length });
        if (frameErrors >= MAX_FRAME_ERRORS_IN_ROW) {
          notes.push("Stopped early: the Media Gallery page stopped responding.");
          break;
        }
      }
    } finally {
      if (Number.isInteger(tab?.id)) {
        try {
          await tabs.remove(tab.id);
        } catch {
          // Already closed.
        }
        try {
          await deps.tabClosed?.();
        } catch {
          // Nothing to forget.
        }
      }
    }
  }

  // Nothing could be read although something was wanted: an empty sync would hide the problem.
  if (todoCount > 0 && entries.length === 0) {
    throw new SyncError(processed > 0 && noSession === processed ? "NO_SESSION_FOUND" : "NO_CAPTIONS_READ");
  }
  if (titleFallbacks) notes.push(`${titleFallbacks} lecture titles come from the page title and have no duration.`);

  report({ stage: "sending", done: 0, total: 0 });
  const payload = buildSyncPayload({ course, entries, files, categoryId, now: new Date(now()) });
  const reply = await postSync(fetchFn, backendUrl, payload);
  if (reply.skippedOtherCourse) {
    notes.push(`${reply.skippedOtherCourse} lectures already belong to another course and were left unchanged.`);
  }

  // The course now exists in the backend, so its slide files can be stored with it.
  const slideNote = slidesNote(await syncSlides({ fetchFn, backendUrl, courseId, files, report }));
  if (slideNote) notes.push(slideNote);

  let storedCount;
  try {
    storedCount = (await getLectures(fetchFn, backendUrl, courseId)).length;
  } catch {
    // The sync itself succeeded; estimate from what was known.
    storedCount = new Set([...storedBefore.keys(), ...entries.map((e) => e.id)]).size;
    notes.push("The stored lecture count could not be read after the sync, so it is an estimate.");
  }
  return {
    courseId,
    code: course.course_code,
    lectures: storedCount,
    added: entries.filter((e) => !storedBefore.has(e.id)).length,
    slides: slideNote,
    pending: todoCount - entries.length,
    finishedAt: new Date(now()).toISOString(),
    notes,
    skipped,
  };
}

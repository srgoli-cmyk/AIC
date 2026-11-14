// Syncs every active Canvas course of the student in one run: for each one, the same sync as
// "Sync this Canvas course" (lectures from its Media Gallery, then its slides). A course without
// a Media Gallery still has its slides read; a course with neither is skipped. One course failing
// does not stop the others, but a missing Canvas login or a backend problem stops the run, since
// every later course would fail the same way.
import { confirmBackend } from "./backend.js";
import { fetchMyCourses } from "./canvas.js";
import { CANVAS_ORIGIN } from "./config.js";
import { SyncError, messageFor } from "./errors.js";
import { displayCode } from "./summary.js";
import { courseError, runSync } from "./sync.js";

// Problems that would repeat for every course.
const STOPS_ALL = new Set(["CANVAS_LOGIN", "BACKEND_DOWN", "BACKEND_OTHER", "BACKEND_REJECTED", "KS_IN_PAYLOAD"]);

/**
 * Returns { mode: "all", courses: [{ courseId, code, lectures, added, slides }], pending,
 * finishedAt, notes, skipped }. deps are runSync's, without tabUrl; tests may pass runOne.
 */
export async function runAllSync(deps) {
  const { fetch: fetchFn, backendUrl, report, now } = deps;
  const courses = [];
  const notes = [];
  const skipped = {};
  const empty = [];
  let pending = 0;
  let lastError;

  report({ stage: "backend", done: 0, total: 0 });
  await confirmBackend(fetchFn, backendUrl);

  report({ stage: "canvas", done: 0, total: 0 });
  let mine;
  try {
    mine = await fetchMyCourses(fetchFn);
  } catch (e) {
    throw courseError(e);
  }
  if (mine.length === 0) throw new SyncError("NO_CANVAS_COURSES");

  for (const [i, c] of mine.entries()) {
    const label = displayCode(c.course_code);
    const where = { index: i + 1, count: mine.length, label };
    try {
      const s = await (deps.runOne ?? runSync)({
        ...deps,
        tabUrl: `${CANVAS_ORIGIN}/courses/${c.id}`,
        slidesOnlyOk: true,
        report: (patch) => report({ ...patch, ...where }),
      });
      courses.push({ courseId: s.courseId, code: s.code, lectures: s.lectures, added: s.added, slides: s.slides ?? null });
      pending += s.pending;
      for (const note of s.notes) if (!note.startsWith("Slides:")) notes.push(`${label}: ${note}`);
      for (const [code, n] of Object.entries(s.skipped)) skipped[code] = (skipped[code] || 0) + n;
    } catch (e) {
      const code = e instanceof SyncError ? e.code : "UNKNOWN";
      if (STOPS_ALL.has(code)) throw e;
      if (code === "NOTHING_TO_SYNC" || code === "NO_MEDIA_GALLERY") empty.push(label);
      else notes.push(`${label} could not be synced: ${messageFor(e)}`);
      lastError = e;
    }
  }
  if (empty.length) notes.push(`No recordings or slides: ${empty.join(", ")}.`);
  if (courses.length === 0) throw lastError instanceof SyncError && lastError.code !== "NOTHING_TO_SYNC" ? lastError : new SyncError("ALL_FAILED");
  return { mode: "all", courses, pending, finishedAt: new Date(now()).toISOString(), notes, skipped };
}

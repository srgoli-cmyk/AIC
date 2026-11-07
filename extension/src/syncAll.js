// Syncs every class podcast.ucsd.edu shows under My Podcasts, with all of its lecture sections, in
// one run. Each class is stored under the student's Canvas course for it. Like sync.js, everything
// is injected and every step is sequential.
import { httpStore } from "./backend.js";
import { fetchCourse, fetchMyCourses, fetchSections } from "./canvas.js";
import {
  ENTRY_CALL_MS,
  KS_PREFIX,
  LIST_CALL_MS,
  MAX_FRAME_ERRORS_IN_ROW,
  MAX_LECTURES,
  PODCAST_HOST,
  PODCAST_ORIGIN,
  PODCAST_WAIT_MS,
  CATEGORY_POLL_MS,
  CATEGORY_PROBE_MS,
} from "./config.js";
import { SyncError } from "./errors.js";
import { readEntryCaptions } from "./frame.js";
import { buildSyncPayload } from "./payload.js";
import { classLabel, lectureUrl, matchClasses, parseSectionPath } from "./podcast.js";
import { listLectures, listMyPodcasts, podcastHost } from "./podcastFrame.js";
import { bump, courseError, normalizeEntry, runIn, safeCode } from "./sync.js";

// Waits until the tab shows podcast.ucsd.edu. A tab that stays elsewhere (the UCSD login page) is
// a missing login.
async function waitForPodcast(deps, tabId) {
  const { sleep, now } = deps;
  const start = now();
  for (;;) {
    const left = PODCAST_WAIT_MS - (now() - start);
    try {
      const results = await runIn(deps, { tabId, frameIds: [0] }, podcastHost, [], Math.max(1, Math.min(CATEGORY_PROBE_MS, left)));
      if (results[0]?.result?.host === PODCAST_HOST) return;
    } catch {
      // Still loading, or on a page the extension cannot read.
    }
    if (now() - start >= PODCAST_WAIT_MS) throw new SyncError("PODCAST_LOGIN");
    await sleep(CATEGORY_POLL_MS);
  }
}

async function callTab(deps, tabId, func, args, ms) {
  const results = await runIn(deps, { tabId, frameIds: [0] }, func, args, ms);
  const v = results[0]?.result;
  if (!v || v.host !== PODCAST_HOST) throw new SyncError("PODCAST_LOGIN", "left the site");
  return v;
}

/**
 * Returns { mode: "all", courses: [{ courseId, code, lectures, added }], pending, finishedAt,
 * notes, skipped }: counts, course codes and fixed text only.
 */
export async function runAllSync(deps) {
  const { fetch: fetchFn, tabs, backendUrl, report, now } = deps;
  // The IndexedDB store when the extension runs on its own; the local HTTP backend otherwise.
  const store = deps.store ?? httpStore(fetchFn, backendUrl);
  const notes = [];
  const skipped = {};
  const courses = [];
  let pending = 0;

  report({ stage: "backend", done: 0, total: 0 });
  await store.confirm();

  report({ stage: "canvas", done: 0, total: 0 });
  let canvasCourses;
  try {
    canvasCourses = await fetchMyCourses(fetchFn);
  } catch (e) {
    throw courseError(e);
  }

  let tab;
  let lastError;
  try {
    try {
      tab = await tabs.create({ url: `${PODCAST_ORIGIN}/MyCourses.aspx`, active: false });
    } catch {
      throw new SyncError("TAB_FAILED");
    }
    if (!Number.isInteger(tab?.id)) throw new SyncError("TAB_FAILED");
    await deps.tabOpened?.(tab.id);

    report({ stage: "classes", done: 0, total: 0 });
    await waitForPodcast(deps, tab.id);
    const mine = await callTab(deps, tab.id, listMyPodcasts, [], LIST_CALL_MS);
    if (mine.problem === "LOGIN") throw new SyncError("PODCAST_LOGIN");
    const paths = (Array.isArray(mine.sections) ? mine.sections : [])
      .map((s) => s?.path)
      .filter((p) => parseSectionPath(p));
    if (paths.length === 0) throw new SyncError("PODCAST_NONE", mine.problem ? safeCode(mine.problem) : undefined);

    const { matched, unmatched } = matchClasses(paths, canvasCourses);
    for (const key of unmatched) notes.push(`${classLabel(key)} has recordings but no Canvas course, so it was skipped.`);
    if (matched.length === 0) throw new SyncError("NO_CANVAS_MATCH");

    // First every lecture list, so progress has a total.
    report({ stage: "lectures", done: 0, total: 0 });
    for (const cls of matched) {
      cls.pages = [];
      for (const path of cls.sections) {
        const listed = await callTab(deps, tab.id, listLectures, [path], LIST_CALL_MS);
        if (listed.problem === "LOGIN") throw new SyncError("PODCAST_LOGIN");
        if (listed.problem) notes.push(`A ${classLabel(parseSectionPath(path).key)} section could not be listed (${safeCode(listed.problem)}).`);
        for (const n of Array.isArray(listed.lectures) ? listed.lectures : []) {
          if (Number.isInteger(n) && n >= 1 && n <= 500) cls.pages.push({ path, n });
        }
      }
    }
    const total = matched.reduce((s, c) => s + c.pages.length, 0);

    let done = 0;
    let preferredLength = null;
    let frameErrors = 0;
    report({ stage: "captions", done, total });
    for (const cls of matched) {
      let stored;
      let course;
      try {
        stored = await store.lectures(cls.courseId);
        course = await fetchCourse(fetchFn, cls.courseId);
        if (!course.course_code || !course.name) throw new SyncError("COURSE_INCOMPLETE");
        try {
          course.sections = await fetchSections(fetchFn, cls.courseId);
        } catch {
          // Students can't always list sections.
        }
      } catch (e) {
        lastError = e;
        notes.push(`${classLabel(parseSectionPath(cls.sections[0]).key)} was skipped: its course could not be read.`);
        done += cls.pages.length;
        report({ stage: "captions", done, total });
        continue;
      }
      const skipIds = stored.filter((l) => l.has_captions).map((l) => l.entry_id);
      const entries = [];
      let capped = 0;
      for (const { path, n } of cls.pages) {
        if (entries.length >= MAX_LECTURES) {
          capped++;
          continue;
        }
        let outcome;
        let alreadyStored = false;
        try {
          const results = await runIn(
            deps,
            { tabId: tab.id, frameIds: [0] },
            readEntryCaptions,
            [null, null, preferredLength, KS_PREFIX, `${path}/${n}`, skipIds],
            ENTRY_CALL_MS,
          );
          const raw = results[0]?.result;
          alreadyStored = raw?.host === PODCAST_HOST && raw.ok === false && raw.reason === "ALREADY_STORED";
          // The tab answered with nothing, or from another site: say which, both count as tab errors.
          if (!raw || typeof raw !== "object") outcome = { ok: false, codes: ["FRAME_ERROR", "NO_RESULT"] };
          else if (raw.host !== PODCAST_HOST) outcome = { ok: false, codes: ["FRAME_ERROR", "WRONG_HOST"] };
          else outcome = alreadyStored ? null : normalizeEntry(raw, null, PODCAST_HOST);
        } catch (e) {
          outcome = { ok: false, codes: ["FRAME_ERROR", e?.message === "deadline" ? "TIMEOUT" : "INJECT_FAILED"] };
        }
        if (outcome?.ok) {
          frameErrors = 0;
          if (!entries.some((e) => e.id === outcome.entry.id)) {
            entries.push({ ...outcome.entry, mediaUrl: lectureUrl(path, n) });
            skipIds.push(outcome.entry.id);
          }
          if (outcome.ksLength) preferredLength = outcome.ksLength;
        } else if (outcome) {
          const [reason, ...details] = outcome.codes;
          bump(skipped, reason);
          for (const d of details) bump(skipped, d);
          pending++;
          frameErrors = reason === "FRAME_ERROR" ? frameErrors + 1 : 0;
        } else {
          frameErrors = 0;
        }
        done++;
        report({ stage: "captions", done, total });
        if (frameErrors >= MAX_FRAME_ERRORS_IN_ROW) break;
        // A tab that stopped answering (discarded in the background, or moved off the site) is
        // sent back to the site before the next lecture.
        if (frameErrors > 0 && typeof tabs.update === "function") {
          try {
            await tabs.update(tab.id, { url: `${PODCAST_ORIGIN}/MyCourses.aspx` });
            await waitForPodcast(deps, tab.id);
          } catch {
            // The next call reports what is wrong.
          }
        }
      }
      if (capped) notes.push(`${capped} lectures will be read on the next sync (${MAX_LECTURES} per class per sync).`);

      const before = new Set(stored.map((l) => l.entry_id));
      try {
        if (entries.length) {
          report({ stage: "sending", done, total });
          const reply = await store.save(buildSyncPayload({ course, entries, files: [], now: new Date(now()) }));
          if (reply.skippedOtherCourse) {
            notes.push(`${reply.skippedOtherCourse} lectures already belong to another course and were left unchanged.`);
          }
        }
        let count;
        try {
          count = (await store.lectures(cls.courseId)).length;
        } catch {
          count = new Set([...before, ...entries.map((e) => e.id)]).size;
        }
        courses.push({
          courseId: cls.courseId,
          code: course.course_code,
          lectures: count,
          added: entries.filter((e) => !before.has(e.id)).length,
        });
        report({ stage: "captions", done, total });
      } catch (e) {
        lastError = e;
        notes.push(`${classLabel(parseSectionPath(cls.sections[0]).key)} could not be sent to the backend.`);
      }
      // What was read is kept above; the rest of this class waits for the next sync.
      if (frameErrors >= MAX_FRAME_ERRORS_IN_ROW) {
        notes.push(`${classLabel(parseSectionPath(cls.sections[0]).key)} stopped early: its lecture pages stopped responding.`);
        if (courses.length === 0) lastError = new SyncError("PODCAST_LOGIN", "page stopped responding");
        frameErrors = 0;
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

  if (courses.length === 0) throw lastError instanceof SyncError ? lastError : new SyncError("ALL_FAILED");
  return { mode: "all", courses, pending, finishedAt: new Date(now()).toISOString(), notes, skipped };
}

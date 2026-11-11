// The lecture store inside the browser (IndexedDB), in place of the Postgres backend. Same rules as
// backend/app/db.py: nothing is ever deleted by a sync, a lecture stored under one course is never
// moved to another, optional fields never erase a stored value, and chunks are replaced only for
// the captions they were built from.
//
// Everything stays in this browser profile. Nothing here touches the network.
import { CHUNKER_VERSION } from "./chunking.js";
import { parseTitle } from "./titles.js";

export const DB_NAME = "lecture-search";
const DB_VERSION = 1;
// Part of every captions hash, so a new chunker or embedding model makes every lecture due again.
export const INDEX_VERSION = `chunker-${CHUNKER_VERSION}|bge-small-en-v1.5-q8`;

export const courseIdFor = (canvasCourseId) => `canvas-${canvasCourseId}`;

export class StoreError extends Error {
  constructor(code) {
    super(code);
    this.name = "StoreError";
    this.code = code;
  }
}

const done = (req) =>
  new Promise((resolve, reject) => {
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });

const finished = (tx) =>
  new Promise((resolve, reject) => {
    tx.oncomplete = () => resolve();
    tx.onerror = () => reject(tx.error);
    tx.onabort = () => reject(tx.error || new StoreError("ABORTED"));
  });

/** Opens (and creates or upgrades) the database. `indexedDB` is injectable for tests. */
export function openDb(idb = globalThis.indexedDB, name = DB_NAME) {
  return new Promise((resolve, reject) => {
    if (!idb) {
      reject(new StoreError("NO_INDEXEDDB"));
      return;
    }
    const req = idb.open(name, DB_VERSION);
    req.onupgradeneeded = () => {
      const db = req.result;
      if (!db.objectStoreNames.contains("courses")) db.createObjectStore("courses", { keyPath: "course_id" });
      if (!db.objectStoreNames.contains("lectures")) {
        const lectures = db.createObjectStore("lectures", { keyPath: "entry_id" });
        lectures.createIndex("by_course", "course_id");
      }
      if (!db.objectStoreNames.contains("chunks")) {
        const chunks = db.createObjectStore("chunks", { keyPath: "id" });
        chunks.createIndex("by_entry", "entry_id");
        chunks.createIndex("by_course", "course_id");
      }
    };
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error || new StoreError("OPEN_FAILED"));
    req.onblocked = () => reject(new StoreError("OPEN_BLOCKED"));
  });
}

/** SHA-256 of the captions, prefixed with the index version, as hex. */
export async function captionsHash(captions) {
  const bytes = new TextEncoder().encode(`${INDEX_VERSION}\n${captions}`);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

const isDue = (lecture) => lecture.chunked_hash !== lecture.captions_hash;
const coalesce = (next, prev) => (next === undefined || next === null ? (prev ?? null) : next);

/**
 * Upserts a validated sync payload in ONE transaction. Returns { courseId, skipped }: skipped is
 * the number of distinct lectures left alone because they belong to another course.
 */
export async function saveSync(db, payload) {
  const cid = courseIdFor(payload.course.canvas_course_id);
  // One row per entry_id (the last one wins). Hashes are computed before the transaction opens:
  // an IndexedDB transaction closes if it waits on anything that is not a request.
  const rows = new Map();
  for (const lec of payload.lectures) rows.set(lec.entry_id, lec);
  const hashes = new Map();
  for (const [id, lec] of rows) hashes.set(id, await captionsHash(lec.captions_srt));

  const tx = db.transaction(["courses", "lectures"], "readwrite");
  const courses = tx.objectStore("courses");
  const lectures = tx.objectStore("lectures");
  let skipped = 0;
  const c = payload.course;
  const prev = await done(courses.get(cid));
  courses.put({
    course_id: cid,
    canvas_course_id: c.canvas_course_id,
    code: c.code,
    name: c.name,
    term: coalesce(c.term, prev?.term),
    term_start: coalesce(c.term_start, prev?.term_start),
    term_end: coalesce(c.term_end, prev?.term_end),
    instructors: coalesce(c.instructors, prev?.instructors),
    sections: coalesce(c.sections, prev?.sections),
    synced_at: payload.synced_at,
    indexed: prev?.indexed ?? false,
  });
  for (const [id, lec] of rows) {
    const old = await done(lectures.get(id));
    if (old && old.course_id !== cid) {
      skipped++; // a lecture stored under another course is left completely untouched
      continue;
    }
    const { lectureNo, section } = parseTitle(lec.title);
    lectures.put({
      entry_id: id,
      course_id: cid,
      title: lec.title,
      lecture_no: lectureNo,
      section,
      recorded_at: coalesce(lec.recorded_at, old?.recorded_at),
      recorded_start: coalesce(lec.recorded_start, old?.recorded_start),
      duration_sec: lec.duration_sec,
      media_url: lec.media_url,
      captions_srt: lec.captions_srt,
      captions_hash: hashes.get(id),
      chunked_hash: old?.chunked_hash ?? null,
    });
  }
  // Never claim "indexed" while a lecture is new or has changed captions.
  const all = await done(lectures.index("by_course").getAll(cid));
  if (all.some(isDue)) {
    const course = await done(courses.get(cid));
    courses.put({ ...course, indexed: false });
  }
  await finished(tx);
  return { courseId: cid, skipped };
}

/** Every course: { course_id, code, name, lectures, indexed }, ordered by course_id. */
export async function listCourses(db) {
  const tx = db.transaction(["courses", "lectures"], "readonly");
  const courses = await done(tx.objectStore("courses").getAll());
  const index = tx.objectStore("lectures").index("by_course");
  const out = [];
  for (const c of courses.sort((a, b) => (a.course_id < b.course_id ? -1 : a.course_id > b.course_id ? 1 : 0))) {
    const lectures = await done(index.count(c.course_id));
    out.push({ course_id: c.course_id, code: c.code, name: c.name, lectures, indexed: c.indexed === true });
  }
  return out;
}

export async function courseExists(db, courseId) {
  const tx = db.transaction("courses", "readonly");
  return (await done(tx.objectStore("courses").count(courseId))) > 0;
}

/** [{ entry_id, has_captions }] for one course, ordered by entry_id. */
export async function lectureStatuses(db, courseId) {
  const tx = db.transaction("lectures", "readonly");
  const rows = await done(tx.objectStore("lectures").index("by_course").getAll(courseId));
  return rows
    .map((l) => ({ entry_id: l.entry_id, has_captions: typeof l.captions_srt === "string" && l.captions_srt.length > 0 }))
    .sort((a, b) => (a.entry_id < b.entry_id ? -1 : 1));
}

const nullsLast = (a, b) => (a === b ? 0 : a === null || a === undefined ? 1 : b === null || b === undefined ? -1 : a < b ? -1 : 1);

/** The course with its details and every stored lecture (no captions), or null. */
export async function getCourse(db, courseId) {
  const tx = db.transaction(["courses", "lectures"], "readonly");
  const course = await done(tx.objectStore("courses").get(courseId));
  if (!course) return null;
  const rows = await done(tx.objectStore("lectures").index("by_course").getAll(courseId));
  rows.sort(
    (a, b) =>
      nullsLast(a.lecture_no, b.lecture_no) ||
      nullsLast(a.recorded_start, b.recorded_start) ||
      nullsLast(a.recorded_at, b.recorded_at) ||
      (a.entry_id < b.entry_id ? -1 : a.entry_id > b.entry_id ? 1 : 0),
  );
  return {
    course_id: course.course_id,
    canvas_course_id: course.canvas_course_id,
    code: course.code,
    name: course.name,
    term: course.term ?? null,
    term_start: course.term_start ?? null,
    term_end: course.term_end ?? null,
    instructors: course.instructors ?? [],
    sections: course.sections ?? [],
    synced_at: course.synced_at,
    indexed: course.indexed === true,
    lectures: rows.map((l) => ({
      entry_id: l.entry_id,
      title: l.title,
      lecture_no: l.lecture_no,
      section: l.section,
      recorded_at: l.recorded_at ?? null,
      recorded_start: l.recorded_start ?? null,
      duration_sec: l.duration_sec,
      media_url: l.media_url,
      indexed: !isDue(l),
    })),
  };
}

/** Entry ids of the course's lectures whose chunks are missing or stale. */
export async function dueLectureIds(db, courseId) {
  const tx = db.transaction("lectures", "readonly");
  const rows = await done(tx.objectStore("lectures").index("by_course").getAll(courseId));
  return rows
    .filter(isDue)
    .map((l) => l.entry_id)
    .sort();
}

/** Course ids that have at least one due lecture. */
export async function dueCourseIds(db) {
  const tx = db.transaction("lectures", "readonly");
  const rows = await done(tx.objectStore("lectures").getAll());
  return [...new Set(rows.filter(isDue).map((l) => l.course_id))].sort();
}

/** { captions, hash, course_id } of one lecture, or null. */
export async function lectureCaptions(db, entryId) {
  const tx = db.transaction("lectures", "readonly");
  const l = await done(tx.objectStore("lectures").get(entryId));
  return l ? { captions: l.captions_srt, hash: l.captions_hash, course_id: l.course_id } : null;
}

/**
 * Replaces a lecture's chunks with ones built from the captions with this hash. If the captions
 * changed in the meantime, nothing is written (the lecture stays due). Returns true if written.
 */
export async function replaceChunks(db, entryId, hash, chunks, vectors) {
  if (vectors.length !== chunks.length) throw new StoreError("VECTOR_COUNT");
  const tx = db.transaction(["lectures", "chunks"], "readwrite");
  const lectures = tx.objectStore("lectures");
  const store = tx.objectStore("chunks");
  const lecture = await done(lectures.get(entryId));
  if (!lecture || lecture.captions_hash !== hash) {
    tx.abort();
    await finished(tx).catch(() => {});
    return false;
  }
  const old = await done(store.index("by_entry").getAllKeys(entryId));
  for (const key of old) store.delete(key);
  chunks.forEach((c, i) => {
    store.put({
      id: `${entryId}:${i}`,
      entry_id: entryId,
      course_id: lecture.course_id,
      start_sec: c.start,
      end_sec: c.end,
      text: c.text,
      vec: vectors[i] instanceof Float32Array ? vectors[i] : Float32Array.from(vectors[i]),
    });
  });
  lectures.put({ ...lecture, chunked_hash: hash });
  await finished(tx);
  return true;
}

/** indexed = the course has lectures and none of them is due. */
export async function refreshIndexed(db, courseId) {
  const tx = db.transaction(["courses", "lectures"], "readwrite");
  const courses = tx.objectStore("courses");
  const course = await done(courses.get(courseId));
  if (course) {
    const rows = await done(tx.objectStore("lectures").index("by_course").getAll(courseId));
    courses.put({ ...course, indexed: rows.length > 0 && !rows.some(isDue) });
  }
  await finished(tx);
}

function dot(a, b) {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += a[i] * b[i];
  return s;
}

/**
 * The `limit` chunks of one course closest to the (normalized) query vector, closest first. With
 * `entryId`, only that lecture's chunks. Hits: { entry_id, lecture_title, lecture_no, media_url,
 * start_sec, end_sec, text, distance }.
 */
export async function searchChunks(db, courseId, queryVector, limit, entryId) {
  const tx = db.transaction(["lectures", "chunks"], "readonly");
  const lectures = new Map((await done(tx.objectStore("lectures").index("by_course").getAll(courseId))).map((l) => [l.entry_id, l]));
  const chunks = entryId
    ? await done(tx.objectStore("chunks").index("by_entry").getAll(entryId))
    : await done(tx.objectStore("chunks").index("by_course").getAll(courseId));
  const hits = [];
  for (const c of chunks) {
    const l = lectures.get(c.entry_id);
    if (!l || !c.vec || c.vec.length !== queryVector.length) continue;
    hits.push({
      entry_id: c.entry_id,
      lecture_title: l.title,
      lecture_no: l.lecture_no,
      media_url: l.media_url,
      start_sec: c.start_sec,
      end_sec: c.end_sec,
      text: c.text,
      distance: 1 - dot(c.vec, queryVector),
    });
  }
  hits.sort((a, b) => a.distance - b.distance);
  return hits.slice(0, limit);
}

/** Every chunk of one lecture in lecture order, in the same shape as search hits (no distance). */
export async function lectureHits(db, entryId) {
  const tx = db.transaction(["lectures", "chunks"], "readonly");
  const l = await done(tx.objectStore("lectures").get(entryId));
  if (!l) return [];
  const chunks = await done(tx.objectStore("chunks").index("by_entry").getAll(entryId));
  return chunks
    .map((c) => ({ entry_id: c.entry_id, lecture_title: l.title, lecture_no: l.lecture_no, media_url: l.media_url, start_sec: c.start_sec, end_sec: c.end_sec, text: c.text }))
    .sort((a, b) => a.start_sec - b.start_sec);
}

/** Deletes one course with its lectures and chunks (the "Remove course" button). */
export async function deleteCourse(db, courseId) {
  const tx = db.transaction(["courses", "lectures", "chunks"], "readwrite");
  tx.objectStore("courses").delete(courseId);
  const lectures = tx.objectStore("lectures");
  for (const key of await done(lectures.index("by_course").getAllKeys(courseId))) lectures.delete(key);
  const chunks = tx.objectStore("chunks");
  for (const key of await done(chunks.index("by_course").getAllKeys(courseId))) chunks.delete(key);
  await finished(tx);
}

/** Deletes everything this extension stored. */
export async function deleteEverything(db) {
  const tx = db.transaction(["courses", "lectures", "chunks"], "readwrite");
  for (const name of ["courses", "lectures", "chunks"]) tx.objectStore(name).clear();
  await finished(tx);
}

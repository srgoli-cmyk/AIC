// The course page's data when it runs inside the extension: the same calls as the HTTP backend
// (GET /courses, GET /courses/{id}, POST /ask), answered from this browser's lecture store.
import { ASK_URL } from "../config.js";
import { answerQuestion } from "./answer.js";
import { answerClient } from "./client.js";
import { courseExists, deleteCourse, getCourse, listCourses, openDb, searchChunks } from "./db.js";
import { embedQuery } from "./embedder.js";

let dbPromise = null;
const db = () => (dbPromise ??= openDb());

export async function courses() {
  return listCourses(await db());
}

export async function course(courseId) {
  return getCourse(await db(), courseId);
}

export async function removeCourse(courseId) {
  await deleteCourse(await db(), courseId);
}

const writeAnswer = answerClient(ASK_URL);

export async function ask(courseId, question, entryId) {
  const store = await db();
  if (!(await courseExists(store, courseId))) throw new Error("course not synced");
  return answerQuestion(question, {
    embedQuery: (q) => embedQuery(q),
    search: (vector, limit) => searchChunks(store, courseId, vector, limit, entryId),
    writeAnswer,
  });
}

/** Asks the worker to index anything still due (after a worker restart, say). */
export function kickIndexing() {
  chrome.runtime.sendMessage({ type: "INGEST" }).catch(() => {});
}

/** { courseId, done, total } while lectures are being indexed, else null; and a change listener. */
export async function indexing() {
  const { indexing: value } = await chrome.storage.local.get("indexing");
  return value ?? null;
}

export function onIndexing(fn) {
  chrome.storage.onChanged.addListener((changes, area) => {
    if (area === "local" && changes.indexing) fn(changes.indexing.newValue ?? null);
  });
}

// Offscreen document: chunks and embeds every lecture that is due, one course at a time, then asks
// the worker to close it. Progress goes to chrome.storage.local ("indexing") so the app page can
// show it. Only course ids and counts are written there.
import { dueCourseIds, openDb } from "./src/local/db.js";
import { embed } from "./src/local/embedder.js";
import { ingestCourse } from "./src/local/ingest.js";

let running = false;
let again = false;

async function setProgress(value) {
  await chrome.storage.local.set({ indexing: value }).catch(() => {});
}

async function work() {
  if (running) {
    again = true;
    return;
  }
  running = true;
  try {
    const db = await openDb();
    do {
      again = false;
      for (const courseId of await dueCourseIds(db)) {
        await ingestCourse(db, courseId, (texts) => embed(texts), {
          onProgress: (p) => setProgress({ ...p, at: Date.now() }),
        });
      }
    } while (again);
  } catch {
    // Lectures stay due; the next sync or the next app-page visit tries again.
  } finally {
    running = false;
    await setProgress(null);
    chrome.runtime.sendMessage({ type: "INGEST_DONE" }).catch(() => {});
  }
}

chrome.runtime.onMessage.addListener((msg, sender) => {
  if (sender?.id !== chrome.runtime.id || msg?.target !== "offscreen") return false;
  if (msg.type === "INGEST") work();
  return false;
});

// The document is only ever opened to index, so it starts at once (a message sent while this
// script was still loading would be lost).
work();

// Turns stored captions into embedded chunks, like backend/app/ingest.py. Runs in the offscreen
// document after a sync. Never logs caption text: failures are reported as entry ids and error
// names only.
import { chunkCues } from "./chunking.js";
import { dueLectureIds, lectureCaptions, refreshIndexed, replaceChunks } from "./db.js";
import { parseSrtWithStats } from "./srt.js";

export class ModelLoadError extends Error {
  constructor(cause) {
    super(cause);
    this.name = "ModelLoadError";
  }
}

const BATCH = 16;

/** Chunks and embeds one lecture. `embed(texts)` returns one normalized vector per text. */
export async function ingestLecture(db, entryId, embed, { dim = 384 } = {}) {
  const row = await lectureCaptions(db, entryId);
  if (!row) return false;
  const { cues } = parseSrtWithStats(row.captions);
  const chunks = chunkCues(cues);
  const vectors = [];
  for (let i = 0; i < chunks.length; i += BATCH) {
    const batch = await embed(chunks.slice(i, i + BATCH).map((c) => c.text));
    vectors.push(...batch);
  }
  if (vectors.length !== chunks.length || vectors.some((v) => v.length !== dim)) throw new Error("EMBEDDING_DIMENSION");
  return replaceChunks(db, entryId, row.hash, chunks, vectors);
}

/**
 * Chunks every due lecture of the course, then recomputes `indexed`. Returns { done, failed }.
 * A model that cannot load stops the run (the next lecture would fail the same way); lectures stay
 * due for the next try. `onProgress({ courseId, done, total })` is optional.
 */
export async function ingestCourse(db, courseId, embed, { onProgress, log = () => {} } = {}) {
  const due = await dueLectureIds(db, courseId);
  let doneCount = 0;
  let failed = 0;
  onProgress?.({ courseId, done: 0, total: due.length });
  for (const entryId of due) {
    try {
      await ingestLecture(db, entryId, embed);
      doneCount++;
    } catch (e) {
      if (e instanceof ModelLoadError) {
        log(`ingestion stopped: embedding model not loaded (${e.message})`);
        failed += due.length - doneCount - failed;
        break;
      }
      failed++;
      log(`ingestion of lecture ${entryId} failed: ${e?.name || "Error"}`);
    }
    onProgress?.({ courseId, done: doneCount + failed, total: due.length });
  }
  await refreshIndexed(db, courseId);
  return { done: doneCount, failed };
}

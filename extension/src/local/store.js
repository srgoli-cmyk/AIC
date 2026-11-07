// The sync's storage when the extension runs on its own: the same three calls as the HTTP backend
// (confirm, lectures, save), backed by IndexedDB in this browser. After a save, `onSaved` starts
// chunking and embedding in the offscreen document.
import { looksLikeSession } from "../backend.js";
import { SyncError } from "../errors.js";
import { lectureStatuses, openDb, saveSync } from "./db.js";
import { PayloadError, validatePayload } from "./validate.js";

export function localStore({ open = () => openDb(), onSaved = () => {} } = {}) {
  let dbPromise = null;
  const db = async () => {
    dbPromise ??= open().catch((e) => {
      dbPromise = null;
      throw e;
    });
    try {
      return await dbPromise;
    } catch {
      throw new SyncError("STORE_FAILED");
    }
  };
  return {
    async confirm() {
      await db();
    },
    /** [{ entry_id, has_captions }]; a course that is not stored has none. */
    async lectures(canvasCourseId) {
      try {
        return await lectureStatuses(await db(), `canvas-${canvasCourseId}`);
      } catch (e) {
        throw e instanceof SyncError ? e : new SyncError("STORE_FAILED");
      }
    },
    async save(payload) {
      // Same guard as before a POST: nothing shaped like a Kaltura session is ever stored.
      if (looksLikeSession(JSON.stringify(payload))) throw new SyncError("KS_IN_PAYLOAD");
      try {
        validatePayload(payload);
      } catch (e) {
        throw new SyncError("STORE_REJECTED", e instanceof PayloadError ? e.field : undefined);
      }
      let result;
      try {
        result = await saveSync(await db(), payload);
      } catch (e) {
        throw e instanceof SyncError ? e : new SyncError("STORE_FAILED");
      }
      try {
        await onSaved(result.courseId);
      } catch {
        // Indexing is retried when the app page opens; the captions are already stored.
      }
      return { skippedOtherCourse: result.skipped };
    },
  };
}

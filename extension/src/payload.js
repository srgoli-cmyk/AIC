// Builds the POST /sync body (contracts/sync_payload.schema.json) from raw Canvas + Kaltura data.
//
// SECURITY: this is an explicit allowlist mapping. Raw objects may contain tokens, cookies,
// signed URLs or a Kaltura KS; none of that may ever reach the payload. Never spread raw
// objects ({...raw}) into the output. Add fields only after the contract changes.

const MEDIA_BASE = "https://canvaskaf.ucsd.edu/media/t/";

function toIsoDate(unixSeconds) {
  if (typeof unixSeconds !== "number" || !Number.isFinite(unixSeconds)) return undefined;
  return new Date(unixSeconds * 1000).toISOString().slice(0, 10);
}

/**
 * @param {object} args
 * @param {object} args.course   Canvas course object: { id, course_code, name, term?: { name } }
 * @param {Array}  args.entries  Kaltura entries: { id, name, duration, createdAt, srt }
 * @param {Array}  args.files    Canvas file objects: { id, display_name, "content-type" }
 * @param {Date}   [args.now]
 */
export function buildSyncPayload({ course, entries, files, now = new Date() }) {
  const payload = {
    schema_version: "1",
    synced_at: now.toISOString(),
    course: {
      canvas_course_id: course.id,
      code: course.course_code,
      name: course.name,
    },
    lectures: entries
      .filter((e) => typeof e.srt === "string" && e.srt.length > 0)
      .map((e) => {
        const lecture = {
          source: "kaltura",
          entry_id: e.id,
          title: e.name,
          duration_sec: Number(e.duration) || 0,
          media_url: MEDIA_BASE + e.id,
          captions_srt: e.srt,
        };
        const recorded = toIsoDate(e.createdAt);
        if (recorded) lecture.recorded_at = recorded;
        return lecture;
      }),
    files: files.map((f) => {
      const file = { canvas_file_id: f.id, name: f.display_name };
      if (f["content-type"]) file.content_type = f["content-type"];
      return file;
    }),
  };
  if (course.term?.name) payload.course.term = course.term.name;
  return payload;
}

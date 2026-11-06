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

function toIsoTime(unixSeconds) {
  if (typeof unixSeconds !== "number" || !Number.isFinite(unixSeconds)) return undefined;
  return new Date(unixSeconds * 1000).toISOString();
}

/**
 * @param {object} args
 * @param {object} args.course   Canvas course object: { id, course_code, name, term?: { name, start?, end? },
 *                                 instructors?: string[], sections?: string[] }
 * @param {Array}  args.entries  Kaltura entries: { id, name, duration, createdAt, srt, mediaUrl? }
 * @param {Array}  args.files    Canvas file objects: { id, display_name, "content-type" }
 * @param {string} [args.categoryId]  Gallery category (digits); the bare /media/t/<id> link is denied
 * @param {Date}   [args.now]
 */
export function buildSyncPayload({ course, entries, files, categoryId, now = new Date() }) {
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
          // A podcast lecture brings its own page; a gallery lecture is linked through its category.
          media_url: typeof e.mediaUrl === "string" ? e.mediaUrl : MEDIA_BASE + e.id + (categoryId ? `/${categoryId}` : ""),
          captions_srt: e.srt,
        };
        const recorded = toIsoDate(e.createdAt);
        if (recorded) lecture.recorded_at = recorded;
        const started = toIsoTime(e.createdAt);
        if (started) lecture.recorded_start = started;
        return lecture;
      }),
    files: files.map((f) => {
      const file = { canvas_file_id: f.id, name: f.display_name };
      if (f["content-type"]) file.content_type = f["content-type"];
      return file;
    }),
  };
  if (course.term?.name) payload.course.term = course.term.name;
  if (course.term?.start) payload.course.term_start = course.term.start;
  if (course.term?.end) payload.course.term_end = course.term.end;
  if (course.instructors?.length) payload.course.instructors = [...course.instructors];
  if (course.sections?.length) payload.course.sections = [...course.sections];
  return payload;
}

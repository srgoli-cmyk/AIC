// Checks a sync payload against contracts/sync_payload.schema.json before it is stored, the job
// the backend's pydantic models did. Unknown fields are refused (a token must never be stored), and
// the error names the field only, never the rejected value.

export const MEDIA_URL_RE =
  /^https:\/\/(canvaskaf\.ucsd\.edu\/media\/t\/[0-9]_[a-z0-9]{8}\/[0-9]+|podcast\.ucsd\.edu\/watch\/[a-z]{2}[0-9]{2}\/[a-z0-9]{2,12}_[0-9]{3}-[0-9]{3}-[a-z]{2}\/[0-9]{1,3})$/;
export const ENTRY_ID_RE = /^[0-9]_[a-z0-9]{8}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const DATETIME_RE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d{1,9})?)?(Z|[+-]\d{2}:?\d{2})$/;

export class PayloadError extends Error {
  constructor(field) {
    super(`invalid ${field}`);
    this.name = "PayloadError";
    this.field = field;
  }
}

const cpLen = (s) => {
  let n = 0;
  for (const _ of s) n++;
  return n;
};
const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

function only(obj, allowed, where) {
  for (const k of Object.keys(obj)) if (!allowed.includes(k)) throw new PayloadError(`${where}.${k.slice(0, 40)}`);
}
function str(v, min, max, field) {
  if (typeof v !== "string" || cpLen(v) < min || cpLen(v) > max) throw new PayloadError(field);
}
function optStr(v, max, field) {
  if (v !== undefined && v !== null) str(v, 0, max, field);
}
function optDate(v, field) {
  if (v !== undefined && v !== null && (typeof v !== "string" || !DATE_RE.test(v) || Number.isNaN(Date.parse(v)))) throw new PayloadError(field);
}
function datetime(v, field) {
  if (typeof v !== "string" || !DATETIME_RE.test(v) || Number.isNaN(Date.parse(v))) throw new PayloadError(field);
}
function strList(v, maxItems, min, max, field) {
  if (v === undefined || v === null) return;
  if (!Array.isArray(v) || v.length > maxItems) throw new PayloadError(field);
  v.forEach((s) => str(s, min, max, field));
}

/** Throws PayloadError, or returns the payload unchanged. */
export function validatePayload(p) {
  if (!isObj(p)) throw new PayloadError("payload");
  only(p, ["schema_version", "synced_at", "course", "lectures", "files"], "payload");
  if (p.schema_version !== "1") throw new PayloadError("schema_version");
  datetime(p.synced_at, "synced_at");

  const c = p.course;
  if (!isObj(c)) throw new PayloadError("course");
  only(c, ["canvas_course_id", "code", "name", "term", "term_start", "term_end", "instructors", "sections"], "course");
  if (!Number.isSafeInteger(c.canvas_course_id) || c.canvas_course_id < 1) throw new PayloadError("course.canvas_course_id");
  str(c.code, 1, 100, "course.code");
  str(c.name, 1, 300, "course.name");
  optStr(c.term, 50, "course.term");
  optDate(c.term_start, "course.term_start");
  optDate(c.term_end, "course.term_end");
  strList(c.instructors, 20, 1, 200, "course.instructors");
  strList(c.sections, 50, 1, 100, "course.sections");

  if (!Array.isArray(p.lectures) || p.lectures.length > 200) throw new PayloadError("lectures");
  p.lectures.forEach((l, i) => {
    const at = `lectures[${i}]`;
    if (!isObj(l)) throw new PayloadError(at);
    only(l, ["source", "entry_id", "title", "recorded_at", "recorded_start", "duration_sec", "media_url", "captions_srt"], at);
    if (l.source !== "kaltura") throw new PayloadError(`${at}.source`);
    if (typeof l.entry_id !== "string" || !ENTRY_ID_RE.test(l.entry_id)) throw new PayloadError(`${at}.entry_id`);
    str(l.title, 1, 300, `${at}.title`);
    optDate(l.recorded_at, `${at}.recorded_at`);
    if (l.recorded_start !== undefined && l.recorded_start !== null) datetime(l.recorded_start, `${at}.recorded_start`);
    if (typeof l.duration_sec !== "number" || !Number.isFinite(l.duration_sec) || l.duration_sec < 0) throw new PayloadError(`${at}.duration_sec`);
    if (typeof l.media_url !== "string" || !MEDIA_URL_RE.test(l.media_url)) throw new PayloadError(`${at}.media_url`);
    str(l.captions_srt, 1, 2_000_000, `${at}.captions_srt`);
  });

  if (!Array.isArray(p.files) || p.files.length > 500) throw new PayloadError("files");
  p.files.forEach((f, i) => {
    const at = `files[${i}]`;
    if (!isObj(f)) throw new PayloadError(at);
    only(f, ["canvas_file_id", "name", "content_type"], at);
    if (!Number.isSafeInteger(f.canvas_file_id) || f.canvas_file_id < 1) throw new PayloadError(`${at}.canvas_file_id`);
    str(f.name, 1, 300, `${at}.name`);
    optStr(f.content_type, 100, `${at}.content_type`);
  });
  return p;
}

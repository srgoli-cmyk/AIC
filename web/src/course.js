// Display helpers for the course page. Pure functions.

const TZ = "America/Los_Angeles";
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/** "Product and Quotient Rules" from "Lecture 5 - Product and Quotient Rules [001-000-LE]". */
const MONTH = "(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\\.?";
const DATE_ONLY = new RegExp(`^(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*,?\\s+)?${MONTH}\\s+\\d{1,2}(?:st|nd|rd|th)?,?(?:\\s+\\d{4})?$|^\\d{1,2}[/-]\\d{1,2}(?:[/-]\\d{2,4})?$`, "i");

/**
 * "Product and Quotient Rules" from "Lecture 5 - Product and Quotient Rules [001-000-LE]".
 * Titles that are only a date and the course code ("Lecture 3 - October 1st 2026 - MATH 018 - LE")
 * become "Lecture 3", since the date is shown next to it anyway.
 */
export function topic(lecture) {
  const parts = lecture.title
    .replace(/\[[^\]]*\]/g, "")
    .replace(/^\s*Lecture\s+\d+\s*[-–—:]?\s*/i, "")
    .split(/\s+[-–—|]\s+/)
    .map((p) => p.replace(/\s+/g, " ").trim())
    .filter((p) => p && !DATE_ONLY.test(p) && !/^[A-Z]{2,5}[\s-]?\d{1,3}[A-Z]{0,2}$/.test(p) && !/^(LE|LEC|DI|LA|SE)$/i.test(p));
  const t = parts.join(" – ");
  if (t) return t;
  return lecture.lecture_no != null ? `Lecture ${lecture.lecture_no}` : lecture.title;
}

export function duration(sec) {
  if (!sec) return "";
  const m = Math.round(sec / 60);
  return m >= 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m} min`;
}

export function day(lecture) {
  const iso = lecture.recorded_start || (lecture.recorded_at && `${lecture.recorded_at}T12:00:00Z`);
  if (!iso) return "";
  return new Date(iso).toLocaleDateString("en-US", { timeZone: TZ, weekday: "short", month: "short", day: "numeric" });
}

function parts(iso) {
  const f = new Intl.DateTimeFormat("en-US", { timeZone: TZ, weekday: "short", hour: "numeric", minute: "numeric", hourCycle: "h23" });
  const p = Object.fromEntries(f.formatToParts(new Date(iso)).map((x) => [x.type, x.value]));
  return { day: DAYS.indexOf(p.weekday), minutes: Number(p.hour) * 60 + Number(p.minute) };
}

/**
 * Usual meeting days and start time, read from when the recordings started. Days that hold at
 * least 15% of the recordings (and two or more) count. Null when there's too little to go on.
 */
export function schedule(lectures) {
  const starts = lectures.filter((l) => l.recorded_start).map((l) => parts(l.recorded_start));
  if (starts.length < 3) return null;
  const counts = new Array(7).fill(0);
  for (const s of starts) counts[s.day]++;
  const days = counts.map((n, d) => (n >= 2 && n >= starts.length * 0.15 ? d : -1)).filter((d) => d >= 0);
  if (!days.length) return null;
  const mins = starts.filter((s) => days.includes(s.day)).map((s) => s.minutes).sort((a, b) => a - b);
  const median = Math.round(mins[Math.floor(mins.length / 2)] / 5) * 5;
  const h = Math.floor(median / 60);
  const time = `${((h + 11) % 12) + 1}:${String(median % 60).padStart(2, "0")} ${h < 12 ? "AM" : "PM"}`;
  return { days: days.map((d) => DAYS[d]), time };
}

/** Lecture sections present, sorted; empty when every lecture is in one (or none). */
export function sectionsOf(lectures) {
  const set = [...new Set(lectures.map((l) => l.section).filter(Boolean))].sort();
  return set.length > 1 ? set : [];
}

export function termRange(course) {
  const fmt = (d) => new Date(`${d}T12:00:00Z`).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
  if (course.term_start && course.term_end) return `${fmt(course.term_start)} – ${fmt(course.term_end)}`;
  if (course.term_start) return `from ${fmt(course.term_start)}`;
  return "";
}

/**
 * Canvas lecture sections ("MATH-018 - 004-000-LE" → "004") with how many synced lectures each
 * has, sorted. Discussion and catch-all sections are left out. Empty when none look like that.
 */
export function lectureSections(course) {
  const codes = new Set();
  for (const name of course.sections || []) {
    const m = /\b(\d{3})-000-LE\b/.exec(name);
    if (m) codes.add(m[1]);
  }
  const counts = {};
  for (const l of course.lectures || []) if (l.section) counts[l.section] = (counts[l.section] || 0) + 1;
  for (const s of Object.keys(counts)) codes.add(s);
  if (!codes.size || ![...(course.sections || [])].some((n) => /-000-LE\b/.test(n))) return [];
  return [...codes].sort().map((code) => ({ code, lectures: counts[code] || 0 }));
}

/** One line on where the recordings come from, or "" when every lecture section has some. */
export function coverageNote(sections) {
  const withRec = sections.filter((s) => s.lectures > 0).map((s) => s.code);
  const without = sections.filter((s) => s.lectures === 0).map((s) => s.code);
  if (!without.length || !withRec.length) return without.length ? "No section has posted recordings on Canvas yet." : "";
  const list = (a) => (a.length === 1 ? a[0] : `${a.slice(0, -1).join(", ")} and ${a.at(-1)}`);
  return `Recordings come from section ${list(withRec)}. Section${without.length > 1 ? "s" : ""} ${list(without)} ${without.length > 1 ? "don't" : "doesn't"} post recordings on Canvas, but it's the same course material.`;
}

/** "MATH 018" from Canvas codes like "MATH018_FA26_001"; other codes as they are. */
export function prettyCode(code) {
  const m = /^([A-Z]{2,5})\s*-?\s*(\d{1,3}[A-Z]{0,2})(?:_|$)/.exec(code || "");
  return m ? `${m[1]} ${m[2]}` : code;
}

/** "Linear Algebra - Anzaldo, ..." from "MATH 018 - [All Sections] - Linear Algebra - Anzaldo, ... [FA26]". */
export function prettyName(name, code) {
  let n = (name || "").replace(/\s*\[[A-Z]{2}\d{2}\]\s*$/, "");
  const pc = prettyCode(code);
  if (pc && n.toUpperCase().startsWith(pc.toUpperCase())) n = n.slice(pc.length);
  n = n.replace(/^\s*-\s*/, "").replace(/^\[All Sections\]\s*-\s*/i, "").trim();
  return n || name;
}

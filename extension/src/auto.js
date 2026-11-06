// Auto-sync: every tracked course syncs once a day, the first time the computer is in use after
// the morning starts. Pure functions; the worker (background.js) supplies the clock, the idle
// state and storage. Stored per course in chrome.storage.local under "autoSync": only ids, counts,
// timestamps and a code.

export const AUTO_KEY = "autoSync";
export const ALARM = "autosync";
// How often a sleeping worker wakes to check. Alarms don't fire while the computer sleeps; the
// missed one fires soon after it wakes.
export const ALARM_MINUTES = 15;
// The day's sync starts after this local hour.
export const MORNING_HOUR = 5;
// Someone touched the mouse or keyboard within this many seconds.
export const IDLE_SECONDS = 300;

/** The start of the current sync day: today at MORNING_HOUR local time, or yesterday's before then. */
export function dayStart(now) {
  const d = new Date(now);
  d.setHours(MORNING_HOUR, 0, 0, 0);
  if (d.getTime() > now) d.setDate(d.getDate() - 1);
  return d.getTime();
}

/** Whether `c` (one course's record) still needs today's sync. One try per day, success or not. */
export function dueToday(c, now) {
  if (!c || c.on !== true) return false;
  return (c.lastAttempt || 0) < dayStart(now);
}

/** The tracked course that has waited longest for today's sync, as its id, or null. */
export function pickDue(all, now) {
  let best = null;
  for (const [id, c] of Object.entries(all || {})) {
    const courseId = Number(id);
    if (!Number.isSafeInteger(courseId) || courseId < 1 || !dueToday(c, now)) continue;
    if (!best || (c.lastAttempt || 0) < best.lastAttempt) best = { courseId, lastAttempt: c.lastAttempt || 0 };
  }
  return best ? best.courseId : null;
}

/** The record after a successful sync. A course seen for the first time is turned on. */
export function afterSuccess(prev, summary, now) {
  const p = prev || {};
  const before = Number.isInteger(p.lectures) ? p.lectures : summary.lectures;
  const added = Math.max(0, summary.lectures - before);
  return {
    on: p.on !== false,
    code: typeof summary.code === "string" ? summary.code.slice(0, 64) : p.code,
    lectures: summary.lectures,
    lastAttempt: now,
    lastSuccess: now,
    failed: false,
    loginNeeded: false,
    unseen: (p.unseen | 0) + added,
    // Kept by the podcast sync of all classes (every section), not by this course's Canvas page.
    ...(p.via === "podcast" || summary.via === "podcast" ? { via: "podcast" } : {}),
  };
}

/** The record after a failed sync. Only courses already tracked are kept. */
export function afterFailure(prev, code, now) {
  if (!prev) return null;
  return {
    ...prev,
    lastAttempt: now,
    failed: true,
    loginNeeded: code === "CANVAS_LOGIN" || code === "NO_KAF_FRAME" || code === "PODCAST_LOGIN",
  };
}

/** Text and color for the toolbar badge: "!" when a login is needed, else new lectures. */
export function badge(all) {
  const list = Object.values(all || {}).filter((c) => c?.on === true);
  if (list.some((c) => c.loginNeeded)) return { text: "!", color: "#f57a60" };
  const n = list.reduce((s, c) => s + (c.unseen | 0), 0);
  return { text: n > 0 ? (n > 99 ? "99+" : `+${n}`) : "", color: "#c8f560" };
}

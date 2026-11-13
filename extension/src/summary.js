// Text for the popup, built from what the worker keeps in chrome.storage.local.

const STAGES = {
  backend: "Checking the backend",
  course: "Reading the course",
  files: "Reading course files",
  gallery: "Opening the Media Gallery",
  canvas: "Reading your Canvas courses",
  classes: "Opening podcast.ucsd.edu",
  lectures: "Listing lectures",
  captions: "Reading captions",
  sending: "Sending to the backend",
  slides: "Reading slides",
};

/** "MATH 018" from Canvas codes like "MATH018_FA26_001"; other codes as they are. */
export function displayCode(code) {
  const m = /^([A-Z]{2,5})\s*-?\s*(\d{1,3}[A-Z]{0,2})(?:_|$)/.exec(typeof code === "string" ? code : "");
  return m ? `${m[1]} ${m[2]}` : code;
}

export function formatLastSync(last, format = (iso) => new Date(iso).toLocaleString()) {
  if (!last || typeof last !== "object") return "Not synced yet.";
  if (last.mode === "all") {
    const lines = [];
    for (const c of Array.isArray(last.courses) ? last.courses : []) {
      const added = c.added > 0 ? ` (${c.added} new)` : "";
      lines.push(`${displayCode(c.code) || `Course ${c.courseId}`}: ${c.lectures} lectures${added}`);
    }
    lines.push(`${last.pending} pending captions, last synced ${format(last.finishedAt)}`);
    for (const note of Array.isArray(last.notes) ? last.notes : []) lines.push(note);
    const skipped = Object.entries(last.skipped || {});
    if (skipped.length) lines.push(`Skipped: ${skipped.map(([code, n]) => `${code} x${n}`).join(", ")}`);
    return lines.join("\n");
  }
  const lines = [typeof last.code === "string" && last.code ? displayCode(last.code) : `Course ${last.courseId}`];
  lines.push(`${last.lectures} lectures, ${last.pending} pending captions, last synced ${format(last.finishedAt)}`);
  for (const note of Array.isArray(last.notes) ? last.notes : []) lines.push(note);
  const skipped = Object.entries(last.skipped || {});
  if (skipped.length) lines.push(`Skipped: ${skipped.map(([code, n]) => `${code} x${n}`).join(", ")}`);
  return lines.join("\n");
}

/** The search page for the last synced course, or null. */
export function courseLink(last, backendUrl, webPath) {
  if (last?.mode === "all") return Array.isArray(last.courses) && last.courses.length ? `${backendUrl}${webPath}` : null;
  if (!last || !Number.isSafeInteger(last.courseId) || last.courseId < 1) return null;
  return `${backendUrl}${webPath}?course=canvas-${last.courseId}`;
}

export function formatState(state, last, format) {
  const lines = [];
  if (state?.status === "running") {
    const stage = STAGES[state.stage] || "Syncing";
    const auto = state.mode === "auto" ? "Checking for new lectures. " : "";
    lines.push(state.total ? `${auto}${stage}: ${state.done} of ${state.total}` : `${auto}${stage}...`);
  } else if (state?.status === "error") {
    lines.push(`${state.mode === "auto" ? "Background sync" : "Sync"} failed: ${state.message}`);
  }
  lines.push(formatLastSync(last, format));
  return lines.join("\n");
}

/** The line under the auto-sync switch for one course's record. */
export function autoLine(c, format = (ms) => new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })) {
  if (!c) return "";
  if (c.on !== true) return "Off. New lectures sync only when you click.";
  if (c.loginNeeded) {
    return c.via === "podcast"
      ? "Login expired. Log in to podcast.ucsd.edu, then click Sync."
      : "Canvas login expired. Log in, then click Sync.";
  }
  if (c.failed) return "This morning's sync failed. Tries again tomorrow.";
  return c.lastSuccess ? `Syncs each morning. Last ${format(c.lastSuccess)}.` : "Syncs each morning.";
}

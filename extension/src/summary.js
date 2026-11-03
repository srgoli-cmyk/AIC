// Text for the popup, built from what the worker keeps in chrome.storage.local.

const STAGES = {
  backend: "Checking the backend",
  course: "Reading the course",
  files: "Reading course files",
  gallery: "Opening the Media Gallery",
  captions: "Reading captions",
  sending: "Sending to the backend",
};

export function formatLastSync(last, format = (iso) => new Date(iso).toLocaleString()) {
  if (!last || typeof last !== "object") return "Not synced yet.";
  const lines = [`Course ${last.courseId}`];
  lines.push(`${last.lectures} lectures, ${last.pending} pending captions, last synced ${format(last.finishedAt)}`);
  for (const note of Array.isArray(last.notes) ? last.notes : []) lines.push(note);
  const skipped = Object.entries(last.skipped || {});
  if (skipped.length) lines.push(`Skipped: ${skipped.map(([code, n]) => `${code} x${n}`).join(", ")}`);
  return lines.join("\n");
}

export function formatState(state, last, format) {
  const lines = [];
  if (state?.status === "running") {
    const stage = STAGES[state.stage] || "Syncing";
    lines.push(state.total ? `${stage}: ${state.done} of ${state.total}` : `${stage}...`);
  } else if (state?.status === "error") {
    lines.push(`Sync failed: ${state.message}`);
  }
  lines.push(formatLastSync(last, format));
  return lines.join("\n");
}

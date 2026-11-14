import { BACKEND_URL, WEB_PATH } from "./src/config.js";
import { AUTO_KEY } from "./src/auto.js";
import { autoLine, courseLink, displayCode, formatState } from "./src/summary.js";

const out = document.getElementById("out");
const status = document.getElementById("status");
const open = document.getElementById("open");
const auto = document.getElementById("auto");
const autoOn = document.getElementById("auto-on");
let current = {};

function render() {
  status.textContent = formatState(current.syncState, current.lastSync);
  const url = current.syncState?.status === "running" ? null : courseLink(current.lastSync, BACKEND_URL, WEB_PATH);
  open.hidden = !url;
  const all = current.lastSync?.mode === "all";
  if (url) {
    open.href = url;
    open.textContent = all ? "Open Lecture Search ↗" : `Open ${displayCode(current.lastSync.code) || "course"} in Lecture Search ↗`;
  }
  const id = current.lastSync?.courseId;
  // After a sync of every class, one switch keeps them all; the records share their state.
  const rec = all
    ? Object.values(current[AUTO_KEY] || {}).find((c) => c?.via === "all" || c?.via === "podcast")
    : Number.isSafeInteger(id)
      ? current[AUTO_KEY]?.[id]
      : undefined;
  auto.hidden = !rec;
  if (rec) {
    document.getElementById("auto-title").textContent = all
      ? "Keep my classes updated"
      : `Keep ${displayCode(rec.code) || "this course"} updated`;
    document.getElementById("auto-line").textContent = autoLine(rec);
    auto.classList.toggle("paused", rec.on === true && Boolean(rec.loginNeeded));
    autoOn.checked = rec.on === true;
  }
}

autoOn.addEventListener("change", async () => {
  const msg =
    current.lastSync?.mode === "all"
      ? { type: "AUTO_SET", all: true, on: autoOn.checked }
      : { type: "AUTO_SET", courseId: current.lastSync?.courseId, on: autoOn.checked };
  const res = await chrome.runtime.sendMessage(msg).catch(() => null);
  if (!res?.ok) autoOn.checked = !autoOn.checked;
});

open.addEventListener("click", (e) => {
  e.preventDefault();
  if (!open.hidden) chrome.tabs.create({ url: open.href });
});

async function send(type) {
  try {
    return await chrome.runtime.sendMessage({ type });
  } catch {
    return { ok: false, error: "The extension did not respond." };
  }
}

document.getElementById("ping").addEventListener("click", async () => {
  out.textContent = "PING...";
  out.textContent = JSON.stringify(await send("PING"), null, 2);
});

for (const [id, type] of [
  ["sync", "SYNC"],
  ["sync-all", "SYNC_ALL"],
]) {
  document.getElementById(id).addEventListener("click", async () => {
    const res = await send(type);
    if (!res?.ok) status.textContent = `${res?.error || "Could not start."}\n${formatState(undefined, current.lastSync)}`;
  });
}

// Asking the worker first wakes it, so a sync that died is marked before the state is shown.
// Opening the popup counts as seeing the new lectures, which clears the badge.
send("STATUS")
  .then(() => send("SEEN"))
  .then(() => chrome.storage.local.get(["syncState", "lastSync", AUTO_KEY]))
  .then((items) => {
    current = { ...current, ...items };
    render();
  });

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  for (const key of ["syncState", "lastSync", AUTO_KEY]) {
    if (changes[key]) current[key] = changes[key].newValue;
  }
  render();
});

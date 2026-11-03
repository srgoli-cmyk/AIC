import { formatState } from "./src/summary.js";

const out = document.getElementById("out");
const status = document.getElementById("status");
let current = {};

function render() {
  status.textContent = formatState(current.syncState, current.lastSync);
}

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

document.getElementById("sync").addEventListener("click", async () => {
  const res = await send("SYNC");
  if (!res?.ok) status.textContent = `${res?.error || "Could not start."}\n${formatState(undefined, current.lastSync)}`;
});

// Asking the worker first wakes it, so a sync that died is marked before the state is shown.
send("STATUS")
  .then(() => chrome.storage.local.get(["syncState", "lastSync"]))
  .then((items) => {
    current = { ...current, ...items };
    render();
  });

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== "local") return;
  for (const key of ["syncState", "lastSync"]) {
    if (changes[key]) current[key] = changes[key].newValue;
  }
  render();
});

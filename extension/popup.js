const out = document.getElementById("out");

async function send(type) {
  out.textContent = `${type}...`;
  try {
    const res = await chrome.runtime.sendMessage({ type });
    out.textContent = JSON.stringify(res, null, 2);
  } catch (e) {
    out.textContent = `error: ${e.message}`;
  }
}

document.getElementById("ping").addEventListener("click", () => send("PING"));
document.getElementById("sync").addEventListener("click", () => send("SYNC"));

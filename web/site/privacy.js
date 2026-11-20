// "Delete my account and data" on /privacy. Same origin as the backend, so the session cookie is sent.
import { deleteState } from "./account.js";

const status = document.getElementById("delete-status");
const button = document.getElementById("delete-btn");

function show(state) {
  status.textContent = state.text;
  button.hidden = !state.button;
  button.disabled = Boolean(state.busy);
}

async function signedIn() {
  try {
    const res = await fetch("/auth/me", { credentials: "same-origin" });
    if (res.status === 401) return null;
    if (!res.ok) throw new Error(String(res.status));
    return await res.json();
  } catch {
    return undefined; // the backend could not be reached
  }
}

async function remove() {
  if (!window.confirm("Delete your account and every course stored for it? This cannot be undone.")) return;
  show(deleteState("deleting"));
  let res;
  try {
    res = await fetch("/auth/account", { method: "DELETE", credentials: "same-origin" });
  } catch {
    show(deleteState("error"));
    return;
  }
  show(deleteState(res.status === 204 ? "deleted" : res.status === 401 ? "signed_out" : "error"));
}

button.addEventListener("click", remove);
const user = await signedIn();
show(user === undefined ? deleteState("error") : user ? deleteState("signed_in", user) : deleteState("signed_out"));

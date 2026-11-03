// Small helpers shared by the worker modules.

/** Trims, cuts to `max` UTF-16 units and never leaves half of an emoji at the end. */
export function clipText(value, max) {
  if (typeof value !== "string") return "";
  let out = value.trim().slice(0, max);
  const last = out.charCodeAt(out.length - 1);
  if (last >= 0xd800 && last <= 0xdbff) out = out.slice(0, -1);
  return out;
}

/** An abort signal that fires after `ms`, so no request can hang forever. */
export function timeoutSignal(ms) {
  return AbortSignal.timeout(ms);
}

// A run of base64-like characters this long could be a session; page titles must not contain one.
export function hasSessionLikeToken(text) {
  return /[A-Za-z0-9+/=_-]{40,}/.test(text);
}

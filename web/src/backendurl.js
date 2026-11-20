// Which backend the page talks to. Pure, so it runs under node tests.
//
// Served by the backend at /app/, the page uses its own origin. Served on its own (`make web`), it
// uses the default local port. `?backend=` may only point at another port on this computer: a link
// with ?backend=https://... could otherwise show the real sign-in screen with a button that leads
// somewhere else.

export const DEFAULT_BACKEND = "http://localhost:8000";

/** The origin in `value` if it is http://localhost or http://127.0.0.1 (any port), else null. */
export function localBackend(value) {
  if (typeof value !== "string" || !value) return null;
  let url;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  const local = url.hostname === "localhost" || url.hostname === "127.0.0.1";
  const bare = !url.username && !url.password && (url.pathname === "/" || url.pathname === "") && !url.search && !url.hash;
  return url.protocol === "http:" && local && bare ? url.origin : null;
}

/** The backend for a page at `loc` (a Location or URL) with the given query parameters. */
export function backendFor(loc, params) {
  const own = loc.pathname.startsWith("/app/") ? loc.origin : DEFAULT_BACKEND;
  return localBackend(params.get("backend")) || own;
}

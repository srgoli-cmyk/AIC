// Functions injected into the canvaskaf frame with chrome.scripting.executeScript.
//
// Each one is serialized to source text, so it must be self-contained: no imports, no module
// constants, no closures. Anything it needs comes in as an argument. They run on the lecture site's
// own origin, so the Kaltura sessions found on lecture pages exist only inside readEntryCaptions.
// It returns the caption text and the length of the session that worked, never a session itself,
// and refuses to return anything that contains one.

/** Finds the gallery's category id from the frame's address or from its lecture links. */
export function findCategory() {
  const out = { host: location.hostname, categoryId: null };
  const channel = location.pathname.match(/^\/channel\/[^/]+\/(\d+)(?:\/|$)/);
  if (channel) {
    out.categoryId = channel[1];
    return out;
  }
  const counts = {};
  let best = null;
  for (const a of document.querySelectorAll("a[href]")) {
    const m = (a.getAttribute("href") || "").match(/\/media\/t\/[0-9]_[a-z0-9]{8}\/(\d+)/);
    if (!m) continue;
    counts[m[1]] = (counts[m[1]] || 0) + 1;
    if (best === null || counts[m[1]] > counts[best]) best = m[1];
  }
  out.categoryId = best;
  return out;
}

/** Walks the gallery pages until one adds no new entry ids. A failure keeps what was found. */
export async function collectEntryIds(categoryId, maxPages) {
  const out = { host: location.hostname, entryIds: [], problem: null };
  if (!/^\d{1,15}$/.test(String(categoryId))) {
    out.problem = "BAD_CATEGORY";
    return out;
  }
  const limit = Math.min(Number(maxPages) || 1, 50);
  const seen = new Set();
  let page = 1;
  for (; page <= limit; page++) {
    let r;
    let body;
    try {
      r = await fetch(`/channel/channelid/${categoryId}?format=ajax&page=${page}`, {
        credentials: "same-origin",
        headers: { "X-Requested-With": "XMLHttpRequest" },
        signal: AbortSignal.timeout(25000),
      });
      if (!r.ok) {
        out.problem = `PAGE_${page}_HTTP_${Number(r.status) | 0}`;
        break;
      }
      // An expired session lands on a login page with a 200, which would look like the last page.
      if (r.redirected) {
        out.problem = `PAGE_${page}_REDIRECTED`;
        break;
      }
      body = await r.text();
    } catch {
      out.problem = `PAGE_${page}_FAILED`;
      break;
    }
    const before = seen.size;
    // Optional backslashes also match links inside JSON-escaped responses.
    for (const m of body.matchAll(/media\\?\/t\\?\/([0-9]_[a-z0-9]{8})\\?\/(\d+)/g)) {
      if (m[2] === String(categoryId) && !seen.has(m[1])) {
        seen.add(m[1]);
        out.entryIds.push(m[1]);
      }
    }
    if (seen.size === before) break;
  }
  if (!out.problem && page > limit) out.problem = "PAGE_CAP";
  return out;
}

/**
 * Reads one lecture's SRT. Returns { host, ok: true, entryId, srt, title, titleSource, duration?,
 * createdAt?, ksLength } or { host, ok: false, reason, failures?, entryId? }. Every code is plain
 * capitals, an HTTP status or a call name.
 *
 * The lecture page is /media/t/<entryId>/<categoryId> on canvaskaf.
 */
export async function readEntryCaptions(entryId, categoryId, preferredLength, ksPrefix) {
  const host = location.hostname;
  const API = "https://cdnapisec.kaltura.com/api_v3/service/";
  const MAX_CANDIDATES = 6; // once a session length is known
  const MAX_COLD_CANDIDATES = 20; // before any session has worked, so a late one is still found
  const seen = new Set();

  function finish(result) {
    const text = JSON.stringify(result);
    for (const ks of seen) {
      if (text.includes(ks) || text.includes(encodeURIComponent(ks))) {
        return { host, ok: false, reason: "KS_IN_OUTPUT" };
      }
    }
    return result;
  }

  const ENTRY = /^[0-9]_[a-z0-9]{8}$/;
  if (!ENTRY.test(String(entryId)) || !/^\d{1,15}$/.test(String(categoryId))) {
    return { host, ok: false, reason: "BAD_INPUT" };
  }
  if (typeof ksPrefix !== "string" || !/^[A-Za-z0-9]{1,8}$/.test(ksPrefix)) {
    return { host, ok: false, reason: "BAD_INPUT" };
  }

  function errorCode(body) {
    return (
      body.match(/"code"\s*:\s*"([A-Z0-9_]{1,64})"/)?.[1] || body.match(/<code>([A-Z0-9_]{1,64})<\/code>/)?.[1]
    );
  }

  // Kaltura requests: POST, session in the body, no cookies. Returns { body } or { code }.
  async function kaltura(ks, path, params) {
    let r;
    try {
      r = await fetch(API + path, {
        method: "POST",
        credentials: "omit",
        body: new URLSearchParams({ ks, ...params }),
        signal: AbortSignal.timeout(25000),
      });
    } catch {
      return { code: "NETWORK" };
    }
    if (!r.ok) return { code: `HTTP_${Number(r.status) | 0}` };
    try {
      return { body: await r.text() };
    } catch {
      return { code: "NETWORK" };
    }
  }

  function parseJson(body) {
    try {
      return JSON.parse(body);
    } catch {
      return undefined;
    }
  }

  async function captionsWith(ks) {
    const list = await kaltura(ks, "caption_captionasset/action/list", {
      format: "1",
      "filter[entryIdEqual]": entryId,
    });
    if (list.code) return { code: `list:${list.code}` };
    const data = parseJson(list.body);
    if (!data) return { code: "list:NON_JSON" };
    if (data.objectType === "KalturaAPIException") return { code: `list:${errorCode(list.body) || "UNKNOWN"}` };
    const srt = (Array.isArray(data.objects) ? data.objects : []).filter(
      (a) => String(a?.format) === "1" && (a.status === undefined || String(a.status) === "2"),
    );
    const asset = srt.find((a) => Number(a.isDefault) === 1) || srt.find((a) => a.language === "English") || srt[0];
    if (!asset || asset.id === undefined) return { code: "list:NO_SRT" };
    const served = await kaltura(ks, "caption_captionasset/action/serve", { captionAssetId: String(asset.id) });
    if (served.code) return { code: `serve:${served.code}` };
    // Kaltura reports serve errors with a 200, so check that this really is SRT: it starts with
    // a cue number. A JSON-quoted body or an error object does not.
    const text = served.body.replace(/^\uFEFF/, "").trimStart();
    if (!/^\d/.test(text) || !text.includes("-->")) {
      return { code: `serve:${served.body.trim() ? errorCode(served.body) || "NOT_SRT" : "empty"}` };
    }
    return { srt: served.body };
  }

  function decodeEntities(s) {
    const named = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " " };
    return s.replace(/&(#x[0-9a-f]+|#\d+|[a-z]+);/gi, (whole, g) => {
      let code;
      if (g[0] === "#") code = g[1].toLowerCase() === "x" ? parseInt(g.slice(2), 16) : parseInt(g.slice(1), 10);
      else return named[g.toLowerCase()] ?? whole;
      return code > 0 && code <= 0x10ffff ? String.fromCodePoint(code) : whole;
    });
  }

  function isLecturePage(url) {
    try {
      const u = new URL(url);
      return u.hostname === host && u.pathname.startsWith("/media/");
    } catch {
      return false;
    }
  }

  let html;
  try {
    const r = await fetch(`/media/t/${entryId}/${categoryId}`, {
      credentials: "include",
      signal: AbortSignal.timeout(25000),
    });
    if (!r.ok) return { host, ok: false, reason: `PAGE_HTTP_${Number(r.status) | 0}` };
    // A redirect to a login page looks like a lecture page without sessions.
    if (r.redirected && !isLecturePage(r.url)) return { host, ok: false, reason: "PAGE_REDIRECTED" };
    html = await r.text();
  } catch {
    return { host, ok: false, reason: "PAGE_FAILED" };
  }

  // Lecture pages hold several strings shaped like a session; only some work.
  const escaped = ksPrefix.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");
  const found = [...new Set(html.match(new RegExp(`${escaped}[A-Za-z0-9+/=_-]{40,}`, "g")) || [])];
  for (const ks of found) {
    seen.add(ks);
    // In a URL path the match runs on past the session ("<KS>/uiconf_id/1"), so guard the first part too.
    const firstPart = ks.split("/")[0];
    if (firstPart.length >= 40) seen.add(firstPart);
  }
  const sameLength = typeof preferredLength === "number" ? found.filter((k) => k.length === preferredLength) : [];
  const others = found.filter((k) => !sameLength.includes(k));
  const known = typeof preferredLength === "number";
  const candidates = [...sameLength, ...others].slice(0, known ? MAX_CANDIDATES : MAX_COLD_CANDIDATES);
  if (candidates.length === 0) return finish({ host, ok: false, reason: "NO_SESSION", entryId });

  const failures = [];
  for (const ks of candidates) {
    const got = await captionsWith(ks);
    if (got.code) {
      failures.push(got.code);
      continue;
    }
    // Title: the entry's own metadata. Some sessions are limited, so try the others on this
    // lecture before falling back to the lecture page's <title>.
    const result = { host, ok: true, entryId, srt: got.srt, ksLength: ks.length };
    for (const other of [ks, ...candidates.filter((c) => c !== ks)]) {
      const meta = await kaltura(other, "baseentry/action/get", { format: "1", entryId });
      const data = meta.body ? parseJson(meta.body) : undefined;
      if (data && data.objectType !== "KalturaAPIException" && typeof data.name === "string" && data.name.trim()) {
        result.title = data.name.trim();
        result.titleSource = "kaltura";
        result.duration = Number(data.duration) || 0;
        if (typeof data.createdAt === "number") result.createdAt = data.createdAt;
        return finish(result);
      }
    }
    const title = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i)?.[1];
    let decoded = title ? decodeEntities(title).replace(/\s+/g, " ").trim() : "";
    // A title holding a long base64-like run could be a session: refuse it.
    if (!decoded || /[A-Za-z0-9+/=_-]{40,}/.test(decoded)) return finish({ host, ok: false, reason: "NO_TITLE" });
    result.title = decoded;
    result.titleSource = "page";
    result.duration = 0;
    return finish(result);
  }
  return finish({ host, ok: false, reason: "NO_CAPTIONS", failures, entryId });
}

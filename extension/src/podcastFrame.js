// Functions injected into a podcast.ucsd.edu tab with chrome.scripting.executeScript.
//
// Same rules as frame.js: each one is serialized to source text, so it must be self-contained (no
// imports, no module constants, no closures). They read pages with the student's own login and
// return only paths, short labels and counts.

/** Where the tab is now. The UCSD login page has another host, so this also tells a login apart. */
export function podcastHost() {
  return { host: location.hostname, path: location.pathname };
}

/**
 * The course sections podcast.ucsd.edu shows under My Podcasts: the ones the student's enrollments
 * (or Canvas TA access) open. Returns { host, sections: [{ path, title }], problem }.
 */
export async function listMyPodcasts() {
  const out = { host: location.hostname, sections: [], problem: null };
  const SECTION = /^(?:https:\/\/podcast\.ucsd\.edu)?(\/watch\/[a-z]{2}[0-9]{2}\/[a-z0-9]{2,12}_[0-9]{3}-[0-9]{3}-[a-z]{2})\/?$/;
  let html;
  try {
    const r = await fetch("/MyCourses.aspx", { credentials: "include", signal: AbortSignal.timeout(25000) });
    if (!r.ok) {
      out.problem = `HTTP_${Number(r.status) | 0}`;
      return out;
    }
    if (r.redirected && new URL(r.url).hostname !== location.hostname) {
      out.problem = "LOGIN";
      return out;
    }
    html = await r.text();
  } catch {
    out.problem = "FAILED";
    return out;
  }
  // Only links to a whole section count (not its /kaltura player link). Read as text, so no
  // script on the fetched page ever runs.
  const seen = new Set();
  for (const a of html.matchAll(/<a\b[^>]*\bhref\s*=\s*["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi)) {
    const m = a[1].match(SECTION);
    if (!m || seen.has(m[1])) continue;
    seen.add(m[1]);
    const title = a[2].replace(/<[^>]*>/g, " ").replace(/&amp;/g, "&").replace(/\s+/g, " ").trim().slice(0, 200);
    out.sections.push({ path: m[1], title });
    if (out.sections.length >= 40) break;
  }
  if (out.sections.length === 0 && /Load My Podcasts/i.test(html)) out.problem = "NOT_LOADED";
  return out;
}

/** The lecture numbers listed on one section's page. Returns { host, lectures: [n], problem }. */
export async function listLectures(sectionPath) {
  const out = { host: location.hostname, lectures: [], problem: null };
  const SECTION = /^\/watch\/[a-z]{2}[0-9]{2}\/[a-z0-9]{2,12}_[0-9]{3}-[0-9]{3}-[a-z]{2}$/;
  if (typeof sectionPath !== "string" || !SECTION.test(sectionPath)) {
    out.problem = "BAD_INPUT";
    return out;
  }
  let html;
  try {
    const r = await fetch(sectionPath, { credentials: "include", signal: AbortSignal.timeout(25000) });
    if (!r.ok) {
      out.problem = `HTTP_${Number(r.status) | 0}`;
      return out;
    }
    if (r.redirected && new URL(r.url).hostname !== location.hostname) {
      out.problem = "LOGIN";
      return out;
    }
    html = await r.text();
  } catch {
    out.problem = "FAILED";
    return out;
  }
  const escaped = sectionPath.replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");
  const numbers = new Set();
  for (const m of html.matchAll(new RegExp(`${escaped}/([0-9]{1,3})(?=["'?#])`, "g"))) {
    const n = Number(m[1]);
    if (n >= 1 && n <= 500) numbers.add(n);
  }
  out.lectures = [...numbers].sort((a, b) => a - b);
  return out;
}

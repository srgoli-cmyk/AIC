// The course page's slides panel: how many slide files the course has, and its own website
// (contracts/api.md, "The course's own website"). A site that matches the course is used
// without asking; one that only might be it is shown here for the student to pick.
import * as api from "./api.js";
import { $, el } from "./dom.js";

const POLL_MS = 3000;
const panel = () => $("slides-panel");

let current = null; // the course the panel is showing
let timer = null;
let last = { docs: [], site: null };
let candidates = [];
let searched = false;
let busy = ""; // "", "search", "pick", "forget", "resync"
let error = "";

export function hideSlidesPanel() {
  current = null;
  clearTimeout(timer);
  panel().hidden = true;
}

export async function showSlidesPanel(courseId) {
  if (api.demo) return hideSlidesPanel();
  if (current !== courseId) {
    candidates = [];
    searched = false;
    busy = "";
    error = "";
  }
  current = courseId;
  await refresh();
}

async function refresh() {
  const id = current;
  clearTimeout(timer);
  let docs, site;
  try {
    [docs, site] = await Promise.all([api.documents(id), api.site(id)]);
  } catch {
    // An older backend without these routes, or the backend is down: no panel.
    if (id === current) panel().hidden = true;
    return;
  }
  if (id !== current) return;
  last = { docs, site };
  render();
  if (site?.status === "syncing") timer = setTimeout(refresh, POLL_MS);
}

/** The host of an https link, or null for anything else. */
export function httpsHost(url) {
  try {
    const u = new URL(url);
    return u.protocol === "https:" ? u.hostname : null;
  } catch {
    return null;
  }
}

const plural = (n, one, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** "Slides: 59 Canvas files and 20 lecture files from dsc10.com." */
export function slidesSummary(docs, site) {
  const canvas = docs.filter((d) => d.source === "canvas").length;
  const web = docs.filter((d) => d.source === "web").length;
  const host = site ? httpsHost(site.url) : null;
  const parts = [];
  if (canvas) parts.push(plural(canvas, "Canvas file"));
  if (web) parts.push(`${plural(web, "lecture file")}${host ? ` from ${host}` : " from the course website"}`);
  if (!parts.length) return "No slides yet. Canvas files arrive when you sync the course with the extension.";
  const pending = docs.filter((d) => !d.indexed).length;
  return `Slides: ${parts.join(" and ")}${pending ? ` (${pending} still being indexed)` : ""}. Answers cite them by page.`;
}

/** What the site's sync has done so far, in words. */
export function siteProgress(site) {
  const done = site.stored + site.unchanged + site.unreadable + site.failed;
  if (site.status === "syncing") return site.found ? `Reading it: ${done} of ${site.found} files.` : "Reading it…";
  if (site.status === "failed") return "The last read failed. Try reading it again.";
  const extra = [];
  if (site.unreadable) extra.push(`${site.unreadable} with no text`);
  if (site.failed) extra.push(`${site.failed} could not be opened`);
  const read = `${plural(site.stored + site.unchanged, "lecture file")} read`;
  return site.found ? `${read}${extra.length ? ` (${extra.join(", ")})` : ""}.` : "No lecture files found on it.";
}

async function act(kind, run) {
  busy = kind;
  error = "";
  render();
  try {
    await run();
  } catch (e) {
    error = e.message;
  }
  busy = "";
  await refresh();
}

const search = () =>
  act("search", async () => {
    const r = await api.searchSite(current);
    searched = true;
    candidates = r.candidates.filter((c) => c.decision === "ask" && c.url !== r.site?.url);
  });

const pick = (url) =>
  act("pick", async () => {
    await api.pickSite(current, url);
    candidates = [];
  });

const forget = () =>
  act("forget", async () => {
    await api.forgetSite(current);
    searched = false;
  });

const resync = () => act("resync", () => api.resyncSite(current));

function button(label, onClick, { primary = false, kind = "" } = {}) {
  return el("button", { type: "button", class: primary ? "primary" : null, disabled: busy !== "" || null, on: { click: onClick } }, busy && busy === kind ? `${label}…` : label);
}

function siteBlock(site) {
  const host = httpsHost(site.url);
  const link = host ? el("a", { href: site.url, target: "_blank", rel: "noopener noreferrer" }, host) : site.title;
  return [
    el("p", { class: "site-line" }, "Course website: ", link, ` · ${site.chosen_by === "search" ? "matched automatically" : "picked by you"}. `, siteProgress(site)),
    el("div", { class: "actions" }, button("Read it again", resync, { kind: "resync" }), button("Not this site", forget, { kind: "forget" })),
  ];
}

function candidateList() {
  return [
    el("p", { class: "site-line" }, "Is one of these the course's website? Each matches the course but not everything could be confirmed."),
    el(
      "ul",
      { class: "candidates" },
      ...candidates.map((c) =>
        el(
          "li",
          {},
          el("span", { class: "cand-title" }, c.title || httpsHost(c.url) || c.url),
          el("span", { class: "cand-url" }, c.url),
          el("span", { class: "cand-why" }, c.reasons.join("; ")),
          el("div", { class: "actions" }, button("Use this site", () => pick(c.url), { kind: "pick" }))
        )
      )
    ),
  ];
}

function render() {
  const { docs, site } = last;
  const parts = [el("p", {}, slidesSummary(docs, site))];
  if (site) parts.push(...siteBlock(site));
  if (!site && busy === "search") {
    parts.push(el("p", { class: "site-line" }, "Searching the web for the course's website. This can take a minute."));
  } else if (!site) {
    if (searched && !candidates.length) parts.push(el("p", { class: "site-line" }, "No website for this course and term was found."));
    if (!searched || !candidates.length) {
      parts.push(
        el("p", { class: "site-line" }, "Some courses post their slides on their own website instead of Canvas."),
        el("div", { class: "actions" }, button(searched ? "Search again" : "Find the course website", search, { primary: true, kind: "search" }))
      );
    }
  }
  if (!site && candidates.length) parts.push(...candidateList());
  if (error) parts.push(el("p", { class: "error", role: "alert" }, error));
  panel().replaceChildren(...parts);
  panel().hidden = false;
}

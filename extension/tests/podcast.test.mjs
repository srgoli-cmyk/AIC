// The podcast.ucsd.edu sync: path matching, the injected page readers and the run over all classes.
// Every page, session and course below is made up.
import assert from "node:assert/strict";
import test from "node:test";
import { readEntryCaptions } from "../src/frame.js";
import { canvasClassKey, classLabel, lectureUrl, matchClasses, parseSectionPath } from "../src/podcast.js";
import { listLectures, listMyPodcasts } from "../src/podcastFrame.js";
import { runAllSync } from "../src/syncAll.js";
import { buildSyncPayload } from "../src/payload.js";
import { SRT, goodKs, decoyKs, makeScripting, makeTabs, rebuild, res } from "./helpers.mjs";

const HOST = "podcast.ucsd.edu";
const DSC2 = "/watch/fa26/dsc010_002-000-le";
const DSC4 = "/watch/fa26/dsc010_004-000-le";
const MATH = "/watch/fa26/math018_004-000-le";
const ANAR = "/watch/fa26/anar181_001-000-le";
// Lecture n of each section is one Kaltura entry.
const ENTRY = { [DSC2]: ["1_dsc2aaa1", "1_dsc2aaa2"], [DSC4]: ["1_dsc4aaa1"], [MATH]: ["1_matha001", "1_matha002", "1_matha003"] };

const myCourses = (paths = [DSC2, DSC4, MATH, ANAR]) =>
  `<h2>Current Podcasts</h2><table>${paths
    .map((p) => `<tr><td><a href="https://podcast.ucsd.edu${p}">X &amp; Y - LE</a></td><td><a href="${p}/kaltura">v</a></td></tr>`)
    .join("")}</table>`;

const lecturePage = (path, n, { entry = ENTRY[path]?.[n - 1], extraEntry = "1_zzzzzzzz" } = {}) =>
  res(
    `<html><head><title>DSC 010 - LE [002-000-LE] - Course Podcasts - UC San Diego</title></head><body>` +
      `<div>Lecture ${n}, 9/2${n}/2026</div>` +
      (ENTRY[path] || []).map((_, i) => `<div class="lecture"><a href="${path}/${i + 1}">Thu</a></div>`).join("") +
      `<script>var a="${decoyKs(entry)}";var b="${goodKs(entry)}";` +
      `kalturaPlayer.loadMedia({ entryId: '${entry}' }, { startTime: 0 });</script>` +
      `<p>Reason for ${extraEntry}: Entry exists</p></body></html>`,
  );

/** podcast.ucsd.edu and Kaltura, as seen from a tab on the site. */
function makePodcast(o = {}) {
  const calls = [];
  const fetch = async (url, init = {}) => {
    const u = String(url);
    calls.push(u);
    if (u.startsWith("https://cdnapisec.kaltura.com/api_v3/service/")) {
      const path = u.slice("https://cdnapisec.kaltura.com/api_v3/service/".length);
      const body = init.body;
      const key = body.get("ks");
      const id = body.get("filter[entryIdEqual]") || body.get("entryId") || body.get("captionAssetId")?.replace("cap-", "");
      if (key !== goodKs(id)) return res({ objectType: "KalturaAPIException", code: "INVALID_KS" });
      if (path.endsWith("caption_captionasset/action/list")) return res({ objects: [{ id: `cap-${id}`, format: "1", status: 2, isDefault: 1 }] });
      if (path.endsWith("caption_captionasset/action/serve")) return res(SRT);
      if (path.endsWith("baseentry/action/get")) return res({ id, name: `Lecture of ${id}`, duration: 50, createdAt: 1790000000 });
      return res("", { status: 404 });
    }
    if (u === "/MyCourses.aspx") return o.myCourses ?? res(myCourses(o.paths));
    const lecture = u.match(/^(\/watch\/[a-z0-9]+\/[a-z0-9_-]+)\/(\d+)$/);
    if (lecture) return o.lecturePage?.(lecture[1], Number(lecture[2])) ?? lecturePage(lecture[1], Number(lecture[2]));
    if (ENTRY[u]) return lecturePage(u, 1);
    return res("", { status: 404 });
  };
  return { calls, env: { fetch, location: { hostname: o.hostname ?? HOST, pathname: "/MyCourses.aspx" }, document: {} } };
}

const DSC_ID = 77867;
const MATH_ID = 78412;

/** Canvas and the backend for two classes. */
function makeServer(o = {}) {
  const stored = { [DSC_ID]: o.storedDsc ?? null, [MATH_ID]: o.storedMath ?? null };
  const posted = [];
  const fetch = async (url, init = {}) => {
    const u = new URL(url);
    if (u.origin === "http://localhost:8000") {
      if (u.pathname === "/openapi.json") return res({ info: { title: "AI Lecture Search" } });
      const m = u.pathname.match(/^\/courses\/canvas-(\d+)\/lectures$/);
      if (m) {
        const list = stored[m[1]];
        return list === null ? res({}, { status: 404 }) : res(list);
      }
      if (u.pathname === "/sync") {
        if (o.syncStatus) return res({}, { status: o.syncStatus });
        const p = JSON.parse(init.body);
        posted.push(p);
        const id = p.course.canvas_course_id;
        const map = new Map((stored[id] || []).map((l) => [l.entry_id, l]));
        for (const l of p.lectures) map.set(l.entry_id, { entry_id: l.entry_id, has_captions: true });
        stored[id] = [...map.values()];
        return res({ status: "accepted" });
      }
    }
    if (u.origin === "https://canvas.ucsd.edu") {
      if (o.canvasStatus) return res("", { status: o.canvasStatus });
      if (u.pathname === "/api/v1/courses") {
        return res(
          `while(1);${JSON.stringify([
            { id: DSC_ID, course_code: "DSC010_FA26_001", name: "DSC 010", access_token: "SECRETCANVAS" },
            { id: MATH_ID, course_code: "MATH018_FA26_001" },
            { id: 75872, course_code: "ORG_HDSIOrientation_AY2627" },
          ])}`,
        );
      }
      const c = u.pathname.match(/^\/api\/v1\/courses\/(\d+)$/);
      if (c) {
        const code = c[1] === String(DSC_ID) ? "DSC010_FA26_001" : "MATH018_FA26_001";
        return res(`while(1);${JSON.stringify({ id: Number(c[1]), course_code: code, name: `${code} name`, term: { name: "Fall 2026" } })}`);
      }
      if (/\/sections$/.test(u.pathname)) return res(`while(1);[{"name":"LE 002"}]`);
    }
    return res("", { status: 404 });
  };
  return { fetch, posted, stored };
}

function deps(server, site, extra = {}) {
  const tabs = makeTabs();
  const scripting = makeScripting([{ frameId: 0, env: site.env }]);
  let t = 0;
  return {
    tabs,
    scripting,
    run: () =>
      runAllSync({
        fetch: server.fetch,
        tabs,
        scripting,
        backendUrl: "http://localhost:8000",
        report: () => {},
        now: () => (t += 10),
        sleep: async () => {},
        ...extra,
      }),
  };
}

test("section paths and Canvas codes give the same class key", () => {
  assert.deepEqual(parseSectionPath(DSC2), { term: "fa26", course: "dsc010", section: "002-000-le", key: "dsc010_fa26" });
  assert.equal(parseSectionPath(`${DSC2}/3`), null);
  assert.equal(parseSectionPath(`${DSC2}/kaltura`), null);
  assert.equal(parseSectionPath("/watch/fa26/../x"), null);
  assert.equal(canvasClassKey("DSC010_FA26_001"), "dsc010_fa26");
  assert.equal(canvasClassKey("MATH020A_FA26_A00"), "math020a_fa26");
  assert.equal(canvasClassKey("ORG_HDSIOrientation_AY2627"), null);
  assert.equal(classLabel("math018_fa26"), "MATH 018");
  assert.equal(lectureUrl(DSC2, 3), "https://podcast.ucsd.edu/watch/fa26/dsc010_002-000-le/3");
});

test("all sections of a class go to its Canvas course; a class without one is reported", () => {
  const { matched, unmatched } = matchClasses([DSC4, DSC2, MATH, ANAR, "nonsense"], [
    { id: DSC_ID, course_code: "DSC010_FA26_001" },
    { id: MATH_ID, course_code: "MATH018_FA26_001" },
    { id: 1, course_code: "DSC010_SP26_001" }, // another quarter
  ]);
  assert.deepEqual(matched, [
    { courseId: DSC_ID, code: "DSC010_FA26_001", sections: [DSC2, DSC4] },
    { courseId: MATH_ID, code: "MATH018_FA26_001", sections: [MATH] },
  ]);
  assert.deepEqual(unmatched, ["anar181_fa26"]);
});

test("My Podcasts gives each section once, without its player links", async () => {
  const site = makePodcast();
  const out = await rebuild(listMyPodcasts, site.env)();
  assert.deepEqual(
    out.sections.map((s) => s.path),
    [DSC2, DSC4, MATH, ANAR],
  );
  assert.equal(out.sections[0].title, "X & Y - LE");
  assert.equal(out.problem, null);
});

test("My Podcasts that redirects to the UCSD login is a login problem", async () => {
  const site = makePodcast({ myCourses: { ...res("<html>login</html>"), redirected: true, url: "https://a4.ucsd.edu/tritON/Authn" } });
  const out = await rebuild(listMyPodcasts, site.env)();
  assert.equal(out.problem, "LOGIN");
  assert.deepEqual(out.sections, []);
});

test("a section page lists its lecture numbers once each, in order", async () => {
  const site = makePodcast();
  assert.deepEqual((await rebuild(listLectures, site.env)(MATH)).lectures, [1, 2, 3]);
  assert.equal((await rebuild(listLectures, site.env)("/watch/fa26/../admin")).problem, "BAD_INPUT");
});

test("a podcast lecture page: the player's entry is read, captions come back, the session does not", async () => {
  const site = makePodcast();
  const read = rebuild(readEntryCaptions, site.env);
  const out = await read(null, null, null, "djJ8", `${DSC2}/2`, []);
  assert.equal(out.ok, true);
  assert.equal(out.entryId, "1_dsc2aaa2", "the player's entry, not another id on the page");
  assert.equal(out.title, "Lecture of 1_dsc2aaa2");
  assert.ok(out.srt.includes("-->"));
  assert.ok(!JSON.stringify(out).includes("djJ8"), "no session in the result");
});

test("a lecture already stored is skipped before any Kaltura call", async () => {
  const site = makePodcast();
  const out = await rebuild(readEntryCaptions, site.env)(null, null, null, "djJ8", `${DSC2}/1`, ["1_dsc2aaa1"]);
  assert.deepEqual(out, { host: HOST, ok: false, reason: "ALREADY_STORED", entryId: "1_dsc2aaa1" });
  assert.ok(!site.calls.some((c) => c.includes("kaltura")));
});

test("a podcast page path that is not one lecture is refused", async () => {
  const read = rebuild(readEntryCaptions, makePodcast().env);
  for (const bad of [DSC2, `${DSC2}/1/kaltura`, "https://evil.example/watch/fa26/dsc010_002-000-le/1", "/watch/fa26/../x/1"]) {
    assert.equal((await read(null, null, null, "djJ8", bad, [])).reason, "BAD_INPUT", bad);
  }
});

test("a page without a named entry is NO_ENTRY", async () => {
  const site = makePodcast({ lecturePage: () => res("<html><title>t</title>no player</html>") });
  assert.equal((await rebuild(readEntryCaptions, site.env)(null, null, null, "djJ8", `${DSC2}/1`, [])).reason, "NO_ENTRY");
});

test("podcast lectures keep their own page as media_url", () => {
  const p = buildSyncPayload({
    course: { id: 1, course_code: "DSC010_FA26_001", name: "DSC" },
    entries: [{ id: "1_dsc2aaa1", name: "L1", duration: 1, srt: SRT, mediaUrl: lectureUrl(DSC2, 1) }],
    files: [],
  });
  assert.equal(p.lectures[0].media_url, "https://podcast.ucsd.edu/watch/fa26/dsc010_002-000-le/1");
});

test("syncing all classes sends every section of each class to its Canvas course", async () => {
  const server = makeServer({ storedMath: [{ entry_id: "1_matha001", has_captions: true }] });
  const site = makePodcast();
  const d = deps(server, site);
  const summary = await d.run();

  assert.equal(server.posted.length, 2);
  const [dsc, math] = server.posted;
  assert.equal(dsc.course.canvas_course_id, DSC_ID);
  assert.deepEqual(
    dsc.lectures.map((l) => [l.entry_id, l.media_url]),
    [
      ["1_dsc2aaa1", "https://podcast.ucsd.edu/watch/fa26/dsc010_002-000-le/1"],
      ["1_dsc2aaa2", "https://podcast.ucsd.edu/watch/fa26/dsc010_002-000-le/2"],
      ["1_dsc4aaa1", "https://podcast.ucsd.edu/watch/fa26/dsc010_004-000-le/1"],
    ],
  );
  assert.deepEqual(dsc.files, []);
  assert.deepEqual(
    math.lectures.map((l) => l.entry_id),
    ["1_matha002", "1_matha003"],
    "the stored lecture is not read again",
  );
  assert.equal(summary.mode, "all");
  assert.deepEqual(
    summary.courses.map((c) => [c.courseId, c.lectures, c.added]),
    [
      [DSC_ID, 3, 3],
      [MATH_ID, 3, 2],
    ],
  );
  assert.ok(summary.notes.includes("ANAR 181 has recordings but no Canvas course, so it was skipped."));
  assert.equal(summary.pending, 0);
  // The tab was on the site, and closed afterwards.
  assert.equal(d.tabs.state.created[0].url, "https://podcast.ucsd.edu/MyCourses.aspx");
  assert.equal(d.tabs.state.created[0].active, false);
  assert.deepEqual(d.tabs.state.removed, [1001]);
  for (const p of server.posted) {
    const text = JSON.stringify(p);
    assert.ok(!text.includes("djJ8") && !text.includes("SECRET"), "no session or Canvas secret in a payload");
  }
});

test("a tab that never reaches podcast.ucsd.edu is a login problem, and the tab is closed", async () => {
  const server = makeServer();
  const site = makePodcast({ hostname: "a4.ucsd.edu" });
  const d = deps(server, site);
  await assert.rejects(d.run(), (e) => e.code === "PODCAST_LOGIN");
  assert.equal(server.posted.length, 0);
  assert.deepEqual(d.tabs.state.removed, [1001]);
});

test("no recordings, or no matching Canvas course, sends nothing", async () => {
  const server = makeServer();
  await assert.rejects(deps(server, makePodcast({ paths: [] })).run(), (e) => e.code === "PODCAST_NONE");
  await assert.rejects(deps(server, makePodcast({ paths: [ANAR] })).run(), (e) => e.code === "NO_CANVAS_MATCH");
  assert.equal(server.posted.length, 0);
});

test("Canvas logged out is told apart from a podcast problem", async () => {
  const server = makeServer({ canvasStatus: 401 });
  await assert.rejects(deps(server, makePodcast()).run(), (e) => e.code === "CANVAS_LOGIN");
});

test("lectures without captions are counted as pending, the rest are still sent", async () => {
  const server = makeServer();
  const site = makePodcast({
    lecturePage: (path, n) => (path === MATH && n === 2 ? res("<html><title>t</title>loadMedia({ entryId: '1_matha002' })</html>") : undefined),
  });
  const summary = await deps(server, site).run();
  assert.equal(summary.pending, 1);
  assert.deepEqual(summary.skipped, { NO_SESSION: 1 });
  assert.deepEqual(
    server.posted[1].lectures.map((l) => l.entry_id),
    ["1_matha001", "1_matha003"],
  );
});

test("a backend that rejects one class still syncs nothing silently: the error comes back", async () => {
  const server = makeServer({ syncStatus: 500 });
  await assert.rejects(deps(server, makePodcast()).run(), (e) => e.code === "BACKEND_REJECTED");
});

test("a class kept by the podcast sync stays that way, and its login message names the site", async () => {
  const { afterSuccess, afterFailure } = await import("../src/auto.js");
  const { autoLine, courseLink, formatLastSync } = await import("../src/summary.js");
  const first = afterSuccess(undefined, { lectures: 3, code: "DSC010_FA26_001", via: "podcast" }, 5);
  assert.equal(first.via, "podcast");
  assert.equal(first.on, true);
  const later = afterSuccess(first, { lectures: 5, code: "DSC010_FA26_001" }, 9);
  assert.equal(later.via, "podcast", "a Canvas sync of the same course does not undo it");
  assert.equal(later.unseen, 2);
  const failed = afterFailure(later, "PODCAST_LOGIN", 10);
  assert.equal(failed.loginNeeded, true);
  assert.equal(autoLine(failed), "Login expired. Log in to podcast.ucsd.edu, then click Sync.");

  const last = {
    mode: "all",
    courses: [
      { courseId: DSC_ID, code: "DSC010_FA26_001", lectures: 40, added: 12 },
      { courseId: MATH_ID, code: "MATH018_FA26_001", lectures: 13, added: 0 },
    ],
    pending: 1,
    finishedAt: "2026-11-05T20:00:00Z",
    notes: ["ANAR 181 has recordings but no Canvas course, so it was skipped."],
    skipped: { NO_SESSION: 1 },
  };
  assert.equal(
    formatLastSync(last, () => "today"),
    [
      "DSC 010: 40 lectures (12 new)",
      "MATH 018: 13 lectures",
      "1 pending captions, last synced today",
      "ANAR 181 has recordings but no Canvas course, so it was skipped.",
      "Skipped: NO_SESSION x1",
    ].join("\n"),
  );
  assert.equal(courseLink(last, "http://localhost:8000", "/app/"), "http://localhost:8000/app/");
});

test("a class whose pages stop answering is cut short, and the next class still syncs", async () => {
  const server = makeServer();
  const site = makePodcast();
  const d = deps(server, site);
  const real = d.scripting.executeScript;
  let dscCalls = 0;
  d.scripting.executeScript = async (call) => {
    if (call.func.name === "readEntryCaptions" && call.args[4].startsWith(DSC2) && ++dscCalls > 1) throw new Error("No tab with id");
    return real(call);
  };
  const updates = [];
  d.tabs.update = async (id, opts) => updates.push([id, opts.url]);
  const summary = await d.run();
  assert.deepEqual(summary.skipped, { FRAME_ERROR: 1, INJECT_FAILED: 1 });
  assert.deepEqual(updates, [[1001, "https://podcast.ucsd.edu/MyCourses.aspx"]], "the tab is sent back to the site");
  assert.deepEqual(
    summary.courses.map((c) => [c.courseId, c.added]),
    [
      [DSC_ID, 2],
      [MATH_ID, 3],
    ],
  );
});

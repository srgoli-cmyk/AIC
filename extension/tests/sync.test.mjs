import { test } from "node:test";
import assert from "node:assert/strict";
import { normalizeEntry, runSync, safeCode } from "../src/sync.js";
import { A, B, C, CAT, COURSE, D, KAF, SRT, baseDeps, goodKs, ks, makeKaf, makeScripting, makeServer, makeTabs, res, tracker } from "./helpers.mjs";

const SECRETS = [A, B, C, D].flatMap((id) => [goodKs(id), `djJ8DECOY${id}`.padEnd(52, "k"), `djJ8LIMIT${id}`.padEnd(60, "k")]);

function setup({ kaf = {}, server = {}, frames, tabs } = {}) {
  const t = tracker();
  const kt = tracker();
  const k = makeKaf({ tracker: kt, ...kaf });
  const s = makeServer({ tracker: t, ...server });
  const canvasFrame = { frameId: 0, env: { fetch: k.env.fetch, location: { hostname: "canvas.ucsd.edu", pathname: `/courses/${COURSE}/external_tools/77` }, document: { querySelectorAll: () => [] } } };
  const scripting = makeScripting(frames ?? [canvasFrame, { frameId: 5, env: k.env }], t);
  const deps = baseDeps({ server: s, scripting, tabs: tabs ?? makeTabs() });
  return { t, kt, k, s, scripting, deps, tabs: deps.tabs };
}

function assertNoSecrets(value) {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  for (const secret of SECRETS) assert.ok(!text.includes(secret) && !text.includes(encodeURIComponent(secret)), "a session leaked");
  assert.doesNotMatch(text, /djJ8[A-Za-z0-9+/=_-]{40,}/);
  assert.ok(!text.includes("SECRET"));
}

test("full flow: new entries are read and sent; summary has counts", async () => {
  const { s, deps, tabs, scripting } = setup();
  const summary = await runSync(deps);
  assert.equal(summary.lectures, 3);
  assert.equal(summary.pending, 0);
  assert.equal(summary.courseId, COURSE);
  assert.match(summary.finishedAt, /^2026-11-03T12:/);
  const posted = s.opts.posted;
  assert.equal(posted.schema_version, "1");
  assert.deepEqual(posted.course, { canvas_course_id: COURSE, code: "MATH 20A", name: "Calculus", term: "FA26" });
  assert.deepEqual(posted.lectures.map((l) => l.entry_id), [A, B, C]);
  assert.equal(posted.lectures[0].captions_srt, SRT);
  assert.equal(posted.lectures[0].title, `Lecture ${A}`);
  assert.equal(posted.lectures[0].media_url, `https://canvaskaf.ucsd.edu/media/t/${A}/${CAT}`);
  assert.deepEqual(posted.files, [{ canvas_file_id: 7, name: "Notes.pdf", content_type: "application/pdf" }]);
  assert.equal(tabs.state.created.length, 1);
  assert.deepEqual(tabs.state.created[0].active, false);
  assertNoSecrets(posted);
  assertNoSecrets(scripting.returned);
  assertNoSecrets(summary);
});

test("the tab opens in the background on a Canvas URL and is closed afterwards", async () => {
  const { deps, tabs } = setup();
  await runSync(deps);
  assert.equal(tabs.state.created[0].url, "https://canvas.ucsd.edu/courses/12345/external_tools/77");
  assert.equal(tabs.state.created[0].active, false);
  assert.deepEqual(tabs.state.removed, [1001]);
  assert.equal(tabs.state.open.size, 0);
});

test("the tab is closed when the category is never found", async () => {
  const { deps, tabs } = setup({ kaf: { location: { hostname: KAF, pathname: "/" } } });
  await assert.rejects(runSync(deps), /loaded but its lecture category/);
  assert.equal(tabs.state.open.size, 0);
  assert.equal(tabs.state.removed.length, 1);
});

test("the tab is closed when a later step throws", async () => {
  const { deps, tabs } = setup({ server: { syncStatus: 500 } });
  await assert.rejects(runSync(deps), /rejected/);
  assert.equal(tabs.state.open.size, 0);
});

test("the tab is closed when reading the gallery fails entirely", async () => {
  const { deps, tabs } = setup({ kaf: { galleryFail: () => res("", { status: 500 }) } });
  await assert.rejects(runSync(deps), /No lectures were found/);
  assert.equal(tabs.state.open.size, 0);
});

test("no tab is opened when the course has no Media Gallery", async () => {
  const { deps, tabs } = setup({ server: { tabs: [] } });
  await assert.rejects(runSync(deps), /no Media Gallery tab/);
  assert.equal(tabs.state.created.length, 0);
});

test("a tab URL off Canvas is never opened", async () => {
  const { deps, tabs } = setup({ server: { tabs: [{ label: "Media Gallery", full_url: "https://evil.example/x" }] } });
  await assert.rejects(runSync(deps));
  assert.equal(tabs.state.created.length, 0);
});

test("a failing tab creation is reported with a fixed message", async () => {
  const tabs = makeTabs();
  tabs.state.createFails = true;
  const { deps } = setup({ tabs });
  await assert.rejects(runSync(deps), (e) => e.message === "Could not open the Media Gallery tab.");
});

test("not a course page: a clear message and no request at all", async () => {
  const { deps, s, tabs } = setup();
  deps.tabUrl = "https://canvas.ucsd.edu/profile";
  await assert.rejects(runSync(deps), /Open a Canvas course page/);
  assert.equal(s.requests.length, 0);
  assert.equal(tabs.state.created.length, 0);
});

test("course failure stops with a log-in message before anything else", async () => {
  const { deps, tabs, s } = setup({ server: { courseStatus: 401 } });
  await assert.rejects(runSync(deps), /Log in to Canvas/i);
  assert.equal(tabs.state.created.length, 0);
  assert.ok(!s.requests.some((r) => r.method === "POST"));
});

test("files 403: the sync continues with files [] and a note", async () => {
  const { deps, s } = setup({ server: { filesStatus: 403 } });
  const summary = await runSync(deps);
  assert.deepEqual(s.opts.posted.files, []);
  assert.ok(summary.notes.some((n) => /files could not be read/.test(n)));
});

test("category: foreign frames are ignored, the KAF frame is used", async () => {
  const k = makeKaf();
  const foreign = { frameId: 2, env: { ...k.env, location: { hostname: "evil.example", pathname: "/channel/x/999" } } };
  const kaf = { frameId: 7, env: k.env };
  const t = tracker();
  const s = makeServer({ tracker: t });
  const scripting = makeScripting([foreign, kaf], t);
  const deps = baseDeps({ server: s, scripting });
  await runSync(deps);
  assert.equal(s.opts.posted.lectures.length, 3);
  // Every call after the poll is aimed at the KAF frame only.
  assert.ok(scripting.returned.slice(1).every((r) => r.length === 1 && r[0].frameId === 7));
});

test("category: only a foreign frame times out after about 30 seconds", async () => {
  const k = makeKaf();
  const foreign = { frameId: 2, env: { ...k.env, location: { hostname: "evil.example", pathname: "/channel/x/999" } } };
  const t = tracker();
  const s = makeServer({ tracker: t });
  const clock = { t: 0 };
  const deps = baseDeps({ server: s, scripting: makeScripting([foreign], t) });
  deps.now = () => clock.t;
  deps.sleep = async (ms) => void (clock.t += ms);
  await assert.rejects(runSync(deps), /did not load in the background tab/);
  assert.ok(clock.t >= 30000 && clock.t <= 32000, String(clock.t));
});

test("category: a frame that is not ready yet is retried", async () => {
  const k = makeKaf();
  const t = tracker();
  const s = makeServer({ tracker: t });
  const scripting = makeScripting([{ frameId: 3, env: k.env }], t);
  let n = 0;
  const real = scripting.executeScript;
  scripting.executeScript = async (opts) => {
    if (opts.target.allFrames && n++ < 2) throw new Error("tab is loading");
    return real(opts);
  };
  const deps = baseDeps({ server: s, scripting });
  await runSync(deps);
  assert.equal(s.opts.posted.lectures.length, 3);
});

test("partial gallery is reported as a note and what was found is synced", async () => {
  const { deps, s } = setup({ kaf: { galleryFail: (n) => (n === 2 ? res("", { status: 500 }) : undefined) } });
  const summary = await runSync(deps);
  assert.deepEqual(s.opts.posted.lectures.map((l) => l.entry_id), [A, B]);
  assert.ok(summary.notes.some((n) => /incomplete \(PAGE_2_HTTP_500\)/.test(n)));
});

test("stored with captions is skipped; new and has_captions:false are fetched", async () => {
  const { deps, s, k } = setup({
    server: { stored: [{ entry_id: A, has_captions: true }, { entry_id: B, has_captions: false }] },
  });
  const summary = await runSync(deps);
  assert.deepEqual(s.opts.posted.lectures.map((l) => l.entry_id), [B, C]);
  const touched = new Set(k.calls.map((c) => c.id).filter(Boolean));
  assert.ok(!touched.has(A), "an entry that already has captions is not fetched");
  assert.equal(summary.lectures, 3, "stored lectures after the sync");
  assert.equal(summary.pending, 0);
});

test("a 404 from the backend means nothing is stored", async () => {
  const { deps, s } = setup();
  await runSync(deps);
  assert.equal(s.statuses[0], 404, "the first lectures request was a 404");
  assert.equal(s.opts.posted.lectures.length, 3);
});

test("each entry uses only its own session", async () => {
  const { deps, k } = setup();
  await runSync(deps);
  for (const id of [A, B, C]) {
    const used = k.calls.filter((c) => c.id === id);
    assert.ok(used.length >= 2);
    assert.ok(used.every((c) => c.ks.includes(id)), "a session from another entry was used");
  }
});

test("the session length of the last success is preferred for the next entry", async () => {
  const { deps, k } = setup();
  await runSync(deps);
  const lists = k.calls.filter((c) => c.path.endsWith("action/list"));
  // First entry: decoy then good (2 lists). The others: the good length first (1 list each).
  assert.equal(lists.length, 4);
});

test("entries whose captions cannot be read are skipped with codes and counted as pending", async () => {
  const { deps, s } = setup({ kaf: { serve: (asset) => (asset === `cap-${B}` ? "" : SRT) } });
  const summary = await runSync(deps);
  assert.deepEqual(s.opts.posted.lectures.map((l) => l.entry_id), [A, C]);
  assert.equal(summary.pending, 1);
  assert.equal(summary.skipped.NO_CAPTIONS, 1);
  assert.ok(summary.skipped["serve:empty"] >= 1);
  assertNoSecrets(summary);
});

test("title fallback is counted in a note", async () => {
  const { deps, s } = setup({ kaf: { titleFails: true } });
  const summary = await runSync(deps);
  assert.ok(summary.notes.some((n) => /3 lecture titles come from the page title/.test(n)));
  assert.ok(s.opts.posted.lectures.every((l) => l.duration_sec === 0));
});

test("a session in the payload stops the sync before anything is sent", async () => {
  const stray = ks("STRAY", 70);
  const { deps, s } = setup({ kaf: { serve: () => `${SRT}${stray}\n` } });
  await assert.rejects(runSync(deps), (e) => e.message.includes("Nothing was sent") && !e.message.includes(stray));
  assert.ok(!s.requests.some((r) => r.method === "POST"));
});

test("a session in a frame result is refused inside the frame and never reaches the worker", async () => {
  const { deps, scripting, k } = setup({ kaf: { titleFails: true, pageTitle: `<title>${goodKs(A)}</title>` } });
  const summary = await runSync(deps).catch((e) => e);
  assertNoSecrets(scripting.returned);
  assertNoSecrets(summary instanceof Error ? summary.message : summary);
  assert.ok(k.calls.length > 0);
});

test("another app on the port: nothing is sent and the lectures list is not even requested", async () => {
  const { deps, s } = setup({ server: { backendTitle: "Something Else" } });
  await assert.rejects(runSync(deps), /another app/);
  assert.ok(s.requests.every((r) => r.url.endsWith("/openapi.json")));
});

test("the backend is re-checked before the lectures list and before the POST", async () => {
  const { deps, s } = setup();
  await runSync(deps);
  const urls = s.requests.filter((r) => r.url.startsWith("http://localhost")).map((r) => r.url.replace("http://localhost:8000", ""));
  for (const [i, u] of urls.entries()) {
    if (u.includes("/lectures") || u === "/sync") assert.equal(urls[i - 1], "/openapi.json", u);
  }
});

test("a backend that is down gives a fixed message", async () => {
  const { deps } = setup({ server: { backendDown: true } });
  await assert.rejects(runSync(deps), /Cannot reach the backend/);
});

test("more than 200 lectures: 200 are sent and the rest are pending", async () => {
  const unique = [...new Set(Array.from({ length: 205 }, (_, i) => `1_${i.toString(36).padStart(8, "0")}`))];
  const { deps, s, k } = setup({ kaf: { galleryPages: [unique.map((id) => `/media/t/${id}/${CAT}`).join(" ")] } });
  // The synthetic pages only serve sessions for ids they know about, so every entry gets the good one.
  const summary = await runSync(deps);
  assert.equal(s.opts.posted.lectures.length, 200);
  assert.equal(summary.pending, 5);
  assert.ok(summary.notes.some((n) => /5 lectures will be read on the next sync/.test(n)));
  assert.equal(new Set(k.calls.map((c) => c.id).filter(Boolean)).size, 200, "nothing beyond the limit is downloaded");
});

test("stops early after repeated frame failures", async () => {
  const five = [A, B, C, D, "1_eeeeeeee"];
  const { deps, scripting } = setup({ kaf: { galleryPages: [five.map((id) => `/media/t/${id}/${CAT}`).join(" ")] } });
  const real = scripting.executeScript;
  scripting.executeScript = async (opts) => {
    if (opts.func.name === "readEntryCaptions" && opts.args[0] !== A) throw new Error("frame gone");
    return real(opts);
  };
  const summary = await runSync(deps);
  assert.equal(summary.skipped.FRAME_ERROR, 3);
  assert.equal(summary.pending, 4);
  assert.ok(summary.notes.some((n) => /stopped responding/.test(n)));
});

test("requests and injected calls are strictly sequential", async () => {
  const { deps, t, kt } = setup();
  await runSync(deps);
  assert.equal(t.max, 1, "worker requests and injected calls");
  assert.equal(kt.max, 1, "requests inside the frame");
  assert.ok(t.log.length > 8 && kt.log.length > 10);
});

test("normalizeEntry keeps only allowlisted fields", () => {
  const out = normalizeEntry(
    { host: KAF, ok: true, srt: SRT, title: "  T  ", titleSource: "kaltura", duration: 5, createdAt: 9, ksLength: 60, ks: "SECRET", extra: { a: 1 } },
    A,
  );
  assert.deepEqual(out, { ok: true, entry: { id: A, name: "T", duration: 5, srt: SRT, createdAt: 9 }, fromPage: false, ksLength: 60 });
  assert.equal(normalizeEntry({ host: "evil.example", ok: true, srt: SRT, title: "T" }, A).ok, false);
  assert.equal(normalizeEntry(null, A).ok, false);
  assert.equal(normalizeEntry({ host: KAF, ok: true, srt: "no arrows", title: "T" }, A).ok, false);
  assert.equal(normalizeEntry({ host: KAF, ok: true, srt: SRT, title: "ab ".repeat(200), titleSource: "page", duration: 99 }, A).entry.name.length, 300);
  assert.equal(normalizeEntry({ host: KAF, ok: true, srt: SRT, title: "x", titleSource: "page", duration: 99 }, A).entry.duration, 0);
});

test("safeCode drops anything that is not a plain code", () => {
  assert.equal(safeCode("list:INVALID_KS"), "list:INVALID_KS");
  assert.equal(safeCode("serve:empty"), "serve:empty");
  assert.equal(safeCode("NO_CAPTIONS"), "NO_CAPTIONS");
  assert.equal(safeCode(ks("X")), "UNKNOWN");
  assert.equal(safeCode("<html>"), "UNKNOWN");
  assert.equal(safeCode(5), "UNKNOWN");
});

// ---- Review fixes

test("nothing read although something was wanted: no POST, specific error", async () => {
  const { deps, s, tabs } = setup({ kaf: { serve: () => "" } });
  await assert.rejects(runSync(deps), (e) => e.message === "No captions could be read for any lecture, so nothing was sent.");
  assert.ok(!s.requests.some((r) => r.method === "POST"));
  assert.equal(tabs.state.open.size, 0);
});

test("every lecture page without a session names the assumed prefix", async () => {
  const { deps, s } = setup({ kaf: { pageFor: () => res("<title>t</title> no sessions here") } });
  await assert.rejects(runSync(deps), (e) => e.message.includes('"djJ8"') && /Nothing was sent/.test(e.message));
  assert.ok(!s.requests.some((r) => r.method === "POST"));
});

test("nothing wanted (all stored) still posts, so files are refreshed", async () => {
  const { deps, s } = setup({ server: { stored: [A, B, C].map((entry_id) => ({ entry_id, has_captions: true })) } });
  const summary = await runSync(deps);
  assert.equal(s.opts.posted.lectures.length, 0);
  assert.equal(summary.lectures, 3);
  assert.equal(summary.pending, 0);
});

test("the two missing-frame cases have different messages", async () => {
  const noKaf = setup({ frames: [] });
  await assert.rejects(runSync(noKaf.deps), /did not load in the background tab/);
  const noCat = setup({ kaf: { location: { hostname: KAF, pathname: "/" } } });
  await assert.rejects(runSync(noCat.deps), /loaded but its lecture category/);
});

test("an empty gallery carries the safe problem code", async () => {
  const { deps } = setup({ kaf: { galleryFail: () => res("login page", { redirected: true }) } });
  await assert.rejects(runSync(deps), (e) => e.message === "No lectures were found in the Media Gallery. (PAGE_1_REDIRECTED)");
});

test("a frame that navigated during the gallery call is found again and the call retried once", async () => {
  const k = makeKaf();
  const t = tracker();
  const s = makeServer({ tracker: t });
  const frame = { frameId: 4, env: k.env, navigates: 1, navigatesOn: "collectEntryIds" };
  const scripting = makeScripting([frame], t);
  await runSync(baseDeps({ server: s, scripting }));
  assert.equal(s.opts.posted.lectures.length, 3);
  assert.equal(scripting.calls.filter((c) => c.name === "collectEntryIds").length, 2);
});

test("a gallery call that fails twice gives NO_ENTRIES", async () => {
  const k = makeKaf();
  const t = tracker();
  const s = makeServer({ tracker: t });
  const scripting = makeScripting([{ frameId: 4, env: k.env, navigates: 5, navigatesOn: "collectEntryIds" }], t);
  await assert.rejects(runSync(baseDeps({ server: s, scripting })), /No lectures were found/);
});

const shortTimers = { setTimer: (fn, ms) => setTimeout(fn, ms / 1000), clearTimer: clearTimeout };

test("a stuck frame cannot hold the category wait past about 30 simulated seconds", async () => {
  const k = makeKaf();
  const t = tracker();
  const s = makeServer({ tracker: t });
  const clock = { t: 0 };
  const deps = { ...baseDeps({ server: s, scripting: makeScripting([{ frameId: 2, env: k.env, hang: true }], t) }), ...shortTimers };
  deps.now = () => clock.t;
  deps.sleep = async (ms) => void (clock.t += ms);
  await assert.rejects(runSync(deps), /did not load in the background tab/);
  assert.ok(clock.t >= 30000 && clock.t <= 32000);
});

test("a stuck gallery call and a stuck entry call fail instead of hanging", async () => {
  const k = makeKaf();
  const t = tracker();
  const s = makeServer({ tracker: t });
  const gallery = { ...baseDeps({ server: s, scripting: makeScripting([{ frameId: 2, env: k.env, hang: (n) => n === "collectEntryIds" }], t) }), ...shortTimers };
  await assert.rejects(runSync(gallery), /No lectures were found/);
  const t2 = tracker();
  const s2 = makeServer({ tracker: t2 });
  const entries = { ...baseDeps({ server: s2, scripting: makeScripting([{ frameId: 2, env: makeKaf().env, hang: (n) => n === "readEntryCaptions" }], t2) }), ...shortTimers };
  await assert.rejects(runSync(entries), /No captions could be read/);
});

test("every worker request carries a timeout signal", async () => {
  const { deps, s } = setup();
  await runSync(deps);
  assert.ok(s.requests.length > 8);
  assert.ok(s.requests.every((r) => r.init.signal instanceof AbortSignal), "a request has no timeout");
});

test("the background tab id is reported when it opens and when it closes", async () => {
  const { deps } = setup();
  const events = [];
  deps.tabOpened = async (id) => events.push(["open", id]);
  deps.tabClosed = async () => events.push(["closed"]);
  await runSync(deps);
  assert.deepEqual(events, [["open", 1001], ["closed"]]);
});

test("if the count cannot be read after a successful POST, the sync still succeeds with a note", async () => {
  const { deps, s } = setup({ server: { lecturesFailAfterPost: true } });
  const summary = await runSync(deps);
  assert.ok(s.opts.posted);
  assert.equal(summary.lectures, 3);
  assert.ok(summary.notes.some((n) => /could not be read after the sync/.test(n)));
});

test("lectures the backend left under another course are reported", async () => {
  const { deps } = setup({ server: { skippedOther: 2 } });
  const summary = await runSync(deps);
  assert.ok(summary.notes.some((n) => /2 lectures already belong to another course/.test(n)));
});

test("a page title that looks like a session is not accepted from a frame", () => {
  const out = normalizeEntry({ host: KAF, ok: true, srt: SRT, title: `Lec ${"Ab1+".repeat(12)}`, titleSource: "page" }, A);
  assert.deepEqual(out, { ok: false, codes: ["NO_TITLE"] });
  assert.equal(normalizeEntry({ host: KAF, ok: true, srt: SRT, title: "Lec 1: derivatives", titleSource: "page" }, A).ok, true);
});

test("titles are cut without splitting an emoji", () => {
  const title = `${"a".repeat(299)}\u{1F600}`; // the emoji would straddle the 300 limit
  const out = normalizeEntry({ host: KAF, ok: true, srt: SRT, title, titleSource: "kaltura" }, A);
  assert.equal(out.entry.name, "a".repeat(299));
});

test("an absurd createdAt is dropped so the payload can still be built", () => {
  for (const createdAt of [1e15, -5, Infinity, NaN]) {
    const out = normalizeEntry({ host: KAF, ok: true, srt: SRT, title: "T", titleSource: "kaltura", createdAt }, A);
    assert.equal(out.entry.createdAt, undefined, String(createdAt));
  }
});

test("a login problem and another course failure are told apart, with a status only", async () => {
  for (const status of [401, 403]) {
    const { deps } = setup({ server: { courseStatus: status } });
    await assert.rejects(runSync(deps), (e) => /Log in to Canvas/.test(e.message) && e.message.endsWith(`(HTTP ${status})`));
  }
  const { deps } = setup({ server: { courseStatus: 500 } });
  await assert.rejects(runSync(deps), (e) => e.message === "Canvas request failed. (HTTP 500)");
});

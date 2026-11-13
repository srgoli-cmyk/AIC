// Slide sync: which files count, what is downloaded and sent, and that nothing leaks or throws.
// Synthetic data; fetch is faked.
import { test } from "node:test";
import assert from "node:assert/strict";
import { deckKey, fetchModuleFiles, isSlideFile, latestRevisions, mergeFiles, slideRank, slidesNote, syncSlides, versionOf } from "../src/slides.js";
import { MAX_SLIDE_BYTES, MAX_SLIDE_FILES } from "../src/config.js";

const COURSE = 12345;
const BACKEND = "http://localhost:8000";
const PDF = "application/pdf";
const file = (id, name = `Week ${id}.pdf`, extra = {}) => ({ id, display_name: name, "content-type": PDF, size: 1000 + id, modified_at: "2026-10-01T00:00:00Z", ...extra });

function res(body, { status = 200, url, headers = {} } = {}) {
  return {
    ok: status >= 200 && status < 300,
    status,
    url,
    headers: { get: (k) => headers[k] ?? null },
    text: async () => (typeof body === "string" ? body : JSON.stringify(body)),
    arrayBuffer: async () => (body instanceof ArrayBuffer ? body : new TextEncoder().encode(String(body)).buffer),
  };
}

/** A fake Canvas and backend. `o.stored`: documents the backend already has. */
function world(o = {}) {
  const calls = [];
  const puts = [];
  const deletes = [];
  const fetch = async (url, init = {}) => {
    const u = new URL(url);
    calls.push({ url, init });
    if (u.origin === BACKEND) {
      if (u.pathname === "/openapi.json") return res({ info: { title: o.title ?? "AI Lecture Search" } });
      if (u.pathname === `/courses/canvas-${COURSE}/documents` && !init.method) return o.documentsStatus ? res("", { status: o.documentsStatus }) : res(o.stored ?? []);
      const m = /\/documents\/canvas\/(\d+)$/.exec(u.pathname);
      if (m && init.method === "DELETE") {
        deletes.push(Number(m[1]));
        return res("", { status: 204 });
      }
      if (m && init.method === "PUT") {
        puts.push({ id: Number(m[1]), name: u.searchParams.get("name"), version: u.searchParams.get("version"), bytes: init.body.byteLength, credentials: init.credentials });
        return res({ status: "stored" }, { status: o.putStatus?.[m[1]] ?? 200 });
      }
    }
    if (u.origin === "https://canvas.ucsd.edu") {
      const d = /^\/api\/v1\/files\/(\d+)\/public_url$/.exec(u.pathname);
      if (d) {
        if (o.downloadStatus?.[d[1]]) return res("", { status: o.downloadStatus[d[1]] });
        return res(`while(1);${JSON.stringify({ public_url: o.signed?.[d[1]] ?? `https://inst-fs-pdx-prod.inscloudgate.net/files/${d[1]}?token=SECRETTOKEN` })}`);
      }
      if (u.pathname === "/api/v1/courses/12345/modules") return res(`while(1);${JSON.stringify(o.modules ?? [])}`);
      const f = /^\/api\/v1\/courses\/12345\/files\/(\d+)$/.exec(u.pathname);
      if (f) return o.fileMeta?.[f[1]] ? res(`while(1);${JSON.stringify(o.fileMeta[f[1]])}`) : res("", { status: 403 });
    }
    const s = /\/files\/(\d+)/.exec(u.pathname);
    if (s) return res(o.body?.[s[1]] ?? "%PDF-1.4 fake", { url: o.finalUrl?.[s[1]] ?? url, headers: o.downloadHeaders?.[s[1]] ?? {} });
    return res("", { status: 404 });
  };
  return { fetch, calls, puts, deletes };
}

const run = (w, files, report) => syncSlides({ fetchFn: w.fetch, backendUrl: BACKEND, courseId: COURSE, files, report });

test("only PDF and PowerPoint files of a sane size are slides", () => {
  assert.ok(isSlideFile(file(1)));
  assert.ok(isSlideFile({ id: 2, display_name: "Deck.PPTX" }), "by name when Canvas gives no type");
  assert.ok(isSlideFile({ id: 3, display_name: "deck", "content-type": "application/vnd.openxmlformats-officedocument.presentationml.presentation" }));
  for (const bad of [
    { id: 4, display_name: "hw1.docx" },
    { id: 5, display_name: "photo.png", "content-type": "image/png" },
    { id: 6, display_name: "old.ppt" },
    file(7, "huge.pdf", { size: MAX_SLIDE_BYTES + 1 }),
    file(8, "empty.pdf", { size: 0 }),
    { id: "9", display_name: "x.pdf" },
    null,
  ]) {
    assert.equal(isSlideFile(bad), false, JSON.stringify(bad));
  }
});

test("new files are downloaded with the login and sent to the backend without it", async () => {
  const w = world();
  const seen = [];
  const out = await run(w, [file(1), file(2, "Deck.pptx"), { id: 3, display_name: "hw.docx" }], (s) => seen.push(s));
  assert.deepEqual(out, { found: 2, sent: 2, unchanged: 0, failed: 0, unreadable: 0, later: 0, older: 0 });
  assert.deepEqual(w.puts, [
    { id: 1, name: "Week 1.pdf", version: "1001:2026-10-01T00:00:00Z", bytes: 13, credentials: "omit" },
    { id: 2, name: "Deck.pptx", version: "1002:2026-10-01T00:00:00Z", bytes: 13, credentials: "omit" },
  ]);
  const links = w.calls.filter((c) => c.url.includes("/public_url"));
  assert.equal(links.length, 2);
  assert.ok(links.every((c) => c.init.credentials === "include"), "the signed link is asked for with the login");
  const downloads = w.calls.filter((c) => c.url.includes("inscloudgate.net"));
  assert.equal(downloads.length, 2);
  assert.ok(downloads.every((c) => c.init.credentials === "omit"), "the file is fetched without it");
  assert.ok(!JSON.stringify(w.calls.filter((c) => c.url.startsWith(BACKEND))).includes("SECRET"), "the signed link never reaches the backend");
  assert.deepEqual(seen.at(-1), { stage: "slides", done: 2, total: 2 });
});

test("a file the backend already has in this version is not downloaded again", async () => {
  const w = world({ stored: [{ doc_id: "canvas-file-1", version: versionOf(file(1)) }, { doc_id: "canvas-file-2", version: "old" }] });
  const out = await run(w, [file(1), file(2)]);
  assert.deepEqual([out.sent, out.unchanged], [1, 1]);
  assert.deepEqual(w.puts.map((p) => p.id), [2]);
  assert.equal(w.calls.filter((c) => c.url.includes("/files/1/")).length, 0);
});

test("a download that ends on another host, fails, or is too big is not sent", async () => {
  const w = world({
    finalUrl: { 1: "https://evil.example/files/1" },
    signed: { 4: "https://inscloudgate.net.evil.example/files/4", 6: "http://inst-fs.inscloudgate.net/files/6" },
    downloadStatus: { 2: 403 },
    downloadHeaders: { 3: { "Content-Length": String(MAX_SLIDE_BYTES + 1) } },
  });
  const out = await run(w, [file(1), file(2), file(3), file(4), file(5), file(6)]);
  assert.deepEqual([out.sent, out.failed], [1, 5]);
  assert.ok(!w.calls.some((c) => c.url.includes("evil.example/files/4") || c.url.startsWith("http://inst-fs")), "a bad signed link is never fetched");
  assert.deepEqual(w.puts.map((p) => p.id), [5]);
});

test("a file the backend cannot read is counted apart from a failed one", async () => {
  const w = world({ putStatus: { 1: 422, 2: 500 } });
  const out = await run(w, [file(1), file(2), file(3)]);
  assert.deepEqual([out.sent, out.unreadable, out.failed], [1, 1, 1]);
});

test("another app on the port, or a backend error, sends nothing and does not throw", async () => {
  for (const o of [{ title: "Other" }, { documentsStatus: 500 }]) {
    const w = world(o);
    const out = await run(w, [file(1)]);
    assert.deepEqual([out.sent, out.failed], [0, 1]);
    assert.equal(w.calls.filter((c) => c.url.includes("/public_url")).length, 0, "nothing is downloaded when it cannot be stored");
  }
  const down = await syncSlides({ fetchFn: async () => Promise.reject(new TypeError("fetch failed")), backendUrl: BACKEND, courseId: COURSE, files: [file(1)] });
  assert.equal(down.failed, 1);
});

test("no slide files means no requests at all", async () => {
  const w = world();
  assert.equal((await run(w, [{ id: 3, display_name: "hw.docx" }])).found, 0);
  assert.equal(w.calls.length, 0);
});

test("more files than one sync takes are left for the next one", async () => {
  const w = world();
  const out = await run(w, Array.from({ length: MAX_SLIDE_FILES + 3 }, (_, i) => file(i + 1)));
  assert.deepEqual([out.sent, out.later], [MAX_SLIDE_FILES, 3]);
});

test("files linked from Modules are found when the Files tab is hidden", async () => {
  const w = world({
    modules: [
      { name: "Week 1", items: [{ type: "File", content_id: 21, title: "Slides 1" }, { type: "Page", content_id: 99 }, { type: "File", content_id: 21 }] },
      { name: "Week 2", items: [{ type: "File", content_id: 22 }, { type: "File", content_id: 23 }, { type: "ExternalUrl", external_url: "https://x" }] },
      { name: "Empty" },
    ],
    fileMeta: {
      21: { id: 21, display_name: "Slides 1.pdf", "content-type": PDF, size: 5, modified_at: "2026-10-02T00:00:00Z", url: "https://x/?verifier=SECRETVERIFIER" },
      22: { id: 22, display_name: "Slides 2.pptx", size: 6 },
    },
  });
  const files = await fetchModuleFiles(w.fetch, COURSE);
  assert.deepEqual(files, [
    { id: 21, display_name: "Slides 1.pdf", "content-type": PDF, size: 5, modified_at: "2026-10-02T00:00:00Z" },
    { id: 22, display_name: "Slides 2.pptx", size: 6 },
  ]);
  assert.ok(!JSON.stringify(files).includes("SECRET"));
  assert.deepEqual(mergeFiles([file(21, "From files.pdf")], files).map((f) => f.display_name), ["From files.pdf", "Slides 2.pptx"]);
});

test("the popup line says what happened, and nothing when there are no slides", () => {
  assert.equal(slidesNote({ found: 0 }), null);
  assert.equal(
    slidesNote({ found: 9, sent: 3, unchanged: 2, unreadable: 1, failed: 2, later: 1 }),
    "Slides: 9 PDF and PowerPoint files (3 new, 2 already synced, 1 with no readable text, 2 could not be downloaded, 1 left for the next sync).",
  );
});

test("copies and revisions of a deck share one name", () => {
  const same = ["18_Chapter1_Fall2026.pdf", "18_Chapter1_Fall2026_1015.pdf", "18_Chapter1_Fall2026 (1).pdf", "18_Chapter1_Fall2026_v2.PDF", "18_Chapter1_Fall2026 copy.pdf"];
  assert.equal(new Set(same.map(deckKey)).size, 1);
  for (const [a, b] of [
    ["Lecture_1_MATH_18_Section_002_Fall_2026.pdf", "Lecture_1_MATH_18_Section_001_Fall_2026.pdf"],
    ["Lecture_1.pdf", "Lecture_11.pdf"],
    ["Notes_Fall_2026.pdf", "Notes_Fall.pdf"], // a year is not a date suffix
    ["Week_1.pdf", "Week_1_1340.pdf"], // no month 13
  ]) assert.notEqual(deckKey(a), deckKey(b), `${a} vs ${b}`);
});

test("the most recently modified copy of a deck is kept", () => {
  const files = [
    file(1, "Ch1.pdf", { modified_at: "2026-09-20T00:00:00Z" }),
    file(2, "Ch1_1015.pdf", { modified_at: "2026-10-15T00:00:00Z" }),
    file(3, "Ch2.pdf"),
  ];
  const { kept, older } = latestRevisions(files);
  assert.deepEqual(kept.map((f) => f.id), [2, 3]);
  assert.deepEqual(older, [1]);
});

test("lecture slides go before worksheets, and exams and solutions go last", () => {
  const rank = (name) => slideRank({ display_name: name });
  assert.equal(rank("Lecture_12_MATH_18.pdf"), 0);
  assert.equal(rank("18_Chapter4_Fall2026.pdf"), 0);
  assert.equal(rank("18WSFall2026_Week3.pdf"), 1);
  for (const n of ["FinalSolns_Fall2025.pdf", "18_Midterm1A_Fall2026_solns.pdf", "18_Syllabus_Fall2026.pdf", "Week3_solutions.pdf"]) assert.equal(rank(n), 2, n);
});

test("only the newest copy is sent, an older copy the backend holds is removed, and lectures go first", async () => {
  const w = world({ stored: [{ doc_id: "canvas-file-1", version: "old" }] });
  const files = [
    file(4, "FinalSolns_2025.pdf", { modified_at: "2026-11-01T00:00:00Z" }),
    file(1, "Ch1.pdf", { modified_at: "2026-09-20T00:00:00Z" }),
    file(2, "Ch1_1015.pdf", { modified_at: "2026-10-15T00:00:00Z" }),
    file(3, "Lecture 3.pdf"),
  ];
  const out = await run(w, files);
  assert.deepEqual(w.deletes, [1]);
  assert.deepEqual(w.puts.map((p) => p.id), [2, 3, 4]);
  assert.equal(out.older, 1);
  assert.match(slidesNote(out), /1 older copy skipped/);
});

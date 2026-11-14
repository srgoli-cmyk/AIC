// Syncing every class: each active Canvas course goes through the one-course sync, one at a time.
import assert from "node:assert/strict";
import test from "node:test";
import { SyncError } from "../src/errors.js";
import { formatLastSync, formatState } from "../src/summary.js";
import { runAllSync } from "../src/syncAll.js";
import { res } from "./helpers.mjs";

const COURSES = [
  { id: 78412, course_code: "MATH18_FA26_004" },
  { id: 77867, course_code: "DSC10_FA26_002" },
  { id: 70000, course_code: "UCSD_Integrity_Module" },
];

function world({ courses = COURSES, coursesStatus = 200, backendDown = false } = {}) {
  const requests = [];
  const fetch = async (url) => {
    const u = new URL(url);
    requests.push(String(url));
    if (u.pathname === "/openapi.json") {
      if (backendDown) throw new TypeError("Failed to fetch");
      return res({ info: { title: "AI Lecture Search" } });
    }
    if (u.pathname === "/api/v1/courses") {
      if (coursesStatus !== 200) return res("", { status: coursesStatus });
      return res(`while(1);${JSON.stringify(courses)}`);
    }
    return res("", { status: 404 });
  };
  return { fetch, requests };
}

function deps(w, runOne, reports = []) {
  return {
    fetch: w.fetch,
    backendUrl: "http://localhost:8000",
    now: () => Date.parse("2026-11-14T12:00:00Z"),
    sleep: async () => {},
    report: (p) => reports.push(p),
    runOne,
  };
}

const done = (id, extra = {}) => ({
  courseId: id,
  code: COURSES.find((c) => c.id === id).course_code,
  lectures: 15,
  added: 2,
  slides: "Slides: 110 PDF and PowerPoint files (3 new, 107 already synced).",
  pending: 1,
  notes: ["Slides: 110 PDF and PowerPoint files (3 new, 107 already synced)."],
  skipped: { NO_CAPTIONS: 1 },
  ...extra,
});

test("every active course is synced in turn, with slides, and progress names the class", async () => {
  const seen = [];
  const reports = [];
  const out = await runAllSync(
    deps(world(), async (d) => {
      seen.push(d.tabUrl);
      assert.equal(d.slidesOnlyOk, true);
      d.report({ stage: "captions", done: 1, total: 2 });
      const id = Number(d.tabUrl.split("/").pop());
      if (id === 70000) throw new SyncError("NOTHING_TO_SYNC");
      return done(id);
    }, reports),
  );
  assert.deepEqual(seen, COURSES.map((c) => `https://canvas.ucsd.edu/courses/${c.id}`));
  assert.deepEqual(out.courses.map((c) => c.courseId), [78412, 77867]);
  assert.equal(out.pending, 2);
  assert.deepEqual(out.skipped, { NO_CAPTIONS: 2 });
  assert.deepEqual(out.notes, ["No recordings or slides: UCSD_Integrity_Module."]);
  assert.deepEqual(reports.find((r) => r.label === "DSC 10"), { stage: "captions", done: 1, total: 2, index: 2, count: 3, label: "DSC 10" });
  const text = formatLastSync(out, () => "today");
  assert.match(text, /MATH 18: 15 lectures \(2 new\), 110 slide files/);
  assert.match(formatState({ status: "running", stage: "captions", done: 3, total: 9, index: 2, count: 3, label: "DSC10_FA26_002" }, out), /^DSC 10 \(2 of 3\)\. Reading captions: 3 of 9/);
});

test("one class failing does not stop the others", async () => {
  const out = await runAllSync(
    deps(world(), async (d) => {
      const id = Number(d.tabUrl.split("/").pop());
      if (id === 78412) throw new SyncError("NO_CAPTIONS_READ");
      return done(id);
    }),
  );
  assert.deepEqual(out.courses.map((c) => c.courseId), [77867, 70000]);
  assert.match(out.notes[0], /^MATH 18 could not be synced: No captions could be read/);
});

test("a Canvas login or backend problem stops the whole run", async () => {
  let calls = 0;
  const stop = (code) => async () => {
    calls++;
    throw new SyncError(code);
  };
  await assert.rejects(runAllSync(deps(world(), stop("CANVAS_LOGIN"))), /Log in to Canvas/);
  await assert.rejects(runAllSync(deps(world(), stop("BACKEND_DOWN"))), /Cannot reach the backend/);
  assert.equal(calls, 2, "stopped after the first course each time");
});

test("Canvas logged out, no courses, or nothing synced at all are errors", async () => {
  const never = async () => assert.fail("no course should be synced");
  await assert.rejects(runAllSync(deps(world({ coursesStatus: 401 }), never)), /Log in to Canvas/);
  await assert.rejects(runAllSync(deps(world({ courses: [] }), never)), /no active courses/);
  await assert.rejects(runAllSync(deps(world({ backendDown: true }), never)), /Cannot reach the backend/);
  await assert.rejects(
    runAllSync(deps(world(), async () => { throw new SyncError("NOTHING_TO_SYNC"); })),
    /No class could be synced/,
  );
});

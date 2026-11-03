import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { buildSyncPayload } from "../src/payload.js";

const raw = JSON.parse(readFileSync(new URL("./raw_inputs.json", import.meta.url)));
const payload = buildSyncPayload({ ...raw, now: new Date("2026-11-02T18:00:00Z") });

test("maps course fields", () => {
  assert.deepEqual(payload.course, {
    canvas_course_id: 78600,
    code: "MATH 20A",
    name: "Calculus for Science and Engineering",
    term: "FA26",
  });
});

test("skips lectures without captions and builds media url", () => {
  assert.equal(payload.lectures.length, 1);
  assert.equal(payload.lectures[0].media_url, "https://canvaskaf.ucsd.edu/media/t/1_fake0001");
  assert.equal(payload.lectures[0].recorded_at, "2026-10-12");
});

test("SECURITY: no secrets survive the mapping", () => {
  const text = JSON.stringify(payload);
  assert.ok(!text.includes("SECRET"), "a secret leaked into the payload");
});

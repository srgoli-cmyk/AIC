import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { buildSyncPayload } from "../src/payload.js";

const raw = JSON.parse(readFileSync(new URL("./raw_inputs.json", import.meta.url)));
const payload = buildSyncPayload({ ...raw, now: new Date("2026-11-02T18:00:00Z") });

test("maps course fields", () => {
  assert.deepEqual(payload.course, {
    canvas_course_id: 999999,
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

test("media url carries the category id when given", () => {
  const withCat = buildSyncPayload({ ...raw, categoryId: "123456", now: new Date("2026-11-02T18:00:00Z") });
  assert.equal(withCat.lectures[0].media_url, "https://canvaskaf.ucsd.edu/media/t/1_fake0001/123456");
  assert.ok(!JSON.stringify(withCat).includes("SECRET"));
});

const schema = JSON.parse(readFileSync(new URL("../../contracts/sync_payload.schema.json", import.meta.url)));
const mediaPattern = new RegExp(schema.properties.lectures.items.properties.media_url.pattern);
const NOW = new Date("2026-11-02T18:00:00Z");

test("contract media_url pattern: accepts a payload built with a category", () => {
  const withCat = buildSyncPayload({ ...raw, categoryId: "111111", now: NOW });
  assert.match(withCat.lectures[0].media_url, mediaPattern);
});

test("contract media_url pattern: rejects the bare url and malformed variants", () => {
  const bare = buildSyncPayload({ ...raw, categoryId: undefined, now: NOW });
  assert.doesNotMatch(bare.lectures[0].media_url, mediaPattern);
  const ok = "https://canvaskaf.ucsd.edu/media/t/1_fake0001/111111";
  assert.match(ok, mediaPattern);
  for (const bad of [
    ok + "?st=5",
    ok + "#t=5",
    ok + "/",
    "https://evil.example.com/media/t/1_fake0001/111111",
    "http://canvaskaf.ucsd.edu/media/t/1_fake0001/111111",
    "https://canvaskaf.ucsd.edu/media/t/1_fake0001/abc",
  ]) assert.doesNotMatch(bad, mediaPattern, bad);
});

test("SECURITY: no secrets survive the mapping", () => {
  const text = JSON.stringify(payload);
  assert.ok(!text.includes("SECRET"), "a secret leaked into the payload");
});

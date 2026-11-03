import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { confirmBackend, courseKey, getLectures, looksLikeSession, postSync } from "../src/backend.js";
import { buildSyncPayload } from "../src/payload.js";
import { COURSE, ks, makeServer, res } from "./helpers.mjs";

const BASE = "http://localhost:8000";

test("identity check accepts this app and refuses another one that answers /health", async () => {
  await confirmBackend(makeServer().fetch, BASE);
  const other = makeServer({ backendTitle: "Other App" });
  await assert.rejects(confirmBackend(other.fetch, BASE), /another app/);
  const notJson = async () => res("<html>hello</html>");
  await assert.rejects(confirmBackend(notJson, BASE), /another app/);
  const missing = async () => res("", { status: 404 });
  await assert.rejects(confirmBackend(missing, BASE), /another app/);
  const down = async () => {
    throw new TypeError("Failed to fetch");
  };
  await assert.rejects(confirmBackend(down, BASE), /Cannot reach the backend/);
});

test("lectures: the identity check comes first, 404 means nothing stored", async () => {
  const server = makeServer();
  assert.deepEqual(await getLectures(server.fetch, BASE, COURSE), []);
  assert.ok(server.requests[0].url.endsWith("/openapi.json"));
  assert.ok(server.requests[1].url.endsWith(`/courses/${courseKey(COURSE)}/lectures`));
  const stored = makeServer({ stored: [{ entry_id: "1_aaaaaaaa", has_captions: true, extra: "x" }, { nope: 1 }] });
  assert.deepEqual(await getLectures(stored.fetch, BASE, COURSE), [{ entry_id: "1_aaaaaaaa", has_captions: true }]);
});

test("another app on the port gets no lectures request and no sync", async () => {
  const server = makeServer({ backendTitle: "Other App" });
  await assert.rejects(getLectures(server.fetch, BASE, COURSE));
  await assert.rejects(postSync(server.fetch, BASE, { lectures: [{ captions_srt: "secret captions" }] }));
  assert.ok(server.requests.every((r) => r.url.endsWith("/openapi.json")));
  assert.ok(server.requests.every((r) => r.body === undefined));
});

test("POST /sync sends JSON without credentials", async () => {
  const server = makeServer();
  await postSync(server.fetch, BASE, { hello: "world" });
  const post = server.requests.find((r) => r.method === "POST");
  assert.equal(post.init.credentials, "omit");
  assert.equal(post.body, '{"hello":"world"}');
});

test("a rejected sync reports the status only", async () => {
  const server = makeServer({ syncStatus: 422 });
  await assert.rejects(postSync(server.fetch, BASE, {}), (e) => e.message === "The backend rejected the sync. (HTTP 422)");
});

test("guard: a session-shaped string anywhere in the body stops the POST", async () => {
  const server = makeServer();
  const payload = { lectures: [{ captions_srt: `1\n00:00:01,000 --> 00:00:02,000\n${ks("STRAY", 60)}` }] };
  await assert.rejects(postSync(server.fetch, BASE, payload), /Nothing was sent/);
  assert.equal(server.requests.length, 0);
  assert.ok(looksLikeSession(`x ${ks("A+B/C==", 70)} y`));
  assert.ok(!looksLikeSession("djJ8short"));
});

// ---- Contract shape: the same mapping the sync uses, checked against the schema file.

const schema = JSON.parse(readFileSync(new URL("../../contracts/sync_payload.schema.json", import.meta.url)));

function check(value, s, path = "$") {
  const errors = [];
  if ("const" in s && value !== s.const) errors.push(`${path}: const`);
  if (s.type === "object") {
    if (typeof value !== "object" || value === null || Array.isArray(value)) return [`${path}: not an object`];
    for (const k of s.required || []) if (!(k in value)) errors.push(`${path}.${k}: missing`);
    for (const [k, v] of Object.entries(value)) {
      if (!s.properties?.[k]) errors.push(`${path}.${k}: unknown field`);
      else errors.push(...check(v, s.properties[k], `${path}.${k}`));
    }
  } else if (s.type === "array") {
    if (!Array.isArray(value)) return [`${path}: not an array`];
    if (s.maxItems !== undefined && value.length > s.maxItems) errors.push(`${path}: too many`);
    value.forEach((v, i) => errors.push(...check(v, s.items, `${path}[${i}]`)));
  } else if (s.type === "string") {
    if (typeof value !== "string") return [`${path}: not a string`];
    if (s.minLength !== undefined && value.length < s.minLength) errors.push(`${path}: too short`);
    if (s.maxLength !== undefined && value.length > s.maxLength) errors.push(`${path}: too long`);
    if (s.pattern && !new RegExp(s.pattern).test(value)) errors.push(`${path}: pattern`);
    if (s.format === "date-time" && Number.isNaN(Date.parse(value))) errors.push(`${path}: date-time`);
    if (s.format === "date" && !/^\d{4}-\d{2}-\d{2}$/.test(value)) errors.push(`${path}: date`);
  } else if (s.type === "integer" || s.type === "number") {
    if (typeof value !== "number" || (s.type === "integer" && !Number.isInteger(value))) return [`${path}: not a number`];
    if (s.minimum !== undefined && value < s.minimum) errors.push(`${path}: below minimum`);
  }
  return errors;
}

test("the payload a sync builds has the contract's shape", async () => {
  const { runSync } = await import("../src/sync.js");
  const h = await import("./helpers.mjs");
  const t = h.tracker();
  const k = h.makeKaf({ tracker: h.tracker() });
  const s = makeServer({ tracker: t });
  const deps = h.baseDeps({ server: s, scripting: h.makeScripting([{ frameId: 1, env: k.env }], t) });
  await runSync(deps);
  assert.deepEqual(check(s.opts.posted, schema), []);
  assert.deepEqual(check(buildSyncPayload({ course: { id: 1, course_code: "C", name: "N" }, entries: [], files: [] }), schema), []);
  assert.notDeepEqual(check({ ...s.opts.posted, extra: 1 }, schema), []);
});

test("every backend request has a timeout; the sync reply's other-course count is returned", async () => {
  const server = makeServer({ skippedOther: 3 });
  await getLectures(server.fetch, BASE, COURSE);
  const reply = await postSync(server.fetch, BASE, { a: 1 });
  assert.deepEqual(reply, { skippedOtherCourse: 3 });
  assert.ok(server.requests.every((r) => r.init.signal instanceof AbortSignal));
  const unreadable = async (url) => (String(url).endsWith("/openapi.json") ? res({ info: { title: "AI Lecture Search" } }) : res("not json"));
  assert.deepEqual(await postSync(unreadable, BASE, {}), { skippedOtherCourse: 0 });
});

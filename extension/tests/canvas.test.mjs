import { test } from "node:test";
import assert from "node:assert/strict";
import { fetchCourse, fetchFiles, findMediaGalleryUrl, getList, nextLink, parseCourseId, stripPrefix } from "../src/canvas.js";
import { COURSE, makeServer, res } from "./helpers.mjs";

test("parses the course id from a Canvas course URL", () => {
  assert.equal(parseCourseId("https://canvas.ucsd.edu/courses/12345"), 12345);
  assert.equal(parseCourseId("https://canvas.ucsd.edu/courses/12345/modules?x=1"), 12345);
  assert.equal(parseCourseId("https://canvas.ucsd.edu/courses/12345#top"), 12345);
});

test("anything that is not a course page gives null", () => {
  for (const url of [
    undefined,
    "",
    "not a url",
    "https://canvas.ucsd.edu/",
    "https://canvas.ucsd.edu/courses",
    "https://canvas.ucsd.edu/courses/abc",
    "https://canvas.ucsd.edu/courses/0",
    "https://canvas.ucsd.edu/courses/12345abc",
    "https://canvas.ucsd.edu.evil.example/courses/12345",
    "http://canvas.ucsd.edu/courses/12345",
    "https://evil.example/courses/12345",
    "https://canvaskaf.ucsd.edu/courses/12345",
  ]) {
    assert.equal(parseCourseId(url), null, String(url));
  }
});

test("strips the while(1); prefix", () => {
  assert.equal(stripPrefix("while(1);[1]"), "[1]");
  assert.equal(stripPrefix("while(1); {\"a\":1}"), " {\"a\":1}");
  assert.equal(stripPrefix("[1]"), "[1]");
});

test("reads the next link and ignores other relations", () => {
  const h = '<https://canvas.ucsd.edu/a?page=1>; rel="current", <https://canvas.ucsd.edu/a?page=2>; rel="next", <https://canvas.ucsd.edu/a?page=9>; rel="last"';
  assert.equal(nextLink(h), "https://canvas.ucsd.edu/a?page=2");
  assert.equal(nextLink('<https://canvas.ucsd.edu/a>; rel="last"'), null);
  assert.equal(nextLink(null), null);
});

test("course: prefix stripped, only allowlisted fields, sent with cookies", async () => {
  const server = makeServer();
  const course = await fetchCourse(server.fetch, COURSE);
  assert.deepEqual(course, { id: COURSE, course_code: "MATH 20A", name: "Calculus", term: { name: "FA26" } });
  assert.ok(server.requests[0].url.endsWith("/api/v1/courses/12345?include[]=term"));
  assert.equal(server.requests[0].init.credentials, "include");
});

test("course failure throws without echoing anything", async () => {
  const server = makeServer({ courseStatus: 401 });
  await assert.rejects(fetchCourse(server.fetch, COURSE), (e) => e.message === "Canvas request failed. (HTTP 401)");
});

test("files: follows pagination, drops signed urls, keeps allowlisted fields", async () => {
  const server = makeServer({
    filePages: {
      1: { next: "https://canvas.ucsd.edu/api/v1/courses/12345/files?per_page=100&page=2", items: [{ id: 1, display_name: "a.pdf", "content-type": "application/pdf", url: "https://s/?verifier=SECRETVERIFIER" }] },
      2: { items: [{ id: 2, display_name: "b.txt" }, { id: "x", display_name: "bad" }, { id: 3 }] },
    },
  });
  const files = await fetchFiles(server.fetch, COURSE);
  assert.deepEqual(files, [
    { id: 1, display_name: "a.pdf", "content-type": "application/pdf" },
    { id: 2, display_name: "b.txt" },
  ]);
  assert.ok(!JSON.stringify(files).includes("SECRET"));
});

test("a next link off Canvas is refused and never fetched", async () => {
  const server = makeServer({
    filePages: { 1: { next: "https://evil.example/steal?page=2", items: [{ id: 1, display_name: "a" }] } },
  });
  await assert.rejects(fetchFiles(server.fetch, COURSE), /unexpected next link/);
  assert.ok(!server.requests.some((r) => r.url.includes("evil.example")));
});

test("pagination stops at the page cap", async () => {
  let calls = 0;
  const fetchFn = async () => {
    calls++;
    return res("while(1);[1]", { headers: { Link: '<https://canvas.ucsd.edu/api/v1/x?page=9>; rel="next"' } });
  };
  const items = await getList(fetchFn, "https://canvas.ucsd.edu/api/v1/x");
  assert.equal(calls, 20);
  assert.equal(items.length, 20);
});

test("files 403 rejects so the caller can fall back to an empty list", async () => {
  const server = makeServer({ filesStatus: 403 });
  await assert.rejects(fetchFiles(server.fetch, COURSE), /HTTP 403/);
});

test("finds the Media Gallery tab by label, with no hardcoded tool id", async () => {
  const server = makeServer();
  assert.equal(await findMediaGalleryUrl(server.fetch, COURSE), "https://canvas.ucsd.edu/courses/12345/external_tools/77");
  const other = makeServer({
    tabs: [{ id: "x", label: "MEDIA GALLERY", full_url: "https://canvas.ucsd.edu/courses/12345/external_tools/4242" }],
  });
  assert.equal(await findMediaGalleryUrl(other.fetch, COURSE), "https://canvas.ucsd.edu/courses/12345/external_tools/4242");
});

test("no Media Gallery tab gives a clear message", async () => {
  const server = makeServer({ tabs: [{ id: "home", label: "Home", full_url: "https://canvas.ucsd.edu/courses/12345" }] });
  await assert.rejects(findMediaGalleryUrl(server.fetch, COURSE), /no Media Gallery tab/);
});

test("a tab URL off Canvas is refused", async () => {
  const server = makeServer({ tabs: [{ id: "x", label: "Media Gallery", full_url: "https://evil.example/launch" }] });
  await assert.rejects(findMediaGalleryUrl(server.fetch, COURSE), /Media Gallery tab/);
});

test("names are cut without splitting an emoji, and requests carry a timeout", async () => {
  const name = `${"a".repeat(299)}\u{1F600}`;
  const server = makeServer({ filePages: { 1: { items: [{ id: 1, display_name: name }] } } });
  const files = await fetchFiles(server.fetch, COURSE);
  assert.equal(files[0].display_name, "a".repeat(299));
  assert.ok(server.requests.every((r) => r.init.signal instanceof AbortSignal));
});

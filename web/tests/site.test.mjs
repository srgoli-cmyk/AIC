// The slides panel's wording: counts of slide files, a site's sync progress, and only https
// links being shown as links.
import { test } from "node:test";
import assert from "node:assert/strict";

globalThis.location = new URL("http://localhost:5173/"); // api.js reads it when loaded
const { httpsHost, siteProgress, slidesSummary } = await import("../src/site.js");

const doc = (source, indexed = true) => ({ source, indexed });
const site = (o = {}) => ({ url: "https://dsc10.com/", status: "done", found: 0, stored: 0, unchanged: 0, unreadable: 0, failed: 0, ...o });

test("the summary counts Canvas files and the site's files", () => {
  assert.match(slidesSummary([], null), /No slides yet/);
  assert.equal(slidesSummary([doc("canvas"), doc("canvas")], null), "Slides: 2 Canvas files. Answers cite them by page.");
  assert.equal(slidesSummary([doc("canvas"), doc("web", false)], site()), "Slides: 1 Canvas file and 1 lecture file from dsc10.com (1 still being indexed). Answers cite them by page.");
});

test("a site's progress is told in words", () => {
  assert.equal(siteProgress(site({ status: "syncing", found: 20, stored: 4, unreadable: 1 })), "Reading it: 5 of 20 files.");
  assert.equal(siteProgress(site({ found: 20, stored: 18, unreadable: 1, failed: 1 })), "18 lecture files read (1 with no text, 1 could not be opened).");
  assert.equal(siteProgress(site({ found: 0 })), "No lecture files found on it.");
  assert.match(siteProgress(site({ status: "failed" })), /failed/);
});

test("only an https address becomes a link", () => {
  assert.equal(httpsHost("https://dsc10.com/x"), "dsc10.com");
  for (const bad of ["http://dsc10.com/", "javascript:alert(1)", "not a url"]) assert.equal(httpsHost(bad), null);
});

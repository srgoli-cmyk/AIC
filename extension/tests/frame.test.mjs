import { test } from "node:test";
import assert from "node:assert/strict";
import { collectEntryIds, findCategory, readEntryCaptions } from "../src/frame.js";
import { KS_PREFIX } from "../src/config.js";
import { A, B, C, CAT, KAF, SRT, decoyKs, goodKs, ks, limitedKs, makeKaf, rebuild, res } from "./helpers.mjs";

const run = (fn, kaf, ...args) => rebuild(fn, kaf.env)(...args);
const entry = (kaf, id, preferred = null) => run(readEntryCaptions, kaf, id, CAT, preferred, KS_PREFIX);

test("injected functions are self-contained: they run from their source text alone", async () => {
  const kaf = makeKaf();
  for (const fn of [findCategory, collectEntryIds, readEntryCaptions]) {
    assert.doesNotMatch(fn.toString(), /\bimport\b|\bconsole\b/);
    // Only fetch, location and document are provided; a reference to anything else would throw.
    const rebuilt = new Function("fetch", "location", "document", `return (${fn.toString()});`)(
      kaf.env.fetch,
      kaf.env.location,
      kaf.env.document,
    );
    assert.equal(typeof rebuilt, "function");
  }
  assert.equal((await run(collectEntryIds, kaf, CAT, 20)).host, KAF);
});

test("category from the frame address; the channel segment is cosmetic", () => {
  const kaf = makeKaf({ location: { hostname: KAF, pathname: "/channel/whatever-name/777" } });
  assert.deepEqual(run(findCategory, kaf), { host: KAF, categoryId: "777" });
});

test("category from the most frequent lecture link category", () => {
  const kaf = makeKaf({
    location: { hostname: KAF, pathname: "/hosted/index" },
    links: [`/media/t/${A}/111`, `/media/t/${B}/222`, `/media/t/${C}/222`, "/other"],
  });
  assert.equal(run(findCategory, kaf).categoryId, "222");
});

test("no category yet gives null", () => {
  const kaf = makeKaf({ location: { hostname: KAF, pathname: "/" }, links: [] });
  assert.equal(run(findCategory, kaf).categoryId, null);
});

test("gallery: union of pages, only this category, stops when a page adds nothing", async () => {
  const kaf = makeKaf({ galleryPages: [`/media/t/${A}/${CAT} /media/t/${B}/${CAT}`, `/media/t/${C}/${CAT} /media/t/1_zzzzzzzz/999`, `/media/t/${C}/${CAT}`] });
  const out = await run(collectEntryIds, kaf, CAT, 20);
  assert.deepEqual(out.entryIds, [A, B, C]);
  assert.equal(out.problem, null);
  assert.equal(kaf.calls.length, 0);
});

test("gallery: JSON-escaped links match", async () => {
  const kaf = makeKaf({ galleryPages: [String.raw`{"c":"<a href=\"\/media\/t\/${A}\/${CAT}\">"}`] });
  assert.deepEqual((await run(collectEntryIds, kaf, CAT, 20)).entryIds, [A]);
});

test("gallery: the page cap keeps what was found and reports it", async () => {
  const kaf = makeKaf();
  let n = 0;
  kaf.env.fetch = async () => res(`/media/t/1_${String(++n).padStart(8, "a")}/${CAT}`);
  const out = await run(collectEntryIds, kaf, CAT, 3);
  assert.equal(out.entryIds.length, 3);
  assert.equal(out.problem, "PAGE_CAP");
});

test("gallery: a failed page keeps earlier entries and reports a code", async () => {
  const kaf = makeKaf({ galleryFail: (n) => (n === 2 ? res("", { status: 500 }) : undefined) });
  const out = await run(collectEntryIds, kaf, CAT, 20);
  assert.deepEqual(out.entryIds, [A, B]);
  assert.equal(out.problem, "PAGE_2_HTTP_500");
});

test("gallery: a redirected page counts as failed", async () => {
  const kaf = makeKaf({ galleryFail: (n) => (n === 2 ? res(`/media/t/${C}/${CAT}`, { redirected: true }) : undefined) });
  const out = await run(collectEntryIds, kaf, CAT, 20);
  assert.deepEqual(out.entryIds, [A, B]);
  assert.equal(out.problem, "PAGE_2_REDIRECTED");
});

test("gallery: a network error is a partial result", async () => {
  const kaf = makeKaf();
  kaf.env.fetch = async () => {
    throw new TypeError("Failed to fetch");
  };
  assert.deepEqual(await run(collectEntryIds, kaf, CAT, 20), { host: KAF, entryIds: [], problem: "PAGE_1_FAILED" });
});

test("entry: tries candidates in document order, uses the one that works, returns only its length", async () => {
  const kaf = makeKaf();
  const out = await entry(kaf, A);
  assert.equal(out.ok, true);
  assert.equal(out.srt, SRT);
  assert.equal(out.ksLength, goodKs(A).length);
  assert.equal(out.title, `Lecture ${A}`);
  assert.equal(out.titleSource, "kaltura");
  assert.equal(out.duration, 61);
  const text = JSON.stringify(out);
  for (const k of [decoyKs(A), goodKs(A), limitedKs(A)]) assert.ok(!text.includes(k));
  const lists = kaf.calls.filter((c) => c.path.endsWith("action/list"));
  assert.equal(lists.length, 2, "decoy first, then the working one");
});

test("entry: a preferred length puts same-length candidates first", async () => {
  const kaf = makeKaf();
  await entry(kaf, A, goodKs(A).length);
  assert.equal(kaf.calls.filter((c) => c.path.endsWith("action/list")).length, 1);
});

test("entry: Kaltura requests are POST with the session in the body and no cookies", async () => {
  const kaf = makeKaf();
  const seen = [];
  const inner = kaf.env.fetch;
  kaf.env.fetch = (u, init) => {
    if (String(u).includes("kaltura")) seen.push({ u: String(u), init });
    return inner(u, init);
  };
  await entry(kaf, A);
  assert.ok(seen.length >= 3);
  for (const s of seen) {
    assert.equal(s.init.method, "POST");
    assert.equal(s.init.credentials, "omit");
    assert.ok(s.init.body.get("ks").startsWith("djJ8"));
    assert.ok(!s.u.includes("djJ8"));
  }
});

test("entry: with a known session length at most 6 candidates are tried", async () => {
  const many = Array.from({ length: 9 }, (_, i) => ks(`NOPE${i}`, 50 + i));
  const kaf = makeKaf({ pageFor: () => res(`<title>x</title>${many.map((k) => `"${k}"`).join(" ")}`) });
  const out = await entry(kaf, A, 60);
  assert.equal(out.ok, false);
  assert.equal(out.reason, "NO_CAPTIONS");
  assert.equal(kaf.calls.filter((c) => c.path.endsWith("action/list")).length, 6);
  assert.equal(out.failures.length, 6);
  assert.ok(out.failures.every((f) => f === "list:INVALID_KS"));
});

test("entry: on a cold start a working session in 7th place is still found", async () => {
  const decoys = Array.from({ length: 8 }, (_, i) => ks(`NOPE${i}`, 50 + i));
  const kaf = makeKaf({ pageFor: () => res(`<title>t</title>${decoys.map((k) => `"${k}"`).join(" ")} "${goodKs(A)}"`) });
  const out = await entry(kaf, A, null);
  assert.equal(out.ok, true);
  assert.equal(out.ksLength, goodKs(A).length);
  // Decoys 0 to 7 failed first, so the working one was the 9th candidate.
  assert.equal(kaf.calls.filter((c) => c.path.endsWith("action/list")).length, 9);
});

test("entry: a cold start stops at 20 candidates", async () => {
  const many = Array.from({ length: 30 }, (_, i) => ks(`NOPE${i}`, 50 + i));
  const kaf = makeKaf({ pageFor: () => res(`<title>x</title>${many.map((k) => `"${k}"`).join(" ")}`) });
  const out = await entry(kaf, A, null);
  assert.equal(kaf.calls.filter((c) => c.path.endsWith("action/list")).length, 20);
  assert.equal(out.failures.length, 20);
});

test("entry: a candidate past the cap is still guarded", async () => {
  const many = Array.from({ length: 8 }, (_, i) => ks(`NOPE${i}`, 50 + i));
  // The seventh candidate also shows up in the caption text, so it would leave through the output.
  const kaf = makeKaf({
    serve: () => `${SRT}${many[6]}\n`,
    pageFor: () => res(`<title>t</title> "${goodKs(A)}" ${many.map((k) => `"${k}"`).join(" ")}`),
  });
  assert.deepEqual(await entry(kaf, A, 999), { host: KAF, ok: false, reason: "KS_IN_OUTPUT" });
});

test("entry: a session in a URL path is guarded by the part before the first slash", async () => {
  const kaf = makeKaf({
    serve: () => `${SRT}${limitedKs(A)}\n`,
    pageFor: () => res(`<title>t</title> "${goodKs(A)}" "${limitedKs(A)}/uiconf_id/1"`),
  });
  assert.deepEqual(await entry(kaf, A), { host: KAF, ok: false, reason: "KS_IN_OUTPUT" });
});

test("entry: a URL-encoded session in the output is caught too", async () => {
  const odd = ks("A+B/C==", 60);
  const kaf = makeKaf({ serve: () => `${SRT}${encodeURIComponent(odd)}\n`, pageFor: () => res(`<title>t</title> "${goodKs(A)}" "${odd}"`) });
  assert.equal((await entry(kaf, A)).reason, "KS_IN_OUTPUT");
});

test("entry: serve is requested without a format", async () => {
  const kaf = makeKaf();
  const inner = kaf.env.fetch;
  const seen = [];
  kaf.env.fetch = (u, init) => {
    if (String(u).endsWith("serve/") || String(u).includes("action/serve")) seen.push(init.body.get("format"));
    return inner(u, init);
  };
  const ok = await entry(kaf, A);
  assert.equal(ok.ok, true);
  assert.ok(seen.length > 0 && seen.every((f) => f === null), "serve is sent without a format");
});

test("entry: a serve body that starts with a quote or brace gives serve:NOT_SRT", async () => {
  for (const body of [`"${SRT}"`, `{"x":"1 --> 2"}`]) {
    const kaf = makeKaf();
    const inner = kaf.env.fetch;
    // Wrap serve responses so the body is exactly `body`.
    kaf.env.fetch = (u, init) =>
      String(u).includes("action/serve") ? Promise.resolve(res(body)) : inner(u, init);
    const out = await entry(kaf, A);
    assert.equal(out.ok, false);
    assert.ok(out.failures.includes("serve:NOT_SRT"), body);
  }
  // A leading byte order mark and blank lines are fine.
  const bom = makeKaf({ serve: () => `\uFEFF\n${SRT}` });
  assert.equal((await entry(bom, A)).ok, true);
});

test("entry: the mock honours format, so asking for JSON on serve would be caught", async () => {
  const kaf = makeKaf();
  const r = await kaf.env.fetch("https://cdnapisec.kaltura.com/api_v3/service/caption_captionasset/action/serve", {
    method: "POST",
    body: new URLSearchParams({ ks: goodKs(A), captionAssetId: `cap-${A}`, format: "1" }),
  });
  assert.equal((await r.text())[0], '"');
});

test("entry: when the working session cannot read the title, another candidate is tried first", async () => {
  const kaf = makeKaf({ getKs: () => ks("TITLEONLY", 55), pageFor: (id) => res(`<title>Page title</title> "${decoyKs(id)}" "${goodKs(id)}" "${ks("TITLEONLY", 55)}"`) });
  const out = await entry(kaf, A);
  assert.equal(out.ok, true);
  assert.equal(out.titleSource, "kaltura");
  assert.equal(out.title, `Lecture ${A}`);
});

test("entry: a page title that looks like a session is refused", async () => {
  const kaf = makeKaf({ titleFails: true, pageTitle: `<title>Lecture ${"Ab1+".repeat(12)}</title>` });
  assert.deepEqual(await entry(kaf, A), { host: KAF, ok: false, reason: "NO_TITLE" });
});

test("entry: a lecture page that redirected to a login page is PAGE_REDIRECTED", async () => {
  const login = makeKaf({ pageFor: () => ({ ...res("<html>login</html>"), redirected: true, url: "https://canvaskaf.ucsd.edu/login" }) });
  assert.equal((await entry(login, A)).reason, "PAGE_REDIRECTED");
  const elsewhere = makeKaf({ pageFor: () => ({ ...res("x"), redirected: true, url: "https://evil.example/media/x" }) });
  assert.equal((await entry(elsewhere, A)).reason, "PAGE_REDIRECTED");
});

test("entry: a lecture page that redirects to another lecture URL is read normally", async () => {
  const base = makeKaf();
  const inner = base.env.fetch;
  base.env.fetch = async (u, init) => {
    const r = await inner(u, init);
    return String(u).startsWith("/media/t/") ? { ...r, redirected: true, url: `https://canvaskaf.ucsd.edu/media/title/${A}` } : r;
  };
  assert.equal((await entry(base, A)).ok, true);
});

test("every request inside the frame carries a timeout signal", async () => {
  const kaf = makeKaf();
  const inner = kaf.env.fetch;
  const signals = [];
  kaf.env.fetch = (u, init) => (signals.push(init?.signal), inner(u, init));
  await entry(kaf, A);
  await run(collectEntryIds, kaf, CAT, 20);
  assert.ok(signals.length >= 7);
  assert.ok(signals.every((s) => s instanceof AbortSignal));
});

test("entry: title falls back to the decoded page title with duration 0", async () => {
  const kaf = makeKaf({ titleFails: true, pageTitle: "<title>\n  Lecture &amp; &#39;Intro&#x21;&#39;  </title>" });
  const out = await entry(kaf, A);
  assert.equal(out.ok, true);
  assert.equal(out.title, "Lecture & 'Intro!'");
  assert.equal(out.titleSource, "page");
  assert.equal(out.duration, 0);
  assert.equal(out.createdAt, undefined);
});

test("entry: no title anywhere is skipped with a reason", async () => {
  const kaf = makeKaf({ titleFails: true, pageTitle: "<title>  </title>" });
  assert.deepEqual(await entry(kaf, A), { host: KAF, ok: false, reason: "NO_TITLE" });
  const none = makeKaf({ titleFails: true, pageTitle: "" });
  assert.equal((await entry(none, A)).reason, "NO_TITLE");
});

test("entry: all candidates failing gives codes, never bodies", async () => {
  const kaf = makeKaf({ serve: () => "" });
  // The good session serves an empty body; the decoys fail the list call.
  const out = await entry(kaf, A);
  assert.equal(out.ok, false);
  assert.equal(out.reason, "NO_CAPTIONS");
  assert.ok(out.failures.includes("list:INVALID_KS"));
  assert.ok(out.failures.includes("serve:empty"));
  for (const f of out.failures) assert.match(f, /^(list|serve):([A-Z0-9_]+|empty)$/);
  assert.ok(!JSON.stringify(out).includes("djJ8"));
});

test("entry: a non-SRT serve body reports a Kaltura code or NOT_SRT, not the body", async () => {
  const withCode = makeKaf({ serve: () => '{"objectType":"KalturaAPIException","code":"CAPTION_ASSET_NOT_READY","message":"secret words"}' });
  const a = await entry(withCode, A);
  assert.ok(a.failures.includes("serve:CAPTION_ASSET_NOT_READY"));
  assert.ok(!JSON.stringify(a).includes("secret words"));
  const plain = makeKaf({ serve: () => "just some words" });
  assert.ok((await entry(plain, A)).failures.includes("serve:NOT_SRT"));
});

test("entry: HTTP failures are reported as statuses", async () => {
  const kaf = makeKaf({ kalturaFail: 503 });
  assert.ok((await entry(kaf, A)).failures.every((f) => f === "list:HTTP_503"));
});

test("entry: a lecture page failure and a page without sessions", async () => {
  assert.equal((await entry(makeKaf({ pageFor: () => res("", { status: 404 }) }), A)).reason, "PAGE_HTTP_404");
  assert.equal((await entry(makeKaf({ pageFor: () => res("<title>t</title> nothing") }), A)).reason, "NO_SESSION");
});

test("entry: bad input is refused before any request", async () => {
  const kaf = makeKaf();
  assert.equal((await run(readEntryCaptions, kaf, "../x", CAT, null, KS_PREFIX)).reason, "BAD_INPUT");
  assert.equal((await run(readEntryCaptions, kaf, A, "5/../6", null, KS_PREFIX)).reason, "BAD_INPUT");
  assert.equal((await run(readEntryCaptions, kaf, A, CAT, null, "(")).reason, "BAD_INPUT");
});

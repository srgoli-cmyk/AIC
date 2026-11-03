// Runs scripts/console_sync.js in a sandbox against synthetic KAF and Kaltura responses.
// Every network call is mocked: this test never touches Canvas, KAF or Kaltura.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import vm from "node:vm";
import { buildSyncPayload } from "../extension/src/payload.js";

const ROOT = new URL("../", import.meta.url);
const SNIPPET = readFileSync(new URL("scripts/console_sync.js", ROOT), "utf8");

// The "+" in the prefix also proves the candidate regex escapes it.
const KS = `F+KE/value==${"p".repeat(50)}`;
const entryKs = (id) => `F+KE${id}`.padEnd(KS.length, "e");
const OTHER_KS = "F+KE".padEnd(KS.length, "o");
const LIMITED_KS = "F+KE".padEnd(50, "l");
const CAT = "555";
const A = "1_aaaaaaaa";
const B = "1_bbbbbbbb";
const C = "1_cccccccc";
const D = "1_dddddddd";
const SRT = "1\n00:00:01,000 --> 00:00:02,000\nSynthetic\n";
const ALL = [A, B, C, D];

function text(body) {
  return { ok: true, status: 200, text: async () => body };
}

function json(data) {
  return text(JSON.stringify(data));
}

function fail(status) {
  return { ok: false, status, text: async () => "" };
}

function blocked() {
  throw new TypeError("Failed to fetch");
}

function link(id) {
  return `<a href="/media/t/${id}/${CAT}">x</a>`;
}

function defaultRoutes() {
  return {
    // Page 1 repeats A and has B JSON-escaped on purpose, as an ajax response can.
    gallery: (page) =>
      text(page === 1 ? `${link(A)}${link(A)}{"content":"<a href=\\"\\/media\\/t\\/${B}\\/${CAT}\\">"}${link(C)}` : link(D)),
    get: (id) => json({ id, name: `Lecture ${id}`, duration: 60, createdAt: 1790000000, downloadUrl: "https://signed.example/y" }),
    page: (id) => text(`<script>var session = "${entryKs(id)}";</script>`),
    list: (id, ks) =>
      ks === KS || ks === entryKs(id)
        ? json({ objects: [{ id: `cap-${id}`, format: "1", status: 2, isDefault: 1, language: "English" }] })
        : json({ objectType: "KalturaAPIException", code: "INVALID_KS", message: `bad ${ks}` }),
    // Like the live system: the page KS serves another lecture's captions as an empty 200.
    serve: (assetId, ks) => {
      if (ks === KS) return text("");
      if (ks === entryKs(assetId.replace("cap-", ""))) return text(SRT);
      return json({ objectType: "KalturaAPIException", code: "INVALID_KS", message: `bad ${ks}` });
    },
    answers: ["999999", "TEST 1", "Synthetic"],
    pathname: `/media/t/${A}/${CAT}`,
    hostname: "canvaskaf.ucsd.edu",
  };
}

// Anything saved lands on the object itself, where the KS test's JSON.stringify can see it.
function storageSpy() {
  return {
    setItem(key, value) {
      this[key] = value;
    },
  };
}

async function run(overrides = {}) {
  const routes = { ...defaultRoutes(), ...overrides };
  const answers = [...routes.answers];
  const calls = [];
  const logs = [];
  const downloads = [];
  const asked = [];
  const pagesRequested = [];
  let inFlight = 0;
  let maxInFlight = 0;

  const fetchMock = async (url, opts = {}) => {
    inFlight++;
    maxInFlight = Math.max(maxInFlight, inFlight);
    await new Promise((resolve) => setImmediate(resolve));
    inFlight--;
    const params = opts.body instanceof URLSearchParams ? Object.fromEntries(opts.body) : {};
    calls.push({ url, opts, params, rawBody: String(opts.body ?? "") });
    const gallery = url.match(new RegExp(`^/channel/channelid/${CAT}\\?format=ajax&page=(\\d+)$`));
    if (gallery) {
      pagesRequested.push(Number(gallery[1]));
      return routes.gallery(Number(gallery[1]));
    }
    const lecture = url.match(new RegExp(`^/media/t/([^/]+)/${CAT}$`));
    if (lecture) return routes.page(lecture[1]);
    if (url.endsWith("baseentry/action/get")) return routes.get(params.entryId);
    if (url.endsWith("caption_captionasset/action/list")) return routes.list(params["filter[entryIdEqual]"], params.ks);
    if (url.endsWith("caption_captionasset/action/serve")) return routes.serve(params.captionAssetId, params.ks);
    throw new Error(`unexpected request: ${url}`);
  };

  const record = (...args) => logs.push(args.join(" "));
  const blobs = new Map();
  const anchor = { click: () => downloads.push({ name: anchor.download, text: blobs.get(anchor.href) }), remove() {} };
  const sandbox = {
    fetch: fetchMock,
    console: { log: record, warn: record, error: record },
    location: { hostname: routes.hostname, pathname: routes.pathname },
    KalturaPlayer: routes.noPlayer ? undefined : { getPlayers: () => ({ p: { config: { provider: { ks: KS } } } }) },
    URLSearchParams,
    window: {},
    localStorage: storageSpy(),
    sessionStorage: storageSpy(),
    prompt: (message) => {
      asked.push(message);
      return answers.length ? answers.shift() : null;
    },
    Blob: class {
      constructor(parts) {
        this.text = parts.join("");
      }
    },
    URL: {
      createObjectURL(blob) {
        blobs.set("blob:1", blob.text);
        return "blob:1";
      },
      revokeObjectURL() {},
    },
    document: { createElement: () => anchor, body: { append() {} } },
    setTimeout: () => {},
  };
  const globalsBefore = Object.keys(sandbox);
  await vm.runInNewContext(SNIPPET, sandbox);
  return {
    sandbox,
    globalsBefore,
    calls,
    logs,
    log: logs.join("\n"),
    asked,
    pagesRequested,
    downloads,
    maxInFlight,
    payload: downloads[0] && JSON.parse(downloads[0].text),
  };
}

function lectureIds(payload) {
  return payload.lectures.map((l) => l.entry_id);
}

function assertMatchesContract(payload) {
  const r = spawnSync("python3", ["scripts/validate_payload.py"], {
    cwd: fileURLToPath(ROOT),
    input: JSON.stringify(payload),
    encoding: "utf8",
  });
  assert.equal(r.status, 0, r.stderr);
}

// ---- Payload

test("builds the same payload as extension/src/payload.js and matches the contract", async () => {
  const { payload, downloads } = await run();
  assert.equal(downloads[0].name, "sync_999999.json");
  const expected = buildSyncPayload({
    course: { id: 999999, course_code: "TEST 1", name: "Synthetic" },
    entries: ALL.map((id) => ({ id, name: `Lecture ${id}`, duration: 60, createdAt: 1790000000, srt: SRT })),
    files: [],
    now: new Date(payload.synced_at),
  });
  assert.deepEqual(payload, expected);
  assert.deepEqual(payload.files, []);
  assertMatchesContract(payload);
});

test("the mapping source is identical to extension/src/payload.js", () => {
  const normalize = (src) =>
    src
      .slice(src.indexOf('const MEDIA_BASE = "'))
      .replace(/\/\*\*[\s\S]*?\*\//g, "")
      .replace(/export /g, "")
      .replace(/\s+/g, " ")
      .trim();
  const original = readFileSync(new URL("extension/src/payload.js", ROOT), "utf8");
  const copy = SNIPPET.slice(0, SNIPPET.indexOf("// ---- End of copy."));
  assert.equal(normalize(copy), normalize(original));
});

test("keeps lectures repeated across sections and logs the unique titles", async () => {
  const { payload, log } = await run({ get: (id) => json({ id, name: id === A || id === B ? "Same" : id }) });
  assert.deepEqual(lectureIds(payload), ALL);
  assert.match(log, /Lectures: 4 of 4\. Unique titles: 3\./);
});

// ---- Requests

test("makes one request at a time, in order", async () => {
  const { calls, maxInFlight } = await run();
  assert.equal(maxInFlight, 1);
  const kaltura = calls.filter((c) => c.url.includes("kaltura.com")).map((c) => c.url.split("/service/")[1]);
  const perEntry = ["baseentry/action/get", "caption_captionasset/action/list", "caption_captionasset/action/serve"];
  assert.deepEqual(kaltura, ALL.flatMap(() => perEntry));
  const pages = calls.filter((c) => c.url.startsWith("/media/t/")).map((c) => c.url);
  assert.deepEqual(pages, ALL.map((id) => `/media/t/${id}/${CAT}`));
});

test("makes no Canvas calls and only talks to KAF and Kaltura", async () => {
  const { calls } = await run();
  const hosts = new Set(calls.map((c) => new URL(c.url, "https://canvaskaf.ucsd.edu").hostname));
  assert.deepEqual([...hosts].sort(), ["canvaskaf.ucsd.edu", "cdnapisec.kaltura.com"]);
});

test("does not rely on the course-gallery redirect", async () => {
  const { calls, downloads } = await run();
  assert.equal(downloads.length, 1);
  assert.ok(!calls.some((c) => c.url.includes("course-gallery")));
  assert.equal(calls[0].url, `/channel/channelid/${CAT}?format=ajax&page=1`);
});

test("requests gallery pages as ajax GETs and calls no playlist endpoints", async () => {
  const { calls } = await run();
  const kaf = calls.filter((c) => c.url.includes("format=ajax"));
  assert.equal(kaf.length, 3);
  for (const c of kaf) {
    assert.equal(c.opts.method, undefined);
    assert.equal(c.opts.headers["X-Requested-With"], "XMLHttpRequest");
    assert.ok(!c.url.includes("playlist"));
  }
});

// ---- KS

test("every KS only travels in Kaltura POST bodies", async () => {
  const { calls, log, downloads } = await run();
  const every = [KS, ...ALL.map(entryKs)];
  const hasKs = (s) =>
    every.some((ks) => s.includes(ks) || s.includes(encodeURIComponent(ks)) || s.includes(new URLSearchParams({ k: ks }).toString().slice(2)));
  assert.ok(!hasKs(downloads[0].text));
  assert.ok(!hasKs(log));
  for (const c of calls) {
    assert.ok(!hasKs(c.url));
    assert.ok(!hasKs(JSON.stringify(c.opts.headers ?? {})));
    if (c.url.includes("kaltura.com")) {
      assert.equal(c.opts.method, "POST");
      assert.equal(c.opts.credentials, "omit");
      assert.ok(every.includes(c.params.ks));
    } else {
      assert.ok(!hasKs(c.rawBody));
    }
  }
});

test("leaves no globals behind and no KS on the page objects", async () => {
  const { sandbox, globalsBefore } = await run();
  assert.deepEqual(Object.keys(sandbox), globalsBefore);
  assert.deepEqual(Object.keys(sandbox.window), []);
  assert.deepEqual(Object.keys(sandbox.localStorage), ["setItem"]);
  assert.deepEqual(Object.keys(sandbox.sessionStorage), ["setItem"]);
  const state = JSON.stringify(sandbox);
  assert.ok(![KS, ...ALL.map(entryKs)].some((ks) => state.includes(ks)));
});

test("refuses to download if the page KS reaches the output, raw or URL-encoded", async () => {
  for (const leak of [KS, encodeURIComponent(KS)]) {
    const { downloads, log } = await run({ serve: () => text(`${SRT}${leak}\n`) });
    assert.equal(downloads.length, 0);
    assert.match(log, /Refusing to download/);
    assert.ok(!log.includes(KS));
  }
});

test("refuses to download if a per-entry KS reaches the output, raw or URL-encoded", async () => {
  for (const leak of [entryKs(B), encodeURIComponent(entryKs(B))]) {
    const { downloads, log } = await run({ get: (id) => json({ id, name: id === A ? leak : id }) });
    assert.equal(downloads.length, 0);
    assert.match(log, /Refusing to download/);
    assert.ok(!log.includes(entryKs(B)));
  }
});

test("the guard catches a KS found inside a URL path on the lecture page", async () => {
  const inPath = "F+KEpath".padEnd(KS.length, "k");
  const page = (id) => text(`${entryKs(id)} <a href="/ks/${inPath}/uiconf_id/1">`);
  const { downloads, log } = await run({ page, get: (id) => json({ id, name: id === A ? inPath : id }) });
  assert.equal(downloads.length, 0);
  assert.match(log, /Refusing to download/);
});

test("the guard also covers a KS that was seen but never used", async () => {
  const unused = Array.from({ length: 7 }, (_, i) => `F+KE${i}`.padEnd(KS.length, "u"));
  const page = (id) => text([entryKs(id), ...unused].join(" "));
  const leak = unused[6]; // past the cap of 6, so never tried
  const { downloads, calls, log } = await run({ page, get: (id) => json({ id, name: id === D ? leak : id }) });
  assert.ok(!calls.some((c) => c.params.ks === leak));
  assert.equal(downloads.length, 0);
  assert.match(log, /Refusing to download/);
});

test("a Kaltura exception with no readable code logs unknown", async () => {
  const { log } = await run({ get: () => json({ objectType: "KalturaAPIException", message: "SECRETBODY" }) });
  assert.match(log, /baseentry\/action\/get: Kaltura error unknown/);
  assert.ok(!log.includes("SECRETBODY"));
});

test("does not log the contents of Kaltura errors", async () => {
  const { log } = await run({
    get: () => json({ objectType: "KalturaAPIException", code: "INVALID_KS", message: `bad ks ${KS}` }),
  });
  assert.match(log, /INVALID_KS/);
  assert.ok(!log.includes(KS));
});

test("non-JSON responses cannot echo their body into the logs", async () => {
  const { downloads, log } = await run({ get: () => text(KS) });
  assert.equal(downloads.length, 0);
  assert.match(log, /baseentry\/action\/get: non-JSON response/);
  assert.ok(!log.includes(KS));
});

// ---- Collecting entry IDs

const ids = (from, n) => Array.from({ length: n }, (_, i) => `1_${String(from + i).padStart(8, "0")}`);

test("takes the union of the pages: 15 + 9 = 24, like MATH 20A", async () => {
  const first = ids(0, 15);
  const second = ids(15, 9);
  const gallery = (page) => text((page === 1 ? first : page === 2 ? [...first.slice(0, 2), ...second] : second).map(link).join(""));
  const { payload, pagesRequested, log } = await run({ gallery });
  assert.deepEqual(lectureIds(payload), [...first, ...second]);
  assert.deepEqual(pagesRequested, [1, 2, 3]);
  assert.match(log, /Category 555: 24 entries\./);
  assert.doesNotMatch(log, /partial/);
});

test("matches entry links in JSON-escaped responses", async () => {
  const { payload } = await run();
  assert.ok(lectureIds(payload).includes(B));
});

test("stops at the first page that adds no new IDs", async () => {
  const { pagesRequested, log } = await run();
  assert.deepEqual(pagesRequested, [1, 2, 3]);
  assert.doesNotMatch(log, /partial/);
});

test("stops at an empty page", async () => {
  const { pagesRequested, payload } = await run({ gallery: (page) => text(page === 1 ? link(A) : "") });
  assert.deepEqual(pagesRequested, [1, 2]);
  assert.deepEqual(lectureIds(payload), [A]);
});

test("stops at the 20-page cap and reports partial", async () => {
  const gallery = (page) => text(link(ids(page, 1)[0]));
  const { pagesRequested, log, downloads } = await run({ gallery });
  assert.deepEqual(pagesRequested, Array.from({ length: 20 }, (_, i) => i + 1));
  assert.match(log, /partial: 20 entries \(stopped at the 20-page cap\)/);
  assert.equal(downloads.length, 1);
});

test("page 20 adding nothing new ends the walk without reporting partial", async () => {
  const gallery = (page) => text(link(ids(Math.min(page, 19), 1)[0]));
  const { pagesRequested, log } = await run({ gallery });
  assert.equal(pagesRequested.length, 20);
  assert.doesNotMatch(log, /partial/);
});

test("a redirected page counts as a failure, not as the last page", async () => {
  const gallery = (page) => (page === 2 ? { ...text("<p>login</p>"), redirected: true } : text(link(A)));
  const { payload, log } = await run({ gallery });
  assert.deepEqual(lectureIds(payload), [A]);
  assert.match(log, /partial: 1 entries \(gallery page 2: redirected/);
});

test("ignores an ID that is too long instead of truncating it", async () => {
  const gallery = (page) => text(page === 1 ? `${link("1_zzzzzzzzz")}${link(A)}` : "");
  const { payload } = await run({ gallery });
  assert.deepEqual(lectureIds(payload), [A]);
});

test("drops links from another category, and links with no category", async () => {
  const gallery = (page) =>
    text(page === 1 ? `${link(A)}<a href="/media/t/${B}/999">foreign</a><a href="/media/t/${C}">none</a>` : "");
  const { payload, calls } = await run({ gallery });
  assert.deepEqual(lectureIds(payload), [A]);
  assert.ok(!calls.some((c) => c.url.startsWith(`/media/t/${B}`) || c.url.startsWith(`/media/t/${C}`)));
});

test("drops a JSON-escaped link from another category", async () => {
  const gallery = (page) => text(page === 1 ? `${link(A)}"\\/media\\/t\\/${B}\\/999"` : "");
  const { payload } = await run({ gallery });
  assert.deepEqual(lectureIds(payload), [A]);
});

test("a page with only foreign links adds nothing, so it ends the walk", async () => {
  const gallery = (page) => text(page === 1 ? link(A) : `<a href="/media/t/${B}/999">foreign</a>`);
  const { pagesRequested, payload } = await run({ gallery });
  assert.deepEqual(pagesRequested, [1, 2]);
  assert.deepEqual(lectureIds(payload), [A]);
});

test("a failed page keeps what was found and reports partial", async () => {
  for (const failure of [() => fail(500), blocked]) {
    const gallery = (page) => (page === 2 ? failure() : text(link(A)));
    const { payload, log, pagesRequested } = await run({ gallery });
    assert.deepEqual(pagesRequested, [1, 2]);
    assert.deepEqual(lectureIds(payload), [A]);
    assert.match(log, /partial: 1 entries \(gallery page 2: /);
  }
});

test("stops without prompting when no page gives any ID", async () => {
  for (const gallery of [() => fail(403), () => text("<p>login</p>")]) {
    const { downloads, log, asked } = await run({ gallery });
    assert.equal(downloads.length, 0);
    assert.equal(asked.length, 0);
    assert.match(log, /No lectures found in category 555/);
  }
});

// ---- Per-entry sessions

const listCalls = (calls, id) =>
  calls.filter((c) => c.url.endsWith("action/list") && c.params["filter[entryIdEqual]"] === id).map((c) => c.params.ks);

test("page KS serves empty: the KS from each lecture page gets the captions", async () => {
  const { payload, calls, log } = await run();
  assert.deepEqual(lectureIds(payload), ALL);
  assert.ok(payload.lectures.every((l) => l.captions_srt === SRT));
  for (const c of calls.filter((x) => x.url.includes("/caption_captionasset/"))) assert.notEqual(c.params.ks, KS);
  assert.doesNotMatch(log, /skipped/);
});

test("moves past a candidate that serves empty", async () => {
  const page = (id) => text(`${KS} ${entryKs(id)}`);
  const { payload, calls, log } = await run({ page });
  assert.deepEqual(lectureIds(payload), ALL);
  assert.deepEqual(listCalls(calls, A), [KS, entryKs(A)]);
  assert.doesNotMatch(log, /skipped/);
});

test("does not reuse one lecture's KS for another", async () => {
  const { calls } = await run();
  for (const id of ALL) assert.deepEqual(listCalls(calls, id), [entryKs(id)]);
  const serves = calls.filter((c) => c.url.endsWith("action/serve"));
  assert.deepEqual(serves.map((c) => c.params.ks), ALL.map(entryKs));
});

test("tries same-length candidates first, removes repeats, stops at the first that works", async () => {
  const page = (id) => text(`${LIMITED_KS} ${OTHER_KS} ${OTHER_KS} ${entryKs(id)} "${LIMITED_KS}x"`);
  const { calls, payload } = await run({ page });
  assert.equal(payload.lectures.length, 4);
  assert.deepEqual(listCalls(calls, A), [OTHER_KS, entryKs(A)]);
});

test("falls back to other lengths after the same-length candidates", async () => {
  const shorter = (id) => `F+KE${id}`.padEnd(60, "s");
  const page = (id) => text(`${shorter(id)} ${OTHER_KS}`);
  const list = (id, ks) =>
    ks === shorter(id)
      ? json({ objects: [{ id: `cap-${id}`, format: "1", status: 2 }] })
      : json({ objectType: "KalturaAPIException", code: "INVALID_KS" });
  const serve = () => text(SRT);
  const { calls, payload } = await run({ page, list, serve });
  assert.equal(payload.lectures.length, 4);
  assert.deepEqual(listCalls(calls, A), [OTHER_KS, shorter(A)]);
});

test("tries at most 6 candidates", async () => {
  const many = Array.from({ length: 8 }, (_, i) => `F+KE${i}`.padEnd(KS.length, "u"));
  const { calls } = await run({ page: () => text(many.join(" ")), gallery: (n) => text(n === 1 ? link(A) : "") });
  assert.deepEqual(listCalls(calls, A), many.slice(0, 6));
});

test("all candidates fail: skips with the codes and logs no candidate", async () => {
  const page = () => text(`${OTHER_KS} ${LIMITED_KS}`);
  const gallery = (n) => text(n === 1 ? link(A) : "");
  const list = () => json({ objects: [{ id: "cap", format: "1", status: 2 }] });
  const serve = (_, ks) =>
    ks === OTHER_KS ? text("") : json({ objectType: "KalturaAPIException", code: "INVALID_ACTIONS_LIMIT" });
  const { downloads, log } = await run({ page, gallery, list, serve });
  assert.equal(downloads.length, 0);
  assert.match(log, /captions not readable with 2 session\(s\)/);
  assert.match(log, /serve: empty; .*serve: INVALID_ACTIONS_LIMIT/);
  assert.ok(!log.includes(OTHER_KS) && !log.includes(LIMITED_KS));
});

test("one lecture failing does not stop the others", async () => {
  const page = (id) => text(id === A ? OTHER_KS : entryKs(id));
  const { payload, log } = await run({ page });
  assert.deepEqual(lectureIds(payload), [B, C, D]);
  assert.match(log, new RegExp(`${A} skipped: captions not readable`));
  assert.match(log, /Skipped 1:/);
});

test("skips an entry whose lecture page has no session or cannot be read", async () => {
  const cases = [
    [() => text("<p>login</p>"), /no session found on its lecture page/],
    [() => fail(403), /lecture page: HTTP 403/],
    [blocked, /lecture page: Failed to fetch/],
  ];
  for (const [page, message] of cases) {
    const { downloads, log } = await run({ page });
    assert.equal(downloads.length, 0);
    assert.match(log, message);
  }
});

test("a non-SRT serve logs only a code, never the body", async () => {
  const secret = "SECRETBODY";
  const cases = [
    [text(""), /serve: empty/],
    [text("  \n"), /serve: empty/],
    [json({ objectType: "KalturaAPIException", code: "INVALID_KS", message: secret }), /serve: INVALID_KS/],
    [text(`<xml><error><code>INVALID_KS</code><message>${secret}</message></error></xml>`), /serve: INVALID_KS/],
    [json({ code: `<b>${secret}</b>` }), /serve: not SRT/],
    [text(secret), /serve: not SRT/],
    [json({ code: "A".repeat(65) }), /serve: not SRT/],
    [text(`<code>${"A".repeat(65)}</code>`), /serve: not SRT/],
  ];
  for (const [response, message] of cases) {
    const { log } = await run({ serve: () => response });
    assert.match(log, message);
    assert.ok(!log.includes(secret));
  }
});

// ---- Prompts

test("asks for the course ID, code and name, and builds the course from them", async () => {
  const { payload, asked } = await run({ answers: [" 999999 ", "MATH 20A", "Calculus"] });
  assert.equal(asked.length, 3);
  assert.deepEqual(payload.course, { canvas_course_id: 999999, code: "MATH 20A", name: "Calculus" });
});

test("rejects a course ID that is not a positive integer", async () => {
  for (const id of ["abc", "12.5", "0", "-1", "1e5", "99999999999999999999"]) {
    const { downloads, log, calls } = await run({ answers: [id, "TEST 1", "Synthetic"] });
    assert.equal(downloads.length, 0);
    assert.match(log, /must be a positive integer/);
    assert.ok(!calls.some((c) => c.url.includes("kaltura.com")));
  }
});

test("stops when a prompt is cancelled", async () => {
  for (const answers of [[], ["999999"], ["999999", "TEST 1"]]) {
    const { downloads, log } = await run({ answers });
    assert.equal(downloads.length, 0);
    assert.match(log, /required/);
  }
});

// ---- Captions

test("skips and reports an entry with no SRT captions, keeps the rest", async () => {
  const { payload, log } = await run({
    list: (id) =>
      json({ objects: id === A ? [{ id: "vtt", format: "3", status: 2 }] : [{ id: `cap-${id}`, format: "1", status: 2 }] }),
  });
  assert.deepEqual(lectureIds(payload), [B, C, D]);
  assert.match(log, new RegExp(`${A}: .*no SRT captions`));
});

test("skips an entry with no title", async () => {
  const { payload, log } = await run({ get: (id) => json({ id, name: id === A ? "" : id }) });
  assert.deepEqual(lectureIds(payload), [B, C, D]);
  assert.match(log, new RegExp(`${A}: entry has no title`));
});

test("does not store a serve error as captions", async () => {
  const { downloads, log } = await run({
    serve: () => text(JSON.stringify({ objectType: "KalturaAPIException", code: "SOME_ERROR" })),
  });
  assert.equal(downloads.length, 0);
  assert.match(log, /serve: SOME_ERROR/);
});

test("prefers the default caption, then English", async () => {
  const objects = [
    { id: "es", format: "1", status: 2, language: "Spanish" },
    { id: "en", format: "1", status: 2, language: "English" },
  ];
  const servedWith = async (list) => {
    const served = [];
    await run({
      list: () => json({ objects: list }),
      serve: (id) => {
        served.push(id);
        return text(SRT);
      },
    });
    return new Set(served);
  };
  const withDefault = [...objects, { id: "default", format: "1", status: 2, language: "French", isDefault: true }];
  assert.deepEqual(await servedWith(withDefault), new Set(["default"]));
  assert.deepEqual(await servedWith(objects), new Set(["en"]));
});

test("ignores a caption that is not ready", async () => {
  const { log } = await run({ list: () => json({ objects: [{ id: "x", format: "1", status: 0, isDefault: 1 }] }) });
  assert.match(log, /no SRT captions/);
});

test("names the failing call for API, network and HTTP errors", async () => {
  const cases = [
    [
      { get: () => json({ objectType: "KalturaAPIException", code: "SERVICE_FORBIDDEN" }) },
      /No captions could be read:[\s\S]*baseentry\/action\/get: Kaltura error SERVICE_FORBIDDEN/,
    ],
    [{ get: blocked }, /baseentry\/action\/get: Failed to fetch/],
    [{ list: blocked }, /caption_captionasset\/action\/list: Failed to fetch/],
    [{ serve: blocked }, /caption_captionasset\/action\/serve: Failed to fetch/],
    [{ serve: () => fail(403) }, /caption_captionasset\/action\/serve: HTTP 403/],
  ];
  for (const [overrides, message] of cases) {
    const { log } = await run(overrides);
    assert.match(log, message);
  }
});

// ---- Limits and setup

test("stops before downloading more lectures than the contract allows", async () => {
  const gallery = (page) => text(page === 1 ? ids(0, 201).map(link).join("") : "");
  const { downloads, log } = await run({ gallery });
  assert.equal(downloads.length, 0);
  assert.match(log, /lectures \(contract limit 200\)/);
});

test("fails without downloading when the setup is wrong", async () => {
  const cases = [
    [{ hostname: "canvas.ucsd.edu" }, /canvaskaf/],
    [{ noPlayer: true }, /No Kaltura session/],
    [{ pathname: `/media/t/${A}` }, /\/media\/t\/<entryId>\/<categoryId>/],
    [{ pathname: `/channel/channelid/${CAT}` }, /\/media\/t\/<entryId>\/<categoryId>/],
  ];
  for (const [overrides, message] of cases) {
    const { downloads, log, calls } = await run(overrides);
    assert.equal(downloads.length, 0);
    assert.match(log, message);
    assert.equal(calls.length, 0);
  }
});

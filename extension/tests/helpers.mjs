// Synthetic Canvas, KAF, Kaltura and backend for the tests. Nothing here touches a network.
// Every "session" below is made up.
export const COURSE = 12345;
export const CAT = "555";
export const A = "1_aaaaaaaa";
export const B = "1_bbbbbbbb";
export const C = "1_cccccccc";
export const D = "1_dddddddd";
export const SRT = "1\n00:00:01,000 --> 00:00:02,000\nSynthetic caption\n";
export const KAF = "canvaskaf.ucsd.edu";

// Looks like a session: the assumed prefix plus 40 or more allowed characters.
export const ks = (tag, len = 60) => `djJ8${tag}`.padEnd(len, "k");
export const goodKs = (id) => ks(`GOOD${id}`);
export const decoyKs = (id) => ks(`DECOY${id}`, 52);
export const limitedKs = (id) => ks(`LIMIT${id}`, 60);

export function res(body, { status = 200, headers = {}, redirected = false } = {}) {
  const text = typeof body === "string" ? body : JSON.stringify(body);
  return {
    ok: status >= 200 && status < 300,
    status,
    redirected,
    headers: { get: (k) => headers[k] ?? headers[k.toLowerCase()] ?? null },
    text: async () => text,
    json: async () => JSON.parse(text),
  };
}

// Tracks how many requests or injected calls run at once.
export function tracker() {
  const t = { inflight: 0, max: 0, log: [] };
  t.run = async (label, fn) => {
    t.inflight++;
    t.max = Math.max(t.max, t.inflight);
    t.log.push(label);
    try {
      await new Promise((r) => setImmediate(r));
      return await fn();
    } finally {
      t.inflight--;
    }
  };
  return t;
}

// Runs an injected function the way executeScript does: from its source text only.
export function rebuild(fn, env) {
  return new Function("fetch", "location", "document", `return (${fn.toString()});`)(env.fetch, env.location, env.document);
}

const exception = (code, message = "failed") => ({ objectType: "KalturaAPIException", code, message });

/** The lecture site as seen from inside its frame. */
export function makeKaf(opts = {}) {
  const o = {
    galleryPages: [`/media/t/${A}/${CAT} /media/t/${A}/${CAT} /media/t/${B}/${CAT}`, `/media/t/${C}/${CAT} /media/t/${A}/999`],
    entries: [A, B, C],
    tracker: tracker(),
    titleFails: false,
    ...opts,
  };
  const calls = [];
  const idOf = (body) => body.get("filter[entryIdEqual]") || body.get("entryId");
  async function kaltura(path, body) {
    const key = body.get("ks");
    const id = idOf(body);
    calls.push({ path, id, ks: key });
    if (o.kalturaFail) return res("", { status: o.kalturaFail });
    if (path.endsWith("caption_captionasset/action/list")) {
      if (key === goodKs(id)) return res({ objects: [{ id: `cap-${id}`, format: "1", status: 2, isDefault: 1 }] });
      return res(exception("INVALID_KS", `bad ${key}`));
    }
    if (path.endsWith("caption_captionasset/action/serve")) {
      const asset = body.get("captionAssetId");
      if (key !== goodKs(asset.replace("cap-", ""))) return res("");
      const text = o.serve ? o.serve(asset) : SRT;
      // Like Kaltura: asking for JSON wraps the text in quotes.
      return res(body.get("format") === "1" ? JSON.stringify(text) : text);
    }
    if (path.endsWith("baseentry/action/get")) {
      const getKey = o.getKs ? o.getKs(id) : goodKs(id);
      if (o.titleFails || key !== getKey) return res(exception("INVALID_KS"));
      return res({ id, name: `Lecture ${id}`, duration: 61, createdAt: 1790000000, downloadUrl: "https://signed.example/x" });
    }
    return res("", { status: 404 });
  }
  const page = (id) => {
    if (o.pageFor) return o.pageFor(id);
    const title = o.pageTitle === undefined ? `<title>Lecture &amp; page ${id}</title>` : o.pageTitle;
    return res(
      `<html><head>${title}</head><script>var a="${decoyKs(id)}";var b="${goodKs(id)}";var c="${limitedKs(id)}/uiconf_id/1";</script></html>`,
    );
  };
  const fetch = (url, init = {}) =>
    o.tracker.run(String(url), async () => {
      const u = String(url);
      if (u.startsWith("https://cdnapisec.kaltura.com/api_v3/service/")) {
        const body = init.body;
        return kaltura(u.slice("https://cdnapisec.kaltura.com/api_v3/service/".length), body);
      }
      const gallery = u.match(/^\/channel\/channelid\/(\d+)\?format=ajax&page=(\d+)$/);
      if (gallery) {
        const n = Number(gallery[2]);
        if (o.galleryFail?.(n)) return o.galleryFail(n);
        return res(o.galleryPages[n - 1] ?? o.galleryPages.at(-1));
      }
      const lecture = u.match(/^\/media\/t\/([0-9]_[a-z0-9]{8})\/(\d+)$/);
      if (lecture) return page(lecture[1]);
      return res("", { status: 404 });
    });
  const links = (o.links ?? []).map((href) => ({ getAttribute: () => href }));
  return {
    ...o,
    calls,
    env: {
      fetch,
      location: o.location ?? { hostname: KAF, pathname: `/channel/channelid/${CAT}` },
      document: { querySelectorAll: () => links },
    },
  };
}

/**
 * chrome.scripting.executeScript over a list of frames { frameId, env }. The function really is
 * rebuilt from its source, so closures and imports would fail here as they do in the browser.
 */
export function makeScripting(frames, t = tracker()) {
  const returned = [];
  const calls = [];
  return {
    returned,
    calls,
    tracker: t,
    executeScript: async ({ target, func, args = [] }) => {
      calls.push({ target, name: func.name });
      return t.run("executeScript", async () => {
        const chosen = target.allFrames ? frames : frames.filter((f) => target.frameIds?.includes(f.frameId));
        const out = [];
        for (const f of chosen) {
          if (f.throws) continue;
          // A frame that never answers.
          if (f.hang && (f.hang === true || f.hang(func.name))) await new Promise(() => {});
          // A frame that navigates away while the call is running: the call is rejected.
          if (f.navigates > 0 && target.frameIds && func.name === (f.navigatesOn ?? func.name)) {
            f.navigates--;
            throw new Error("Frame with ID was removed.");
          }
          const result = await rebuild(func, f.env)(...JSON.parse(JSON.stringify(args)));
          out.push({ frameId: f.frameId, documentId: `doc-${f.frameId}`, result: JSON.parse(JSON.stringify(result ?? null)) });
        }
        returned.push(out);
        return out;
      });
    },
  };
}

/** Canvas and the backend, behind one fetch. */
export function makeServer(opts = {}) {
  const o = { tracker: tracker(), stored: null, ...opts };
  o.storedAfter ??= (p) => {
    const map = new Map((o.stored || []).map((l) => [l.entry_id, l.has_captions]));
    for (const l of p.lectures) map.set(l.entry_id, true);
    return [...map].map(([entry_id, has_captions]) => ({ entry_id, has_captions }));
  };
  const requests = [];
  const statuses = [];
  const canvasList = (items) => res(`while(1);${JSON.stringify(items)}`);
  const fetch = (url, init = {}) =>
    o.tracker.run(String(url), async () => {
      const u = new URL(url);
      requests.push({ url: String(url), method: init.method || "GET", body: init.body, init });
      if (u.origin === "http://localhost:8000") {
        if (u.pathname === "/openapi.json") {
          if (o.backendDown) throw new TypeError("Failed to fetch");
          return res({ info: { title: o.backendTitle ?? "AI Lecture Search" } });
        }
        if (u.pathname === `/courses/canvas-${COURSE}/lectures`) {
          let reply;
          if (o.posted && o.lecturesFailAfterPost) reply = res("", { status: 500 });
          else if (o.stored === null && !o.posted) reply = res({ detail: "not found" }, { status: 404 });
          else reply = res(o.posted ? o.storedAfter(o.posted) : o.stored);
          statuses.push(reply.status);
          return reply;
        }
        if (u.pathname === "/sync" && init.method === "POST") {
          if (o.syncStatus) return res({ detail: "no" }, { status: o.syncStatus });
          o.posted = JSON.parse(init.body);
          return res({ status: "accepted", lectures_skipped_other_course: o.skippedOther ?? 0 });
        }
        if (u.pathname === "/health") return res({ status: "ok" });
      }
      if (u.origin === "https://canvas.ucsd.edu") {
        if (u.pathname === `/api/v1/courses/${COURSE}`) {
          if (o.courseStatus) return res("", { status: o.courseStatus });
          return res(
            `while(1);${JSON.stringify({ id: COURSE, course_code: "MATH 20A", name: "Calculus", term: { name: "FA26" }, access_token: "SECRETCANVAS" })}`,
          );
        }
        if (u.pathname === `/api/v1/courses/${COURSE}/files`) {
          if (o.filesStatus) return res("", { status: o.filesStatus });
          if (o.filePages) {
            const p = Number(u.searchParams.get("page") || 1);
            const headers = o.filePages[p]?.next ? { Link: `<${o.filePages[p].next}>; rel="next"` } : {};
            return res(`while(1);${JSON.stringify(o.filePages[p].items)}`, { headers });
          }
          return canvasList([{ id: 7, display_name: "Notes.pdf", "content-type": "application/pdf", url: "https://x/?verifier=SECRETVERIFIER" }]);
        }
        if (u.pathname === `/api/v1/courses/${COURSE}/tabs`) {
          return canvasList(
            o.tabs ?? [
              { id: "home", label: "Home", full_url: "https://canvas.ucsd.edu/courses/12345" },
              { id: "context_external_tool_77", label: " Media Gallery ", full_url: "https://canvas.ucsd.edu/courses/12345/external_tools/77" },
            ],
          );
        }
      }
      return res("", { status: 404 });
    });
  return { fetch, requests, statuses, tracker: o.tracker, opts: o };
}

export function makeTabs() {
  const state = { created: [], removed: [], open: new Set() };
  return {
    state,
    create: async (opts) => {
      if (state.createFails) throw new Error("no");
      state.created.push(opts);
      state.open.add(1000 + state.created.length);
      return { id: 1000 + state.created.length };
    },
    remove: async (id) => {
      state.removed.push(id);
      state.open.delete(id);
    },
  };
}

export function fakeClock() {
  let t = 0;
  return { now: () => t, sleep: async (ms) => void (t += ms) };
}

export function baseDeps({ server, scripting, tabs = makeTabs(), clock = fakeClock(), tabUrl = `https://canvas.ucsd.edu/courses/${COURSE}` }) {
  return {
    tabUrl,
    fetch: server.fetch,
    tabs,
    scripting,
    backendUrl: "http://localhost:8000",
    now: () => Date.parse("2026-11-03T12:00:00Z") + clock.now(),
    sleep: clock.sleep,
    report: () => {},
  };
}

// Roadmap step 1: sync prototype. Paste into DevTools on a canvaskaf.ucsd.edu lecture page whose
// URL is /media/t/<entryId>/<categoryId> (open the lecture from the course's Media Gallery).
//
// Downloads sync_<courseId>.json (contracts/sync_payload.schema.json); save it into data/.
// Canvas is CORS-blocked from this origin, so the snippet makes no Canvas calls: it asks for the
// course details and always sends files: [].
//
// SECURITY: the page KS and each lecture's own KS live only in this closure and only ever go in
// POST bodies to Kaltura. They are never logged, stored on window or put in the payload, and the
// download is refused if any KS seen during the run appears in the output. Do not log raw API
// responses or URLs here.
(async () => {
  const CHANNEL_PATH = "/channel/channelid/";
  const MAX_GALLERY_PAGES = 20;
  const MAX_KS_CANDIDATES = 6;
  const KALTURA_API = "https://cdnapisec.kaltura.com/api_v3/service/";
  const SRT_FORMAT = "1"; // KalturaCaptionType.SRT
  const ASSET_READY = "2"; // KalturaCaptionAssetStatus.READY

  // ---- Copied from extension/src/payload.js: keep identical (a paste cannot import a module).
  // scripts/console_sync.test.mjs fails if the two mappings drift apart.
  const MEDIA_BASE = "https://canvaskaf.ucsd.edu/media/t/";

  function toIsoDate(unixSeconds) {
    if (typeof unixSeconds !== "number" || !Number.isFinite(unixSeconds)) return undefined;
    return new Date(unixSeconds * 1000).toISOString().slice(0, 10);
  }

  function buildSyncPayload({ course, entries, files, categoryId, now = new Date() }) {
    const payload = {
      schema_version: "1",
      synced_at: now.toISOString(),
      course: {
        canvas_course_id: course.id,
        code: course.course_code,
        name: course.name,
      },
      lectures: entries
        .filter((e) => typeof e.srt === "string" && e.srt.length > 0)
        .map((e) => {
          const lecture = {
            source: "kaltura",
            entry_id: e.id,
            title: e.name,
            duration_sec: Number(e.duration) || 0,
            media_url: MEDIA_BASE + e.id + (categoryId ? `/${categoryId}` : ""),
            captions_srt: e.srt,
          };
          const recorded = toIsoDate(e.createdAt);
          if (recorded) lecture.recorded_at = recorded;
          return lecture;
        }),
      files: files.map((f) => {
        const file = { canvas_file_id: f.id, name: f.display_name };
        if (f["content-type"]) file.content_type = f["content-type"];
        return file;
      }),
    };
    if (course.term?.name) payload.course.term = course.term.name;
    return payload;
  }
  // ---- End of copy.

  // JSON.parse errors quote the response body, so never let one reach a log.
  function parseJson(body) {
    try {
      return JSON.parse(body);
    } catch {
      throw new Error("non-JSON response");
    }
  }

  function readKs() {
    const player =
      typeof KalturaPlayer === "undefined" ? undefined : Object.values(KalturaPlayer.getPlayers())[0];
    const ks = player?.config?.provider?.ks;
    if (typeof ks !== "string" || !ks) {
      throw new Error("No Kaltura session found. Open a lecture and wait for the player to load.");
    }
    return ks;
  }

  function readCategoryId() {
    const match = location.pathname.match(/^\/media\/t\/[0-9]_[a-z0-9]{8}\/(\d+)(?:\/|$)/);
    if (!match) {
      throw new Error("Open the lecture from the Media Gallery: the URL must be /media/t/<entryId>/<categoryId>.");
    }
    return match[1];
  }

  // Only links into this gallery's category count, so entries from elsewhere cannot slip in. The
  // optional backslashes also match links inside JSON-escaped responses.
  function entryIdsIn(text, categoryId) {
    const links = text.matchAll(/media\\?\/t\\?\/([0-9]_[a-z0-9]{8})\\?\/(\d+)/g);
    return [...links].filter((m) => m[2] === categoryId).map((m) => m[1]);
  }

  // Walks the gallery pages until one adds no new IDs. A failure keeps what was found so far.
  async function readGallery(categoryId) {
    const ids = new Set();
    let problem;
    let page = 1;
    try {
      for (; page <= MAX_GALLERY_PAGES; page++) {
        const r = await fetch(`${CHANNEL_PATH}${categoryId}?format=ajax&page=${page}`, {
          credentials: "same-origin",
          headers: { "X-Requested-With": "XMLHttpRequest" },
        });
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        // An expired session lands on a login page with a 200, which would look like the last page.
        if (r.redirected) throw new Error("redirected, session may have expired");
        const before = ids.size;
        for (const id of entryIdsIn(await r.text(), categoryId)) ids.add(id);
        if (ids.size === before) break;
      }
      if (page > MAX_GALLERY_PAGES) problem = `stopped at the ${MAX_GALLERY_PAGES}-page cap`;
    } catch (e) {
      problem = `gallery page ${page}: ${e.message}`;
    }
    return { entryIds: [...ids], problem };
  }

  function askForCourse() {
    const id = prompt("Canvas course ID, the number in canvas.ucsd.edu/courses/<id>:")?.trim();
    if (!id) throw new Error("The course ID is required.");
    if (!/^[1-9]\d*$/.test(id) || !Number.isSafeInteger(Number(id))) {
      throw new Error("The course ID must be a positive integer.");
    }
    const code = prompt("Course code, e.g. MATH 20A:")?.trim();
    const name = code && prompt("Course name, e.g. Calculus for Science and Engineering:")?.trim();
    if (!name) throw new Error("Course code and name are required.");
    return { id: Number(id), course_code: code, name };
  }

  async function kalturaCall(ks, path, params, parse) {
    try {
      const r = await fetch(KALTURA_API + path, {
        method: "POST",
        credentials: "omit",
        body: new URLSearchParams({ ks, ...params }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return parse(await r.text());
    } catch (e) {
      throw new Error(`${path}: ${e.message}`); // say which call failed
    }
  }

  // The only part of an error body that may be logged: a short code of plain capitals.
  function kalturaErrorCode(body) {
    return body.match(/"code"\s*:\s*"([A-Z0-9_]{1,64})"/)?.[1] || body.match(/<code>([A-Z0-9_]{1,64})<\/code>/)?.[1];
  }

  function kalturaJson(ks, path, params) {
    return kalturaCall(ks, path, { format: "1", ...params }, (body) => {
      const data = parseJson(body);
      if (data?.objectType === "KalturaAPIException") throw new Error(`Kaltura error ${kalturaErrorCode(body) || "unknown"}`);
      return data;
    });
  }

  function pickCaption(assets) {
    const srt = assets.filter(
      (a) => String(a.format) === SRT_FORMAT && (a.status === undefined || String(a.status) === ASSET_READY),
    );
    return srt.find((a) => Number(a.isDefault) === 1) || srt.find((a) => a.language === "English") || srt[0];
  }

  // Each lecture page embeds its own KS among other session strings. The page KS can list another
  // lecture's captions but serves them empty. Candidates go into seenKs for the final guard.
  async function readKsCandidates(id, categoryId, pageKs, seenKs) {
    let html;
    try {
      const r = await fetch(`/media/t/${id}/${categoryId}`, { credentials: "include" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      html = await r.text();
    } catch (e) {
      throw new Error(`lecture page: ${e.message}`);
    }
    const prefix = pageKs.slice(0, 4).replace(/[.*+?^${}()|[\]\\/]/g, "\\$&");
    const found = [...new Set(html.match(new RegExp(`${prefix}[A-Za-z0-9+/=_-]{40,}`, "g")) || [])];
    for (const ks of found) {
      seenKs.add(ks);
      // In a URL path the match runs on past the KS ("<KS>/uiconf_id/1"), so guard the first part too.
      const firstPart = ks.split("/")[0];
      if (firstPart.length >= 40) seenKs.add(firstPart);
    }
    const sameLength = found.filter((ks) => ks.length === pageKs.length);
    const others = found.filter((ks) => ks.length !== pageKs.length);
    return [...sameLength, ...others].slice(0, MAX_KS_CANDIDATES);
  }

  async function readCaptions(ks, id) {
    const list = await kalturaJson(ks, "caption_captionasset/action/list", { "filter[entryIdEqual]": id });
    const asset = pickCaption(list.objects || []);
    if (!asset) throw new Error("no SRT captions");
    return kalturaCall(ks, "caption_captionasset/action/serve", { captionAssetId: asset.id }, (body) => {
      // Kaltura reports serve errors with a 200, so check that this really is SRT.
      if (!body.includes("-->")) throw new Error(body.trim() ? kalturaErrorCode(body) || "not SRT" : "empty");
      return body;
    });
  }

  async function readEntry(pageKs, id, categoryId, seenKs) {
    const meta = await kalturaJson(pageKs, "baseentry/action/get", { entryId: id });
    if (!meta.name) throw new Error("entry has no title");
    const candidates = await readKsCandidates(id, categoryId, pageKs, seenKs);
    if (candidates.length === 0) throw new Error("no session found on its lecture page");
    const failures = [];
    for (const ks of candidates) {
      try {
        const srt = await readCaptions(ks, id);
        return { id, name: meta.name, duration: meta.duration, createdAt: meta.createdAt, srt };
      } catch (e) {
        failures.push(e.message);
      }
    }
    throw new Error(`captions not readable with ${candidates.length} session(s): ${failures.join("; ")}`);
  }

  function download(filename, text) {
    const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  try {
    if (location.hostname !== "canvaskaf.ucsd.edu") {
      throw new Error("Run this on a canvaskaf.ucsd.edu lecture page.");
    }
    const pageKs = readKs();
    const seenKs = new Set([pageKs]);
    const categoryId = readCategoryId();

    const { entryIds, problem } = await readGallery(categoryId);
    if (entryIds.length === 0) {
      throw new Error(`No lectures found in category ${categoryId}${problem ? ` (${problem})` : ""}.`);
    }
    console.log(`Category ${categoryId}: ${entryIds.length} entries.`);
    if (problem) console.warn(`partial: ${entryIds.length} entries (${problem})`);

    const course = askForCourse();

    const entries = [];
    const skipped = [];
    for (const [i, id] of entryIds.entries()) {
      try {
        entries.push(await readEntry(pageKs, id, categoryId, seenKs));
        console.log(`[${i + 1}/${entryIds.length}] ${id} ok`);
      } catch (e) {
        skipped.push(`${id}: ${e.message}`);
        console.warn(`[${i + 1}/${entryIds.length}] ${id} skipped: ${e.message}`);
      }
    }
    if (entries.length === 0) throw new Error(`No captions could be read:\n${skipped.join("\n")}`);

    const payload = buildSyncPayload({ course, entries, files: [], categoryId });
    if (payload.lectures.length > 200) throw new Error(`${payload.lectures.length} lectures (contract limit 200).`);
    const text = JSON.stringify(payload, null, 2);
    for (const ks of seenKs) {
      if (text.includes(ks) || text.includes(encodeURIComponent(ks))) {
        throw new Error("Refusing to download: a Kaltura session is in the output.");
      }
    }

    download(`sync_${course.id}.json`, text);

    // Sections can repeat a lecture under separate entries; all are kept for now.
    const titles = new Set(payload.lectures.map((l) => l.title));
    console.log(`Downloaded sync_${course.id}.json: ${course.course_code} / ${course.name}`);
    console.log(`Lectures: ${payload.lectures.length} of ${entryIds.length}. Unique titles: ${titles.size}. Files: 0.`);
    if (problem) console.warn(`partial: ${entryIds.length} entries (${problem})`);
    if (skipped.length) console.warn(`Skipped ${skipped.length}:\n${skipped.join("\n")}`);
  } catch (e) {
    console.error(`Sync failed, nothing downloaded: ${e.message}`);
  }
})();

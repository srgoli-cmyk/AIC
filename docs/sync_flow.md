# How sync works

The extension does the sync from its popup. `scripts/console_sync.js` is the older manual route: it
runs in the browser console on a lecture page and uses the same requests. The numbered steps are what
was verified by hand on MATH 20A on Oct 30 and corrected by live runs on Nov 2-3; those runs used the
console snippet. The extension's flow passes tests with mocks and has **not been run live yet**.

## The extension's flow

1. **Find the course.** The popup reads the course ID from the active `canvas.ucsd.edu/courses/<id>`
   tab, using the `tabs` permission. Sync runs in the background worker (`src/sync.js`).
2. **Check the backend.** `/openapi.json` must report the title "AI Lecture Search" before any course
   data leaves the browser, because another app can hold the backend's port and answer `/health`.
3. **Read Canvas.** The worker reads the course and its files over the REST API with the student's
   cookies (step 1 below).
4. **LTI launch.** `GET /api/v1/courses/:id/tabs` finds the tab labelled "Media Gallery"
   (case-insensitive, no hardcoded tool ID). Its `full_url` opens in a background tab, which sets the
   `canvaskaf.ucsd.edu` session. The tab is always closed afterwards.
5. **Find the gallery.** Scripts injected into the canvaskaf frame (`chrome.scripting.executeScript`,
   all frames) poll up to 30 s for `/channel/<x>/<digits>` in the location, or the most frequent
   category in `/media/t/<entryId>/<digits>` links. On timeout, the popup asks the student to open the
   Media Gallery tab once.
6. **List lectures.** The frame fetches the paginated gallery and collects the entry IDs (step 2 below).
7. **Skip what is stored.** `GET /courses/{course_id}/lectures` says which entries already have
   captions, so a re-sync only fetches the others. A course the backend hasn't seen returns `404`,
   which means every entry is new.
8. **Per-entry KS and captions.** One injected call per entry, one at a time: fetch the lecture page,
   try its KS candidates, read the SRT and title (step 4 below). The KS is used only inside that call.
9. **`POST /sync`.** `src/payload.js` builds the body from an allowlist. It is checked for KS-shaped
   strings before it is sent, and the popup shows counts.

## What was verified by hand

Unless marked untested, each step below has been seen working.

1. **Canvas REST API** works from a page on canvas.ucsd.edu with the student's session cookies:
   `/api/v1/courses/:id`, `/api/v1/courses/:id/files`. It is CORS-blocked from a canvaskaf.ucsd.edu
   page, which is why `scripts/console_sync.js` asks for the course details and sends `files: []`.
   The extension calls it from the background worker with the student's cookies
   (`host_permissions`, so no CORS; not yet proven live). Any failure reading files gives `files: []`
   and a note; a failing course call stops the sync with a "log in to Canvas" message.

2. **Entry IDs** come from the channel's gallery pages, not the Kaltura list API. The category ID
   is in the lecture URL, `/media/t/<entryId>/<categoryId>`. Fetch
   `GET /channel/channelid/<categoryId>?format=ajax&page=N` with header
   `X-Requested-With: XMLHttpRequest` for N = 1, 2, ... until a page adds no new IDs (cap 20), and
   take the union. Match links with `media\\?/t\\?/([0-9]_[a-z0-9]{8})\\?/(\d+)`, which also fits
   JSON-escaped responses, and keep only those whose category is this gallery's. MATH 20A: 15 on
   page 1 + 9 on page 2 = 24, the "24 Media" count on the gallery tab. This is an internal KAF
   endpoint, not a public API, and may change. The first path segment is cosmetic (`channelid`
   works) and is NOT the Canvas course ID. Do not use `/hosted/index/course-gallery`: it returns 200
   with no HTTP redirect (the redirect is client-side JS) and no links.

3. **The page KS** is on any entry page, MAIN world only:
   `Object.values(KalturaPlayer.getPlayers())[0].config.provider.ks`. It works for
   `baseentry/action/get` and `caption_captionasset/action/list` on any entry of the course, but
   `caption_captionasset/action/serve` for another entry returns HTTP 200 with an empty body. It
   cannot enumerate a category either: `media/action/list` returns `totalCount` 0 and
   `categoryentry/action/list` returns `SERVICE_FORBIDDEN`.

4. **Each entry has its own KS**, embedded in its own lecture page (a full MATH 20A run read
   captions for 24 of 24 entries this way). POST to
   `https://cdnapisec.kaltura.com/api_v3/service/` with it: `caption_captionasset/action/list`
   (`filter[entryIdEqual]`, assets with format 1 = SRT and status 2 = ready), then
   `caption_captionasset/action/serve` (`captionAssetId`), which returns SRT.
   - Fetch `/media/t/<entryId>/<categoryId>` with credentials. The HTML holds several strings that
     look like a KS (same first 4 characters as the page KS, then `[A-Za-z0-9+/=_-]{40,}`). Only
     some work: one sample gave `INVALID_ACTIONS_LIMIT`, `INVALID_KS` and one valid SRT.
   - Try candidates of the same length as the page KS first, then the rest, at most 6. Use the first
     whose serve returns text containing `-->`, for that entry only.
   - The extension does this in the canvaskaf frame of a background tab, one injected call per entry
     (not yet proven live). With no player open there is no page KS, so the prefix is the constant
     `djJ8` (Kaltura v2 sessions, base64 of "v2|"), and candidates of the same length as the last one
     that worked go first. The title comes from `baseentry/action/get` with the working KS, falling
     back to the page `<title>` with duration 0. Both are assumptions to confirm on a live sync.

5. **Every KS stays in the browser.** None is logged, stored or returned, and the output is checked
   against every KS seen during the run. Only the SRT text goes into the payload. On a non-SRT
   response, log only the Kaltura error code (or "empty"), never the body. The extension checks each
   entry's result against the sessions seen on its page, also refuses to POST a KS-shaped string, and
   keeps only counts, the course ID, timestamps, status codes and fixed notes in storage.

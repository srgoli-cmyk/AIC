# How sync works

Verified manually on MATH 20A in the browser console on Oct 30, corrected by live runs on Nov 2-3.
Unless marked untested, each step below has been seen working.

1. **Canvas REST API** works from a page on canvas.ucsd.edu with the student's session cookies:
   `/api/v1/courses/:id`, `/api/v1/courses/:id/files`. It is CORS-blocked from a canvaskaf.ucsd.edu
   page, which is why `scripts/console_sync.js` asks for the course details and sends `files: []`.
   In the extension, call it from the background worker (`host_permissions`, so no CORS). Untested,
   verify in step 6.

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
   - In the extension, fetch each lecture page from the background or content script. No hidden tabs
     are needed. Untested, verify in step 6, including where the 4-character prefix comes from when
     no player is open.

5. **Every KS stays in the browser.** None is logged, stored or returned, and the output is checked
   against every KS seen during the run. Only the SRT text goes into the payload. On a non-SRT
   response, log only the Kaltura error code (or "empty"), never the body.

# API contract (v1)

This file is the single source of truth between the extension and the backend.
Only the maintainer changes it, and only after the change is approved.
Any change here must update `sync_payload.schema.json`, the fixtures and both sides' tests in the same PR.

Base URL in development: `http://localhost:8000`

## GET /openapi.json
FastAPI's schema. `info.title` is `AI Lecture Search`; the extension checks this before sending course data, because another app can hold the port and answer `/health`.

## GET /health
Response `200`: `{"status": "ok"}`

## Auth
All endpoints except `/health`, `/openapi.json`, and `/auth/*` require a verified `@ucsd.edu` student.
Clients authenticate in one of two ways:
- **Web app**: `HttpOnly` session cookie `aic_session` (`SameSite=Lax`; `Secure` in HTTPS deployments).
- **Chrome extension**: `Authorization: Bearer <token>` header.

Student data (courses, lectures, documents, chunks) is private per student.

### Origins and CORS
- CORS allows exactly one cross-origin caller, the extension at `chrome-extension://<extension id>` (env `EXTENSION_ID`). Responses never carry `Access-Control-Allow-Credentials`. Allowed request headers are `Content-Type` and `Authorization`.
- Every `POST`, `PUT` and `DELETE` is checked before the body is read. It is accepted if it has no `Origin` header, if `Origin` equals the backend's own origin (request scheme and `Host`), or if it has a valid `Authorization: Bearer <token>`. Otherwise the response is `403 {"detail": "origin not allowed"}` and nothing runs.
- `POST /auth/extension/exchange` is not checked: it takes no cookie or token, and the one-time code and nonce protect it.
- Browser pages use the `aic_session` cookie and must be served by the backend (`/app/`).

### GET /auth/google/login
Query parameters:
- `client` is `"web"` (default) or `"extension"`.
- `nonce` is required when `client=extension` and refused with `client=web`. It is the challenge for a secret the extension keeps: the base64url encoding, without padding, of the SHA-256 of a random nonce, matching `^[A-Za-z0-9_-]{43}$`. The raw nonce never appears in a URL. A missing or malformed `nonce` with `client=extension`, or any `nonce` with `client=web`, is refused with `422` before any redirect.

Redirects (`302`) to Google's OAuth 2.0 authorization URL using PKCE, an anti-CSRF `state` cookie, and `hd=ucsd.edu`. For `client=extension` the challenge is kept in the signed state cookie and is not sent to Google.
`503 {"detail": "auth service not configured"}` when `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` or `SESSION_SECRET` is unset, or `SESSION_SECRET` is shorter than 32 characters.

### GET /auth/google/callback
Handles Google's OAuth redirect (`code`, `state`). Exchanges `code` with Google's token endpoint over TLS (OpenID Connect Core 3.1.3.7: the ID token comes straight from Google, so its claims are checked instead of its signature). The callback rejects the token with `auth_error=login_failed` unless `aud` is this app's client id (if `aud` is a list it must contain the client id and `azp` must equal it), `iss` is `https://accounts.google.com` or `accounts.google.com`, `exp` is in the future and `iat` is not in the future (both with 60 s of clock skew allowed), and `email` is plain ASCII with exactly one `@`. A valid token is rejected with `auth_error=ucsd_only` unless `email_verified == true`, `hd == "ucsd.edu"` and the email ends in `@ucsd.edu` (the domain is `ALLOWED_EMAIL_DOMAIN`). Rejected logins are logged with a fixed reason only, never the email or domain.
- On invalid `state` or Google error: redirects `302` to `/app/?auth_error=login_failed` (or `/auth/extension/done?error=login_failed` for `client=extension`).
- On non-`@ucsd.edu` or unverified account: redirects `302` to `/app/?auth_error=ucsd_only` (or `/auth/extension/done?error=ucsd_only` for `client=extension`).
- `auth_error` (web) and `error` (extension) take only these two values, `login_failed` and `ucsd_only`. Clients map exactly these, and show a generic sign-in failure for anything else.
- On success (`client=web`): upserts the student, creates a web session, sets `aic_session`, and redirects `302` to `/app/`.
- On success (`client=extension`): upserts the student, creates a single-use exchange code (valid for 60 seconds) bound to the challenge from the login request, and redirects `302` to `/auth/extension/done?code=<one-time-code>`. A signed state without a valid challenge redirects to `/auth/extension/done?error=login_failed`.

### GET /auth/extension/done
Minimal landing page for the extension login tab (`?code=...` or `?error=...`). The Chrome extension watches this URL via `chrome.tabs.onUpdated`, exchanges `code` via `POST /auth/extension/exchange`, and closes the tab.

### POST /auth/extension/exchange
Body:
```json
{"code": "<one-time-code>", "nonce": "<raw-nonce>"}
```
`nonce` is the raw secret whose challenge (base64url, no padding, of its SHA-256) the extension sent as `nonce` to `GET /auth/google/login`. Unknown fields and a missing `code` or `nonce` are rejected with `422`. The exchange succeeds only if the code is valid, unexpired and unused, and `base64url(sha256(nonce))` equals the challenge stored with the code (compared in constant time). The code is consumed on every attempt, right or wrong, so a wrong nonce cannot be retried and the nonce expires with the code. A code made without a challenge is never accepted. On success it mints a revocable extension bearer token.

The extension acts only on the `/auth/extension/done` tab it opened for its own pending login, and forgets the nonce after one exchange attempt.

Response `200`:
```json
{"token": "<bearer-token>", "user": {"id": "usr_...", "email": "student@ucsd.edu", "name": "Ada Lovelace"}}
```
`401 {"detail": "invalid or expired code"}` for every failure: the code is unknown, already used or expired, or the nonce is wrong or malformed. The response never says which.

### GET /auth/me
Requires a valid `aic_session` cookie or `Authorization: Bearer <token>`.
Response `200`:
```json
{"id": "usr_...", "email": "student@ucsd.edu", "name": "Ada Lovelace"}
```
`401 {"detail": "not authenticated"}` when missing, invalid, or expired.

### POST /auth/logout
Revokes the current session (from `aic_session` cookie or `Authorization: Bearer <token>`) and clears the `aic_session` cookie.
Response `204`.

## POST /sync
Body: `SyncPayload`, see `sync_payload.schema.json`. Example: `fixtures/sync_sample.json`.

Rules:
- Unknown fields are rejected with `422`. This is deliberate: it guarantees that tokens or cookies can never be stored by accident.
- Re-syncing the same course is idempotent: lectures are upserted by `entry_id`.
- `files: []` on a re-sync leaves stored files alone and never deletes them. Only a non-empty `files` list upserts, by `canvas_file_id`.
- `media_url` must be `https://canvaskaf.ucsd.edu/media/t/<entryId>/<categoryId>` or a podcast lecture page `https://podcast.ucsd.edu/watch/<term>/<section>/<n>`, with no query or fragment; for example `https://canvaskaf.ucsd.edu/media/t/1_fake0001/111111` or `https://podcast.ucsd.edu/watch/fa26/dsc010_002-000-le/3`. A canvaskaf link without the category answers "Access denied". Anything else is rejected with `422`, and nothing from that sync is saved.
- A lecture already stored under a different course is left untouched and counted in `lectures_skipped_other_course`.

Response `200`:
```json
{"course_id": "canvas-999999", "lectures_received": 2, "files_received": 1, "lectures_skipped_other_course": 0, "status": "accepted"}
```
`status` is always `"accepted"`: ingestion runs in the background after the response. The course's `indexed` in `GET /courses` becomes `true` when all its lectures are chunked. A failure ingesting a lecture does not fail the sync.

## GET /courses
Response `200`:
```json
[{"course_id": "canvas-999999", "code": "MATH 20A", "name": "Calculus for Science and Engineering", "lectures": 22, "indexed": true}]
```

## GET /courses/{course_id}/lectures
Lets a client skip lectures that are already stored. Response `200`, ordered by `entry_id`:
```json
[{"entry_id": "1_fake0001", "has_captions": true}]
```
`has_captions` is `true` when the lecture has stored captions. Lectures with no captions are not stored, so an entry missing from this list has none yet. A synced course with no lectures returns `[]`. `404`: the course is not synced.

## GET /courses/{course_id}
The course page: details from Canvas plus every stored lecture, without captions.

Response `200`:
```json
{"course_id": "canvas-999999", "canvas_course_id": 999999, "code": "MATH 20A", "name": "Calculus for Science and Engineering",
 "term": "FA26", "term_start": "2026-09-24", "term_end": null, "instructors": ["Ada Lovelace"], "sections": ["A00", "B00"],
 "synced_at": "2026-11-03T12:00:00Z", "indexed": true,
 "lectures": [{"entry_id": "1_fake0001", "title": "Lecture 8 - Product Rule [A00]", "lecture_no": 8, "section": null,
   "recorded_at": "2026-10-14", "recorded_start": "2026-10-14T17:00:12Z", "duration_sec": 2950.0,
   "media_url": "https://canvaskaf.ucsd.edu/media/t/1_fake0001/111111", "indexed": true}]}
```
- Lectures are ordered by `lecture_no`, then `recorded_start`, then `recorded_at`. Missing values sort last.
- `section` is read from the first bracket in the title, digits only (e.g. `[002-000-LE]` gives `"002"`). `sections` are Canvas's section names; the two are not linked.
- `recorded_start` is when Kaltura created the recording (UTC), close to when class started.
- Lecture `indexed` is true when its chunks match its current captions.

`404 {"detail": "course not synced"}` when the course is unknown.

## POST /ask
Body:
```json
{"course_id": "canvas-999999", "question": "How do we differentiate a product?", "top_k": 12}
```
`top_k` is optional, 1 to 20, default 12.

Response `200`: see `fixtures/ask_response_sample.json`.
- `sources[].start_sec` / `end_sec` are seconds from the start of the lecture.
- `sources[].timestamp` is `mm:ss` (or `h:mm:ss`) of `start_sec`.
- `sources[].url` is the lecture's `media_url` plus `?st=<seconds>` (`#<seconds>` for a podcast.ucsd.edu page), where seconds is `max(0, floor(start_sec) - 3)`, so playback starts a few seconds before the cited passage. For example `https://canvaskaf.ucsd.edu/media/t/1_fake0001/111111?st=1`.

- `answer` is Markdown with LaTeX (`$...$`, `$$...$$`) that explains the topic, using the course's captions as its main source. It may use general math knowledge to explain, but only what the captions say is cited, as `[Lecture N, m:ss]`. A lecture with no number is cited by its title. An `answer` can carry text from captions, so a client must not render links, images or HTML from it, other than citations it builds from `sources`.
- `sources` lists the passages the answer cites, most relevant first. Citations that match no passage are removed from `answer`, except that a bracket holding at least one valid citation is left whole. If no valid citation remains, `answer` is exactly `The lectures don't cover this.` and `sources` is empty.
- `404`: the course is not synced. A synced course with nothing indexed yet returns `200` with an answer saying so and empty `sources`, once a key is configured.

## POST /ask/stream
The same question as `POST /ask`, answered word by word. Same body, same rules, and the final answer is
exactly what `/ask` returns for the same model reply.

Anything that fails before the model is called is a plain HTTP error, exactly as for `/ask`: `404`
course not synced, `422` invalid body, and the `503`s below. Otherwise the response is `200` with
`Content-Type: text/event-stream` (Server-Sent Events). Each event is an `event:` line, one `data:` line
holding JSON, and a blank line:

```
event: delta
data: {"text": "To find critical points"}

event: delta
data: {"text": ", differentiate first [Lecture 17, 6:50]."}

event: final
data: {"answer": "To find critical points, ...", "sources": [...]}
```

- `delta`: the next piece of the model's reply, as written. It is not checked yet, so it can hold a
  citation that `final` removes. A client shows deltas as plain text only (no Markdown, math, links
  or HTML) and replaces them with `final`.
- `final`: once, last. The body `/ask` would return: citations checked, invalid ones removed, `The
  lectures don't cover this.` when none is valid, and `sources`. A course with nothing indexed sends
  only `final`.
- `error`: once, last, instead of `final`, when the model call fails or times out after the stream
  started: `{"status": 502, "detail": "answer service unavailable"}`, with the same message as `/ask`.
- Lines starting with `:` are comments (keep-alives); a client ignores them.
- Closing the connection stops the model call.

## Course documents (slides)
PDF and PowerPoint files of a course. The extension downloads each one from Canvas and sends it here; the backend reads its text page by page, and `/ask` then searches the pages with the lectures.

### GET /courses/{course_id}/documents
Response `200`, ordered by name:
```json
[{"doc_id": "canvas-file-7", "source": "canvas", "name": "Week 3", "url": "https://canvas.ucsd.edu/courses/999999/files/7",
  "pages": 24, "version": "184223:2026-10-01T00:00:00Z", "lecture_no": null, "indexed": true}]
```
`version` is the value the sender gave; a sender skips a file whose version is unchanged. `indexed` is true once every page is searchable. `404`: the course is not synced.

### PUT /courses/{course_id}/documents/canvas/{canvas_file_id}?name=<file name>&version=<version>
The body is the file itself (raw bytes, at most 40 MB). Only a real PDF or `.pptx` is read, judged by its first bytes, not by its name or `Content-Type`. Sending the same file id again replaces its pages.

Response `200`: `{"doc_id": "canvas-file-7", "pages": 24, "chunks": 26, "status": "stored"}`.
- `name` is shown without its extension (all of them: `Oct12.pdf.pdf` is `Oct12`), without a download copy number (`Week 3 (1).pdf` is `Week 3`) and without square brackets. Names stored before a rule changed are tidied when the backend starts. `url` is built by the backend from the two ids, never taken from the request.
- `404`: the course is not synced. `413`: over 40 MB. `422 {"detail": "file could not be read"}`: not a PDF or PowerPoint, or no text was found in it. A scanned PDF (at least 80% of its pages have no text) uploaded here is read by the OCR vision model, up to its first 40 pages, before the reply (course-website PDFs are not OCRed); without an API key, or if OCR finds nothing, it is refused like this. `422` also when the file id belongs to another course.

### DELETE /courses/{course_id}/documents/canvas/{canvas_file_id}
Removes one Canvas document and its pages. The extension calls it for an older copy of a deck when a newer one is posted (`Week 3.pdf` and `Week 3_1015.pdf`: the most recently modified is kept). Response `204`. `404`: the course has no such document (another course's document is never touched). `422`: invalid file id.

### The course's own website
Some courses post their slides on their own site (dsc10.com) rather than on Canvas. The backend finds that site with a web search, checks each result against what the synced course says (code, school, term, instructors, lecture dates), and reads the site's lecture material (PDF, PowerPoint, public Google Slides, notebook pages) into documents with `"source": "web"`.

`GET /courses/{course_id}/site` → `200` with the site, or `null` when none is chosen:
```json
{"url": "https://dsc10.com/", "title": "Home | DSC 10", "chosen_by": "search", "status": "syncing",
 "found": 31, "stored": 12, "unchanged": 0, "unreadable": 1, "failed": 0}
```
`chosen_by` is `"search"` (it matched without asking) or `"student"`. `status` is `"syncing"`, `"done"` or `"failed"`; the counts grow while it syncs.

`POST /courses/{course_id}/site/search` → `200 {"site": <site or null>, "candidates": [{"url", "title", "decision", "term_found", "reasons": ["..."]}]}`. Takes up to a minute. `decision` is `"accept"` (matched: course, school, this term, and off a ucsd.edu address also this term's lecture calendar and an instructor) or `"ask"` (could be it: another term, or not enough to be sure). An `accept` becomes the site and starts syncing unless the student already picked one. At most 5 candidates. `503`: no web search key configured.

`PUT /courses/{course_id}/site` body `{"url": "https://..."}` → `200` site. The page is fetched and checked again; `422 {"detail": "not this course's website: ..."}` if it does not match. Choosing another site removes the old site's documents.

`POST /courses/{course_id}/site/sync` → `200` site: read the chosen site again. `404` when none is chosen.

`DELETE /courses/{course_id}/site` → `204`: forget the site and remove its documents (Canvas files stay).

All of these return `404` for a course that is not synced. Only public `https` pages are fetched.

### Slides in /ask
A page of a document is cited as `[Slides: <name>, p. <page>]`. Its entry in `sources` has `"kind": "slides"`, `"page"`, `"timestamp": "p. <page>"`, `lecture_title` = the document's name, `entry_id` = its `doc_id`, `start_sec` = `end_sec` = the page number, and `url` = the file's page on Canvas. Lecture sources have `"kind": "lecture"` and `"page": null`. A question about one lecture (`entry_id` in the request) does not search slides.

## POST /study
A lesson, flashcards or practice problems written from one lecture's captions.

Body:
```json
{"entry_id": "1_fake0001", "kind": "teach"}
```
`kind` is `"teach"`, `"flashcards"` or `"quiz"`. The whole lecture is read (up to 60 chunks, an even sample of a longer one), together with the pages of the course's slides that match what the lecture says (chosen by the words used, preferring decks that name the lecture's section, never exams or solutions). Every `source` below has the same shape as in `/ask` (a lecture moment or a slide page), or is `null`.

Response `200` for `"teach"`:
```json
{"sections": [{"title": "The product rule", "body": "Keep one factor...", "source": {},
   "problems": [{"question": "Differentiate $x^2 \\sin x$.", "steps": ["..."], "answer": "$2x\\sin x + x^2\\cos x$", "from_lecture": true, "source": {}}]}],
 "check": ["..."]}
```
- `body`, `question`, `steps`, `answer` and `check` are Markdown with LaTeX, with the same rendering rule as `answer` in `/ask`.
- `problems` are the examples the lecture and its slides work, placed in the section they belong to. Each states every number it needs. A second model (`OPENROUTER_CHECK_MODEL`) works each one again; its steps and answer replace the first ones, and a problem it cannot solve from what it states is dropped. `from_lecture` is `true` only when the problem points at a real passage (`source` is then where it is worked). When the lecture works fewer than three, the model adds its own with `from_lecture: false` and `source: null`; a client must label those as not from the lecture.

Response `200` for `"flashcards"`: `{"cards": [{"front": "...", "back": "...", "source": {}}]}`, up to 20.

Response `200` for `"quiz"`: `{"questions": [{"question": "...", "choices": ["...", "...", "...", "..."], "answer": 2, "why": "...", "level": 1, "source": {}}]}`, up to 10.
- `answer` is the position of the correct choice, 0 to 3. The choices are already in mixed order.
- `level` is 1 (a rule), 2 (work a problem) or 3 (several steps). `why` is the worked solution.
- A second model call works each question out and drops any it sees more than one right answer for. If that check fails or leaves fewer than four, the unchecked questions are returned.

A reply from the model that cannot be read is asked for once more before the request fails with `502`.

`404 {"detail": "lecture not synced"}`: the lecture is unknown. `409 {"detail": "lecture not indexed"}`: it has no chunks yet. The `503` and `502` answer-service errors below also apply. `OPENROUTER_STUDY_MODEL` (optional) picks a different model for this endpoint only.

## Errors
All errors use FastAPI's default shape: `{"detail": ...}`.
- `401 {"detail": "not authenticated"}`: missing, invalid, or expired session cookie or bearer token.
- `503 {"detail": "auth service not configured"}`: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` or `SESSION_SECRET` is not set, or `SESSION_SECRET` is shorter than 32 characters.
- `503 {"detail": "database unavailable"}`: the database cannot be reached.
- `503 {"detail": "answer service not configured"}`: no API key is set for `/ask`.
- `503 {"detail": "search service unavailable"}`: the embedding model could not be loaded for `/ask`.
- `502 {"detail": "answer service unavailable"}`: the answer service failed or timed out.
- `422 {"detail": "a value was rejected by the database"}`: a contract-valid value cannot be stored. Nothing from that sync is saved.

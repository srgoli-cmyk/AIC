# API contract (v1)

This file is the single source of truth between the extension and the backend.
Only the maintainer changes it, and only after the change is approved.
Any change here must update `sync_payload.schema.json`, the fixtures and both sides' tests in the same PR.

Base URL in development: `http://localhost:8000`

## GET /openapi.json
FastAPI's schema. `info.title` is `AI Lecture Search`; the extension checks this before sending course data, because another app can hold the port and answer `/health`.

## GET /health
Response `200`: `{"status": "ok"}`

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
- `name` is shown without its extension and without square brackets. `url` is built by the backend from the two ids, never taken from the request.
- `404`: the course is not synced. `413`: over 40 MB. `422 {"detail": "file could not be read"}`: not a PDF or PowerPoint, or it has no text (scanned pages). `422` also when the file id belongs to another course.

### DELETE /courses/{course_id}/documents/canvas/{canvas_file_id}
Removes one Canvas document and its pages. The extension calls it for an older copy of a deck when a newer one is posted (`Week 3.pdf` and `Week 3_1015.pdf`: the most recently modified is kept). Response `204`. `404`: the course has no such document (another course's document is never touched). `422`: invalid file id.

### Slides in /ask
A page of a document is cited as `[Slides: <name>, p. <page>]`. Its entry in `sources` has `"kind": "slides"`, `"page"`, `"timestamp": "p. <page>"`, `lecture_title` = the document's name, `entry_id` = its `doc_id`, `start_sec` = `end_sec` = the page number, and `url` = the file's page on Canvas. Lecture sources have `"kind": "lecture"` and `"page": null`. A question about one lecture (`entry_id` in the request) does not search slides.

## POST /study
A lesson, flashcards or practice problems written from one lecture's captions.

Body:
```json
{"entry_id": "1_fake0001", "kind": "teach"}
```
`kind` is `"teach"`, `"flashcards"` or `"quiz"`. The whole lecture is read (up to 60 chunks, an even sample of a longer one). Every `source` below has the same shape as in `/ask`, or is `null`.

Response `200` for `"teach"`:
```json
{"sections": [{"title": "The product rule", "body": "Keep one factor...", "source": {},
   "problems": [{"question": "Differentiate $x^2 \\sin x$.", "steps": ["..."], "answer": "$2x\\sin x + x^2\\cos x$", "from_lecture": true, "source": {}}]}],
 "check": ["..."]}
```
- `body`, `question`, `steps`, `answer` and `check` are Markdown with LaTeX, with the same rendering rule as `answer` in `/ask`.
- `problems` are the examples the lecture works, placed in the section they belong to. `from_lecture` is `true` only when the problem points at a real passage (`source` is then where it is worked). When the lecture works fewer than three, the model adds its own with `from_lecture: false` and `source: null`; a client must label those as not from the lecture.

Response `200` for `"flashcards"`: `{"cards": [{"front": "...", "back": "...", "source": {}}]}`, up to 20.

Response `200` for `"quiz"`: `{"questions": [{"question": "...", "choices": ["...", "...", "...", "..."], "answer": 2, "why": "...", "level": 1, "source": {}}]}`, up to 10.
- `answer` is the position of the correct choice, 0 to 3. The choices are already in mixed order.
- `level` is 1 (a rule), 2 (work a problem) or 3 (several steps). `why` is the worked solution.
- A second model call works each question out and drops any it sees more than one right answer for. If that check fails or leaves fewer than four, the unchecked questions are returned.

A reply from the model that cannot be read is asked for once more before the request fails with `502`.

`404 {"detail": "lecture not synced"}`: the lecture is unknown. `409 {"detail": "lecture not indexed"}`: it has no chunks yet. The `503` and `502` answer-service errors below also apply. `OPENROUTER_STUDY_MODEL` (optional) picks a different model for this endpoint only.

## Errors
All errors use FastAPI's default shape: `{"detail": ...}`.
- `503 {"detail": "database unavailable"}`: the database cannot be reached.
- `503 {"detail": "answer service not configured"}`: no API key is set for `/ask`.
- `503 {"detail": "search service unavailable"}`: the embedding model could not be loaded for `/ask`.
- `502 {"detail": "answer service unavailable"}`: the answer service failed or timed out.
- `422 {"detail": "a value was rejected by the database"}`: a contract-valid value cannot be stored. Nothing from that sync is saved.

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

## POST /ask
Body:
```json
{"course_id": "canvas-999999", "question": "How do we differentiate a product?", "top_k": 12}
```
`top_k` is optional, 1 to 20, default 12.

Response `200`: see `fixtures/ask_response_sample.json`.
- `sources[].start_sec` / `end_sec` are seconds from the start of the lecture.
- `sources[].timestamp` is `mm:ss` (or `h:mm:ss`) of `start_sec`.
- `sources[].url` opens the lecture page; time-jump support is still to be verified.

- `answer` is Markdown with LaTeX (`$...$`, `$$...$$`) that explains the topic, using the course's captions as its main source. It may use general math knowledge to explain, but only what the captions say is cited, as `[Lecture N, m:ss]`. A lecture with no number is cited by its title. An `answer` can carry text from captions, so a client must not render links, images or HTML from it, other than citations it builds from `sources`.
- `sources` lists the passages the answer cites, most relevant first. Citations that match no passage are removed from `answer`, except that a bracket holding at least one valid citation is left whole. If no valid citation remains, `answer` is exactly `The lectures don't cover this.` and `sources` is empty.
- `404`: the course is not synced. A synced course with nothing indexed yet returns `200` with an answer saying so and empty `sources`, once a key is configured.

## Errors
All errors use FastAPI's default shape: `{"detail": ...}`.
- `503 {"detail": "database unavailable"}`: the database cannot be reached.
- `503 {"detail": "answer service not configured"}`: no API key is set for `/ask`.
- `503 {"detail": "search service unavailable"}`: the embedding model could not be loaded for `/ask`.
- `502 {"detail": "answer service unavailable"}`: the answer service failed or timed out.
- `422 {"detail": "a value was rejected by the database"}`: a contract-valid value cannot be stored. Nothing from that sync is saved.

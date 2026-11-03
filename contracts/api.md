# API contract (v1)

This file is the single source of truth between the extension and the backend.
Only the maintainer changes it, and only after the change is approved.
Any change here must update `sync_payload.schema.json`, the fixtures and both sides' tests in the same PR.

Base URL in development: `http://localhost:8000`

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

## POST /ask
Body:
```json
{"course_id": "canvas-999999", "question": "How do we differentiate a product?", "top_k": 8}
```
`top_k` is optional, 1 to 20, default 8.

Response `200`: see `fixtures/ask_response_sample.json`.
- `sources[].start_sec` / `end_sec` are seconds from the start of the lecture.
- `sources[].timestamp` is `mm:ss` (or `h:mm:ss`) of `start_sec`.
- `sources[].url` opens the lecture page; time-jump support is still to be verified (see roadmap).

- `answer` answers only from the course's captions and cites them as `[Lecture N, m:ss]`, or says the captions don't contain the answer. A lecture with no number is cited by its title.
- `sources` lists the passages the answer cites, most relevant first. It is empty when the answer cites nothing. Citations that match no passage are removed from `answer`.
- `404`: the course is not synced. A synced course with nothing indexed yet returns `200` with an answer saying so and empty `sources`, once a key is configured.

## Errors
All errors use FastAPI's default shape: `{"detail": ...}`.
- `503 {"detail": "database unavailable"}`: the database cannot be reached.
- `503 {"detail": "answer service not configured"}`: no API key is set for `/ask`.
- `503 {"detail": "search service unavailable"}`: the embedding model could not be loaded for `/ask`.
- `502 {"detail": "answer service unavailable"}`: the answer service failed or timed out.
- `422 {"detail": "a value was rejected by the database"}`: a contract-valid value cannot be stored. Nothing from that sync is saved.

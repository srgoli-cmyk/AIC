# MVP roadmap

Goal for the team demo: sync MATH 20A live, ask "how do we differentiate a product?", get an
answer that links to the right lecture and minute.

Steps are done in this order on purpose: the extension comes last, so a working search exists
even if the extension slips (sync can be demoed with the console snippet).

- [x] **0. Scaffold** (maintainer). Contract, gates, stubs.
  Done when: `make check` passes; extension loads and "Check backend" returns ok.

- [x] **1. Sync prototype as a console snippet** (maintainer, `scripts/console_sync.js`).
  Paste into DevTools on a canvaskaf.ucsd.edu lecture page; it collects entry IDs and SRTs for
  the course and downloads the payload (same mapping as `extension/src/payload.js`).
  Done when: running it on MATH 20A downloads a payload that passes `scripts/validate_payload.py`.

- [x] **2. Store syncs in Postgres** (backend). `/sync` upserts course, lectures, files;
  `/courses` reads from DB. Idempotent by `entry_id`.
  Done when: integration test syncs the fixture twice and gets one course with 2 lectures; a
  re-sync with `files: []` keeps stored files; a non-empty list upserts by `canvas_file_id`.

- [x] **3. Ingestion** (backend). SRT parser, chunking into ~45-60 s windows with
  start/end seconds, embeddings into `chunks`. Runs in the background after `/sync`; the course's
  `indexed` becomes true when all its lectures are chunked.
  Done when: unit tests for parser and chunker; fixture produces chunks with correct timestamps;
  syncing the fixture via `make dev` ends with `indexed: true`.

- [ ] **4. /ask** (backend). Embed question, top-k by cosine, prompt OpenRouter to answer
  only from the chunks with `[Lecture, m:ss]` citations, return `sources[]`.
  Done when: test with a mocked LLM checks the prompt and the response shape; smoke test returns 200.

- [ ] **5. Minimal UI** (maintainer or backend). Streamlit page: pick course, ask, show answer
  and clickable sources. Design does not matter.

- [ ] **6. Extension sync** (extension). Popup "Sync" follows `docs/sync_flow.md`
  and POSTs the payload.
  Done when: syncing MATH 20A from the popup makes it appear in `/courses` with all lectures.

Open questions
- Does `https://canvaskaf.ucsd.edu/media/t/<entryId>?st=<seconds>` (or similar) jump to a time?
- How to trigger the LTI launch for a course automatically instead of asking the user to open
  the Media Gallery tab first.

Later (after the demo): quizzes, lecture summaries, slide matching, YouTube and website cards.
Many-to-many course_lectures if a lecture appears in several courses.

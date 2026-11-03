# MVP roadmap

Goal for the team demo: sync MATH 20A live, ask "how do we differentiate a product?", get an
answer that links to the right lecture and minute.

Steps are done in this order on purpose: the extension comes last, so a working search exists
even if the extension slips (sync can be demoed with the console snippet).

- [x] **0. Scaffold** (captain). Contract, agents, gates, stubs.
  Done when: `make check` passes; extension loads and "Check backend" returns ok.

- [ ] **1. Sync prototype as a console snippet** (captain, `scripts/console_sync.js`).
  Paste into DevTools on a canvaskaf.ucsd.edu lecture page; it collects entry IDs and SRTs for
  the course and builds the payload (same mapping as `extension/src/payload.js`).
  Done when: running it on MATH 20A prints a payload that passes `scripts/validate_payload.py`.

- [ ] **2. Store syncs in Postgres** (backend-dev). `/sync` upserts course, lectures, files;
  `/courses` reads from DB. Idempotent by `entry_id`.
  Done when: integration test syncs the fixture twice and gets one course with 2 lectures.

- [ ] **3. Ingestion** (backend-dev). SRT parser, chunking into ~45-60 s windows with
  start/end seconds, embeddings into `chunks`. Runs after `/sync`; status becomes `indexed`.
  Done when: unit tests for parser and chunker; fixture produces chunks with correct timestamps.

- [ ] **4. /ask** (backend-dev). Embed question, top-k by cosine, prompt OpenRouter to answer
  only from the chunks with `[Lecture, m:ss]` citations, return `sources[]`.
  Done when: test with a mocked LLM checks the prompt and the response shape; smoke test returns 200.

- [ ] **5. Minimal UI** (captain or backend-dev). Streamlit page: pick course, ask, show answer
  and clickable sources. Design does not matter.

- [ ] **6. Extension sync** (extension-dev). Popup "Sync" does steps 2-4 from
  `extension/CLAUDE.md` and POSTs the payload.
  Done when: syncing MATH 20A from the popup makes it appear in `/courses` with all lectures.

Open questions
- Does `https://canvaskaf.ucsd.edu/media/t/<entryId>?st=<seconds>` (or similar) jump to a time?
- How to trigger the LTI launch for a course automatically instead of asking the user to open
  the Media Gallery tab first.

Later (after the demo): quizzes, lecture summaries, slide matching, YouTube and website cards.

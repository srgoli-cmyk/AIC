# AI Lecture Search

A Chrome extension copies your UCSD lecture captions and slides from Canvas into a database on
your own computer. A web page then answers questions about the course, citing the lecture minute
or slide page, and turns any lecture into a lesson, flashcards or practice
problems.

Project board: https://miro.com/app/board/uXjVETB4KbI=/

## How it fits together

```
Chrome extension  --(captions, slide files)-->  backend (FastAPI, port 8000)  <--  web page (/app/)
                                                       |
                                                Postgres + pgvector (Docker)
```

| Folder | What is in it |
|---|---|
| `extension/` | Chrome extension (MV3). `popup.html` is the UI; `src/sync.js` syncs a course, `src/slides.js` sends Canvas PDFs and PowerPoints |
| `backend/app/` | API. `main.py` routes, `db.py` database, `answer.py` questions, `study.py` Teach me / Flashcards / Practice, `slides.py` reads slide files, `slidematch.py` finds the slides for a lecture, `coursesite.py` finds a course's own website |
| `backend/db/init.sql` | Database schema |
| `web/` | Course page served at http://localhost:8000/app/ (plain JS, no build step) |
| `contracts/api.md` | The API contract between the extension, the page and the backend. Read it before changing an endpoint |
| `scripts/` | `start.sh`, `check_all.sh`, `smoke.sh` |

## Set it up

You need macOS or Linux, Chrome, Docker Desktop, Python 3.11+, Node 20+ (tests only) and an
[OpenRouter API key](https://openrouter.ai/keys).

1. Clone and enter the repo:
   ```bash
   git clone https://github.com/srgoli-cmyk/AIC.git aic && cd aic
   ```
2. Copy the settings file, then put your key in `OPENROUTER_API_KEY`. Never commit `.env`.
   ```bash
   cp .env.example .env
   ```
3. Start Docker Desktop, then install (about 1.7 GB, mostly torch and the embedding model):
   ```bash
   make setup
   ```
4. Start the database and backend. Leave it running; Ctrl+C stops it and keeps your data.
   ```bash
   make start
   ```
5. Load the extension: open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**
   and pick the repo's `extension/` folder. If an older copy is loaded, turn it off.
6. Log in to canvas.ucsd.edu, click the extension, then **Sync all my classes**. It goes through
   every active Canvas course one at a time: recordings from its Media Gallery, then its PDF and
   PowerPoint files (a course with only slides still gets them). **Sync this Canvas course** does
   just the course open in the tab. Captions and slides are read in the background. **Keep updated** in
   the popup re-syncs the course once a day.
7. Open http://localhost:8000/app/, pick the course, and ask a question or pick a lecture and use
   **Teach me**, **Flashcards** or **Practice**.

If the extension says it cannot reach the backend, `make start` is not running.

## Settings (`.env`)

| Name | What it does |
|---|---|
| `OPENROUTER_API_KEY` | Required for answers and study tools |
| `OPENROUTER_MODEL` | Writes answers (default `openai/gpt-4o-mini`) |
| `OPENROUTER_STUDY_MODEL` | Optional stronger model for the study tools |
| `OPENROUTER_CHECK_MODEL` | Reasoning model that redoes the math in lessons and practice problems (default `openai/gpt-5.6-luna`) |

## Working on it

| Command | What it does |
|---|---|
| `make check` | Lint and all tests (needs `make db`). Run before every commit |
| `make dev` | Backend only, reloads on code changes (needs `make db`) |
| `make db` / `make db-reset` | Start the database / wipe it |
| `make web` | Page only at http://localhost:5173 (`?demo` for fake data) |
| `make smoke` | End-to-end test of a running backend with one real OpenRouter call |

Rules for people and agents:
- Changes to `contracts/api.md`, `extension/manifest.json` or extension permissions need the
  maintainer's approval.
- Never store or log tokens, cookies or `.env` contents. The sync rejects unknown fields for this
  reason.
- Keep tests next to the code they cover: `backend/tests/`, `extension/tests/`, `web/tests/`.

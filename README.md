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

You need macOS or Linux, Chrome, Docker Desktop, Python 3.11+, Node 20+ (tests only), an
[OpenRouter API key](https://openrouter.ai/keys), a UCSD Google account, and the team's Google sign-in
client ID and secret (ask the maintainer; they are shared privately and never go in the repo).

1. Clone and enter the repo:
   ```bash
   git clone https://github.com/srgoli-cmyk/AIC.git aic && cd aic
   ```
2. Copy the settings file and fill it in. Never commit `.env`.
   ```bash
   cp .env.example .env
   openssl rand -base64 48   # paste the output into SESSION_SECRET
   ```
   Set `OPENROUTER_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and `SESSION_SECRET`.
3. Start Docker Desktop, then install (about 1.7 GB, mostly torch and the embedding model):
   ```bash
   make setup
   ```
4. Start the database and backend. Leave it running; Ctrl+C stops it and keeps your data.
   ```bash
   make start
   ```
5. Open http://localhost:8000/app/ and sign in with your UCSD Google account. Only verified
   @ucsd.edu accounts can sign in, and each student sees only their own courses.
6. Load the extension: open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked**
   and pick the repo's `extension/` folder. Its id is always `gdgflcpoijokbdnckcojphfoinhkbbmd`; if
   an older copy with another id is loaded, remove it. Click the extension and **Sign in**.
7. Log in to canvas.ucsd.edu, click the extension, then **Sync all my classes**. It goes through
   every active Canvas course one at a time: recordings from its Media Gallery, then its PDF and
   PowerPoint files (a course with only slides still gets them). **Sync this Canvas course** does
   just the course open in the tab. Captions and slides are read in the background. **Keep updated** in
   the popup re-syncs the course once a day.
8. Open http://localhost:8000/app/, pick the course, and ask a question or pick a lecture and use
   **Teach me**, **Flashcards** or **Practice**.

If the extension says it cannot reach the backend, `make start` is not running.

**Data from before sign-in existed.** If you synced courses before this version, they belong to a
local account nobody can sign in as. Sign in once, stop `make start`, then run
`make claim-local-data EMAIL=you@ucsd.edu` (add `DRY_RUN=1` first to see the counts). It moves
everything to your account and refuses, changing nothing, if that account already has one of those
courses.

## Google sign-in (team client)

One OAuth client serves the whole team. Whoever looks after it:
1. In Google Cloud Console, open **Google Auth Platform**. Under **Branding** set the app name and a
   support email. Under **Audience** choose **External** and keep it in **Testing**, and add every
   teammate's UCSD address under **Test users** (only listed users can sign in while testing).
2. Under **Data Access** add the scopes `openid`, `email` and `profile`.
3. Under **Clients** create a **Web application** client with the authorized JavaScript origin
   `http://localhost:8000` and the redirect URI `http://localhost:8000/auth/google/callback`.
4. Share the client ID and secret privately. Each person makes their own `SESSION_SECRET`.

## Settings (`.env`)

| Name | What it does |
|---|---|
| `OPENROUTER_API_KEY` | Required for answers and study tools |
| `OPENROUTER_MODEL` | Writes answers (default `anthropic/claude-haiku-5.5`, with `OPENROUTER_REASONING=off`) |
| `OPENROUTER_STUDY_MODEL` | Optional stronger model for the study tools |
| `OPENROUTER_CHECK_MODEL` | Reasoning model that redoes the math in lessons and practice problems (default `openai/gpt-5.6-luna`) |
| `OPENROUTER_REASONING`, `OPENROUTER_STUDY_REASONING`, `OPENROUTER_CHECK_REASONING` | Reasoning for the answer, study and check models: empty for the model's default, `off`, or `minimal` to `xhigh`. With the study model empty, the study tools follow `OPENROUTER_REASONING` |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | The team's Google sign-in client. Without them, sign-in answers 503 |
| `SESSION_SECRET` | Signs the sign-in state; at least 32 random characters (`openssl rand -base64 48`) |
| `EXTENSION_ID` | The only extension allowed to call the backend from its own origin (default `gdgflcpoijokbdnckcojphfoinhkbbmd`, the id the manifest key gives) |
| `ALLOWED_HOSTS` | Host names the backend answers to (default `localhost,127.0.0.1`); any other `Host` gets 400 |
| `OPENROUTER_OCR_MODEL` | Vision model that reads scanned PDFs, page images to text and LaTeX (default `google/gemini-3.5-flash`). Only used for Canvas PDFs that have almost no text |

## Working on it

| Command | What it does |
|---|---|
| `make check` | Lint and all tests (needs `make db`). Run before every commit |
| `make dev` | Backend only, reloads on code changes (needs `make db`) |
| `make db` / `make db-reset` | Start the database / wipe it |
| `make web` | Page only at http://localhost:5173, with `?demo` fake data. It can't reach the backend from another port; use http://localhost:8000/app/ for real data |
| `make smoke` | End-to-end test of a running backend with one real OpenRouter call. It signs in through `make dev-session` as `smoke-test@ucsd.edu` and deletes that account afterwards |
| `make dev-session EMAIL=...` | Prints a short-lived sign-in token for an @ucsd.edu account, for local testing with curl. It writes straight to the database, so it needs the database access that already means full control; it is never reachable over HTTP |
| `make claim-local-data EMAIL=...` | Once, after your first sign-in: moves data synced before sign-in existed to your account |

Rules:
- Changes to `contracts/api.md`, `extension/manifest.json` or extension permissions need the
  maintainer's approval.
- Never store or log tokens, cookies, emails or `.env` contents. The sync rejects unknown fields for
  this reason.
- Every query and background job takes the signed-in student's id explicitly; there is no default.
- Keep tests next to the code they cover: `backend/tests/`, `extension/tests/`, `web/tests/`.

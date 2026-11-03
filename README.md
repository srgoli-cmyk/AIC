# AI Lecture Search

A browser extension syncs a whole Canvas course (Kaltura lecture captions with timestamps, plus the
course's file list) into a private database on your computer. You then ask questions on a search page,
and the answers explain the topic and cite the lecture and the minute.

Project board: https://miro.com/app/board/uXjVETB4KbI=/

<!-- ![Project board](assets/board.png) -->

## Prerequisites

macOS or Linux. Check each one:

| Tool | Version | Check | Needed for |
|---|---|---|---|
| Python | 3.11 or newer | `python3 --version` | Backend and search page |
| Docker Desktop | Compose 2.1.1 or newer | `docker compose version` | Database |
| git | Any recent version | `git --version` | Clone |
| Node.js | 20 or newer | `node --version` | `make check` only |

Tested on Python 3.14, Node 26 and Compose 5.6. You also need Chrome and an
[OpenRouter API key](https://openrouter.ai/keys).

Setup downloads torch and the embedding model, about 1.7 GB on disk in total.

## Quickstart

1. Clone the repo:
   ```bash
   git clone https://github.com/srgoli-cmyk/AIC.git aic
   cd aic
   ```
2. Create your settings file, then open `.env` and set `OPENROUTER_API_KEY` to your own key:
   ```bash
   cp .env.example .env
   ```
   Everyone uses their own key. Never commit `.env`.
3. Start Docker Desktop and wait until it says it's running.
4. Install everything (takes a few minutes):
   ```bash
   make setup
   ```
5. Run the database, backend and search page. Ctrl+C stops all three and keeps your data:
   ```bash
   make start
   ```

The search page is at http://localhost:8501.

## Getting data

The database starts empty, and the repo contains no real course data, so you sync a course yourself.
You need to be enrolled in it.

**The extension's sync is new and still being tested, so expect problems.**

1. With `make start` running, open `chrome://extensions` in Chrome.
2. Turn on **Developer mode**, click **Load unpacked** and select the repo's `extension/` folder.
3. Open a course page on canvas.ucsd.edu, logged in, such as `https://canvas.ucsd.edu/courses/<id>`.
4. Click the extension's icon, then **Check backend**, then **Sync**. It reads one lecture at a time,
   so a whole course takes a while.
5. When it's done, open http://localhost:8501, pick the course and ask a question. Answers may be
   incomplete until the page shows the course as indexed.

## Troubleshooting

- **Port 8000 is already in use.** See what holds it, then stop that app:
  ```bash
  lsof -nP -iTCP:8000 -sTCP:LISTEN
  ```
  The extension only talks to port 8000, so moving the backend to another port won't work for syncing.
- **Docker is not running.** Errors like `Cannot connect to the Docker daemon` mean Docker Desktop isn't
  started. Start it, wait until it's running, and try again.
- **`make check` fails with "Postgres is not reachable".** It needs the database: run `make db` first.
- **The page says "answer service not configured".** `OPENROUTER_API_KEY` is empty in `.env`. Set it and
  restart `make start`.
- **A sync failed or stopped halfway.** Sync again. Re-syncing is safe: lectures are updated rather than
  duplicated, and ones already stored aren't fetched again.

## Commands

| Command | What it does |
|---|---|
| `make setup` | Creates `.venv`, installs dependencies, downloads the embedding model, creates `.env` if missing |
| `make start` | Starts the database, backend and search page; Ctrl+C stops all three |
| `make dev` | Runs only the backend on port 8000, reloading when code changes (needs `make db`) |
| `make ui` | Runs only the search page on port 8501 (needs `make dev`) |
| `make db` | Runs only the database, in Docker, in the background |
| `make check` | Runs all linters and tests (needs `make db`) |
| `make smoke` | Tests a running backend end to end; makes one real OpenRouter call, using your key |

## Project layout

```
contracts/   API contract, JSON schema and synthetic fixtures
backend/     FastAPI, Postgres with pgvector, embeddings, OpenRouter
extension/   Chrome extension (plain JavaScript)
ui/          Streamlit search page
scripts/     Checks, smoke test and helpers
```

## Rules for contributors

- Never commit `.env`, real captions or anything under `data/`. Test fixtures are synthetic.
- Work in a branch and open a pull request into `main`.
- Run `make check` before opening a pull request.

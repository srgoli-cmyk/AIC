# AI Lecture Search

A Chrome extension that syncs your UCSD lecture captions (from Canvas or podcast.ucsd.edu), stores
and searches them in your own browser, and answers questions with links to the lecture at the
minute it was said.

Project board: https://miro.com/app/board/uXjVETB4KbI=/

<!-- ![Project board](assets/board.png) -->

## How it works (branch `chrome-web-store`)

- **Sync**: the extension reads lecture titles and captions with your own Canvas and podcast
  sessions, one lecture at a time.
- **Store and search**: captions are chunked and embedded on your computer (bge-small, WebAssembly)
  and kept in IndexedDB. Nothing is uploaded.
- **Answer**: only your question and the best matching excerpts go to the answer service
  (`proxy/`, a Cloudflare Worker), which calls OpenRouter.

The JS ports of the backend's chunking, parsing and citation code are checked against the Python
code by `extension/tests/parity.test.mjs`. Publishing steps: `store/CHECKLIST.md`.

## Quickstart (extension only)

You need Chrome 116+, Node.js 20+, and an [OpenRouter API key](https://openrouter.ai/keys) for
local answers.

1. Clone and fetch the vendored model, runtime and fonts (about 56 MB, checksums verified):
   ```bash
   git clone https://github.com/srgoli-cmyk/AIC.git aic
   cd aic
   git checkout chrome-web-store
   make vendor
   ```
2. Put `OPENROUTER_API_KEY` in `.env` (`cp .env.example .env`; never commit it), then run the
   answer service locally:
   ```bash
   make proxy-dev
   ```
3. In Chrome, open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked** and
   select the repo's `extension/` folder.
4. On a Canvas course page, open the popup and press **Sync this Canvas course** (or **Sync all my
   classes** for podcast.ucsd.edu). Then **Open Lecture Search**, pick the course and ask.

A whole course takes a while: lectures are read one at a time, then indexed on your computer.
Syncing again is safe.

## Optional: the team backend

The original FastAPI + Postgres backend still works for development and the parity tests. It needs
Python 3.11+ and Docker Desktop.

```bash
make setup   # .venv, dependencies, embedding model (about 1.7 GB)
make start   # database + backend; the page is at http://localhost:8000/ext/app.html
```

## Commands

| Command | What it does |
|---|---|
| `make setup` | Creates `.venv`, installs dependencies and the embedding model, creates `.env` if missing |
| `make vendor` | Downloads and verifies the model, embedding runtime, KaTeX and fonts into `extension/vendor/` |
| `make proxy-dev` | Runs the answer service at http://localhost:8787 with the key from `.env` |
| `make package` | Builds the Chrome Web Store zip in `dist/` (needs `ASK_PROXY_URL=https://...`) |
| `make start` | Starts the database and the backend; the course page is at http://localhost:8000/ext/app.html |
| `make dev` | Runs only the backend, reloading when code changes (needs `make db`) |
| `make db` | Runs only the database, in Docker, in the background |
| `make web` | Serves the page at http://localhost:5173/app.html, for working on it (add `?demo` for demo data) |
| `make check` | Runs all linters and tests (needs `make db`) |
| `make smoke` | Tests a running backend end to end; makes one real OpenRouter call with your key |

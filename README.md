# AI Lecture Search

A Chrome extension syncs your UCSD lecture captions (from Canvas or podcast.ucsd.edu) into a database on
your own computer. You ask questions on a course page, and the answers explain the topic and link to
the lecture at the minute it was said.

Project board: https://miro.com/app/board/uXjVETB4KbI=/

<!-- ![Project board](assets/board.png) -->

## Quickstart

You need macOS or Linux, Chrome, an [OpenRouter API key](https://openrouter.ai/keys), and:

| Tool | Version | Check |
|---|---|---|
| Python | 3.11 or newer | `python3 --version` |
| Docker Desktop | Compose 2.1.1 or newer | `docker compose version` |
| git | Any recent version | `git --version` |
| Node.js | 20 or newer, for `make check` only | `node --version` |

1. Clone the repo:
   ```bash
   git clone https://github.com/srgoli-cmyk/AIC.git aic
   cd aic
   ```
2. Create `.env` and set `OPENROUTER_API_KEY` in it to your own key. Never commit `.env`.
   ```bash
   cp .env.example .env
   ```
3. Start Docker Desktop, then install everything. This downloads torch and the embedding model, about
   1.7 GB on disk.
   ```bash
   make setup
   ```
4. Start the database and the backend. Ctrl+C stops both and keeps your data.
   ```bash
   make start
   ```
5. Open http://localhost:8000/app/

## Getting data

The database starts empty, and the repo has no course data, so you sync your own classes.

1. In Chrome, open `chrome://extensions`, turn on **Developer mode**, click **Load unpacked** and select
   the repo's `extension/` folder.
2. With `make start` running, use the extension's popup:
   - **Sync this Canvas course**, on a course page on canvas.ucsd.edu, syncs that course.
   - **Sync all my classes** syncs every class podcast.ucsd.edu lists for you. It may not list every
     class yet.
3. To keep a course up to date, turn on **Keep <course> updated** in the popup. It syncs once a day.
4. Open http://localhost:8000/app/, pick a course and ask a question.
5. To study one lecture, pick it from the list and use the tabs above the question box:
   - **Teach me** writes a full lesson from that lecture, with the problems worked in class placed
     inside it for you to try.
   - **Flashcards** makes about 14 cards.
   - **Practice** writes up to 10 multiple-choice problems, from warm-up to challenge, each with a
     worked solution.

   Each one takes up to a minute. They are written by the model in `OPENROUTER_MODEL`. Practice
   problems come out better with a stronger model: set `OPENROUTER_STUDY_MODEL` in `.env` (for
   example `openai/gpt-4o`) to use one for these three tools only. It costs more per use.

A whole course takes a while, because lectures are read one at a time. Syncing again is safe.

## Commands

| Command | What it does |
|---|---|
| `make setup` | Creates `.venv`, installs dependencies and the embedding model, creates `.env` if missing |
| `make start` | Starts the database and the backend; the course page is at http://localhost:8000/app/ |
| `make dev` | Runs only the backend, reloading when code changes (needs `make db`) |
| `make db` | Runs only the database, in Docker, in the background |
| `make web` | Serves only the page at http://localhost:5173, for working on it (add `?demo` for demo data) |
| `make check` | Runs all linters and tests (needs `make db`) |
| `make smoke` | Tests a running backend end to end; makes one real OpenRouter call with your key |

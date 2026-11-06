# AI Lecture Search

a browser extension syncs a whole Canvas course into a private db on your computer. you then ask questions on a search page,
and the answers explain the topic and cite the lecture and the minute.

project board: https://miro.com/app/board/uXjVETB4KbI=/

<!-- ![Project board](assets/board.png) -->

## prerequisites

macOS/Linux. check each one:

| Tool | Version | Check | Needed for |
|---|---|---|---|
| Python | 3.11 or newer | `python3 --version` | Backend and search page |
| Docker Desktop | Compose 2.1.1 or newer | `docker compose version` | Database |
| git | Any recent version | `git --version` | Clone |
| Node.js | 20 or newer | `node --version` | `make check` only |

u also need Chrome and an [OpenRouter API key](https://openrouter.ai/keys).

## Quickstart

1. ```bash
   git clone https://github.com/srgoli-cmyk/AIC.git aic
   cd aic
   ```
2. set `OPENROUTER_API_KEY` in `.env`
   ```bash
   cp .env.example .env
   ```
3. start Docker Desktop.
4. ```bash
   make setup
   ```
5. ```bash
   make start
   ```

the search page is at http://localhost:8501. the new course page is at http://localhost:8000/app/ (served by the backend; the extension's popup links straight to a synced course there).

## Getting data

the db starts empty, and the repo contains no real course data, so you sync a course urself.

1. with `make start` running, open `chrome://extensions` in Chrome.
2. turn on **Developer mode**, click **Load unpacked** and select the repo's `extension/` folder.
3. open a course page on canvas.ucsd.edu, logged in, such as `https://canvas.ucsd.edu/courses/<id>`.
4. click the extension's icon, then **Check backend**, then **Sync**. It reads one lecture at a time,
   so a whole course takes a while.

## Project layout

```
contracts/   API contract, JSON schema and synthetic fixtures
backend/     FastAPI, Postgres with pgvector, embeddings, OpenRouter
extension/   Chrome extension (plain JavaScript)
ui/          Streamlit search page
scripts/     Checks, smoke test and helpers
```

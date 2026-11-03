# AI Lecture Search

AISC project. A browser extension syncs a whole Canvas course (Kaltura lecture captions with
timestamps + course files) into a private study space; answers cite the lecture and the minute.

## Quick start
```bash
make setup          # venv + deps + embedding model download, creates .env from .env.example
make db             # Postgres + pgvector in Docker (needed from step 2)
make dev            # backend on http://localhost:8000  (docs at /docs)
make ui             # search page on http://localhost:8501; PORT sets the backend's port (needs make dev)
make check          # all automated gates (needs make db)
make smoke          # end-to-end checks against the running backend
```
Extension: `chrome://extensions` > Developer mode > Load unpacked > select `extension/`,
then open the popup and press "Check backend".

## Layout
```
contracts/   API contract + JSON schema + synthetic fixtures (source of truth)
backend/     FastAPI, Postgres/pgvector, embeddings, OpenRouter
extension/   Chrome MV3 extension (plain JS)
scripts/     gates (check_all.sh), smoke test, helpers
ui/          Streamlit search page
docs/        roadmap, sync flow
```

## How we build it
See `docs/roadmap.md` (steps and "done when").

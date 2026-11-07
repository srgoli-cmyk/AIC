.PHONY: setup db db-reset dev web start check smoke vendor proxy-dev package

VENV ?= .venv
PY := $(VENV)/bin/python
PORT ?= 8000

setup:
	python3 -m venv $(VENV)
	$(PY) -m pip install -r backend/requirements-ml.txt
	$(PY) -c "from sentence_transformers import SentenceTransformer as S; S('BAAI/bge-small-en-v1.5')"
	@test -f .env || cp .env.example .env
	@echo "Activate with: source $(VENV)/bin/activate"

db:
	docker compose up -d db

db-reset:
	docker compose down -v
	docker compose up -d db

dev:
	cd backend && ../$(VENV)/bin/uvicorn app.main:app --reload --port $(PORT)

# The search page in demo mode: http://127.0.0.1:5173/app.html?demo
web:
	python3 -m http.server 5173 --bind 127.0.0.1 --directory extension

# Model, embedding runtime, KaTeX and fonts into extension/vendor/ (checksums verified).
vendor:
	cd extension && npm ci && node ../scripts/vendor_extension.mjs

# The answer service on http://localhost:8787 (reads OPENROUTER_API_KEY from .env).
proxy-dev:
	node proxy/dev.mjs

# Chrome Web Store zip in dist/. Needs ASK_PROXY_URL=https://... (the deployed answer service).
package:
	node scripts/package_extension.mjs

start:
	VENV="$(VENV)" PORT="$(PORT)" bash scripts/start.sh

check:
	PATH="$(abspath $(VENV))/bin:$$PATH" bash scripts/check_all.sh

smoke:
	BASE_URL="$${BASE_URL:-http://localhost:$(PORT)}" bash scripts/smoke.sh

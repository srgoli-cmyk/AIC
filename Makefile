.PHONY: setup db db-reset dev check smoke

VENV ?= .venv
PY := $(VENV)/bin/python

setup:
	python3 -m venv $(VENV)
	$(PY) -m pip install -r backend/requirements.txt
	@test -f .env || cp .env.example .env
	@echo "Activate with: source $(VENV)/bin/activate"

db:
	docker compose up -d db

db-reset:
	docker compose down -v
	docker compose up -d db

dev:
	cd backend && ../$(VENV)/bin/uvicorn app.main:app --reload --port 8000

check:
	PATH="$(abspath $(VENV))/bin:$$PATH" bash scripts/check_all.sh

smoke:
	bash scripts/smoke.sh

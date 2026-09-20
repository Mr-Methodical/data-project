PYTHON ?= python3
VENV_PYTHON := .venv/bin/python

.PHONY: setup dev test lint build serve e2e check

setup:
	$(PYTHON) -m venv .venv
	$(VENV_PYTHON) -m pip install -r backend/requirements-dev.txt
	cd frontend && npm ci

dev:
	$(VENV_PYTHON) scripts/dev.py

test:
	$(VENV_PYTHON) -m pytest -q

lint:
	$(VENV_PYTHON) -m ruff check backend scripts

build:
	cd frontend && npm run build

serve:
	PYTHONPATH=backend $(VENV_PYTHON) -m uvicorn rinkcheck.api:app --host 127.0.0.1 --port 8000

e2e: build
	cd frontend && npm run test:e2e

check: lint test build e2e


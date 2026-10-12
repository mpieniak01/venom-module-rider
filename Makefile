.PHONY: check check-python check-web test demo demo-pi
HOST ?= ../..
PYTHON ?= $(HOST)/.venv/bin/python
RUFF ?= $(HOST)/.venv/bin/ruff
WEB_BIN = $(HOST)/web-next/node_modules/.bin

check: check-python check-web

check-python:
	$(RUFF) check venom_module_rider scripts tests
	$(RUFF) format --check venom_module_rider scripts tests
	PYTHONPATH=$(HOST):. $(PYTHON) -m mypy venom_module_rider scripts --no-incremental
	PYTHONPATH=$(HOST):. $(PYTHON) -m pytest -q --cov=venom_module_rider --cov-fail-under=90 --cov-report=term-missing --cov-report=xml:test-results/coverage.xml

check-web:
	$(WEB_BIN)/tsc --project web-next/tsconfig.json --noEmit
	$(WEB_BIN)/eslint web-next tests/web --max-warnings 0 --config web-next/eslint.config.mjs
	node --import $(HOST)/web-next/node_modules/tsx/dist/loader.mjs --test tests/web/*.test.ts

test:
	PYTHONPATH=$(HOST):. $(PYTHON) -m pytest -q

demo:
	PYTHONPATH=$(HOST):. $(PYTHON) scripts/run_demo.py

demo-pi:
	$(PYTHON) -m venom_module_rider.demo --port 18880

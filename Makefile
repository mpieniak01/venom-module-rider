.PHONY: check test demo-pi
HOST ?= ../..
PYTHON ?= $(HOST)/.venv/bin/python
RUFF ?= $(HOST)/.venv/bin/ruff

check:
	$(RUFF) check venom_module_rider tests
	$(RUFF) format --check venom_module_rider tests
	PYTHONPATH=$(HOST):. $(PYTHON) -m pytest -q --cov=venom_module_rider --cov-report=term-missing --cov-report=xml:test-results/coverage.xml

test:
	PYTHONPATH=$(HOST):. $(PYTHON) -m pytest -q

demo-pi:
	$(PYTHON) -m venom_module_rider.demo --port 18880

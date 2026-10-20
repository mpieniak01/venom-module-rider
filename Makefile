.PHONY: check check-python check-web test demo demo-pi
HOST ?= ../..
PYTHON ?= $(HOST)/.venv/bin/python
RUFF ?= $(HOST)/.venv/bin/ruff
WEB_BIN = $(HOST)/web-next/node_modules/.bin

check: check-python check-web

check-python:
	$(RUFF) check venom_module_rider scripts tests
	$(RUFF) format --check venom_module_rider scripts tests
	PYTHONPATH=$(HOST):. $(PYTHON) -m mypy venom_module_rider scripts --no-incremental --cache-dir=/dev/null
	PYTHONPATH=$(HOST):. $(PYTHON) -m pytest -q --cov=venom_module_rider --cov-fail-under=90 --cov-report=term-missing --cov-report=xml:test-results/coverage.xml

check-web:
	node --check tests/browser/form-consistency.js
	node --check tests/browser/ui-consistency.js
	node --check tests/browser/camera-lifecycle.js
	node --check tests/browser/operator-priority.js
	node --check tests/browser/control-workspace.js
	node --check tests/browser/vision-sectors.js
	node --check tests/browser/automatic-capture.js
	node --check tests/browser/analysis-offline.js
	node --check tests/browser/analysis-recovery.js
	node --check tests/browser/samples-capture.js
	node --check tests/browser/samples-recovery.js
	node --check tests/browser/navigation-takeover.js
	node --check tests/browser/manual-continuous.js
	node --check tests/browser/manual-panel-authority.js
	node --check tests/browser/journey-acceptance.js
	node --check tests/browser/journey-settings.js
	node --check tests/browser/journey.js
	$(WEB_BIN)/tsc --project web-next/tsconfig.json --noEmit
	$(WEB_BIN)/eslint web-next tests/web --max-warnings 0 --config web-next/eslint.config.mjs
	node --import $(HOST)/web-next/node_modules/tsx/dist/loader.mjs --test tests/web/*.test.ts

test:
	PYTHONPATH=$(HOST):. $(PYTHON) -m pytest -q

demo:
	PYTHONPATH=$(HOST):. $(PYTHON) scripts/run_demo.py $(ARGS)

demo-pi:
	$(PYTHON) -m venom_module_rider.demo --port 18880

.PHONY: research-depth
DA3_PYTHON ?= data/da3-research/venv/bin/python
DA3_SDK_PATH ?= data/da3-research/sdk/src
research-depth:
	PYTHONPATH=.:$(DA3_SDK_PATH) $(DA3_PYTHON) scripts/compare_depth_models.py $(ARGS)

# Features need only the host runtime; the explicit pose option uses the
# retained model environment, matching research-depth.
MAPPING_PYTHON ?= $(if $(filter --pose,$(ARGS)),$(DA3_PYTHON),$(PYTHON))
.PHONY: research-mapping
research-mapping:
	PYTHONPATH=$(HOST):$(DA3_SDK_PATH):. $(MAPPING_PYTHON) -m scripts.mapping_research $(ARGS)

.PHONY: check-journey
check-journey:
	PYTHONPATH=$(HOST):. $(PYTHON) -m pytest -q tests/test_navigation_journey_api.py tests/test_journey_vision.py tests/test_navigation_trial_api.py tests/test_steering.py tests/test_operator_proxy.py

.PHONY: check check-python check-web test demo demo-pi
HOST ?= ../..
PYTHON ?= $(HOST)/.venv/bin/python
RUFF ?= $(HOST)/.venv/bin/ruff
WEB_BIN = $(HOST)/web-next/node_modules/.bin

check: check-python check-web check-browser

check-python:
	$(RUFF) check venom_module_rider scripts tests
	$(RUFF) format --check venom_module_rider scripts tests
	PYTHONPATH=$(HOST):. $(PYTHON) -m mypy venom_module_rider scripts --no-incremental --cache-dir=/dev/null
	PYTHONPATH=$(HOST):. $(PYTHON) -m pytest -q --cov=venom_module_rider --cov-fail-under=90 --cov-report=term-missing --cov-report=xml:test-results/coverage.xml

check-web:
	node --check tests/browser/operator-demo.js
	node --check tests/browser/height.js
	node --check tests/browser/figures.js
	node --check tests/browser/pulse-trials.js
	node --check tests/browser/input-region.js
	node --check tests/browser/input-region-operator.js
	node --check scripts/check_browser_regressions.mjs
	node --check tests/browser/video-analysis.js
	node --check tests/browser/video-speed.js
	node --check tests/browser/samples-layout.js
	node --check tests/browser/session-archive.js
	node --check tests/browser/room-preset.js
	node --check tests/browser/image-reference-defaults.js
	node --check tests/browser/image-reference-recovery.js
	node --check tests/browser/operator-parameters.js
	node --check tests/browser/operator-workspace.js
	node --check tests/browser/journey-start-prod.js
	node --check tests/browser/operator-status.js
	node --check tests/browser/model-tuning.js
	node --check tests/browser/navigation-profiles.js
	node --check tests/browser/perception-model-selection.js
	node --check tests/browser/unified-study.js
	node --check tests/browser/http-lan.js
	node --check tests/browser/global-study.js
	node --check tests/browser/axis-analysis.js
	node --check tests/browser/attitude-replay.js
	node --check tests/browser/motion-study.js
	node --check tests/browser/loop-timing.js
	node --check tests/browser/form-consistency.js
	node --check tests/browser/ui-consistency.js
	node --check tests/browser/camera-lifecycle.js
	node --check tests/browser/native-stream-recovery.js
	node --check tests/browser/preview-continuity.js
	node --check tests/browser/operator-priority.js
	node --check tests/browser/operator-start-settings.js
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
	node --check tests/browser/operator-journey-priority.js
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

.PHONY: check-browser
check-browser:
	RIDER_WEB_ROOT="$(abspath $(HOST)/web-next)" node scripts/check_browser_regressions.mjs $(BROWSER_ARGS)

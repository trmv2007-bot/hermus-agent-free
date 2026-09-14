# Hermus Agent Free — canonical dev entry points.
# Every target runs inside .venv (created by `make setup`, same as setup.sh).
VENV ?= .venv
PY := $(VENV)/bin/python
RUFF := $(VENV)/bin/ruff

.PHONY: help setup test test-full test-cov bench lint format format-check typecheck run doctor clean

help: ## Show this help.
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

setup: ## Create .venv and install runtime + dev dependencies.
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -r requirements-dev.txt

test: ## Fast suite: unit + integration tests (skips tests/eval, ~1 min).
	$(PY) -m pytest tests/ -q --ignore=tests/eval

test-full: ## Entire suite including evals.
	$(PY) -m pytest tests/ -q

test-cov: ## Suite with coverage report for core/ + gateway/.
	$(PY) -m pytest tests/ -q --ignore=tests/eval --cov=core --cov=gateway --cov-report=term-missing

bench: ## Performance benchmarks (async fan-out, gateway middleware, CLI startup).
	$(PY) scripts/bench.py

bench-json: ## Same as bench, writing bench.json for before/after comparison.
	$(PY) scripts/bench.py --json bench.json

lint: ## Ruff lint over the whole tree (must be clean).
	$(RUFF) check .

format: ## Ruff format over the whole tree (writes changes).
	$(RUFF) format .

format-check: ## Fail if anything needs formatting.
	$(RUFF) format --check .

typecheck: ## Mypy on the strictly-typed foundation modules.
	$(VENV)/bin/mypy core/log.py core/errors.py

typecheck-full: ## Mypy over the whole tree (advisory until legacy modules ratchet up).
	$(VENV)/bin/mypy core gateway hermus.py

run: ## Start the gateway (default port from config).
	$(PY) hermus.py gateway start

doctor: ## Hermus self-diagnostics.
	$(PY) hermus.py doctor

clean: ## Remove caches (never touches data/ or user state).
	rm -rf .ruff_cache .mypy_cache .pytest_cache tests/__pycache__ core/__pycache__ gateway/__pycache__
	find . -name '__pycache__' -not -path './.venv/*' -prune -exec rm -rf {} + 2>/dev/null; true

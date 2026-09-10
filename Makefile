.DEFAULT_GOAL := help
.PHONY: help install lock app operator test test-all lint fmt clean

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	  | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

install:  ## Install from the committed lockfile (fails if uv.lock is stale)
	uv sync --locked --extra dev
	uv run playwright install chromium

lock:  ## Re-resolve dependencies and update uv.lock (commit the result)
	uv lock

app:  ## Run the target app on :8080 (blocking)
	uv run python -m target_app

operator:  ## Operator CLI (see: waypoint intervene --help)
	uv run python -m waypoint.operator.cli

test:  ## Run tests, excluding those that need a live LLM
	uv run pytest -m "not llm" -q

test-all:  ## Run every test, including live-LLM tests
	uv run pytest -q

lint:  ## Lint and type-check
	uv run ruff check .
	uv run mypy waypoint

fmt:  ## Format
	uv run ruff format .

clean:  ## Remove disposable output. NEVER deletes committed showcase evidence.
	@if [ -d evidence/runs ]; then \
	  find evidence/runs -mindepth 1 -maxdepth 1 -type d ! -name 'showcase-*' -exec rm -rf {} + ; \
	fi
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -prune -exec rm -rf {} +

.PHONY: install pull test clean lock complexity lint

install: lock
	uv sync --all-extras

lock:
	uv lock

pull:
	@echo "Pulling and verifying images..."
	@uv run python -c "from testbed.reproduce import pull_and_verify; pull_and_verify()"

test:
	uv run pytest -v -m "not slow"
test-all:
	uv run pytest -v

complexity:
	uv run radon cc src -a -n A

lint:
	uv run prospector .

clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".build" -exec rm -rf {} + 2>/dev/null || true

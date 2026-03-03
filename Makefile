.PHONY: install pull test clean clean-artifacts lock complexity lint yamllint

install: lock
	uv sync --all-extras

lock:
	uv lock

pull:
	@echo "Pulling and verifying images..."
	@if [ -z "$(TOPOLOGY)" ]; then \
		echo "Usage: make pull TOPOLOGY=<topology-id>"; \
		exit 2; \
	fi
	@uv run python -c "from gurney.reproduce import pull_and_verify; pull_and_verify(\"$(TOPOLOGY)\")"

test:
	uv run pytest -v -m "not slow"
test-all:
	uv run pytest -v

complexity:
	uv run radon cc src -a -n B

lint:
	uv run prospector .

yamllint:
	uv run yamllint topologies/*/topology.yaml $$(find topologies -name 'scenario.yaml' -not -path '*/.build/*')

clean-artifacts:
	rm -rf var/artifacts

clean:
	rm -rf build/ dist/ *.egg-info .pytest_cache
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".build" -exec rm -rf {} + 2>/dev/null || true

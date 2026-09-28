.PHONY: run dev test build typecheck clean

run: ## Build the UI and serve everything on one port (default target)
	./run.sh

dev: ## Backend with reload + Vite dev server
	./run.sh dev

test: ## Backend test suite
	./run.sh test

build: ## Production frontend bundle
	cd frontend && npm install --no-fund --no-audit && npm run build

typecheck: ## Frontend type check
	cd frontend && npm run typecheck

clean:
	rm -rf frontend/dist frontend/node_modules .venv backend/.pytest_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

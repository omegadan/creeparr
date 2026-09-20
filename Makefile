.PHONY: dev backend frontend test lint build docker migration

# Run backend + frontend dev servers (two terminals recommended; this uses & for convenience)
dev:
	$(MAKE) -j2 backend frontend

backend:
	cd backend && PATREONARR_CONFIG_DIR=../config PATREONARR_DOWNLOAD_DIR=../downloads \
		uv run uvicorn patreonarr.app:create_app --factory --reload --port 7979

frontend:
	cd frontend && npm run dev

test:
	cd backend && uv run pytest -q

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && npm run lint && npx tsc --noEmit

build:
	cd frontend && npm ci && npm run build
	rm -rf backend/patreonarr/static && mkdir -p backend/patreonarr/static
	cp -r frontend/dist/. backend/patreonarr/static/

docker:
	docker build -t patreonarr:dev .

# usage: make migration m="add foo column"
migration:
	cd backend && PATREONARR_CONFIG_DIR=../config uv run alembic revision --autogenerate -m "$(m)"

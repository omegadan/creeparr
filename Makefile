.PHONY: dev backend frontend test lint build docker migration

# Run backend + frontend dev servers (two terminals recommended; this uses & for convenience)
dev:
	$(MAKE) -j2 backend frontend

backend:
	cd backend && PATREARR_CONFIG_DIR=../config PATREARR_DOWNLOAD_DIR=../downloads \
		uv run uvicorn patrearr.app:create_app --factory --reload --port 7979

frontend:
	cd frontend && npm run dev

test:
	cd backend && uv run pytest -q

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && npm run lint && npx tsc --noEmit

build:
	cd frontend && npm ci && npm run build
	rm -rf backend/patrearr/static && mkdir -p backend/patrearr/static
	cp -r frontend/dist/. backend/patrearr/static/

docker:
	docker build -t patrearr:dev .

# usage: make migration m="add foo column"
migration:
	cd backend && PATREARR_CONFIG_DIR=../config uv run alembic revision --autogenerate -m "$(m)"

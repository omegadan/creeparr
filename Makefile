.PHONY: dev backend frontend test lint build docker migration

# Run backend + frontend dev servers (two terminals recommended; this uses & for convenience)
dev:
	$(MAKE) -j2 backend frontend

backend:
	cd backend && CREEPARR_CONFIG_DIR=../config CREEPARR_DOWNLOAD_DIR=../downloads \
		uv run uvicorn creeparr.app:create_app --factory --reload --port 7979

frontend:
	cd frontend && npm run dev

test:
	cd backend && uv run pytest -q

lint:
	cd backend && uv run ruff check . && uv run ruff format --check .
	cd frontend && npm run lint && npx tsc --noEmit

build:
	cd frontend && npm ci && npm run build
	rm -rf backend/creeparr/static && mkdir -p backend/creeparr/static
	cp -r frontend/dist/. backend/creeparr/static/

docker:
	docker build -t creeparr:dev .

# usage: make migration m="add foo column"
migration:
	cd backend && CREEPARR_CONFIG_DIR=../config uv run alembic revision --autogenerate -m "$(m)"

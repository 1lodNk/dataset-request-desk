.PHONY: up down test test-pg test-docker reset
up:            ## build and start everything on http://localhost:8000
	docker compose up --build
down:
	docker compose down
reset:         ## also wipe the database volume
	docker compose down -v
test:          ## fast: SQLite, no services needed (pip install -r requirements-dev.txt first)
	python -m pytest
test-pg:       ## same suite on PostgreSQL (enables the concurrency tests): TEST_DATABASE_URL=postgresql+psycopg://... make test-pg
	python -m pytest
test-docker:   ## full suite on PostgreSQL, nothing to install locally
	docker compose run --rm tests

# Axon MCP Server

Axon is a code-indexing MCP server focused on Python, Java, JavaScript/TypeScript, and docs/config assets.

## Current Baseline

- Parser stack: Tree-sitter-based language parsers
- Runtime stack: FastAPI + Celery + PostgreSQL (pgvector) + Redis
- Supported repository providers: GitLab, GitHub, and generic Git remotes
- Primary output: symbols, relations, call traversal context, dependency/config/documentation search

## Language Scope

- Python
- Java
- JavaScript / TypeScript / Vue
- Markdown / configuration files

## Quick Start

```bash
cp .env.example .env
make dev-install
make db-reset
make api-dev
```

For dockerized runtime:

```bash
docker compose -f docker/docker-compose.yml up -d
```

## Development Notes

- Use integration-style validation with the real local PostgreSQL/Redis setup.
- The legacy hybrid semantic-analysis pipeline has been removed from this fork.
- For the current WIP cycle, prefer fresh-schema creation via `make db-reset` or `python scripts/reset_db.py --yes`.
- Alembic is still available when you explicitly want the migration path; it is just not the default path for today's branch work.

## Integration Test Prerequisites

For integration or DB-backed tests, set database credentials explicitly before running `pytest` or `make test`:

```bash
source /home/vscode/.venv-dev/bin/activate
```

In the devcontainer, `DATABASE_URL` and `TEST_DATABASE_URL` are already exported by default:
- `DATABASE_URL=postgresql+asyncpg://indexer:indexer@localhost:5432/indexer`
- `TEST_DATABASE_URL=postgresql+asyncpg://indexer:indexer@localhost:5432/indexer_test`

The devcontainer Postgres bootstrap also initializes both databases automatically:
- `indexer`
- `indexer_test`

`TEST_DATABASE_URL` must point at a separate database. The test harness creates and drops schema there.

If you need a deliberate clean-schema reset before a validation run, use:

```bash
python scripts/reset_db.py --yes --use-test-db
# or:
make db-reset-test
```

This is intended as a manual action. The test harness does not reset the database automatically.

Example targeted run:

```bash
pytest -q -rs tests/integration/test_java_import_relationships.py
```

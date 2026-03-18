# Axon MCP Server

Axon is a code-indexing MCP server focused on Python, Java, JavaScript/TypeScript, and docs/config assets.

## Current Baseline

- Parser stack: Tree-sitter-based language parsers
- Runtime stack: FastAPI + Celery + PostgreSQL (pgvector) + Redis
- Supported repository providers: GitLab
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
make api-dev
```

For dockerized runtime:

```bash
docker compose -f docker/docker-compose.yml up -d
```

## Development Notes

- Use integration-style validation with the real local PostgreSQL/Redis setup.
- The legacy hybrid semantic-analysis pipeline has been removed from this fork.

## Integration Test Prerequisites

For integration or DB-backed tests, set database credentials explicitly before running `pytest` or `make test`:

```bash
source /home/vscode/.venv-dev/bin/activate
export DATABASE_URL='postgresql+asyncpg://indexer:indexer@localhost:5432/indexer'
export TEST_DATABASE_URL='postgresql+asyncpg://indexer:indexer@localhost:5432/indexer'
```

If you need a deliberate clean-schema reset before a validation run, use:

```bash
python scripts/reset_db.py --yes --use-test-db
```

This is intended as a manual action. The test harness does not reset the database automatically.

Example targeted run:

```bash
pytest -q -rs tests/integration/test_java_import_relationships.py
```

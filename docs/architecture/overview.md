# Architecture Overview

Axon MCP Server is an AI-oriented code indexer and query surface.

## Services

- API service (FastAPI)
- MCP server
- Background workers (Celery)
- PostgreSQL + pgvector
- Redis

## Processing Flow

1. Discover repository files.
2. Parse supported files with language-specific parsers.
3. Persist files/symbols/relations.
4. Build context artifacts (call graph, architecture map, module summaries).
5. Serve results through REST and MCP tools.

## Repository Lifecycle

- Repositories are registered through the REST API and the initial sync is enqueued automatically.
- Runtime repository access already resolves between generic git and local-directory sources.
- Public provider support and recurring poll/incremental refresh orchestration are still narrower than the runtime foundation.

Canonical references:
- `docs/architecture/repository_registration_and_sync_architecture.md`
- `docs/architecture/repository_source_abstraction.md`

## Parser Layer

Parsers are organized by language and selected through parser routing.

Supported language groups:

- Python / Java
- JavaScript / TypeScript / Vue
- Markdown and selected config/document formats

## Operational Notes

- Integration tests use local PostgreSQL and Redis.
- Background tasks run through Celery workers.
- Docker images are Python-only.

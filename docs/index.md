# Axon MCP Server Docs

This fork targets Python + Java + docs/config indexing.

## Runtime Stack

- Python 3.11
- FastAPI + Celery workers
- PostgreSQL 17 + pgvector
- Redis

## Capabilities

- Symbol extraction and relation graphing
- Call hierarchy traversal
- API endpoint discovery
- Documentation/config search
- Repository and file exploration for MCP clients

## Key References

- [Architecture Overview](architecture/overview.md)
- [Data Models](architecture/data_models.md)
- [MCP Tools](api/mcp_tools.md)
- [Setup Guide](guides/setup.md)

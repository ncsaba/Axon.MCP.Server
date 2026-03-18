# Axon MCP Server Docs

This fork targets Python + Java + docs/config indexing with a graph-oriented code intelligence model.

## Runtime Stack

- Python 3.11
- FastAPI + Celery workers
- PostgreSQL 17 + pgvector
- Redis

## Current Status

- C# / Roslyn runtime path removed
- Java parser routing active
- Integration-first validation using real local infrastructure

## Capabilities

- Symbol extraction and relation graphing
- Call hierarchy traversal
- API endpoint discovery
- Documentation/config search
- Repository and file exploration for MCP clients

## Key References

- [Architecture Overview](architecture/overview.md)
- [Parser Capability Matrix](architecture/parser_capability_matrix.md)
- [Repository Source Abstraction](architecture/repository_source_abstraction.md)
- [Incremental Indexing Spec](architecture/incremental_indexing_spec.md)
- [Streaming Indexing Implementation Plan](architecture/streaming_indexing_implementation_plan.md)
- [Streaming File Inventory Design (Slice 2)](architecture/streaming_file_inventory_design.md)
- [Observability Findings And Recommendations](architecture/observability_findings_and_recommendations.md)
- [File Instance/Content Architecture Design](architecture/file_instance_content_dedup_proposal.md)
- [File Instance/Content Implementation Plan](architecture/file_instance_content_implementation_plan.md)
- [Data Models](architecture/data_models.md)
- [MCP Tools](api/mcp_tools.md)
- [Setup Guide](guides/setup.md)
- [Analysis and Roadmap](AXON_ANALYSIS_AND_ROADMAP.md)
- [Session Handover](SESSION_HANDOVER.md)

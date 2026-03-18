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
- [Retrieval Improvement Plan](architecture/retrieval_improvement_plan.md)
- [Semantic Search Improvement Plan](architecture/semantic_search_improvement_plan.md)
- [Repository Source Abstraction](architecture/repository_source_abstraction.md)
- [Repository Registration And Sync Architecture](architecture/repository_registration_and_sync_architecture.md)
- [Authentication And MCP Token Plan](architecture/authentication_and_mcp_token_plan.md)
- [Incremental Indexing Spec](architecture/incremental_indexing_spec.md)
- [Streaming Indexing Implementation Plan](architecture/streaming_indexing_implementation_plan.md)
- [Streaming File Inventory Design (Slice 2)](architecture/streaming_file_inventory_design.md)
- [Observability Findings And Recommendations](architecture/observability_findings_and_recommendations.md)
- [File Instance/Content Architecture Design](architecture/file_instance_content_dedup_proposal.md)
- [File Instance/Content Implementation Plan](architecture/file_instance_content_implementation_plan.md)
- [Data Models](architecture/data_models.md)
- [MCP Tools](api/mcp_tools.md)
- [MCP Server Startup Guide](guides/mcp_server_startup.md)
- [GitHub Repository Sync Scenario](guides/github_repository_sync_scenario.md)
- [Semantic Search Embedding Runbook](guides/semantic_search_embedding_runbook.md)
- [Setup Guide](guides/setup.md)
- [Semantic Search Index Validation](validation/semantic_search_index_validation_20260318.md)
- [Semantic Search Benchmark Seed](validation/semantic_search_benchmark_seed.md)
- [Semantic Search Benchmark Run 2026-03-18](validation/semantic_search_benchmark_20260318.md)
- [Semantic Search Evaluation Workflow](validation/semantic_search_evaluation_workflow.md)
- [Analysis and Roadmap](AXON_ANALYSIS_AND_ROADMAP.md)
- [Session Handover](SESSION_HANDOVER.md)

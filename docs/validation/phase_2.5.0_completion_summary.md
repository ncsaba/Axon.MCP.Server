# Phase 2.5.0 Baseline Test Data - Completion Summary

**Date:** 2026-03-23
**Status:** ✅ COMPLETED

## Objective

Create baseline test data for multi-branch tracking implementation by indexing two repositories with complete semantic search capabilities (symbols, chunks, embeddings).

## Repositories Indexed

| Repository | Clone URL | Branch | Language | Files | Symbols | Chunks | Embeddings | Relations |
|------------|-----------|--------|----------|-------|---------|--------|------------|-----------|
| workspace-axon-mcp | `https://github.com/ncsaba/workspace-axon-mcp.git` | main | Python | 11 | 219 | 421 | 421 | 28 |
| jverein | `https://github.com/ncsaba/jverein.git` | master | Java | 826 | 8,626 | 9,432 | 9,432 | 28,818 |

## Database Metrics

### Row Counts by Table

| Table | Row Count |
|-------|-----------|
| repositories | 2 |
| file_instances | 837 |
| file_contents | 837 |
| chunks | 9,853 |
| embeddings | 9,853 |
| symbols | 8,845 |
| relations | 28,846 |
| module_summaries | 37 |
| chunk_symbol_links | 9,853 |

### Storage Sizes

| Table | Total Size | Table Size | Index Size |
|-------|------------|------------|------------|
| embeddings | 130 MB | 896 kB | 129 MB |
| chunks | 15 MB | 13 MB | 1872 kB |
| symbols | 8984 kB | 4856 kB | 4128 kB |
| relations | 5064 kB | 2368 kB | 2696 kB |
| chunk_symbol_links | 2456 kB | 800 kB | 1656 kB |
| file_instances | 792 kB | 200 kB | 592 kB |
| file_contents | 584 kB | 128 kB | 456 kB |
| module_summaries | 232 kB | 88 kB | 144 kB |
| repositories | 128 kB | 8192 bytes | 120 kB |

**Total Database Size:** ~161 MB (including vector index)

## Artifacts Created

1. **Database Snapshot:**
   - `axon-src/.archive/baseline_test_data_complete_20260323.dump` (49 MB) - Complete with embeddings

2. **Execution Plan:**
   - `axon-src/docs/validation/phase_2.5.0_baseline_execution_plan.md`

## Issues Resolved

### Ollama Embedding Configuration

**Problem:** Celery worker was started without `OLLAMA_BASE_URL` environment variable, causing it to default to `http://localhost:11434/v1` which doesn't work inside the devcontainer.

**Solution:** Restarted Celery worker with:
```bash
export OLLAMA_BASE_URL="http://host.docker.internal:11434/v1"
export EMBEDDING_PROVIDER="ollama"
export OLLAMA_EMBEDDING_MODEL="mxbai-embed-large"
```

**Verification:** Test script `scripts/test_mxbai_embed_large.py` confirmed:
- Similarity (auth vs auth-like): 0.72 ✅
- Similarity (auth vs math-like): 0.41 ✅

## Embedding Generation

After fixing Ollama configuration, generated embeddings for all 9,853 chunks:
- Processing time: ~6 minutes
- Batch size: 100 chunks per batch
- Model: `mxbai-embed-large` (1024 dimensions)
- Provider: Ollama (host machine via `host.docker.internal:11434`)

## Next Steps - Phase 2.5.1

Phase 2.5.1 will implement the COMMIT entity and tree-based file tracking:

1. **Add COMMIT table** with columns:
   - `commit_sha` (VARCHAR(40), PK) - Git commit hash
   - `repository_id` (FK to repositories)
   - `author_email`, `author_name`
   - `committed_at`, `pushed_at`
   - `message` (TEXT)
   - `parent_commit_sha` (VARCHAR(40), nullable - for delta chains)

2. **Add TREE_FILE junction table** with columns:
   - `id` (serial, PK)
   - `commit_id` (FK to commits)
   - `file_instance_id` (FK to file_instances)
   - `flags` (INTEGER - encodes tree_type and operation)

3. **Migrate existing data** to create COMMIT records for current repository states

4. **Add BRANCH and TAG tables** for named references

5. **Create delta resolution functions** for first-match-wins algorithm

## Running Services (for reference)

- API Server: Terminal 2 (port 8080, auth disabled)
- Celery Worker: Terminal 3 (queues: default, repository_sync, file_parsing, embeddings, discovery_inventory)
  - Configured with `OLLAMA_BASE_URL=http://host.docker.internal:11434/v1`

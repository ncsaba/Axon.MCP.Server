# Semantic Search Embedding Runbook

## Purpose

This runbook defines the operational contract for Axon's semantic-search embedding baseline.

Use it when:

- verifying the current semantic-search corpus
- rebuilding embeddings after a model or dimension change
- validating that the live DB still matches the fixed pgvector contract

## Current Contract

| Item | Current value | Source of truth |
| --- | --- | --- |
| Embedding provider baseline | `ollama` in local dev | `.devcontainer/devcontainer.json`, runtime env |
| Embedding model baseline | `mxbai-embed-large` | `src/config/settings.py` |
| Fixed embedding dimension | `1024` | `src/config/embedding_contract.py` |
| ANN index name | `embeddings_vector_idx` | `src/vector_store/pgvector_store.py` |
| ANN index type | `hnsw` | live DB + `src/vector_store/pgvector_store.py` |
| Vector column contract | `vector(1024)` | migration-backed schema baseline |

## Change Matrix

| Change type | Schema change | Re-embedding required | Full DB reset recommended | Notes |
| --- | --- | --- | --- | --- |
| No model/dimension change, routine validation only | `No` | `No` | `No` | Verify index + corpus shape only. |
| Model version/provider changes but dimension stays `1024` | `No` | `Yes` | `Usually yes` | Existing vectors are semantically incompatible even if dimensions match. |
| Dimension change | `Yes` | `Yes` | `Yes` | Update schema contract, regenerate all vectors, rerun planner validation. |
| HNSW parameter tuning only | `Index only` | `No` | `No` | Recreate the ANN index and rerun planner/latency validation. |

## Pre-Flight

1. Activate the project environment:

```bash
source /home/vscode/.venv-dev/bin/activate
```

2. Set DB env vars explicitly:

```bash
export DATABASE_URL='postgresql+asyncpg://indexer:indexer@localhost:5432/indexer'
export TEST_DATABASE_URL='postgresql+asyncpg://indexer:indexer@localhost:5432/indexer'
```

3. Confirm Ollama reachability for the baseline model:

```bash
python scripts/test_mxbai_embed_large.py
```

## Verify The Live Contract

Run these checks from `/workspaces/axon-mcp/axon-src`.

Check model + dimension homogeneity:

```bash
psql -h /tmp/pgsocket -U indexer -d indexer -At -c \
  "SELECT model_name, model_version, dimension, COUNT(*) FROM embeddings GROUP BY model_name, model_version, dimension ORDER BY COUNT(*) DESC;"
```

Expected shape:

- one row
- `mxbai-embed-large`
- `1.0`
- `1024`

Check ANN index presence:

```bash
psql -h /tmp/pgsocket -U indexer -d indexer -At -c \
  "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname='public' AND tablename='embeddings' ORDER BY indexname;"
```

Expected critical entry:

- `embeddings_vector_idx`
- `USING hnsw (vector vector_cosine_ops)`

## Safe Rebuild Procedure

Use this when the semantic-search corpus must be rebuilt from scratch.

### A. If the model/dimension contract changed

1. Update the code-level contract first:
   - `src/config/embedding_contract.py`
   - relevant runtime settings in `src/config/settings.py`
   - any migration needed for `embeddings.vector`
2. Ensure the new model can actually generate vectors at the intended dimension.
3. Reset the database schema deliberately:

```bash
python scripts/reset_db.py --yes
```

Use `--use-test-db` if the rebuild is meant for the test database only.

### B. Rebuild the indexed corpus

1. Start the normal Axon runtime for indexing.
2. Re-index the target repositories through the standard repository-sync path.
3. Wait for parsing and embedding generation to complete.

Important:

- do not mix old and new embedding contracts in the same corpus
- do not treat a partial re-embedding as valid after a dimension change

## Post-Rebuild Validation

After rebuilding:

1. rerun the live contract checks above
2. rerun the planner/latency validation in `docs/validation/semantic_search_index_validation_20260318.md`
3. confirm the benchmark/evaluation workflow in `docs/validation/semantic_search_evaluation_workflow.md` is now runnable against a corpus with active indexed files

## Current Known Constraint

The current local DB snapshot used during the 2026-03-18 rerun has:

- valid `embeddings` rows on the `1024` contract
- the HNSW ANN index present
- legacy `files` rows still populated
- empty `file_instances`

Practical meaning:

- vector-stage planner validation is still meaningful
- end-to-end retrieval usefulness runs that depend on active-file lifecycle filtering should be done after a clean re-index into the current `file_instances` model

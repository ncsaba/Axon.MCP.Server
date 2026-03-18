# Semantic Search Index Validation 2026-03-18

Note:

- this validation was captured before the later switch to the `mxbai-embed-large` / fixed-`1024` embedding contract
- the current code contract has changed and this validation should be treated as historical until rerun on a real `1024` corpus
- the old `768` corpus should be deleted before rerunning this validation so the post-fix results are not mixed with historical embeddings

## Objective

Validate the new semantic-search DB indexing baseline against a real local corpus:

- confirm corpus size is large enough for meaningful planner checks
- confirm whether the new default ANN index exists
- measure current semantic-query latency
- confirm whether the current semantic SQL shape can use pgvector ANN indexing

## Environment

| Item | Value |
| --- | --- |
| Date | `2026-03-18` |
| Database | local Postgres at `localhost:5432/indexer` |
| Corpus | 2 repositories |
| Files | 972 |
| Symbols | 10,354 |
| Chunks | 11,710 |
| Embeddings | 11,710 |
| Stored embedding model | `sentence-transformers/all-mpnet-base-v2` |
| Stored embedding dimension | `768` |

Repository breakdown:

| Repository | Files | Symbols | Embeddings |
| --- | ---: | ---: | ---: |
| `jverein` | 814 | 8,711 | 9,520 |
| `dasc-ds-recommender` | 158 | 1,643 | 2,190 |

## Corpus Readiness

The corpus is large enough to make planner behavior meaningful.

This is not a toy case:

- 11.7k embeddings is sufficient to distinguish full-scan behavior from ANN-assisted behavior
- embeddings are homogeneous in the live DB: one model, one dimension

## Baseline Index State Before The Fix

Before the fixed-size fix:

- no `embeddings_vector_idx` pgvector ANN index existed on `embeddings`
- only B-tree indexes existed on `id`, `chunk_id`, and `symbol_id`

Observed `embeddings` indexes:

- `embeddings_pkey`
- `idx_embedding_chunk`
- `idx_embedding_symbol`
- `ix_embeddings_chunk_id`
- `ix_embeddings_symbol_id`

## Semantic Query Under Test

The validation used the same broad SQL shape as the shipped semantic search path:

1. fetch a target vector
2. score every embedding with cosine similarity
3. filter by threshold
4. group by `symbol_id` using max similarity
5. join back to symbols/files/repositories
6. sort top results

Threshold used:

- `0.5`

Representative embedding IDs used as target vectors:

- `1`
- `3`
- `9521`
- `9523`

## Measured Baseline Latency Before The Fix

Actual semantic-query timings before ANN index creation attempt:

| Target embedding ID | Avg ms | Min ms | Max ms | Rows |
| --- | ---: | ---: | ---: | ---: |
| `1` | 36.99 | 34.59 | 44.57 | 20 |
| `3` | 32.33 | 31.95 | 32.85 | 20 |
| `9521` | 23.95 | 23.15 | 24.67 | 20 |
| `9523` | 25.71 | 25.23 | 26.64 | 20 |

Interpretation:

- current latency is still tolerable at this corpus size
- the planner is achieving that by brute-force scan behavior, not by ANN indexing
- this will not scale cleanly as the corpus grows

## Planner Evidence Before The Fix

`EXPLAIN ANALYZE` on the current semantic query shape showed:

- `Seq Scan on embeddings e`
- `HashAggregate`
- `Sort`
- joins back to `symbols`, `files`, and `repositories`

Most important excerpt:

```text
Seq Scan on embeddings e  (actual time=0.001..0.411 rows=11710 loops=1)
```

That means the current semantic path is scanning the full embeddings table for the vector stage.

## Implemented Fix

Axon now uses a fixed-size embedding contract instead of an unbounded vector column.

Implemented behavior:

- lock `embeddings.vector` to `vector(1024)`
- enforce `dimension = 1024` in the schema
- create one raw-column HNSW index: `embeddings_vector_idx`
- make embedding generation fail fast if the configured model dimension is not `1024`
- keep the KNN candidate query shape: `ORDER BY distance LIMIT N`, then deduplicate back to symbols

Why this works:

- pgvector ANN indexing works directly on a fixed-size vector column
- the query no longer needs expression casts to match the index
- the application query remains in the pgvector KNN pattern that HNSW is designed for

Relevant code locations:

- `/workspaces/axon-mcp/axon-src/src/vector_store/pgvector_store.py`
- `/workspaces/axon-mcp/axon-src/src/api/services/search_service.py`
- `/workspaces/axon-mcp/axon-src/src/api/main.py`
- `/workspaces/axon-mcp/axon-src/src/database/migrations/versions/7c9a6d5f3e21_add_embeddings_hnsw_index.py`

## Index State After The Fix

Observed index after runtime validation:

- `embeddings_vector_idx`

Observed definition:

```text
CREATE INDEX embeddings_vector_idx
ON public.embeddings
USING hnsw (vector vector_cosine_ops)
WITH (m='16', ef_construction='64')
```

## Planner Evidence After The Fix

Two planner checks matter:

1. A simple KNN semantic query using a bound vector parameter
2. The full Axon-style query shape: KNN candidate retrieval, then symbol-level dedupe and joins

### Full Semantic Query Shape

The full query used:

1. KNN candidate retrieval over embeddings
2. `MAX(vector_score)` grouped by `symbol_id`
3. joins back to `symbols`, `files`, and `repositories`

Important excerpt:

```text
Index Scan using embeddings_vector_idx on embeddings e
```

That means the shipped parameterized semantic-search path is now using the ANN index in the vector stage.

## Measured Latency After The Fix

Full parameterized semantic query timings after the fixed-size fix:

| Query shape | Avg ms | Min ms | Max ms | Rows |
| --- | ---: | ---: | ---: | ---: |
| Full semantic query | 10.88 | 0.58 | 51.87 | 20 |

Warm-path interpretation:

- first run includes cache/setup effects
- warm runs were roughly `0.58 ms` to `0.76 ms`
- this is materially better than the pre-fix full-scan baseline

## Validation Outcome

| Check | Result |
| --- | --- |
| Corpus large enough for planner validation | `✅` |
| Actual semantic query measured on live DB | `✅` |
| Evidence of current full-scan vector stage | `✅` |
| Fixed-size HNSW index exists in live DB | `✅` |
| ANN index can be created on current schema | `✅` |
| Shipped parameterized semantic query uses ANN index | `✅` |
| S0B planner validation completed end-to-end | `✅` |

## Next Action

Capture the operational rebuild/runbook contract next:

1. what happens when someone wants to change embedding model or dimension
2. how schema migration and full re-embedding should be run safely
3. where the fixed `1024` contract is documented for operators

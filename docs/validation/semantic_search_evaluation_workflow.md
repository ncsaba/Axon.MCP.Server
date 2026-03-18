# Semantic Search Evaluation Workflow

## Purpose

This workflow defines how to evaluate semantic-search changes against the canonical seed set.

Use it for:

- chunk-content changes
- ranking/fusion changes
- threshold changes
- snippet-selection changes

## Evaluation Flow

```mermaid
flowchart LR
    A[Pre-flight] --> B[Run Seed Queries]
    B --> C[Record Top-1 / Top-3 / Top-5]
    C --> D[Record Noise and Failure Mode]
    D --> E[Run Follow-up Context Tool]
    E --> F[Summarize Before vs After]
```

## Pre-Flight Checks

| Check | Required | Why |
| --- | --- | --- |
| `python scripts/test_mxbai_embed_large.py` passes | `✅` | Confirms the active local embedding baseline is healthy. |
| `embeddings` are homogeneous on one model/version/dimension | `✅` | Prevents mixed-corpus comparisons. |
| `embeddings_vector_idx` exists | `✅` | Ensures latency/ranking checks are not comparing against accidental full scans. |
| Benchmark corpus is indexed in the current lifecycle model | `✅` | Current retrieval code filters on active file instances. |
| Seed set path is fixed | `✅` | Keeps comparisons stable across changes. |

Pre-flight stop condition:

- if `file_instances` is empty or has no active rows, do not score usefulness yet
- instead, rebuild/reindex the corpus first and record that the run was blocked by corpus state

## Execution Mode

Preferred evaluation surface:

1. run `search_code` in the same mode users consume it
2. capture the first 5 results exactly as returned
3. run one follow-up context/navigation tool from the best result when the seed query expects chaining

Recommended search parameters:

| Parameter | Value |
| --- | --- |
| `limit` | `5` |
| `hybrid` | `true` |
| Repository scope | fixed to the query's seed corpus when possible |

## Scoring Rules

| Metric | Rule |
| --- | --- |
| Top-1 useful | Result 1 directly answers the query. |
| Top-3 useful | Any of results 1-3 directly answer the query. |
| Top-5 useful | Any of results 1-5 directly answer the query. |
| Noisy-result rate | Count obviously irrelevant results in the first 5. |
| Follow-up success | The best result can be used to get more useful context without manual guesswork. |

## Recording Template

Use one row per query run.

| Query ID | Corpus | Top-1 useful | Top-3 useful | Top-5 useful | Noise count in top 5 | Best result | Follow-up tool | Follow-up success | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `AX-01` | `axon-src` | `yes/no` | `yes/no` | `yes/no` | `0-5` | symbol/path summary | `get_symbol_context` | `yes/no` | freeform |

## Before / After Comparison

For every semantic-search change:

1. run the same seed subset before the change
2. run the same seed subset after the change
3. summarize deltas in a compact table

Comparison table shape:

| Metric | Before | After | Delta | Interpretation |
| --- | --- | --- | --- | --- |
| Top-1 useful count | number | number | `+/-` | relevance movement |
| Top-3 useful count | number | number | `+/-` | broader usefulness movement |
| Average noise count | number | number | `+/-` | precision movement |
| Follow-up success count | number | number | `+/-` | chainability movement |

## Minimum Review Set

Use this minimum set before changing thresholds or semantic reranking:

| Slice | Minimum query count |
| --- | ---: |
| Exact identifier | 3 |
| Natural-language intent | 5 |
| Framework/import/docs | 6 |
| Java-specific | 5 |
| Search-to-context | 3 |

## Current Constraint On 2026-03-18

The local DB snapshot currently has:

- valid `1024` embeddings
- the HNSW ANN index
- benchmark repos present
- no active `file_instances`

Practical meaning:

- planner/latency validation can proceed
- usefulness scoring should wait until the corpus is re-indexed into the active lifecycle model

## Exit Criteria For S1

`S1` is complete when:

1. this workflow exists and is stable
2. the seed set exists and covers the target query categories
3. future ranking or threshold changes can cite the same review format

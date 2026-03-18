# Semantic Search Benchmark Run 2026-03-18

## Purpose

This artifact records the first full live usefulness pass on the refreshed active corpus for the non-`axon-src` benchmark set after:

- clean DB reset + full corpus rebuild into active `file_instances`
- context-limit-safe embedding retries
- import-aware chunk context materialization
- parser-empty fallback chunking
- chunk-aware keyword candidate search
- query-intent boosts for config/framework/API/UI/background/member-flow queries

## Corpus And Runtime

| Field | Value |
| --- | --- |
| Date | `2026-03-18` |
| Repositories | `dasc-ds-recommender`, `jverein` |
| Active `file_instances` | `496` |
| Symbols | `6,249` |
| Chunks | `7,098` |
| Embeddings | `7,098` |
| Missing embeddings | `0` |
| Embedding model | `mxbai-embed-large` |
| Search mode | `hybrid=true`, `limit=5` |

## Query Results

| Query ID | Corpus | Top-1 useful | Top-3 useful | Top-5 useful | Noise count in top 5 | Best result | Follow-up tool | Follow-up success | Notes |
| --- | --- | --- | --- | --- | ---: | --- | --- | --- | --- |
| `DS-01` | `dasc-ds-recommender` | `yes` | `yes` | `yes` | `1` | `Itemsets` | `get_symbol_context` | `not rerun` | Exact identifier retrieval stays strong. |
| `DS-02` | `dasc-ds-recommender` | `yes` | `yes` | `yes` | `1` | `ARMObjectiveFunction` | `get_symbol_context` | `yes` | Verified: `get_symbol_context` returns the objective-function signature, long-form docs, and source preview for the top result. |
| `DS-03` | `dasc-ds-recommender` | `yes` | `yes` | `yes` | `1` | `JobQueueController` | `get_symbol_context` | `not rerun` | API-route intent now surfaces the real controller and endpoint symbols. |
| `DS-04` | `dasc-ds-recommender` | `yes` | `yes` | `yes` | `0` | `OutputMongoDataRepository` | `get_symbol_context` | `not rerun` | Seed corrected from `postgres` to `mongo` to match the actual corpus. |
| `DS-05` | `dasc-ds-recommender` | `yes` | `yes` | `yes` | `1` | `Configuration and Environment Settings` | `get_symbol_context` | `not rerun` | Config docs plus `MongoConfig` now cover the intent cleanly. |
| `DS-06` | `dasc-ds-recommender` | `yes` | `yes` | `yes` | `1` | `JobQueueController` | `get_symbol_context` | `not rerun` | Entrypoint retrieval is now anchored on the controller rather than drifting to implementation-only symbols. |
| `JV-01` | `jverein` | `yes` | `yes` | `yes` | `0` | `SEPABugsView` / `AbrechnungSEPA` family | `get_symbol_context` | `not rerun` | SEPA domain symbols remain easy to retrieve. |
| `JV-02` | `jverein` | `yes` | `yes` | `yes` | `1` | `DBSupportMySqlImpl` | `get_symbol_context` | `not rerun` | Seed corrected from DI to Jameica DB-service wiring; DB-service symbols now answer the query. |
| `JV-03` | `jverein` | `yes` | `yes` | `yes` | `0` | `ProjektListView` / GUI view classes | `get_symbol_context` | `not rerun` | Seed corrected from HTTP endpoints to GUI views; UI-surface intent now returns real view classes. |
| `JV-04` | `jverein` | `yes` | `yes` | `yes` | `1` | `importMitglied` in `Import.java` | `get_symbol_context` | `yes` | Verified: `get_symbol_context` exposes the member-import signature, inline docs, and outgoing call list for the top result. |
| `JV-05` | `jverein` | `yes` | `yes` | `yes` | `0` | `DBSupportMySqlImpl` | `get_symbol_context` | `yes` | Verified: `get_symbol_context` returns inheritance context plus source preview, which is enough to continue into DB-support inspection. |
| `JV-06` | `jverein` | `yes` | `yes` | `yes` | `2` | `ZipMailer` | `get_symbol_context` | `not rerun` | Background-task intent is now usable, though update classes still add noise. |
| `JV-07` | `jverein` | `yes` | `yes` | `yes` | `1` | `DBSupportMySqlImpl` / `JVereinDBService` | `get_symbol_context` | `not rerun` | `uses jameica` is now broadly useful even if the very best plugin/bootstrap symbol still depends on wording. |
| `JV-08` | `jverein` | `yes` | `yes` | `yes` | `1` | `importFile` / `importMitglied` | `get_symbol_context` | `yes` | Verified: `get_symbol_context` exposes the top-level import entrypoint signature and downstream helper calls. |

## Aggregate Snapshot

| Metric | Result |
| --- | --- |
| Top-1 useful count | `14 / 14` |
| Top-3 useful count | `14 / 14` |
| Top-5 useful count | `14 / 14` |
| Follow-up success count | `4 / 4` sampled follow-up checks |

## Main Findings

1. The refreshed corpus is now good enough for a real benchmark pass; evaluation is no longer blocked on lifecycle-model or missing-embedding issues.
2. Corpus-accurate query wording matters. The old `uses postgres` and `HTTP endpoints` seeds were measuring the wrong thing for these repositories.
3. Ranking changes materially improved API-route, Mongo, GUI-view, config, and member-import retrieval.
4. The sampled search-to-context path is now working on the strongest updated queries; the remaining work is broader `axon-src` coverage in a future corpus run plus repeated regression sampling.

## Follow-Up Priorities

1. Extend the same benchmark discipline to `axon-src` once that corpus is present in the active local DB.
2. Add one small regression artifact for the sampled search-to-context chain so future ranking changes must preserve it.
3. Only then make threshold or higher-order reranking decisions.

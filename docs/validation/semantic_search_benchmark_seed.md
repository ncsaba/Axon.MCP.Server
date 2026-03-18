# Semantic Search Benchmark Seed

## Purpose

This is the canonical seed set for semantic-search usefulness checks under `search_code`.

It exists to stop search tuning from being heuristic-only.

## Corpus Set

| Corpus | Role | Status on 2026-03-18 | Why it is included |
| --- | --- | --- | --- |
| `axon-src` | Mixed Python + docs/config retrieval | `🧭` canonical target corpus, not required in the current live DB snapshot | Exercises the actual Axon retrieval surface and docs/config cases. |
| `dasc-ds-recommender` | Python-heavy corpus | `✅` embedded in the live DB snapshot | Covers Python implementation and intent queries. |
| `jverein` | Java-heavy corpus | `✅` embedded in the live DB snapshot | Covers Java semantic retrieval and framework/config usage. |

## Acceptance Rules

| Field | Meaning |
| --- | --- |
| Top-1 useful | The first result is directly helpful for the query. |
| Top-3 useful | At least one of the first three results is directly helpful. |
| Top-5 useful | Used for broader framework/docs/import queries where multiple result shapes are reasonable. |
| Follow-up success | The best result cleanly chains into `get_symbol_context` or another graph/context tool. |

## Seed Queries

| ID | Repository scope | Category | Query | Expected useful result type | Acceptance notes |
| --- | --- | --- | --- | --- | --- |
| `AX-01` | `axon-src` | Exact identifier | `SearchService` | `SearchService` class / search implementation symbol | Top-3 useful |
| `AX-02` | `axon-src` | Natural-language intent | `find semantic search ranking logic` | Search ranking / fusion implementation | Top-3 useful |
| `AX-03` | `axon-src` | Framework usage | `where do we define MCP search tools` | MCP search tool handlers or router entries | Top-5 useful |
| `AX-04` | `axon-src` | Import/dependency context | `uses pgvector` | Vector-store code, migrations, or setup docs | Top-5 useful |
| `AX-05` | `axon-src` | Docs-backed | `how to configure redis` | Setup docs, config docs, or runtime config symbols | Top-5 useful |
| `AX-06` | `axon-src` | Ambiguous multi-result | `search code snippets` | Search service, MCP formatters, or snippet-selection code | Top-5 useful with low noise |
| `AX-07` | `axon-src` | Search-to-context | `find request flow entrypoint` | Search result that chains into architecture/context tooling | Top-3 useful and follow-up success |
| `DS-01` | `dasc-ds-recommender` | Exact identifier | `similar items` | Item-item similarity implementation or symbol | Top-3 useful |
| `DS-02` | `dasc-ds-recommender` | Natural-language intent | `find recommendation scoring logic` | Core ranking/recommendation implementation | Top-3 useful |
| `DS-03` | `dasc-ds-recommender` | Framework usage | `where are API routes defined` | API/router/controller symbols | Top-5 useful |
| `DS-04` | `dasc-ds-recommender` | Import/dependency context | `uses mongo` | Mongo repositories, Mongo config, or DB architecture docs | Top-5 useful |
| `DS-05` | `dasc-ds-recommender` | Docs-backed | `how do we configure recommender settings` | Config files, env docs, or setup symbols | Top-5 useful |
| `DS-06` | `dasc-ds-recommender` | Search-to-context | `find the entrypoint for recommendation requests` | Service or API entrypoint that chains into context tools | Top-3 useful and follow-up success |
| `JV-01` | `jverein` | Exact identifier | `SEPA` | SEPA-related class, module, or package | Top-3 useful |
| `JV-02` | `jverein` | Framework wiring | `where is database service wired into jameica` | DB service interface/implementation or Jameica wiring surface | Top-5 useful |
| `JV-03` | `jverein` | GUI surface | `find Jameica GUI views` | Jameica GUI view classes under the plugin UI layer | Top-5 useful |
| `JV-04` | `jverein` | Java semantics | `import member csv data` | Member CSV import workflow, parser, or service classes | Top-5 useful |
| `JV-05` | `jverein` | Java semantics | `database configuration` | Persistence config, datasource setup, or repository layer | Top-5 useful |
| `JV-06` | `jverein` | Background work | `background tasks` | Background task/thread classes or reminder/task providers | Top-5 useful |
| `JV-07` | `jverein` | Framework usage | `uses jameica` | Jameica/Hibiscus integration symbols or imports | Top-5 useful |
| `JV-08` | `jverein` | Search-to-context | `find member import flow entrypoint` | Import flow entrypoint/service result that chains into context tools | Top-5 useful and follow-up success |

## Review Notes

Use this seed set as a contract, not as a one-time checklist.

Rules:

1. Add a new query when a retrieval regression is found in real use.
2. Do not delete a noisy query just because it is hard; either tighten the query wording or record why the corpus makes it ambiguous.
3. Keep at least:
   - 5 Java queries
   - 5 natural-language or intent queries
   - 3 search-to-context chaining queries

## Current Environment Constraint

The 2026-03-18 local DB snapshot now has the corrected embedding corpus on active `file_instances`.

That means:

- this seed set is ready
- usefulness scoring can now be run against the refreshed active lifecycle model
- the full live non-`axon-src` seed baseline is recorded in `docs/validation/semantic_search_benchmark_20260318.md`

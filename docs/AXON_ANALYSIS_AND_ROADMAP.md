# Axon Analysis and Roadmap

## Current Direction

This fork prioritizes:

- Python + Java + documentation/config indexing
- Integration-first validation with real local infrastructure
- Python-only runtime toolchain (no C# / Roslyn path)

## Current Baseline (2026-03-19)

`✅` complete/available, `🚧` incomplete/partial.

| Baseline Area | Status | Notes |
| --- | --- | --- |
| C# runtime path removal | `✅` | Removed from this fork runtime. |
| Python parser routing + symbol baseline | `✅` | Active in parser factory with builtin-AST-backed symbol extraction. |
| Java parser routing | `✅` | Active and functioning in parser factory. |
| Schema baseline reset | `✅` | Clean baseline established for current development track. |
| Local infra alignment | `✅` | PostgreSQL 17 + pgvector, Redis, manual Celery worker. |
| File instance/content lifecycle foundation | `✅` | Lifecycle/query correctness, content-ID-based refresh, chunk-link association, ORM compatibility-alias removal, baseline-schema alignment, and reset-schema full-suite validation are landed. For the current WIP cycle, fresh-schema reset/recreate is the preferred schema workflow, while Alembic remains available when explicitly needed. |
| Python semantic relation depth | `🚧` | Import graph edges, call relations, and FastAPI/Flask baseline endpoint extraction are landed; remaining work is richer import parse fidelity plus broader retrieval/chunk-quality validation. |
| Java semantic relation depth | `🚧` | Not yet at target for imports/calls/endpoints/dependencies. |

## Capability Snapshot

### Strong Areas

- Graph-oriented persistence model (symbols, relations, chunks, dependencies).
- MCP query surface and repository exploration tooling.
- Integration-ready dev setup for real infrastructure testing.

### Gaps

- Python semantic quick wins are now landed for imports, calls, and baseline FastAPI/Flask endpoint extraction.
- The next Python gap is retrieval quality: semantic chunk coherence, corpus discipline, and benchmark evidence versus kilocode-style chunking.
- Python import parsing is still intentionally lightweight: aliases, structured import records, wildcard typing, dynamic imports, and `__init__.py` re-export semantics remain to be done.

- Java semantic coverage is much stronger than before, but still needs benchmark-backed parity validation on shared repositories.

- Parser/query depth for docs/config formats is uneven by type.

## kilocode-Inspired Improvements

Adopt selectively from `external-inspiration/kilocode`:

1. Centralized extension registry and parser capability signaling.
2. Rich query-pack approach for language definitions (including Java and Python where tree-sitter-backed depth is warranted).
3. Explicit fallback chunking policy for parser-weak file types.
4. Broader parser/query regression coverage across languages.

Implementation plan for the current session direction:

- `/workspaces/axon-mcp/axon-src/docs/architecture/python_quick_win_and_semantic_search_plan.md`

## Strategic Plan: Build the Best Combined Indexer

`✅` completed, `🧭` next action, `🔥` risk.

| Phase | Status | Focus | Risk |
| --- | --- | --- | --- |
| Phase 1: File instance/content separation | `✅` | Runtime slice is landed: lifecycle/query correctness, shared-chunk association, incremental git parity, and reset-schema full-suite validation are complete. Remaining follow-up is doc/test-environment alignment, not a runtime blocker. | `🔥` stale branch-status docs could make Phase 1 look less complete than the shipped runtime actually is. |
| Phase 2: Python semantic parity | `🧭` | Reach practical Python parity for imports, calls, and framework endpoints using the language strategy seams already in the extractor layer. | `🔥` Python uses builtin AST today, so parity work must avoid hard-coding tree-sitter assumptions into shared extractor paths. |
| Phase 3: Parser platform consolidation | `🧭` | Capability matrix integration, strategy interfaces, fallback policy. | `🔥` temporary extractor regressions during refactor. |
| Phase 4: Java benchmark validation and precision closure | `🧭` | Confirm current Java breadth on shared repos and close any exposed edge cases. | `🔥` feature surface validation may expose parser/query gaps that were not covered by synthetic tests. |
| Phase 5: Axon differentiation (surpass gate) | `🧭` | Exceed parity using graph-native relations, richer traversal context, and MCP retrieval quality. | `🔥` requires representative benchmark repos and stable scoring criteria. |
| Phase 6: Integration validation at scale | `🧭` | Repeated end-to-end runs on real target repositories. | `🔥` infra/runtime variance can mask parser issues. |

## Near-Term Execution Queue

`✅` completed, `🧭` next action.

| Item | Status |
| --- | --- |
| Parser capability matrix document at `docs/architecture/parser_capability_matrix.md` | `✅` |
| File instance/content architecture design and deleted-file policy decision | `✅` |
| File instance/content implementation plan | `✅` |
| Foundational file instance/content runtime slice (runs + instance/content upsert + missing finalization) | `✅` |
| Lifecycle validation + broad active-instance read-path cleanup | `✅` |
| Sync-worker lifecycle validation (successful vs failed run finalization) | `✅` |
| Remaining query-surface classification for `File`/`FileInstance` callers | `✅` |
| Shared-chunk association model for true content-owned reuse | `✅` |
| Reset-schema full-suite validation for the current branch worktree | `✅` |
| Python import graph vertical slice (`IMPORTS` edges for intra-repo imports) | `✅` |
| Python call graph strategy vertical slice (`CALLS` edges without tree-sitter-only assumptions) | `✅` |
| Python endpoint extraction baseline (FastAPI/Flask-first) | `✅` |
| Python quick-win + later semantic-search overhaul plan | `✅` |
| Python decorator-aware retrieval chunk slice | `✅` |
| Python retrieval benchmark seed expansion | `✅` |
| Call-neighbor retrieval chunk context | `✅` |
| Complete Java feature coverage to kilocode-equivalent baseline (parity gate) | `🚧` |
| Java import parity expansion (package wildcard + static wildcard/member imports) | `✅` |
| Java call parity expansion (qualified static calls + overload arity resolution) | `✅` |
| Java endpoint parity expansion (JAX-RS `@Path` + Spring multi-method `@RequestMapping`) | `✅` |
| Java dependency parity expansion (Maven properties/dependencyManagement + Gradle platform/catalog patterns) | `✅` |
| Repository source abstraction (Git + Local directory) with single-provider runtime cleanup | `✅` |
| Repository registration + polling architecture doc | `✅` |
| Keycloak REST auth + personalized MCP token architecture doc | `✅` |
| Incremental indexing spec (file metadata gate + optional hash fallback + batch DB strategy) | `✅` |
| Streaming indexing implementation plan (slice-by-slice execution) | `✅` |
| Implement provider-neutral repository registration (`GIT`/`GITHUB`) | `✅` |
| Add scheduled repository polling + incremental commit-diff orchestration | `✅` |
| Add GitHub/provider-neutral discovery workflows | `🧭` |
| Add per-repository credentials and polling controls | `🧭` |
| Add webhook-triggered repository refresh | `🧭` |
| Replace runtime provider-enum alignment with explicit migration path | `🧭` |
| Implement Keycloak-backed REST auth | `✅` |
| Implement Keycloak browser login/session flow | `✅` |
| Implement personalized MCP token issuance/revocation | `✅` |
| Approve incremental indexing implementation contract | `🧭` |
| Execute streaming indexing slices (metadata contract -> gate -> parse fanout -> embed -> aggregate) | `🧭` |
| Implement file instance/content separation vertical slice (chunks + embeddings first) | `✅` |
| Introduce interfaces for import/call/dependency extraction by language | `🚧` |
| Implement Python semantic extractor set on the shared strategy seams | `✅` |
| Add integration checks focused on Python relation creation quality | `✅` |
| Implement first Java semantic extractor set | `✅` |
| Add integration checks focused on relation creation quality | `✅` |
| Define and track Axon-surpass metrics after parity gate completion | `🧭` |

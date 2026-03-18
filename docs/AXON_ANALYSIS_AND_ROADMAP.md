# Axon Analysis and Roadmap

## Current Direction

This fork prioritizes:

- Python + Java + documentation/config indexing
- Integration-first validation with real local infrastructure
- Python-only runtime toolchain (no C# / Roslyn path)

## Current Baseline (2026-03-18)

`✅` complete/available, `🚧` incomplete/partial.

| Baseline Area | Status | Notes |
| --- | --- | --- |
| C# runtime path removal | `✅` | Removed from this fork runtime. |
| Java parser routing | `✅` | Active and functioning in parser factory. |
| Schema baseline reset | `✅` | Clean baseline established for current development track. |
| Local infra alignment | `✅` | PostgreSQL 17 + pgvector, Redis, manual Celery worker. |
| File instance/content lifecycle foundation | `✅` | Lifecycle/query correctness, content-ID-based refresh, chunk-link association, ORM compatibility-alias removal, baseline-schema alignment, and reset-schema full-suite validation are landed. For the current WIP cycle, fresh-schema reset/recreate is the preferred schema workflow, while Alembic remains available when explicitly needed. |
| Java semantic relation depth | `🚧` | Not yet at target for imports/calls/endpoints/dependencies. |

## Capability Snapshot

### Strong Areas

- Graph-oriented persistence model (symbols, relations, chunks, dependencies).
- MCP query surface and repository exploration tooling.
- Integration-ready dev setup for real infrastructure testing.

### Gaps

- Java semantic extractors are incomplete relative to target:
- import relationship resolution
- call graph extraction
- endpoint extraction
- dependency semantics for Java ecosystem

- Parser/query depth for docs/config formats is uneven by type.

## kilocode-Inspired Improvements

Adopt selectively from `external-inspiration/kilocode`:

1. Centralized extension registry and parser capability signaling.
2. Rich query-pack approach for language definitions (including Java).
3. Explicit fallback chunking policy for parser-weak file types.
4. Broader parser/query regression coverage across languages.

## Strategic Plan: Build the Best Combined Indexer

`✅` completed, `🧭` next action, `🔥` risk.

| Phase | Status | Focus | Risk |
| --- | --- | --- | --- |
| Phase 1: File instance/content separation | `✅` | Runtime slice is landed: lifecycle/query correctness, shared-chunk association, incremental git parity, and reset-schema full-suite validation are complete. Remaining follow-up is doc/test-environment alignment, not a runtime blocker. | `🔥` stale branch-status docs could make Phase 1 look less complete than the shipped runtime actually is. |
| Phase 2: Parser platform consolidation | `🧭` | Capability matrix integration, strategy interfaces, fallback policy. | `🔥` temporary extractor regressions during refactor. |
| Phase 3: Java semantic completeness (parity gate) | `🧭` | Reach at least kilocode-equivalent Java capability coverage for imports/calls/endpoints/dependencies. | `🔥` feature surface parity can expose parser/query gaps. |
| Phase 4: Axon differentiation (surpass gate) | `🧭` | Exceed kilocode baseline via graph-native relations, richer traversal context, and MCP retrieval quality. | `🔥` requires representative benchmark repos and stable scoring criteria. |
| Phase 5: Integration validation at scale | `🧭` | Repeated end-to-end runs on real Java repositories. | `🔥` infra/runtime variance can mask parser issues. |

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
| Complete Java feature coverage to kilocode-equivalent baseline (parity gate) | `🧭` |
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
| Implement Keycloak-backed REST auth | `✅` |
| Implement Keycloak browser login/session flow | `✅` |
| Implement personalized MCP token issuance/revocation | `✅` |
| Approve incremental indexing implementation contract | `🧭` |
| Execute streaming indexing slices (metadata contract -> gate -> parse fanout -> embed -> aggregate) | `🧭` |
| Implement file instance/content separation vertical slice (chunks + embeddings first) | `✅` |
| Introduce interfaces for import/call/dependency extraction by language | `🚧` |
| Implement first Java semantic extractor set | `🚧` |
| Add integration checks focused on relation creation quality | `🚧` |
| Define and track Axon-surpass metrics after parity gate completion | `🧭` |

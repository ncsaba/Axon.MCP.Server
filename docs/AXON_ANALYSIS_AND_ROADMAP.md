# Axon Analysis and Roadmap

## Current Direction

This fork prioritizes:

- Python + Java + documentation/config indexing
- Integration-first validation with real local infrastructure
- Python-only runtime toolchain (no C# / Roslyn path)

## Current Baseline (2026-03-11)

`✅` complete/available, `🚧` incomplete/partial.

| Baseline Area | Status | Notes |
| --- | --- | --- |
| C# runtime path removal | `✅` | Removed from this fork runtime. |
| Java parser routing | `✅` | Active and functioning in parser factory. |
| Schema baseline reset | `✅` | Clean baseline established for current development track. |
| Local infra alignment | `✅` | PostgreSQL 17 + pgvector, Redis, manual Celery worker. |
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
| Phase 1: Parser platform consolidation | `🧭` | Capability matrix integration, strategy interfaces, fallback policy. | `🔥` temporary extractor regressions during refactor. |
| Phase 2: Java semantic completeness (parity gate) | `🧭` | Reach at least kilocode-equivalent Java capability coverage for imports/calls/endpoints/dependencies. | `🔥` feature surface parity can expose parser/query gaps. |
| Phase 3: Axon differentiation (surpass gate) | `🧭` | Exceed kilocode baseline via graph-native relations, richer traversal context, and MCP retrieval quality. | `🔥` requires representative benchmark repos and stable scoring criteria. |
| Phase 4: Integration validation at scale | `🧭` | Repeated end-to-end runs on real Java repositories. | `🔥` infra/runtime variance can mask parser issues. |

## Near-Term Execution Queue

`✅` completed, `🧭` next action.

| Item | Status |
| --- | --- |
| Parser capability matrix document at `docs/architecture/parser_capability_matrix.md` | `✅` |
| Complete Java feature coverage to kilocode-equivalent baseline (parity gate) | `🧭` |
| Introduce interfaces for import/call/dependency extraction by language | `🚧` |
| Implement first Java semantic extractor set | `🚧` |
| Add integration checks focused on relation creation quality | `🚧` |
| Define and track Axon-surpass metrics after parity gate completion | `🧭` |

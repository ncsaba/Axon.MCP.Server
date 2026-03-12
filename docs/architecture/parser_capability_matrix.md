# Parser Capability Matrix

## Snapshot

- Date: 2026-03-11
- Scope: Current Axon parser/extractor capabilities and next implementation steps
- Goal: Provide the execution checklist for Java semantic parity and parser-platform consolidation

## Status Icons

- `✅`: implemented and active
- `🚧`: implemented with limited scope/fidelity
- `🛑`: not implemented

## Capability Matrix (Current)

`✅` complete, `🚧` partial, `🛑` not available.

| Language / Asset | Discovery + Routing | Symbol Extraction | Import Extraction (Parse) | Import Relations (Graph) | Call Graph Relations | API Endpoint Extraction | Dependency Extraction | Notes |
|---|---|---|---|---|---|---|---|---|
| Python (`.py`) | `✅` | `✅` | `🚧` | `🛑` | `🛑` | `🛑` | `✅` | Python dependency manifests are supported; semantic graph extractors are limited |
| Java (`.java`) | `✅` | `✅` | `✅` | `🚧` | `🚧` | `🚧` | `🚧` | Basic import/call/endpoint/dependency extraction is implemented with partial fidelity |
| JavaScript (`.js/.jsx/.mjs`) | `✅` | `✅` | `✅` | `✅` | `✅` | `✅` | `✅` | Strongest semantic path today |
| TypeScript (`.ts/.tsx`) | `✅` | `✅` | `✅` | `✅` | `✅` | `✅` | `✅` | Shares most JS extractor behavior |
| Vue (`.vue`) | `✅` | `✅` | `🚧` | `🚧` | `🚧` | `🚧` | `🛑` | Backed by JS/TS parser logic; fidelity depends on embedded script content |
| Markdown (`.md/.markdown`) | `✅` | `✅` | `🛑` | `🛑` | `🛑` | `🛑` | `🛑` | Documentation symbols/sections are supported |
| SQL (`.sql/.ddl`) | `✅` | `🚧` | `🛑` | `🛑` | `🛑` | `🛑` | `🛑` | Basic parse support, limited semantic graph usage |
| Config/Docs JSON/YAML/XML/OpenAPI | `🚧` | `🚧` | `🛑` | `🛑` | `🛑` | `🚧` | `🛑` | Target area for parser-depth and fallback chunking improvements |

## Cross-Cutting Extractor Capabilities (Current)

`✅` complete, `🚧` partial, `🛑` not available.

| Capability | JS/TS | Java | Python | Other |
|---|---|---|---|---|
| Import relationship resolver | `✅` | `🚧` | `🛑` | `🛑` |
| Call extraction + `CALLS` relation | `✅` | `🚧` | `🛑` | `🛑` |
| Outgoing API call extraction | `✅` | `🛑` | `🛑` | `🛑` |
| Event publish/subscribe extraction | `✅` | `🛑` | `🛑` | `🛑` |
| API endpoint extraction | `✅` | `🚧` | `🛑` | `🚧` |
| Dependency manifest extraction | `✅` | `🚧` | `✅` | `🛑` |

## Source Mapping (Current Implementation)

- Parser routing: `src/parsers/__init__.py`
- Java parser: `src/parsers/java_parser.py`
- Import relation builder: `src/extractors/import_resolver.py`
- Call graph builder: `src/extractors/call_graph_builder.py`
- Outgoing calls: `src/extractors/outgoing_call_extractor.py`
- Events: `src/extractors/event_extractor.py`
- API endpoints: `src/extractors/api_extractor.py`
- Dependencies: `src/extractors/dependency_extractor.py`
- Strategy interfaces: `src/extractors/strategy_interfaces.py`

## Planned Interfaces (Language Strategy Layer)

1. `ImportExtractionStrategy`
- `extract_imports(parse_result_or_ast, file_ctx) -> list[ImportRef]`
- Implementations: `JavaScriptImportStrategy`, `JavaImportStrategy`, `PythonImportStrategy`

2. `CallExtractionStrategy`
- `extract_calls(symbol_ast, file_ctx) -> list[CallRef]`
- Implementations: `JavaScriptCallStrategy`, `JavaCallStrategy`

3. `EndpointExtractionStrategy`
- `extract_endpoints(symbols_or_ast, file_ctx) -> list[EndpointRef]`
- Implementations: `JavaScriptEndpointStrategy`, `JavaEndpointStrategy`

4. `DependencyManifestStrategy`
- `supports(file_name) -> bool`
- `extract_packages(file_path) -> list[PackageRef]`
- Implementations: `NpmStrategy`, `PythonStrategy`, `JavaMavenStrategy`, `JavaGradleStrategy`

## Implementation Phases Using This Matrix

`✅` completed, `🧭` next action.

| Phase | Status | Scope |
| --- | --- | --- |
| Define strategy interfaces and register existing JS/TS implementations | `🚧` | Strategy interfaces + registry seams added across import/call/endpoint/dependency extractors |
| Add Java implementations for imports, calls, endpoints | `🧭` | Deliver first Java semantic relation wave |
| Add Java dependency strategies (Maven/Gradle) and relation persistence | `🧭` | Expand dependency intelligence for Java repos |
| Add parser capability flags and fallback chunking policy | `🧭` | Improve resilience for parser-weak formats |

## Current Increment (2026-03-11)

`✅` implemented in this increment.

| Increment | Scope | Validation |
| --- | --- | --- |
| Java import relations vertical slice | Add Java import extraction/resolution in `ImportRelationshipBuilder` and persist `IMPORTS` edges for basic class imports | Verified on 2026-03-11 by running `tests/integration/test_java_import_relationships.py` and `tests/integration/test_post_cleanup_integration.py` against local PostgreSQL test DB (`indexer`) |
| Java call relations vertical slice | Add Java method call extraction in `CallGraphBuilder` path and persist `CALLS` edges for common invocation patterns | Verified on 2026-03-11 by running `tests/integration/test_java_call_relationships.py` against local PostgreSQL test DB (`indexer`) |
| Java endpoint extraction vertical slice | Add Java annotation-based endpoint extraction (Spring/JAX-RS baseline) and persist endpoint symbols | Verified on 2026-03-11 by running `tests/integration/test_java_api_endpoint_extraction.py` against local PostgreSQL test DB (`indexer`) |
| Java dependency extraction vertical slice | Add Maven/Gradle manifest extraction in `DependencyExtractor` and persist repository dependencies | Verified on 2026-03-11 by running `tests/integration/test_java_dependency_extraction.py` against local PostgreSQL test DB (`indexer`) |
| Strategy seam refactor (non-breaking) | Introduce explicit strategy interfaces + language/file strategy registries for import/call/endpoint/dependency extraction | Verified on 2026-03-12 by rerunning Java integration slices and baseline post-cleanup integration tests |

## Acceptance Criteria for "Java Semantic Parity v1"

`✅` required.

| Criterion | Gate |
| --- | --- |
| Java import relations persisted as `IMPORTS` edges with measurable resolution rate | `🚧` |
| Java call relations persisted as `CALLS` edges for common invocation patterns | `🚧` |
| Java endpoint extraction supports common controller/router patterns in target repos | `🚧` |
| Java dependency manifests (`pom.xml`, `build.gradle*`) are parsed and stored | `🚧` |
| Integration tests validate end-to-end indexing on real Java repositories in dev-container | `🚧` |

## Benchmark Gates: Match Then Surpass

`✅` required.

| Gate | Goal | Pass Condition |
| --- | --- | --- |
| Parity Gate (kilocode-equivalent) | Match at least the Java capability breadth demonstrated by kilocode reference patterns. | Java imports/calls/endpoints/dependencies all meet parity checklist on shared benchmark repos. |
| Surpass Gate (Axon-native) | Exceed parity using Axon graph model and MCP retrieval strengths. | Improved relation/traversal usefulness over parity baseline with explicit benchmark evidence. |

## Java Parity Checklist (kilocode-equivalent baseline)

`✅` complete in this fork, `🚧` in progress.

| Area | Checklist Item | Status |
| --- | --- | --- |
| Imports | Direct class import relations (`import a.b.C`) | `✅` |
| Imports | Package wildcard import relations (`import a.b.*`) | `✅` |
| Imports | Static wildcard/member import relations (`import static a.b.C.*`, `import static a.b.C.X`) | `✅` |
| Calls | Receiver-aware instance call resolution | `✅` |
| Calls | Static call resolution and overloaded method handling | `✅` |
| Endpoints | Spring + JAX-RS annotation coverage for common controller patterns | `✅` |
| Dependencies | Maven/Gradle coverage including variable/property/BOM-heavy declarations | `🚧` |
| Validation | Run parity checklist on shared benchmark Java repositories | `🚧` |

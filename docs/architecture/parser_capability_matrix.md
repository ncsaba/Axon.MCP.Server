# Parser Capability Matrix

## Snapshot

- Date: 2026-03-19
- Scope: Current Axon parser/extractor capabilities and next implementation steps
- Goal: Provide the execution checklist for Python semantic parity first, then parser-platform consolidation and Java validation closure

## Status Icons

- `✅`: implemented and active
- `🚧`: implemented with limited scope/fidelity
- `🛑`: not implemented

## Capability Matrix (Current)

`✅` complete, `🚧` partial, `🛑` not available.

| Language / Asset | Discovery + Routing | Symbol Extraction | Import Extraction (Parse) | Import Relations (Graph) | Call Graph Relations | API Endpoint Extraction | Dependency Extraction | Notes |
|---|---|---|---|---|---|---|---|---|
| Python (`.py`) | `✅` | `✅` | `🚧` | `✅` | `✅` | `✅` | `✅` | Python import, call graph, and FastAPI/Flask baseline endpoint slices are landed; import parsing still uses flat strings rather than rich import records |
| Java (`.java`) | `✅` | `✅` | `✅` | `✅` | `✅` | `✅` | `✅` | Java import, call, endpoint, and dependency parity slices are landed; shared-repo benchmark validation remains next |
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
| Import relationship resolver | `✅` | `✅` | `✅` | `🛑` |
| Call extraction + `CALLS` relation | `✅` | `✅` | `✅` | `🛑` |
| Outgoing API call extraction | `✅` | `🛑` | `🛑` | `🛑` |
| Event publish/subscribe extraction | `✅` | `🛑` | `🛑` | `🛑` |
| API endpoint extraction | `✅` | `✅` | `✅` | `🚧` |
| Dependency manifest extraction | `✅` | `✅` | `✅` | `🛑` |

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
- Implementations: `JavaScriptCallStrategy`, `JavaCallStrategy`, `PythonCallStrategy`

3. `EndpointExtractionStrategy`
- `extract_endpoints(symbols_or_ast, file_ctx) -> list[EndpointRef]`
- Implementations: `JavaScriptEndpointStrategy`, `JavaEndpointStrategy`, `PythonEndpointStrategy`

4. `DependencyManifestStrategy`
- `supports(file_name) -> bool`
- `extract_packages(file_path) -> list[PackageRef]`
- Implementations: `NpmStrategy`, `PythonStrategy`, `JavaMavenStrategy`, `JavaGradleStrategy`

## Implementation Phases Using This Matrix

`✅` completed, `🧭` next action.

| Phase | Status | Scope |
| --- | --- | --- |
| Define strategy interfaces and register existing JS/TS implementations | `🚧` | Strategy interfaces + registry seams added across import/call/endpoint/dependency extractors |
| Add Python implementations for imports, calls, endpoints | `✅` | First Python semantic relation wave is landed without tree-sitter-only assumptions in shared paths |
| Close Java benchmark validation and remaining precision gaps | `🧭` | Confirm current Java breadth on shared benchmark repos |
| Add parser capability flags and fallback chunking policy | `🧭` | Improve resilience for parser-weak formats |

## Current Increment (2026-03-19)

`🚧` partially implemented in this increment.

| Increment | Scope | Validation |
| --- | --- | --- |
| Python import relations vertical slice | Add `PythonImportStrategy` in `ImportRelationshipBuilder` and persist `IMPORTS` edges for intra-repo `import` / `from ... import ...` cases, including relative imports | Verified on 2026-03-19 by running `tests/integration/test_python_import_relationships.py` and `tests/unit/test_python_parser.py` against the local devcontainer Python environment |
| Python call strategy seam | Refactor `CallGraphBuilder` so Python call extraction can operate on builtin AST-derived nodes instead of tree-sitter-only nodes | Verified on 2026-03-19 by running `tests/integration/test_python_call_relationships.py` against the local devcontainer Python environment |
| Python endpoint extraction baseline | Add framework-aware endpoint extraction for FastAPI/Flask-first patterns and persist endpoint symbols | Verified on 2026-03-19 by running `tests/integration/test_python_api_endpoint_extraction.py` against the local devcontainer Python environment |

## Known Python Import Parsing Gaps

`🧭` next parser-fidelity follow-ups, `✅` already covered by current parse output.

| Gap | Status | Why it is still open |
| --- | --- | --- |
| Module-relative and absolute import string capture | `✅` | Current builtin-AST parser emits enough flat import strings to support the first `IMPORTS` graph slice. |
| Structured import records (`module`, `symbol`, `alias`, `is_wildcard`) | `🧭` | Parser output is still a flat string list, which limits downstream precision and chunk-context quality. |
| Alias capture (`import x as y`, `from x import y as z`) | `🧭` | Alias targets are not preserved explicitly in parse output today. |
| Wildcard vs symbol-vs-module distinction | `🧭` | `from x import *` and `from x import y` are not represented as typed import records at parse level. |
| Dynamic/runtime import detection (`importlib.import_module`, `__import__`) | `🧭` | Current parser intentionally focuses on static AST imports only. |
| Re-export semantics through `__init__.py` / package surfaces | `🧭` | Parse output does not yet model package-level re-exports as a first-class import surface. |

## Acceptance Criteria for "Java Semantic Parity v1"

`✅` required.

| Criterion | Gate |
| --- | --- |
| Java import relations persisted as `IMPORTS` edges with measurable resolution rate | `✅` |
| Java call relations persisted as `CALLS` edges for common invocation patterns | `✅` |
| Java endpoint extraction supports common controller/router patterns in target repos | `✅` |
| Java dependency manifests (`pom.xml`, `build.gradle*`) are parsed and stored | `✅` |
| Integration tests validate end-to-end indexing on real Java repositories in dev-container | `🚧` |

## Acceptance Criteria for "Python Semantic Parity v1"

`✅` required.

| Criterion | Gate |
| --- | --- |
| Python intra-repo imports persist as `IMPORTS` edges for absolute and relative imports | `✅` |
| Python call relations persist as `CALLS` edges for common direct and attribute call patterns | `✅` |
| Python framework endpoint extraction supports FastAPI and Flask baseline patterns | `✅` |
| Integration tests validate end-to-end indexing on representative Python repositories in dev-container | `🚧` |

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
| Dependencies | Maven/Gradle coverage including variable/property/BOM-heavy declarations | `✅` |
| Validation | Run parity checklist on shared benchmark Java repositories | `🚧` |

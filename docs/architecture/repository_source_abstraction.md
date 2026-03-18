# Repository Source Abstraction

## Scope

Define a provider-agnostic repository source layer for sync/indexing runtime paths.

Targets:
- Support generic Git repositories without coupling to GitLab/Azure semantics.
- Support local directory indexing for benchmark and offline workflows.
- Remove Azure-specific runtime branches from indexing pipeline paths.

## Why

Current runtime paths branch directly on provider enum values in multiple subsystems:
- clone/discovery pipeline steps
- file worker path resolution
- API/call/reference/link extractors
- MCP repository file-content tooling

This creates tight coupling and blocks adding new source types cleanly.

## Current Product Status

The runtime layer is ahead of the public registration/API layer.

Today:
- runtime sync/index paths can resolve between generic git and local-directory sources
- repository registration now accepts `GITLAB`, `GITHUB`, and `GIT`
- MCP exposes repository read/navigation tools only; it does not expose repository registration or sync initiation
- periodic repository polling exists, but it is orchestrated by the sync layer rather than this abstraction

Practical takeaway:
- this document describes the runtime source model
- it does **not** mean generic GitHub/any-Git registration is finished end-to-end at the product/API level

## Source Model

`✅` required, `🚧` incremental, `🛑` removed.

| Concern | Target | Status |
| --- | --- | --- |
| Runtime source interface | `RepositorySource` protocol with `sync`, `get_repository_path`, `get_file_tree`, `get_head_commit` | `✅` |
| Source registry/factory | Resolve source by repository metadata instead of hardcoded `if provider` branches | `✅` |
| Generic git source | Single git-backed source using existing repository manager logic | `✅` |
| Local directory source | No-clone source for direct filesystem indexing | `✅` |
| Azure runtime source | Removed from runtime resolution and sync/extractor call sites | `🛑` |

## Migration Plan

`✅` done, `🧭` next, `🔥` risk.

| Step | Status | Notes |
| --- | --- | --- |
| Introduce source interface + registry | `✅` | Implemented in `src/repository_sources` |
| Add local directory source | `✅` | Local path/file URL clone targets resolve to `LocalDirectorySource` |
| Refactor runtime call sites to source resolver | `✅` | Pipeline/workers/extractors/MCP repository tool now use source registry |
| Remove Azure runtime branches and discovery routes | `✅` | Azure discovery route/service paths removed from active runtime |
| Validate end-to-end sync/indexing for git + local path | `🚧` | Unit + parity integration checks pass; benchmark local-directory e2e run pending |
| Expose generic git source through public repository registration APIs | `✅` | Public registration now accepts generic GitHub and Git providers |
| Add scheduled polling/orchestration for registered git repositories | `✅` | Implemented in the sync/orchestration layer, not inside this abstraction |

## Local Directory Contract

Local source is encoded in repository metadata without schema expansion:
- `provider`: existing git-compatible provider value
- `clone_url`: absolute path or `file://` URL to local directory
- `url`: informational, may match `clone_url`
- `path_with_namespace`: stable logical name used by APIs/UI

Current caveat:
- the runtime resolver accepts this model, but GitLab discovery remains the only provider-specific discovery flow

Resolver behavior:
- If `clone_url` is local-path-like (`/abs/path`, `./rel/path`, or `file://...`), use `LocalDirectorySource`.
- Otherwise use `GitRepositorySource`.

## Acceptance Criteria

`✅` required.

| Criterion | Gate |
| --- | --- |
| No indexing runtime code branches on Azure provider-specific logic | `✅` |
| Local directory repositories can be synced/indexed through standard pipeline | `✅` |
| Git repositories continue to sync/index unchanged | `✅` |
| Roadmap/docs updated to reflect source abstraction and Azure retirement | `✅` |

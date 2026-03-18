# Repository Registration And Sync Architecture

## Scope

Define the product-level repository onboarding and refresh model for Axon.

This document is the canonical reference for:
- repository registration
- initial indexing after registration
- recurring refresh/poll behavior
- incremental commit-diff processing
- future MCP registration/sync exposure

Related docs:
- runtime source abstraction: `docs/architecture/repository_source_abstraction.md`
- streaming/file-metadata incremental ideas: `docs/architecture/incremental_indexing_spec.md`
- file/content lifecycle architecture: `docs/architecture/file_instance_content_dedup_proposal.md`

## Current State

`✅` implemented, `🚧` partial, `🛑` not implemented.

| Area | Status | Notes |
| --- | --- | --- |
| REST endpoint to register one repository | `✅` | `POST /api/v1/repositories` creates the DB row and enqueues the first sync |
| REST endpoint to bulk-register repositories | `✅` | `POST /api/v1/repositories/bulk-add` exists |
| Immediate initial indexing after registration | `✅` | Registration triggers background sync automatically |
| Runtime git source abstraction | `✅` | Runtime resolves between generic git and local-directory sources |
| Local-directory indexing through standard pipeline | `✅` | Supported through repository metadata + source resolver |
| Public provider enum for generic Git/GitHub | `🛑` | Current API/provider surface is still `GITLAB`-only |
| GitLab discovery flow | `✅` | Group discovery route exists |
| Generic Git/GitHub registration through public API | `🚧` | Single-create path can persist repository metadata, but the provider/auth model is still GitLab-shaped |
| Periodic polling for new commits | `🛑` | No scheduled repository poller is configured today |
| Incremental commit-diff sync worker | `✅` | `IncrementalSyncWorker` exists and has integration coverage |
| Incremental commit-diff sync wired into production orchestration | `🛑` | Worker exists but is not part of the normal sync/poll flow |
| MCP tool to register repositories | `🛑` | MCP currently exposes read/navigation tools only |
| MCP tool to trigger repository sync | `🛑` | Not exposed today |

## Current Runtime Flow

```mermaid
flowchart TD
    A[REST create repository] --> B[Persist Repository row]
    B --> C[Enqueue sync_repository Celery task]
    C --> D[CloneStep]
    D --> E[Discovery + Parsing + Extraction + Embedding pipeline]
    E --> F[Repository status and last_commit_sha updated]
```

## Current Product Constraints

### What is already true

1. Repository registration is API-driven, not config-only.
2. Initial indexing already happens automatically after registration.
3. Runtime sync code already has the primitives needed for generic git and local-directory sources.
4. A commit-aware incremental worker already exists for modifications-only processing.

### What is not yet true

1. The public registration contract does not cleanly model `GITHUB` or generic `GIT`.
2. HTTPS clone auth is still GitLab-oriented in the current repository manager path.
3. There is no scheduled polling loop that compares remote/head state and chooses incremental processing.
4. MCP does not yet expose repository registration or sync initiation.

## Repository Access Model

## Current

`✅` active, `🚧` partial, `🛑` missing.

| Concern | Status | Notes |
| --- | --- | --- |
| GitLab API discovery | `✅` | Explicit GitLab client + discovery route |
| Generic git clone/update runtime | `✅` | Available through `GitRepositorySource` |
| Local path indexing | `✅` | Available through `LocalDirectorySource` |
| Provider-neutral credential model | `🛑` | Current HTTPS clone auth injects GitLab token semantics |
| GitHub/private generic Git credential handling | `🛑` | Not modeled explicitly yet |

## Target Product Flow

```mermaid
flowchart TD
    A[Register repository] --> B{Already indexed before?}
    B -->|No| C[Full sync/index]
    B -->|Yes| D[Store metadata and polling policy]
    C --> E[Persist last_commit_sha]
    D --> E
    E --> F[Scheduled poll]
    F --> G{HEAD changed?}
    G -->|No| H[No-op]
    G -->|Yes| I[Incremental commit-diff sync]
    I --> J[Selective parse/rebuild]
    J --> K[Persist new last_commit_sha]
```

## Recommended Direction

### Provider model

Move from a GitLab-shaped public model toward:

| Provider | Purpose | Status |
| --- | --- | --- |
| `GITLAB` | GitLab-specific discovery and API integration | `✅` current |
| `GITHUB` | GitHub-specific integration when API-specific discovery/webhooks are needed | `🧭` recommended |
| `GIT` | Provider-neutral git registration for any cloneable remote | `🧭` recommended |
| `LOCAL_DIRECTORY` or existing local-path contract | Local benchmark/offline indexing | `🧭` keep supported |

### Sync orchestration policy

Recommended product behavior:

1. Registration performs an initial full sync.
2. After a repository has a known `last_commit_sha`, scheduled refresh should prefer commit-diff incremental sync.
3. Full sync remains the fallback when:
   - the repository has never been indexed
   - the previous commit is unknown
   - the diff window is invalid or clone state is inconsistent
   - a forced/full reindex is requested

## Best Documentation Shape

Recommended approach: `hybrid`

### Add a new canonical doc

This document should remain the product-level source of truth for:
- current repository registration/sync behavior
- target provider support
- poll/incremental orchestration decisions
- MCP registration/sync direction

### Keep existing docs narrow and accurate

Update existing docs, but do not overload them:

| Doc | Role |
| --- | --- |
| `docs/api/rest_api.md` | Current shipped REST surface |
| `docs/api/mcp_tools.md` | Current shipped MCP tool surface |
| `docs/architecture/repository_source_abstraction.md` | Runtime source-resolution mechanics only |
| `docs/architecture/incremental_indexing_spec.md` | Streaming/file-metadata incremental ideas, not product polling orchestration |

## Execution Backlog

`🧭` next action, `🔥` risk.

| Item | Status | Notes |
| --- | --- | --- |
| Document current registration/sync state clearly | `✅` | This doc + linked API docs |
| Introduce provider enum/support for `GIT` and likely `GITHUB` | `🧭` | Needed for end-to-end generic repository onboarding |
| Decouple clone auth from GitLab-only token injection | `🧭` | Required for private GitHub and generic HTTPS remotes |
| Add scheduled poller for registered repositories | `🧭` | Missing orchestration layer today |
| Wire commit-diff incremental worker into poll flow | `🧭` | Worker exists; orchestration is missing |
| Add MCP registration/manual sync tools | `🧭` | Product UX enhancement after REST contract stabilizes |
| Add webhook-triggered refresh later | `🚧` | Useful later, but polling is the simpler first contract |

## Acceptance Criteria

`✅` required.

| Criterion | Gate |
| --- | --- |
| A repository can be registered through a stable public API contract | `🚧` |
| New registrations trigger initial indexing automatically | `✅` |
| Registered repositories can be refreshed on a schedule without manual intervention | `🛑` |
| Incremental commit-diff sync is used after the initial full index when safe | `🛑` |
| Generic Git/GitHub support is explicit in the provider and credential model | `🛑` |
| MCP support for repository registration/sync is explicitly documented as present or absent | `✅` |

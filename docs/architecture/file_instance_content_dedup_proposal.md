# File Instance / File Content Dedup Proposal (Deferred)

## Intent

Define a future architecture to avoid duplicating parsed artifacts for identical file content across:

1. Branches/worktrees of the same repository family.
2. Local checkouts and remote clones with identical file bytes.

This proposal is intentionally deferred until the streaming indexing pipeline is implemented and stabilized.

## Current Decision

1. Continue current async/streaming implementation first.
2. Do **not** change current interfaces/contracts for future-proofing in this phase.
3. Revisit dedup model after streaming behavior and performance are validated.

## Problem Statement

Current model is file-instance centric (`file_id`-scoped symbols/chunks/relations).  
Identical content in multiple instances is processed and stored repeatedly.

Goal:

- Separate **file identity in a source context** from **canonical content identity**.

## Proposed Conceptual Model

### 1) File Instance

Represents source-context metadata:

- repository/family/variant source
- path
- size
- mtime
- status/freshness

### 2) File Content

Represents canonical parsed content identity:

- content hash
- language / parse mode
- parser fingerprint/version

Content-level artifacts can be shared when safe.

## Shareability Rules (Planned)

| Artifact Type | Share by content key | Notes |
| --- | --- | --- |
| Normalized chunks | `✅` | Content-derived, context-stable |
| Embeddings | `✅` | Reuse for identical content + parser profile |
| Raw symbol extraction | `🚧` | Often shareable, but language/parser mode sensitive |
| Cross-file relations/import graph | `🛑` | Context-dependent, keep instance/variant scoped |

## Why Deferred

Combining dedup data-model refactor with streaming pipeline refactor in one pass would increase risk significantly:

1. Harder to isolate performance regressions.
2. Harder to isolate correctness regressions.
3. Harder to rollback quickly under load.

## Re-entry Criteria

Start this proposal after streaming implementation reaches stable baseline:

1. Discovery/gate/parse/aggregate/embed streaming path in production shape.
2. Stage lag and throughput metrics available.
3. Changed/new-only parse and changed-chunk-only embedding behavior validated.

## Next Step (When Activated)

Create a dedicated implementation plan with:

1. Schema changes (`file_instance` + `file_content` style split).
2. Migration/reset strategy per environment policy.
3. Incremental rollout of shared artifact types (chunks/embeddings first).

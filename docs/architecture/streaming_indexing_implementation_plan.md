# Streaming Indexing Implementation Plan

## Goal

Implement the async streaming indexing architecture end-to-end with:

1. `size + mtime` primary gating.
2. `content_hash` fallback gating.
3. Changed/new-only parse fanout.
4. Incremental graph + embedding processing.

This plan follows the approved constraints in `incremental_indexing_spec.md`.

Note:
- Dedup architecture (`file instance` vs `file content`) is tracked separately and deferred.
- See `docs/architecture/file_instance_content_dedup_proposal.md`.
- No interface changes for dedup are introduced during this streaming implementation plan.

## Current Baseline

`✅` present, `🚧` partial, `🛑` missing.

| Capability | Status | Notes |
| --- | --- | --- |
| Celery multi-queue runtime | `✅` | Queues configured (`repository_sync`, `file_parsing`, `embeddings`, etc.) |
| Monolithic repository sync pipeline | `✅` | Runs clone/discovery/parse/extract in one task |
| File metadata persistence (`size_bytes`, `content_hash`) | `✅` | Updated in `create_or_update_file()` |
| `last_modified` persistence | `✅` | Persisted in `create_or_update_file()`; legacy rows handled by hash fallback |
| Discovery batch producer (`scandir` fallback) | `✅` | Streaming provider + queue batch emission with idempotency key implemented. |
| Batch metadata gate worker | `🚧` | Implemented behind feature flag (`metadata_gate_enabled`); full cutover pending. |
| Changed/new-only parse pipeline | `🛑` | Not implemented |
| Incremental graph aggregator from parse events | `🚧` | Graph logic exists, not event-stream wired |
| Changed-chunk-only embedding batching | `🚧` | Batch generation exists, not filtered by change contract |

## Delivery Strategy

Implement in slices, each independently testable and reversible.

## Slice 1: Metadata Contract Hardening

### Code changes

1. Persist `last_modified` in file upsert/update path.
2. Normalize mtime handling (timezone-aware UTC).
3. Ensure metadata reads are available in one query per file batch.

### Target files

- `src/workers/file_worker.py`
- `src/database/models.py` (if helper updates needed only; schema already has field)
- `src/workers/pipeline/steps/discovery_step.py` (capture stat metadata payload)

### Acceptance criteria

1. New/updated files always store mtime.
2. Missing mtime is handled without exceptions.
3. Existing indexing behavior unchanged functionally.

## Slice 2: Discovery Batch Producer

### Code changes

1. Introduce `FileInventoryProvider` abstraction and batch-emitting discovery producer task.
2. Emit batch schema (`repository_id`, `run_id`, `batch_seq`, `observed_at`, `files[]`).
3. Support OS-native provider backends plus portable fallback.
4. Bound batch size and in-flight batches by config with backpressure.

### Target files

- `src/workers/tasks.py`
- `src/workers/celery_app.py` (queue routing for discovery/metadata gate)
- `src/workers/pipeline/steps/discovery_step.py` (producer mode)
- `src/config/settings.py` (batch-size/tuning knobs)
- `src/workers/file_inventory/*` (new provider module family)

### Acceptance criteria

1. Discovery emits multiple batches for large repos.
2. Batch schema validated in logs/tests and supports idempotency keys.
3. No requirement to hold full file list in memory.
4. Native backend strategy documented and pluggable.

Reference design:
- `docs/architecture/streaming_file_inventory_design.md`

### Slice 2A Status (2026-03-13)

`✅` implemented in this increment:

1. `FileInventoryProvider` abstraction plus portable `os.scandir` backend.
2. Streaming discovery emission with bounded in-flight publish operations.
3. Batch schema in producer includes `repository_id`, `run_id`, `batch_seq`, `observed_at`, `files[]`, and `idempotency_key`.
4. Celery queue route + task registration for discovery inventory payloads.
5. Baseline inventory metrics (`files/directories enumerated`, `batches emitted`, `emit latency`, `in-flight publish lag`).

Remaining Slice 2 items:

1. Native Linux backend + benchmark harness.
2. Full memory-decoupled parse fanout (current pipeline still populates `ctx.files` for Slice 3 compatibility).

## Slice 3: Metadata Gate Workers

### Code changes

1. Add metadata gate task consuming discovery batches.
2. Bulk-fetch DB file metadata for batch paths.
3. Apply policy:
   - `size + mtime` match => unchanged
   - else hash fallback => unchanged/changed
4. Emit parse jobs only for changed/new paths.

### Target files

- `src/workers/tasks.py` (new gate tasks)
- `src/workers/file_worker.py` (shared metadata helper functions)
- `src/workers/utils.py` (hash helper reuse/extensions)
- `src/workers/celery_app.py` (new queue route)

### Acceptance criteria

1. Unchanged files do not enqueue parse tasks.
2. Changed/new files do enqueue parse tasks.
3. Gate operates in batch DB mode (no per-file metadata query loops).

### Slice 3A Status (2026-03-13)

`✅` implemented in this increment:

1. Discovery-batch consumer task now performs metadata gate decisions in batch DB mode.
2. Decision policy implemented:
   - `size + mtime` match => unchanged
   - mismatch => hash fallback (when enabled)
   - new/changed => upsert file metadata + enqueue parse task
3. Batch idempotency claim support added via Redis key (`inventory_batch:{idempotency_key}`).
4. Decision telemetry added (`metadata_gate_files_total`).
5. Monolithic `ParsingStep` is bypassed when streaming cutover flag is enabled (`metadata_gate_enabled=true`), and metadata gate can execute parse inline for deterministic step ordering.

`🚧` remaining:

1. Remove/replace monolithic parsing step path and complete end-to-end streaming cutover.
2. Validate gate behavior with integration coverage over full queue topology.

## Slice 4: Parse Fanout + Idempotent Writes

### Code changes

1. Parse tasks consume per-file jobs from queue.
2. File-level extraction remains idempotent (delete/recreate for changed file only).
3. Emit parse output events for graph + embedding stages.

### Target files

- `src/workers/file_worker.py`
- `src/extractors/knowledge_extractor.py`
- `src/workers/tasks.py`

### Acceptance criteria

1. Parse workers parallelize across files.
2. No duplicate symbol/chunk growth on repeated unchanged runs.
3. Parse output event contract available for downstream consumers.

## Slice 5: Incremental Graph Aggregator Workers

### Code changes

1. Add workers consuming parse output events.
2. Incrementally update graph relations requiring cross-file/shared state.
3. Keep operations idempotent and retry-safe.

### Target files

- `src/workers/tasks.py`
- `src/workers/sync_worker.py` (reduce monolithic graph work)
- `src/services/link_service.py` and/or extractor graph modules

### Acceptance criteria

1. Graph updates happen from streamed parse outputs.
2. Retries do not corrupt relation counts.
3. Shared-state operations are measurable and bounded.

## Slice 6: Changed-Chunk Embedding Pipeline

### Code changes

1. Track changed/new chunk IDs from parse stage.
2. Send only changed chunk IDs to embedding workers.
3. Keep embedding generation batched and idempotent.

### Target files

- `src/workers/embedding_worker.py`
- `src/workers/pipeline/steps/embedding_step.py`
- `src/vector_store/pgvector_store.py`

### Acceptance criteria

1. Unchanged files produce no embedding work.
2. Embedding queue load tracks changed chunk volume only.
3. Repeated runs over unchanged tree keep embedding count stable.

## Slice 7: Streaming Observability + Guardrails

### Code changes

1. Add stage lag metrics and throughput metrics.
2. Emit decision stats (`new/changed/unchanged`) per batch.
3. Add DB query/load timing markers per stage.

### Target files

- `src/utils/metrics.py`
- `src/workers/*` stage tasks
- `src/api/routes/statistics.py` / stats service as needed

### Acceptance criteria

1. Per-stage lag visible.
2. Parse-skip ratio visible.
3. Regressions can be detected from metrics alone.

## Discussion Checkpoints (Before Coding Each Slice)

1. Queue topology and worker concurrency caps by stage.
2. Batch size defaults and backpressure behavior.
3. Event payload size limits and serialization strategy.
4. Failure/retry semantics and idempotency keys.
5. DB isolation/locking assumptions for concurrent updates.

## Performance Guardrails

1. All metadata checks are set-based queries.
2. No full-repo parse on unchanged tree.
3. No full-repo embedding pass on unchanged tree.
4. No unbounded in-memory file list required for discovery.

## Proposed Execution Order

1. Slice 1
2. Slice 2
3. Slice 3
4. Slice 4
5. Slice 6
6. Slice 5
7. Slice 7

Rationale:
- Deliver skip-path + parse fanout first.
- Then reduce embedding cost.
- Then finalize incremental shared graph behavior and observability.
